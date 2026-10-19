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

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from core import theme
from core.main_window import MainWindow

class Ctx:
    def __init__(self, app):
        from core.event_bus import EventBus
        self.bus = EventBus()
        self.info = lambda *a, **k: None
        self.log_service = None

    auth = None
    def info(self, *a, **k):
        pass

def main() -> int:
    app = QApplication(sys.argv)
    try:
        from core.app_context import AppContext  # 尝试真实 ctx
        ctx = AppContext()
        win = MainWindow(ctx)
    except Exception:
        win = MainWindow(Ctx(app))

    win.show()
    win.resize(1240, 780)
    app.processEvents()

    checks = []
    checks.append(("navBar 宽度=76", win.nav.width() == theme.Tokens.NAV_W))
    checks.append(("NAV_W 令牌=76", theme.Tokens.NAV_W == 76))

    from PyQt6.QtWidgets import QPushButton
    btns = win.findChildren(QPushButton)
    win_btns = [b for b in btns if b.objectName() in ("winBtn", "winBtnClose")]
    checks.append(("窗口控制按钮数量=3", len(win_btns) == 3))
    for b in win_btns:
        sz = (b.width(), b.height())
        checks.append((f"{b.objectName()} 尺寸{sz} = Win11标准 46x32",
                       sz == (46, 32)))
    for i, b in enumerate(win._nav_buttons):
        w = b.width()
        checks.append((f"nav[{i}] '{b.text()}' 宽{w} ≤ 64", w <= 64))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)

    # 停 4 秒验证事件循环稳定（外部 timeout 5s，exit=124 = 正常跑满）
    QTimer.singleShot(4000, app.quit)
    if failed:
        print("FAILED_CHECKS", len(failed))
    app.exec()
    return 0

if __name__ == "__main__":
    main()
