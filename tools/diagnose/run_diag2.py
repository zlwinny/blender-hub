"""崩溃诊断启动器 — 在真实桌面会话运行源码版，把异常写到文件。

用途：打包版崩溃时拿不到 Python traceback（无控制台），
本脚本以源码方式启动同样的代码路径，把异常落盘。
"""
import sys
import traceback
from pathlib import Path

LOG = Path(r"C:\Users\ZL Ultra7 270K\AppData\Local\Temp\bh_crash.log")

def _log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

# faulthandler：捕获段错误/栈溢出等原生崩溃
import faulthandler
_fh = open(LOG, "a", encoding="utf-8")
faulthandler.enable(file=_fh, all_threads=True)
_log("\n" + "=" * 60)
_log(f"LAUNCH {sys.executable}")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from PyQt6.QtWidgets import QApplication
    _log("step: QApplication import OK")

    from core import theme
    from core.app_context import AppContext
    from core.main_window import MainWindow
    from modules.settings import SettingsModule
    _log("step: app modules import OK")

    app = QApplication(sys.argv)
    app.setApplicationName("Blender Hub")
    app.setOrganizationName("BlenderHub Team")
    app.setStyleSheet(theme.stylesheet())
    _log("step: QApplication created")

    ctx = AppContext()
    mm = ctx.module_manager
    mm.discover()
    mm.load_all()
    _log("step: modules loaded")

    win = MainWindow(ctx)
    ctx.set_main_window(win)
    _log("step: MainWindow created")

    ICONS = {
        "version_library": "◈", "project_center": "◇",
        "addon_store": "◉", "config_center": "◎",
    }
    for info in mm.get_all_modules():
        w = mm.get_module_widget(info.id)
        if w is None:
            continue
        win.add_page(info.id, info.name, w, ICONS.get(info.id, "◆"),
                     module=mm.load_module(info.id))
        _log(f"step: page added {info.id}")
    win.add_page("settings", "设置", SettingsModule(ctx), "⚙", module=None)
    _log("step: settings page added")

    win.show()
    _log(f"step: window shown, pages={win.stack.count()}")
    for _i in range(win.stack.count()):
        win.select(_i)
        app.processEvents()
        _log(f"step: visited {_i} {win._current_key}")

    rc = app.exec()
    _log(f"app.exec() returned {rc}")
except Exception:
    _log("PYTHON EXCEPTION:\n" + traceback.format_exc())
    raise
finally:
    _log("EXIT")
