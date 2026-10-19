"""安装位置可自选 + 安装后列表自动刷新 + 深度扫描后台静默。

三件事一起验证，因为它们共用同一条链路：
  install_root → 解压目录 → 立刻入列表 → 扫描时要能扫到（extra_dirs）→ 删除保护
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QSettings as _QS  # noqa: E402

_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "install_loc.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
if os.path.exists(_TEST_INI):
    os.remove(_TEST_INI)
_QS_ORIG_INIT = _QS.__init__


def _qs_patched(self, *a, **k):
    """把两参数形态重定向到测试 ini，绝不碰用户真实设置。"""
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)


_QS.__init__ = _qs_patched

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from core.app_context import AppContext  # noqa: E402
import modules.version_library.module as vlm  # noqa: E402
from modules.version_library.module import VersionLibraryModule, VersionSelectorDialog  # noqa: E402


class _FakeProgDlg(vlm.QObject):
    canceled = vlm.pyqtSignal()

    def __init__(self, *a, **k):
        super().__init__()

    def setValue(self, v): pass
    def close(self): pass
    def setWindowTitle(self, t): pass
    def setWindowModality(self, m): pass
    def setMinimumDuration(self, d): pass


vlm.QProgressDialog = _FakeProgDlg  # type: ignore[attr-defined]

failures = []
STATE = {"loop": None}


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


# ── 假扫描线程：真全盘深扫在无头环境会跑很久，这里只验证接线 ──
class _FakeScanWorker(vlm.QThread):
    found = vlm.pyqtSignal(object)
    finished = vlm.pyqtSignal(int)
    last = None

    def __init__(self, svc, deep_scan=False, extra_dirs=None):
        super().__init__()
        self.deep_scan = deep_scan
        self.extra_dirs = extra_dirs or []
        _FakeScanWorker.last = self

    def run(self):
        self.finished.emit(3)


vlm.ScanWorker = _FakeScanWorker  # type: ignore[attr-defined]

tmp = Path(tempfile.mkdtemp(prefix="bh_loc_"))
ctx = AppContext()
ctx.config._base_dir = tmp / "config"  # type: ignore[union-attr]
(ctx.config._base_dir / "blender_versions").mkdir(parents=True, exist_ok=True)

custom_root = tmp / "MyBlender"

mod = VersionLibraryModule()
mod.ctx = ctx
mod.get_widget()

default_root = Path(ctx.config._base_dir) / "blender_versions"  # type: ignore[union-attr]
check("默认安装根目录 = base_dir/blender_versions",
      str(default_root) == mod._default_install_root(), mod._default_install_root())

# ── 1. 对话框带安装位置 ──
dlg = VersionSelectorDialog(None, blender_service=None, default_install_dir=mod._default_install_root())
check("对话框有安装位置输入框", hasattr(dlg, "install_dir_edit"))
check("对话框带出上次/默认安装目录",
      dlg.install_dir_edit.text() == str(default_root), dlg.install_dir_edit.text())
check("对话框有浏览按钮", hasattr(dlg, "install_browse_btn"))
dlg.install_dir_edit.setText(str(custom_root))
dlg._on_accept()
check("_on_accept 取用输入框里的路径", str(custom_root) == dlg.install_dir, str(dlg.install_dir))

# ── 2. 自定义目录安装（用"已有安装包"路径，跳过 400MB 下载）──
dl_dir = ctx.config._base_dir / "downloads" / "blender"  # type: ignore[union-attr]
dl_dir.mkdir(parents=True, exist_ok=True)
pkg = dl_dir / "blender-9.9.7-windows-x64.zip"
with zipfile.ZipFile(pkg, "w") as zf:
    zf.writestr("blender-9.9.7-windows-x64/blender.exe", "MZ fake")
    zf.writestr("blender-9.9.7-windows-x64/2.0/scripts/__init__.py", "")
vlm._REUSE_MIN_BYTES = 100


class _Msg:
    infos: list = []
    warns: list = []
    questions: list = []

    class StandardButton:  # 供 _delete_version 的 question() 使用
        Yes = 16384
        No = 65536

    @classmethod
    def information(cls, *a, **k):
        cls.infos.append(a[1] if len(a) > 1 else "")
        print("    [info]", a[1] if len(a) > 1 else "")
        if STATE["loop"] is not None:
            QTimer.singleShot(0, STATE["loop"].quit)
        return 1024

    @classmethod
    def warning(cls, *a, **k):
        cls.warns.append(a[1] if len(a) > 1 else "")
        print("    [warn]", a[1] if len(a) > 1 else "")
        if STATE["loop"] is not None:
            QTimer.singleShot(0, STATE["loop"].quit)
        return 1024

    @classmethod
    def question(cls, *a, **k):
        cls.questions.append(a[1] if len(a) > 1 else "")
        print("    [question]", a[1] if len(a) > 1 else "")
        if STATE["loop"] is not None:
            QTimer.singleShot(0, STATE["loop"].quit)
        return vlm.QMessageBox.StandardButton.No  # 一律不删，只验证走到了确认步骤


vlm.QMessageBox = _Msg  # type: ignore[attr-defined]

loop = QEventLoop()
STATE["loop"] = loop
mod._download_version("9.9.7", "南京大学镜像（推荐）", str(custom_root))
QTimer.singleShot(8000, loop.quit)
loop.exec()

exe = custom_root / "9.9.7" / "blender-9.9.7-windows-x64" / "blender.exe"
check("装到了自定义目录（不是默认目录）", exe.is_file(), str(exe))
check("自定义目录已登记为托管根目录",
      str(custom_root) in mod._managed_roots(), str(mod._managed_roots()))
check("已记住本次选择（下次带出）",
      mod._default_install_root() == str(custom_root), mod._default_install_root())

# ── 3. 安装完成立刻进列表（不等扫描）──
vers = [i.version for i in mod._installations]
check("安装后列表立即出现该版本", "9.9.7" in vers, str(vers))
check("列表行已渲染（不再需要等一轮扫描）",
      mod._rows_layout is not None and mod._rows_layout.count() >= 1,
      str(mod._rows_layout.count() if mod._rows_layout else None))

# ── 4. 后台静默深度扫描 ──
toasts = []
ctx.event_bus.subscribe("ui.toast", lambda p: toasts.append(p))
before_rows = mod._rows_layout.count() if mod._rows_layout else 0
mod._deep_refresh(silent=True)
loop2 = QEventLoop()
QTimer.singleShot(3000, loop2.quit)
loop2.exec()

check("深度扫描把自定义目录传进 extra_dirs",
      str(custom_root) in [str(d) for d in (_FakeScanWorker.last.extra_dirs if _FakeScanWorker.last else [])],
      str(_FakeScanWorker.last.extra_dirs if _FakeScanWorker.last else None))
check("静默扫描不弹 toast", len(toasts) == 0, str(toasts))
check("静默扫描不清空现有列表", "9.9.7" in [i.version for i in mod._installations])
check("静默扫描用页面小字提示结果",
      mod._status_label is not None and "已更新" in mod._status_label.text(),
      mod._status_label.text() if mod._status_label else "")
check("扫描完成后行仍在", (mod._rows_layout.count() if mod._rows_layout else 0) >= 1,
      f"before={before_rows}")

# ── 5. 自定义目录下的版本允许删除（走到确认框，而不是"外部安装"警告）──
_Msg.warns.clear()
_Msg.questions.clear()
loop3 = QEventLoop()
STATE["loop"] = loop3
mod._delete_version("9.9.7")
QTimer.singleShot(3000, loop3.quit)
loop3.exec()
check("自定义目录的版本被判为可管理（未报外部安装）",
      not any("外部安装" in str(w) for w in _Msg.warns), str(_Msg.warns))
check("删除走到了二次确认", len(_Msg.questions) >= 1, str(_Msg.questions))

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
