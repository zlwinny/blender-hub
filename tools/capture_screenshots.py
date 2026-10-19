"""抓取四个功能页的真实截图，保存到 docs/screenshots/（供 README 使用）。

在真实 Windows 平台运行（不用 offscreen），保证中文字体与渲染正确。
运行时应用窗口会在屏幕上短暂出现约 10 秒后自动退出。
"""
import os
import sys
from pathlib import Path

os.environ.pop("QT_QPA_PLATFORM", None)          # 使用真实 windows 平台
os.environ["BH_SKIP_DISCLAIMER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import theme  # noqa: E402

app = QApplication([])
app.setStyleSheet(theme.stylesheet())

from core.app_context import AppContext  # noqa: E402
from core.main_window import MainWindow  # noqa: E402

ctx = AppContext()
mm = ctx.module_manager
mm.discover()
mm.load_all()
win = MainWindow(ctx)
ctx.set_main_window(win)
for info in mm.get_all_modules():
    win.add_page(info.id, info.name, mm.get_module_widget(info.id), module=mm.load_module(info.id))

out_dir = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
out_dir.mkdir(parents=True, exist_ok=True)

names = {
    "project_center": "project-center",
    "version_library": "version-library",
    "addon_store": "addon-store",
    "config_center": "config-center",
}

win.resize(1240, 780)
win.show()
app.processEvents()

state = {"i": 0}


def capture_next() -> None:
    keys = [k for k in names if k in [p[0] for p in win._pages]]
    if state["i"] >= len(keys):
        print("DONE", [str(p) for p in sorted(out_dir.glob("*.png"))])
        app.quit()
        return
    key = keys[state["i"]]
    idx = [j for j, (k, *_r) in enumerate(win._pages) if k == key][0]
    win.select(idx)

    def snap() -> None:
        pm = win.grab()
        target = out_dir / f"{names[key]}.png"
        pm.save(str(target))
        print(f"[saved] {target.name}  {pm.width()}x{pm.height()}")
        state["i"] += 1
        QTimer.singleShot(300, capture_next)

    # 等页面异步加载（扫描/网络/缩略图）后再抓
    QTimer.singleShot(2500, snap)


QTimer.singleShot(1200, capture_next)
app.exec()
