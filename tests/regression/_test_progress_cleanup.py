"""验证：安装完成后，页面内进度面板 / 主窗口底部条都会真正收掉（不会一直挂着）。

用户反馈"左下角进度条安装完成后提示一直存在"——本测试把整条链路跑完，
等到隐藏定时器该触发的时间点之后，再检查两个 UI 元素是否真的消失了。
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

# ── QSettings 测试隔离：绝不写用户真实注册表 ──
# （曾被真实坑过：测试调 _download_version → save_install_root 把
#   %TEMP%/bh_xxx/config/blender_versions 写进了用户的真实配置）
from PyQt6.QtCore import QSettings as _QS  # noqa: E402
_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "progress_cleanup.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
_QS_ORIG_INIT = _QS.__init__
def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)
_QS.__init__ = _qs_patched

from core.app_context import AppContext  # noqa: E402
from core.main_window import MainWindow  # noqa: E402
import modules.version_library.module as vlm  # noqa: E402
from modules.version_library.module import VersionLibraryModule  # noqa: E402


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


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


class _FakeScanWorker(vlm.QThread):
    found = vlm.pyqtSignal(object)
    finished = vlm.pyqtSignal(int)

    def __init__(self, svc, deep_scan=False, extra_dirs=None):
        super().__init__()
        self.deep_scan = deep_scan

    def run(self):
        self.finished.emit(1)


vlm.ScanWorker = _FakeScanWorker  # type: ignore[attr-defined]

tmp = Path(tempfile.mkdtemp(prefix="bh_cleanup_"))
ctx = AppContext()
ctx.config._base_dir = tmp / "config"  # type: ignore[union-attr]
(ctx.config._base_dir / "blender_versions").mkdir(parents=True, exist_ok=True)

win = MainWindow(ctx)
win.resize(1240, 780)
win.show()
ctx.set_main_window(win)

mod = VersionLibraryModule()
mod.ctx = ctx
mod.get_widget()
app.processEvents()

# 预置完整安装包，走"直接安装"路径（快）
dl_dir = ctx.config._base_dir / "downloads" / "blender"  # type: ignore[union-attr]
dl_dir.mkdir(parents=True, exist_ok=True)
pkg = dl_dir / "blender-9.9.6-windows-x64.zip"
with zipfile.ZipFile(pkg, "w") as zf:
    zf.writestr("blender-9.9.6-windows-x64/blender.exe", "MZ fake")
vlm._REUSE_MIN_BYTES = 100


class _Msg:
    @classmethod
    def information(cls, *a, **k):
        print("    [info]", a[1] if len(a) > 1 else "")
        return 1024

    @classmethod
    def warning(cls, *a, **k):
        print("    [warn]", a[1] if len(a) > 1 else "")
        return 1024


vlm.QMessageBox = _Msg  # type: ignore[attr-defined]

mod._download_version("9.9.6", "南京大学镜像（推荐）")

# 让事件循环跑够久：安装完成的 8s 隐藏定时器 + 状态提示 4s 都会自然触发
loop = QEventLoop()
QTimer.singleShot(11000, loop.quit)
loop.exec()
app.processEvents()

print("--- 安装结束 11s 后 ---")
print("bottom bar visible:", win._dl_widget.isVisible(), "height:", win._dl_widget.height())
print("panel hidden:", mod._dl_panel.isHidden() if mod._dl_panel else None)
print("status label visible:", mod._status_label.isVisible() if mod._status_label else None,
      "text:", repr(mod._status_label.text() if mod._status_label else ""))

check("主窗口底部条已隐藏", not win._dl_widget.isVisible())
check("主窗口底部条高度归零", win._dl_widget.height() == 0, str(win._dl_widget.height()))
check("页面内进度面板已隐藏", mod._dl_panel is not None and mod._dl_panel.isHidden())
check("页面状态提示已自动消失",
      mod._status_label is None or not mod._status_label.isVisible(),
      repr(mod._status_label.text() if mod._status_label else ""))
check("版本确实装好了", (ctx.config._base_dir / "blender_versions" / "9.9.6" /  # type: ignore[union-attr]
                   "blender-9.9.6-windows-x64" / "blender.exe").is_file())

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
