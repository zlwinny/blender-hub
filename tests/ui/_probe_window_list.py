"""真机枚举：把每一个**可见的顶层窗口**都打出来（类名/标题/尺寸/父对象）。

弹窗审计抓不到"空白 Blender Hub 窗口"，说明它既不是 exec 的对话框、
标题也是空的。那就直接把窗口清单列出来——类名（QProgressDialog / QLabel /
QWebEngineView 容器 …）能立刻指向创建它的代码。
"""
import os
import sys

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

app = QApplication(sys.argv)

from core.app_context import AppContext  # noqa: E402
from core.popup_audit import install as install_audit  # noqa: E402

ctx = AppContext()
install_audit(lambda t, k, w: print(f"[POPUP] [{k}] {t} <- {w}", flush=True))

mm = ctx.module_manager
mm.discover()
mm.load_all()

from core.main_window import MainWindow  # noqa: E402

win = MainWindow(ctx)
ctx.set_main_window(win)
for info in mm.get_all_modules():
    widget = mm.get_module_widget(info.id)
    if widget is None:
        continue
    win.add_page(info.id, info.name, widget, module=mm.load_module(info.id))
win.show()

known = {}


def dump_windows(tag=""):
    for w in QApplication.topLevelWidgets():
        try:
            if w is win:
                continue
            if not w.isVisible():
                continue
            cls = w.metaObject().className()
            title = str(w.windowTitle())
            name = str(w.objectName())
            size = f"{w.width()}x{w.height()}"
            parent = w.parent()
            pname = type(parent).__name__ if parent is not None else "None"
            line = f"[WIN]{tag} {cls} | title={title!r} | obj={name!r} | {size} | parent={pname}"
            if line != known.get(id(w)):
                known[id(w)] = line
                print(line, flush=True)
        except Exception:  # noqa: BLE001
            pass


idx_of = {k: i for i, (k, *_r) in enumerate(win._pages)}
print("pages:", list(idx_of), flush=True)

dump = QTimer()
dump.setInterval(700)
dump.timeout.connect(dump_windows)
dump.start()

steps = [
    ("warmup", None),
    ("addon_store", idx_of.get("addon_store")),
    ("version_library", idx_of.get("version_library")),
    ("addon_store", idx_of.get("addon_store")),
    ("project_center", idx_of.get("project_center")),
    ("addon_store", idx_of.get("addon_store")),
    ("config_center", idx_of.get("config_center")),
    ("addon_store", idx_of.get("addon_store")),
]
state = {"i": 0}


def step():
    i = state["i"]
    if i >= len(steps):
        dump_windows("final")
        app.quit()
        return
    name, idx = steps[i]
    state["i"] += 1
    if idx is None:
        print(f"[NAV] warmup", flush=True)
    else:
        print(f"[NAV] -> {name}", flush=True)
        win.select(idx)
        QTimer.singleShot(600, lambda: dump_windows(f"after:{name}"))
    QTimer.singleShot(3500, step)


QTimer.singleShot(2500, step)
QTimer.singleShot(45000, app.quit)
app.exec()

# 最后再关掉任何残留对话框
for w in QApplication.topLevelWidgets():
    if isinstance(w, QDialog) and w.isVisible():
        print(f"[MODAL-LEFT] {w.windowTitle()} {type(w).__name__}", flush=True)
print("done", flush=True)
