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

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from core import theme
from core.app_context import AppContext
from core.main_window import MainWindow

def corner_alpha(img: QImage, x: int, y: int) -> int:
    c = img.pixel(x, y)
    return (c >> 24) & 0xFF

def check(name: str, img: QImage, expect_transparent: bool) -> tuple[str, bool]:
    w, h = img.width(), img.height()
    pts = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]
    alphas = [corner_alpha(img, x, y) for x, y in pts]
    if expect_transparent:
        ok = all(a == 0 for a in alphas)
    else:
        ok = all(a == 255 for a in alphas)
    return (f"{name}: 角alpha={alphas} 期望{'透明' if expect_transparent else '不透明'}", ok)

def main() -> int:
    app = QApplication(sys.argv)
    ctx = AppContext()
    win = MainWindow(ctx)
    # 测试隔离：QSettings 里可能残留上个测试保存的 maximized 几何
    # （restoreGeometry 会连窗口状态一起恢复），先强制还原到普通态
    win.setWindowState(Qt.WindowState.WindowNoState)
    win.show()
    win.resize(1000, 700)
    win._sync_frame_state()
    app.processEvents()

    results = []
    img = win.grab().toImage()
    results.append(check("窗口普通态", img, True))

    # 最大化（offscreen 下 setWindowState + 手动同步）
    win.setWindowState(Qt.WindowState.WindowMaximized)
    win._sync_frame_state()
    app.processEvents()
    img = win.grab().toImage()
    results.append(check("窗口最大化态", img, False))

    # 卡片
    from modules.addon_store.module import AddonCard, AddonInfo
    addon = AddonInfo(
        id="t", name="Test", author="A", category="test",
        description="d" * 120, version="1.0",
        tags=["a", "b"], rating=4.5, downloads=100, source="github", icon="\U0001f6d2",
    )
    card = AddonCard(addon)
    # 关键：卡片必须拿到全局样式表才谈得上圆角（真实运行中它在
    # MainWindow 样式树内；独立 show() 的顶层窗口拿不到 QSS，是测试假象）
    card.setStyleSheet(theme.stylesheet())
    card.show()
    app.processEvents()
    img = card.grab().toImage()
    results.append(check("卡片圆角", img, True))

    # 背景不丢 + thumb 块边界可见（"图块边框没有了"回归防线）
    def rgb(x, y):
        c = img.pixel(x, y)
        return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)

    def hexc(t):
        return "#%02X%02X%02X" % t

    bg_card = tuple(int(theme.Tokens.BG_CARD[i:i + 2], 16) for i in (1, 3, 5))
    bg_hover = tuple(int(theme.Tokens.BG_CARD_HOVER[i:i + 2], 16) for i in (1, 3, 5))
    bg_root = tuple(int(theme.Tokens.OVERLAY_BG[i:i + 2], 16) for i in (1, 3, 5))
    # 查无子控件的区域：卡片左中（14px margin 内、缩略图下方无 label 处）。
    # offscreen 平台可能把卡片判为 hover 态（无真实鼠标位置），两种色都算自绘生效
    pad_area = rgb(5, 150)
    ok_bg = pad_area in (bg_card, bg_hover)
    results.append((f"卡片背景=BG_CARD/HOVER {hexc(pad_area)}（自绘生效）", ok_bg))
    thumb_c = rgb(img.width() // 2, 60)
    ok_thumb = thumb_c == bg_root
    results.append((f"thumb 块底=OVERLAY_BG {hexc(thumb_c)}（边界可见）", ok_thumb))

    failed = [n for n, ok in results if not ok]
    for n, ok in results:
        print(("PASS " if ok else "FAIL ") + n)
    print("\n" + ("ALL PASS" if not failed else "FAILED"))
    # 收尾：还原普通态再退出，避免把 maximized 几何写回 QSettings 污染后续测试
    win.setWindowState(Qt.WindowState.WindowNoState)
    QTimer.singleShot(150, app.quit)
    app.exec()
    return 0

if __name__ == "__main__":
    main()
