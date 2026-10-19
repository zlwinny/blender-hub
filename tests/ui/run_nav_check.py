import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── QSettings 测试隔离（monkeypatch）──
# PyQt6 实测坑：setDefaultFormat(IniFormat) 不生效、显式四参数也不走
# setPath 的目录——产品代码里的 QSettings("org", "app") 仍会读写**用户
# 真实注册表**（HKCU\Software\...）。唯一可靠方案是 patch 构造函数，
# 把两参数形态统一重定向到测试 ini。
from PyQt6.QtCore import QSettings as _QS
_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "test.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
_QS_ORIG_INIT = _QS.__init__
def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)
_QS.__init__ = _qs_patched

from PyQt6.QtCore import QPoint, QSettings, QTimer
from PyQt6.QtWidgets import QApplication, QLabel

from core import theme
from core.app_context import AppContext
from core.main_window import MainWindow

def main() -> int:
    app = QApplication(sys.argv)
    ctx = AppContext()
    win = MainWindow(ctx)
    labels = ["项目", "更新", "插件", "配置"]
    for i, lb in enumerate(labels):
        win.add_page(f"k{i}", lb, QLabel("x"))
    win.show()
    win.resize(1000, 700)
    app.processEvents()

    results = []

    # ── 默认展开态 ──
    ok = win.nav.width() == theme.Tokens.NAV_W == 76
    results.append(("默认展开宽度 76", ok))
    ok = all(b.text() == lb for b, lb in zip(win._nav_buttons, labels))
    results.append(("展开态按钮有文字", ok))

    # ── 顶部对齐：拉高窗口后第一个按钮 y 不动 ──
    y_before = win._nav_buttons[0].mapTo(win.nav, QPoint(0, 0)).y()
    win.resize(1000, 1000)
    app.processEvents()
    y_after = win._nav_buttons[0].mapTo(win.nav, QPoint(0, 0)).y()
    results.append((f"拉高后首按钮 y {y_before}→{y_after} 不变", y_before == y_after))
    # 三横按钮钉底
    toggle_bottom = win._nav_toggle.mapTo(win.nav, QPoint(0, 0)).y() + win._nav_toggle.height()
    results.append((f"三横按钮钉底 ({toggle_bottom} vs {win.nav.height() - 10})",
                    toggle_bottom >= win.nav.height() - 12))

    # ── 合并 ──
    win._toggle_nav()
    app.processEvents()
    ok = win.nav.width() == theme.Tokens.NAV_W_COLLAPSED == 76
    results.append(("合并后宽度固定 76（=展开宽度）", ok))
    ok = all(b.text() == "" for b in win._nav_buttons)
    results.append(("合并态无文字", ok))
    ok = win._nav_toggle.width() <= 44 and win._nav_toggle.isVisible()
    results.append(("三横按钮在合并态仍可见", ok))
    stored = QSettings("BlenderHub Team", "BlenderHub").value("nav_collapsed", False, type=bool)
    results.append(("合并态已持久化", stored is True))

    # ── 再展开 ──
    win._toggle_nav()
    app.processEvents()
    ok = win.nav.width() == 76 and [b.text() for b in win._nav_buttons] == labels
    results.append(("再展开恢复 76+文字", ok))

    failed = [n for n, okk in results if not okk]
    for n, okk in results:
        print(("PASS " if okk else "FAIL ") + n)
    print("\n" + ("ALL PASS" if not failed else "FAILED: " + "; ".join(failed)))
    QTimer.singleShot(150, app.quit)
    app.exec()
    return 0

if __name__ == "__main__":
    main()
