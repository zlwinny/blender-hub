"""真机 GUI 复现：切到插件市场，自动关掉任何弹出的模态对话框并留痕。

offscreen 下复现不出来（线程/网络行为不同），所以在**真实窗口**里跑：
  - 装弹窗审计（谁弹的、标题是什么、调用位置在哪）
  - 定时扫描活动模态对话框 → 打印 + 自动关闭，避免卡住脚本
  - 依次切换各个页面，重点是插件市场
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


def sink(title, kind, where):
    ctx.info(f"popup: [{kind}] {title}  <- {where}", module="popup_audit")
    print(f"[POPUP] [{kind}] {title}  <- {where}", flush=True)


install_audit(sink)

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

pages = [k for k, _l, _w, _m in win._pages]
print("pages:", pages, flush=True)


def close_modals():
    for w in QApplication.topLevelWidgets():
        try:
            if isinstance(w, QDialog) and w.isVisible() and w.isModal():
                print(f"[MODAL] open: {w.windowTitle()}  {type(w).__name__}", flush=True)
                w.reject()
        except Exception:  # noqa: BLE001
            pass


idx_of = {key: i for i, (key, *_r) in enumerate(win._pages)}
target = idx_of.get("addon_store", 0)

timer = QTimer()
timer.setInterval(400)
timer.timeout.connect(close_modals)
timer.start()

steps = [
    ("warmup", None),
    ("addon_store", target),
    ("version_library", idx_of.get("version_library")),
    ("addon_store", target),
    ("project_center", idx_of.get("project_center")),
    ("addon_store", target),
]
state = {"i": 0}


def step():
    i = state["i"]
    if i >= len(steps):
        app.quit()
        return
    name, idx = steps[i]
    state["i"] += 1
    if idx is not None:
        print(f"[NAV] -> {name} (idx={idx})", flush=True)
        win.select(idx)
    else:
        print("[NAV] warmup", flush=True)
    QTimer.singleShot(5000, step)


QTimer.singleShot(3000, step)
QTimer.singleShot(45000, app.quit)
app.exec()
print("done", flush=True)
