"""真实链路验证：本机 HTTP 服务 → DownloadService(threading) → 信号桥 → 主线程 UI。

与 _test_download_progress_e2e.py 的区别：这里**不桩 DownloadService**，
走真实的 requests 流式下载 + 真实节流 + 真实窗口/页面控件，验证：
  1. 下载过程中页面内进度条出现多个中间值（不是 0 → 100 直接跳）
  2. 主窗口底部条也拿到百分比
  3. 下载完成后自动解压安装
"""
import http.server
import os
import sys
import tempfile
import threading
import time
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
_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "progress_http.ini")
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

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


# ── 1. 造一个真 zip 并用本机 HTTP 慢速送出（模拟 403MB 下载要几秒）──
tmp = Path(tempfile.mkdtemp(prefix="bh_http_"))
pkg = tmp / "pkg.zip"
with zipfile.ZipFile(pkg, "w", zipfile.ZIP_STORED) as zf:
    zf.writestr("blender-launcher.exe", os.urandom(3 * 1024 * 1024))
    zf.writestr("9.9/scripts/__init__.py", "")
data = pkg.read_bytes()
print(f"fake package: {len(data)/1024/1024:.1f}MB")

STATE = {"loop": None}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "application/zip")
        self.end_headers()
        step = 64 * 1024
        for i in range(0, len(data), step):
            try:
                self.wfile.write(data[i:i + step])
                self.wfile.flush()
            except OSError:
                return
            time.sleep(0.01)  # 拖慢到 ~1.5s，产生足够多的进度采样点

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}/pkg.zip"
print("serving:", url)

# ── 2. 真实 AppContext + 主窗口 + 版本库页面 ──
ctx = AppContext()
ctx.config._base_dir = tmp / "config"  # type: ignore[union-attr]
(ctx.config._base_dir / "blender_versions").mkdir(parents=True, exist_ok=True)
win = MainWindow(ctx)
win.resize(1240, 780)
win.show()
ctx.set_main_window(win)  # main.py 里同样在 show() 之后调用（底部条依赖它）

mod = ctx.module_manager.get_module("version_library") or VersionLibraryModule()
mod.ctx = ctx
page = mod.get_widget()
app.processEvents()

# 让"官方下载地址"指向本机服务（resolve_mirror_url 不含该前缀时原样返回）
ctx.blender_service.get_download_url = lambda v: url  # type: ignore[method-assign]

# 安装完成会弹模态框 → 直接放行，并结束事件循环
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

# ── 3. 采样 ──
samples = []
win_samples = []
loop = QEventLoop()
STATE["loop"] = loop


def sample():
    if mod._dl_bar is not None:
        samples.append(mod._dl_bar.value())
    try:
        win_samples.append(win._dl_bar.value())
    except Exception:
        pass


timer = QTimer()
timer.setInterval(60)
timer.timeout.connect(sample)
timer.start()

mod._download_version("9.9.9", "南京大学镜像（推荐）")

QTimer.singleShot(30000, loop.quit)
loop.exec()
timer.stop()

uniq = sorted(set(samples))
print("panel samples:", uniq)
print("bottom bar samples:", sorted(set(win_samples)))
print("panel hidden:", mod._dl_panel.isHidden() if mod._dl_panel else None)

final_pct = mod._dl_bar.value() if mod._dl_bar else -1
check("页面进度条出现多个中间值", len([v for v in uniq if 0 < v < 100]) >= 2, str(uniq))
check("页面进度条最终 100", final_pct == 100, str(final_pct))
check("底部条也拿到中间百分比", any(0 < v < 100 for v in win_samples), str(sorted(set(win_samples))))
check("下载全程页面面板可见", mod._dl_panel is not None and not mod._dl_panel.isHidden())

installed = ctx.config._base_dir / "blender_versions" / "9.9.9"  # type: ignore[union-attr]
check("真实下载后已解压安装", (installed / "blender-launcher.exe").is_file(), str(installed))

try:
    srv.shutdown()
except Exception:
    pass

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
