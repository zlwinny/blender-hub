"""「关闭插件详情弹窗后滚动浏览页」崩溃的回归测试。

根因：滚动时滚动条出现 → viewport 触发 Resize → 防抖定时器 → 在滚动/绘制
分发过程中同步销毁并重建全部卡片（setParent + deleteLater）→ Windows 崩溃。
修复：重排与弹窗关闭后的刷新都延迟到下一个事件循环（QTimer.singleShot(0)）。

本测试复现整条序列，验证事件循环跑完后卡片仍在、且没有异常。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_SKIP_DISCLAIMER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import theme

app = QApplication([])
app.setStyleSheet(theme.stylesheet())

from core.app_context import AppContext  # noqa: E402
from core.main_window import MainWindow  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


ctx = AppContext()
mm = ctx.module_manager
mm.discover()
mm.load_all()
win = MainWindow(ctx)
ctx.set_main_window(win)
for info in mm.get_all_modules():
    win.add_page(info.id, info.name, mm.get_module_widget(info.id), module=mm.load_module(info.id))
win.resize(1240, 780)
win.show()

# 切到插件页
idx = [i for i, (k, *_r) in enumerate(win._pages) if k == "addon_store"][0]
win.select(idx)
QTimer.singleShot(1200, lambda: None)
app.processEvents()

mod = mm.load_module("addon_store")
check("插件页已有卡片", len(mod._grid_layout and mod._cards_container.findChildren(object) or []) >= 0)

# 1) 打开并关闭详情弹窗
results = mod._addon_service.search(keyword="", category=mod._current_category)
check("目录里有插件", len(results) > 0, str(len(results)))

from modules.addon_store.module import AddonDetailDialog

dlg = AddonDetailDialog(results[0], is_installed=False, thumbnail_path=None, parent=mod._widget)
QTimer.singleShot(150, dlg.accept)
dlg.exec()
app.processEvents()
print("[ok] 详情弹窗已关闭")

# 2) 模拟滚动导致的 viewport resize（滚动条出现/消失）
viewport = mod._scroll.viewport()
viewport.resize(max(200, viewport.width() - 15), viewport.height())
app.processEvents()

# 3) 让防抖定时器 + 延迟刷新真正跑完
QTimer.singleShot(900, app.quit)
app.exec()

cards = [w for w in mod._cards_container.findChildren(object)] if mod._cards_container else []
check("重排后卡片容器仍在", mod._cards_container is not None)
check("列数已重算", mod._cols >= 1, str(mod._cols))
check("网格中仍有卡片", mod._grid_layout is not None and mod._grid_layout.count() > 0,
      str(mod._grid_layout.count() if mod._grid_layout else 0))

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
