"""回归：插件卡片不能浮成独立窗口（用户报的"空白 240x216 小窗"）。

日志抓到的真凶：
    window shown: AddonCard title='' 240x216 obj=card
原因链：
  1. `_clear_cards()` 用 `w.setParent(None)` 让旧卡片立即脱离父级 →
     它变成**顶层窗口**；当时若可见，就浮成独立小窗（标题空 → 显示 exe
     产品名 "Blender Hub"，自绘背景脱父后画不出来 → 看着就是空白窗）。
  2. 卡片创建时也没给 parent，创建到 addWidget 之间同样是孤儿。
"""
import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QSettings as _QS  # noqa: E402

_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "card_orphan.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
_QS_ORIG_INIT = _QS.__init__


def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)


_QS.__init__ = _qs_patched

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from core.app_context import AppContext  # noqa: E402

ctx = AppContext()

mod = None
for m in ctx.module_manager.get_all_modules():
    if getattr(m, "id", "") == "addon_store":
        mod = m
        break
if mod is None:
    from modules.addon_store.module import AddonStoreModule

    mod = AddonStoreModule()
    mod.ctx = ctx

w = mod.get_widget()
w.resize(1200, 800)
w.show()

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


def visible_orphan_cards():
    """当前有没有"卡片类"的**顶层**窗口（脱离了父级 → 真机上会浮成独立窗）。

    判据用 isWindow() 而不是 isVisible()：offscreen 平台下这种孤儿卡片
    isVisible() 常常是 False，用可见性判断会得到"假通过"（回退修复后
    测不出来）。只要还挂在没有父级的顶层列表里，真机就会显示出来。
    """
    out = []
    for x in QApplication.topLevelWidgets():
        try:
            if not x.isWindow():
                continue
            cls = x.metaObject().className()
            if cls in ("AddonCard", "QFrame") and str(x.objectName()) == "card":
                vis = "visible" if x.isVisible() else "hidden"
                out.append(f"{cls} {x.width()}x{x.height()} {vis}")
        except Exception:  # noqa: BLE001
            pass
    return out


mod._refresh_cards()
app.processEvents()
print("page 1 cards:", mod._grid_layout.count() if mod._grid_layout else 0)
orphans = visible_orphan_cards()
check("首次渲染不产生孤儿卡片窗口", not orphans, str(orphans[:3]))

# 真凶触发点：_clear_cards 的 setParent(None)。
# 直接调用后**立刻**检查（不 processEvents，否则 deleteLater 已经把它们销毁，
# 就抓不到"曾脱离父级"这件事）
mod._refresh_cards()
app.processEvents()
mod._clear_cards()
orphans = visible_orphan_cards()
check("_clear_cards 后卡片没有脱离父级", not orphans, str(orphans[:3]))

mod._refresh_cards()
app.processEvents()
orphans = visible_orphan_cards()
check("重新渲染（清旧建新）不产生孤儿卡片窗口", not orphans, str(orphans[:3]))

# 翻页后再来一次
if getattr(mod, "_total_pages", 1) > 1:
    mod._goto_page(1)
    app.processEvents()
    orphans = visible_orphan_cards()
    check("翻页不产生孤儿卡片窗口", not orphans, str(orphans[:3]))

# 静态断言：清理时必须先 hide，创建时必须给 parent
import inspect  # noqa: E402

src_clear = inspect.getsource(type(mod)._clear_cards)
check("_clear_cards 先 hide、且不再用 setParent(None)",
      "w.hide()" in src_clear and "w.setParent(trash)" in src_clear
      and "w.setParent(None)" not in src_clear,
      "顺序：hide → 转移到隐藏容器 → deleteLater")
src_refresh = inspect.getsource(type(mod)._refresh_cards)
check("创建卡片时给了 parent", "parent=self._cards_container" in src_refresh)

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
