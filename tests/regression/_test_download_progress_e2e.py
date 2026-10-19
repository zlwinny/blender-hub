"""端到端：下载线程（threading.Thread）→ 信号桥 → 主线程 → show_download + 安装。

这是用户"看不到下载进度"的完整复现路径：DownloadService 用 threading.Thread
跑下载（没有 Qt 事件循环），进度回调必须经 pyqtSignal 才能到达主线程。
本测试就从**真实的 Python 线程**里 emit，验证整条链真的通。
"""
import os
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)  # noqa: F841

# ── QSettings 测试隔离：绝不写用户真实注册表 ──
# （曾被真实坑过：测试调 _download_version → save_install_root 把
#   %TEMP%/bh_xxx/config/blender_versions 写进了用户的真实配置）
from PyQt6.QtCore import QSettings as _QS  # noqa: E402
_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "progress_e2e.ini")
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


# offscreen 下真实 QProgressDialog 会卡进嵌套事件循环（本机已知坑），
# 换成等价桩，只验证进度值与取消信号接线。
class _FakeProgDlg(vlm.QObject):
    canceled = vlm.pyqtSignal()

    def __init__(self, *a, **k):
        super().__init__()
        self.value = -1

    def setValue(self, v):
        self.value = v

    def close(self):
        pass

    def setWindowTitle(self, t):
        pass

    def setWindowModality(self, m):
        pass

    def setMinimumDuration(self, d):
        pass


vlm.QProgressDialog = _FakeProgDlg  # type: ignore[attr-defined]


# 安装完成会弹模态 QMessageBox，无头环境没人点确定会永久卡住 → 换成记录型桩
_state = {"loop": None}


class _FakeMsgBox:
    infos: list = []
    warns: list = []

    @classmethod
    def information(cls, *a, **k):
        title = a[1] if len(a) > 1 else k.get("title", "")
        cls.infos.append(title)
        print(f"    [QMessageBox.information] {title}")
        # 安装完成即收工：offscreen 下等 QThread 自然收尾会 native crash，
        # 主动退出事件循环，最后用 os._exit 避开析构竞争。
        if "安装完成" in str(title) and _state["loop"] is not None:
            QTimer.singleShot(0, _state["loop"].quit)
        return 1024

    @classmethod
    def warning(cls, *a, **k):
        cls.warns.append(a[1] if len(a) > 1 else k.get("title", ""))
        return 1024

    @classmethod
    def critical(cls, *a, **k):
        return 1024


vlm.QMessageBox = _FakeMsgBox  # type: ignore[attr-defined]

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


MAIN_THREAD_NAME = threading.current_thread().name


class _StubWindow:
    def __init__(self):
        self.shown = []
        self.pcts = []
        self.threads = []
        self.hidden = 0

    def show_download(self, text: str, percent=None) -> None:
        self.shown.append(text)
        self.pcts.append(percent)
        self.threads.append(threading.current_thread().name)
        print(f"    [show_download] {text}  pct={percent} on={threading.current_thread().name}")

    def hide_download(self) -> None:
        self.hidden += 1


tmp = Path(tempfile.mkdtemp(prefix="bh_e2e_"))
zip_path = tmp / "blender-5.2.2-windows-x64.zip"
with zipfile.ZipFile(zip_path, "w") as zf:
    zf.writestr("blender-launcher.exe", "MZ fake")
    zf.writestr("5.2/scripts/__init__.py", "")

ctx = AppContext()
win = _StubWindow()
ctx.set_main_window(win)
ctx.config._base_dir = tmp / "config"  # type: ignore[union-attr]  # base_dir 只读
(ctx.config._base_dir / "blender_versions").mkdir(parents=True, exist_ok=True)

mod = VersionLibraryModule()
mod.ctx = ctx
mod.get_widget()  # 构建真实页面（含下载进度面板）
# 安装完成后会触发 _refresh()（起 ScanWorker 扫全盘），offscreen 下会 native crash，
# 与本次验证的下载进度链路无关 → stub 掉
mod._refresh = lambda *a, **k: None  # type: ignore[method-assign]

captured = {}


def fake_add_task(**kwargs):
    captured.update(kwargs)
    task = SimpleNamespace(task_id="t", save_path=zip_path, display_name="Blender 5.2.2")

    # 模拟 DownloadService：在**普通 Python 线程**里跑下载并回调
    def worker():
        cb = kwargs.get("on_progress")
        if cb:
            cb(0.0, 0, 350 * 1024 * 1024, 0.0)
            cb(0.42, 147 * 1024 * 1024, 350 * 1024 * 1024, 3.2 * 1024 * 1024)
            cb(1.0, 350 * 1024 * 1024, 350 * 1024 * 1024, 11.5 * 1024 * 1024)
        if kwargs.get("on_complete"):
            kwargs["on_complete"](task)

    threading.Thread(target=worker, daemon=True).start()
    return task


ctx.downloads.add_task = fake_add_task  # type: ignore[method-assign]

mod._download_version("5.2.2", "南京大学镜像（推荐）")

loop = QEventLoop()
_state["loop"] = loop
QTimer.singleShot(8000, loop.quit)
loop.exec()

# ── 断言 ──
check("add_task 收到了 on_progress", captured.get("on_progress") is not None)
check("show_download 被调用过", len(win.shown) >= 1, f"{len(win.shown)} 次")
check("进度文本含百分比", any("%" in t for t in win.shown), win.shown[:1])
check("进度文本含速度", any("MB/s" in t for t in win.shown))
check("进度文本含已下载/总大小", any("MB" in t for t in win.shown))
check("下载完成后 hide_download 被调用", win.hidden >= 1)
# 关键回归：槽必须跑在**主线程**。跑在下载线程时 GUI 更新无效，
# 这正是用户"点了下载却看不到任何进度"的机制。
check(
    "show_download 全部在主线程执行",
    bool(win.threads) and all(t == MAIN_THREAD_NAME for t in win.threads),
    str(set(win.threads)),
)
check("show_download 收到了百分比数值", any(isinstance(p, int) for p in win.pcts), str(win.pcts))
# 页面内进度面板（用户真正看的地方）
# 用 isHidden() 而非 isVisible()：父页面在无头测试里没有 show()，
# isVisible() 恒 False，但面板本身确实是"未隐藏"状态
check("页面内进度面板已显示", mod._dl_panel is not None and not mod._dl_panel.isHidden())
check("页面内进度条到 100", mod._dl_bar is not None and mod._dl_bar.value() == 100,
      str(mod._dl_bar.value() if mod._dl_bar else None))

installed = ctx.config._base_dir / "blender_versions" / "5.2.2"  # type: ignore[union-attr]
check("安装目录已创建并解压", (installed / "blender-launcher.exe").is_file(), str(installed))
check("安装完成弹窗已触发", len(_FakeMsgBox.infos) >= 1, str(_FakeMsgBox.infos))

print()
if failures:
    print(f"FAILED {len(failures)}: " + ", ".join(failures))
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)  # 避开 offscreen 下 QThread 析构的 native crash