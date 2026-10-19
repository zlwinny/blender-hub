"""崩溃二分定位 — 用环境变量逐个禁用可疑功能，找出 0xC0000409 的真凶。

用法：BH_DISABLE=thumb|refresh|pager|toast|navscan python run_bisect.py
"""
import os
import sys
import traceback
from pathlib import Path

DISABLE = set(filter(None, os.environ.get("BH_DISABLE", "").split(",")))
LOG = Path(r"C:\Users\ZL Ultra7 270K\AppData\Local\Temp\bh_bisect.log")

def _log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
        f.flush()

import faulthandler
_fh = open(LOG, "a", encoding="utf-8")
faulthandler.enable(file=_fh, all_threads=True)

_log(f"\n{'=' * 60}\nBH_DISABLE={DISABLE or '(none)'}")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication
from core import theme
from core.app_context import AppContext
from core.main_window import MainWindow
from modules.settings import SettingsModule

# ── 打补丁：按开关禁用可疑路径 ──
if "thumb" in DISABLE:
    from modules.addon_store.module import AddonStoreModule
    AddonStoreModule._download_thumbnail_async = lambda self, addon: None
    _log("  patched: thumbnail download disabled")

if "refresh" in DISABLE:
    from core.services.addon_service import AddonService
    AddonService.refresh_cache = lambda self, wait=False, timeout=45.0: 0
    _log("  patched: refresh_cache disabled")

if "pager" in DISABLE:
    from modules.addon_store.module import AddonStoreModule
    AddonStoreModule._update_pager = lambda self, start, total, per_page: None
    _log("  patched: pager disabled")

if "toast" in DISABLE:
    from core.widgets import Toast
    Toast.__init__ = lambda self, parent: (_ for _ in ()).throw(RuntimeError("disabled"))
    _log("  patched: toast disabled")

if "navscan" in DISABLE:
    from modules.project_center.module import ProjectCenterModule
    ProjectCenterModule.on_navigate_to = lambda self: None
    _log("  patched: project scan on navigate disabled")

if "toastfilter" in DISABLE:
    # 单独关掉 Toast 的事件过滤器（递归嫌疑）
    from core.widgets import Toast
    def _no_filter_init(self, parent):
        QLabel.__init__(self, parent)
    from PyQt6.QtWidgets import QLabel
    Toast.__init__ = _no_filter_init
    _log("  patched: toast event filter disabled")

app = QApplication(sys.argv)
app.setStyleSheet(theme.stylesheet())

# BH_APPNAME=1 才设置 applicationName（QSettings 的前提）
if os.environ.get("BH_APPNAME") == "1":
    app.setApplicationName("Blender Hub")
    app.setOrganizationName("BlenderHub Team")
    _log("  setApplicationName/setOrganizationName ON")
else:
    _log("  setApplicationName SKIPPED")
_log("QApplication OK")

ctx = AppContext()
mm = ctx.module_manager
mm.discover()
mm.load_all()
_log("modules loaded")

win = MainWindow(ctx)
ctx.set_main_window(win)

ICONS = {"version_library": "◈", "project_center": "◇",
         "addon_store": "◉", "config_center": "◎"}
for info in mm.get_all_modules():
    w = mm.get_module_widget(info.id)
    if w is None:
        continue
    win.add_page(info.id, info.name, w, ICONS.get(info.id, "◆"),
                 module=mm.load_module(info.id))
    _log(f"page added: {info.id}")

win.add_page("settings", "设置", SettingsModule(ctx), "⚙", module=None)
win.show()
_log(f"shown, pages={win.stack.count()}")

# 依次访问所有页面（触发各模块 on_navigate_to）
for i in range(win.stack.count()):
    win.select(i)
    app.processEvents()
    _log(f"visited page {i}: {win._current_key}")

_log("ENTERING app.exec()")
rc = app.exec()
_log(f"app.exec() returned {rc}")
