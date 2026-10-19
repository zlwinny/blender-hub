"""验证：本地已有完整安装包时，点「下载并安装」直接进入安装，不再重复下载 400MB。"""
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
_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "reuse_package.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
_QS_ORIG_INIT = _QS.__init__
def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)
_QS.__init__ = _qs_patched

from core.app_context import AppContext  # noqa: E402
import modules.version_library.module as vlm  # noqa: E402
from modules.version_library.module import VersionLibraryModule  # noqa: E402


class _FakeProgDlg(vlm.QObject):
    canceled = vlm.pyqtSignal()

    def __init__(self, *a, **k):
        super().__init__()

    def setValue(self, v):
        pass

    def close(self):
        pass

    def setWindowTitle(self, t):
        pass

    def setWindowModality(self, m):
        pass

    def setMinimumDuration(self, d):
        pass


vlm.QProgressDialog = _FakeProgDlg  # type: ignore[attr-defined]

failures = []
STATE = {"loop": None}


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


vlm._REUSE_MIN_BYTES = 100  # 测试用小包，绕过 10MB 可信门槛

tmp = Path(tempfile.mkdtemp(prefix="bh_reuse_"))
ctx = AppContext()
ctx.config._base_dir = tmp / "config"  # type: ignore[union-attr]
(ctx.config._base_dir / "blender_versions").mkdir(parents=True, exist_ok=True)

# 预置一个"上次下载成功"的完整包
dl_dir = ctx.config._base_dir / "downloads" / "blender"  # type: ignore[union-attr]
dl_dir.mkdir(parents=True, exist_ok=True)
pkg = dl_dir / "blender-9.9.8-windows-x64.zip"
with zipfile.ZipFile(pkg, "w") as zf:
    zf.writestr("blender-launcher.exe", "MZ fake")
    zf.writestr("9.9/scripts/__init__.py", "")
print(f"cached package: {pkg}  {pkg.stat().st_size} bytes")

mod = VersionLibraryModule()
mod.ctx = ctx
mod.get_widget()
mod._refresh = lambda *a, **k: None  # type: ignore[method-assign]

called = {"url": 0}


def boom(*a, **k):
    called["url"] += 1
    raise AssertionError("有本地安装包时不应再请求下载 URL")


ctx.blender_service.get_download_url = boom  # type: ignore[method-assign]


class _Msg:
    @classmethod
    def information(cls, *a, **k):
        print("    [info]", a[1] if len(a) > 1 else "")
        if STATE["loop"] is not None:
            QTimer.singleShot(0, STATE["loop"].quit)
        return 1024

    @classmethod
    def warning(cls, *a, **k):
        print("    [warn]", a[1] if len(a) > 1 else "")
        if STATE["loop"] is not None:
            QTimer.singleShot(0, STATE["loop"].quit)
        return 1024


vlm.QMessageBox = _Msg  # type: ignore[attr-defined]

loop = QEventLoop()
STATE["loop"] = loop
mod._download_version("9.9.8", "南京大学镜像（推荐）")
QTimer.singleShot(8000, loop.quit)
loop.exec()

installed = ctx.config._base_dir / "blender_versions" / "9.9.8"  # type: ignore[union-attr]
check("未请求下载 URL（直接复用本地包）", called["url"] == 0, str(called["url"]))
check("没有启动真实下载任务", all(
    t.display_name != "Blender 9.9.8" for t in ctx.downloads.get_all_tasks()
))
check("已从本地包完成安装", (installed / "blender-launcher.exe").is_file(), str(installed))
check("面板显示了安装阶段", mod._dl_title is not None and "安装" in mod._dl_title.text()
      or mod._dl_title is not None and "Blender" in mod._dl_title.text(),
      mod._dl_title.text() if mod._dl_title else "")

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
