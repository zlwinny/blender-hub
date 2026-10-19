"""回归用户报的"每次切到插件市场弹出空白 Blender Hub 小窗口"。

真凶：`_install_budeco` 里的 QProgressDialog —— 没有 setWindowTitle（标题栏
退化成 exe 产品名 "Blender Hub"）、NonModal + show()、AutoClose(False)，
只有 thread.finished 才 close。一旦线程没正常收尾或实例被覆盖，它就作为
本页面（插件市场）的子窗口常驻，于是"切过来就看到一个空窗口"。
"""
import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QSettings as _QS  # noqa: E402

_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "orphan.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
_QS_ORIG_INIT = _QS.__init__


def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)


_QS.__init__ = _qs_patched

from PyQt6.QtWidgets import QApplication, QProgressDialog  # noqa: E402

app = QApplication(sys.argv)

import modules.addon_store.module as asm  # noqa: E402
from modules.addon_store.module import AddonStoreModule  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


mod = AddonStoreModule()
mod._widget = None  # get_widget 会建真实页面，这里只测进度框生命周期
mod._resolve_thread = None
mod._resolve_progress = None

# 1) 模拟"忘了关"的残留窗口 → _close_resolve_progress 必须能收掉
p = QProgressDialog("正在解析下载地址…", "", 0, 0, None)
p.show()
mod._resolve_progress = p
check("残留窗口初始是可见的", p.isVisible())
mod._close_resolve_progress()
check("_close_resolve_progress 关掉了窗口", not p.isVisible())
check("关闭后引用被清空", mod._resolve_progress is None)
mod._close_resolve_progress()  # 幂等
check("重复调用不报错（幂等）", True)

# 2) on_navigate_to 会清掉"线程已结束但窗口还在"的残留
p2 = QProgressDialog("正在解析下载地址…", "", 0, 0, None)
p2.show()
mod._resolve_progress = p2
mod._refresh_cards = lambda: None  # type: ignore[method-assign]
mod._update_stats = lambda: None  # type: ignore[method-assign]
mod._addon_service = None
mod.on_navigate_to()
check("on_navigate_to 清掉了残留窗口", not p2.isVisible(), str(p2.isVisible()))

# 3) 静态断言：进度框必须设标题、必须关掉取消假按钮
import inspect  # noqa: E402

src = inspect.getsource(AddonStoreModule._install_budeco)
check("进度框设置了窗口标题", "setWindowTitle" in src)
check("不再出现无标题的裸 QProgressDialog",
      'QProgressDialog("正在解析下载地址…", ""' in src, "取消按钮已置空")
check("关闭走统一收尾函数", "_close_resolve_progress" in src)
check("AutoClose 打开（不再 False）", "setAutoClose(True)" in src)
check("去掉了永不响应的取消按钮", "setCancelButton(None)" in src)

# 4) 审计要能抓到只 show() 的窗口
from core.popup_audit import install  # noqa: E402

hits = []
install(lambda title, kind, where: hits.append((title, kind, where)))
pd = QProgressDialog("x", "", 0, 0, None)
pd.setWindowTitle("测试进度窗")
pd.show()
check("审计能记录 show() 的进度框",
      any(h[0] == "测试进度窗" for h in hits), str(hits[:2]))
pd.close()

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
