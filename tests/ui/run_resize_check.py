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

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from core.app_context import AppContext
from core.main_window import MainWindow, RESIZE_MARGIN

def mouse(ev_type, local, button=Qt.MouseButton.LeftButton, buttons=None):
    """构造投给窗口的鼠标事件（local = 窗口局部坐标）。"""
    if buttons is None:
        buttons = button if ev_type in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove) else Qt.MouseButton.NoButton
    mods = Qt.KeyboardModifier.NoModifier
    return QMouseEvent(
        ev_type,
        QPointF(local),               # localPos
        QPointF(local),               # globalPos（offscreen 下值无所谓，占位）
        button, buttons, mods,
    )

def drag_edge(win, edge_name, start_local, dxy):
    """在 start_local 按下 → 移动 dxy → 释放，返回 (before, after) 几何。"""
    before = win.frameGeometry().getRect()
    g0 = win.mapToGlobal(start_local)
    # press
    QApplication.sendEvent(win, mouse(QEvent.Type.MouseButtonPress, start_local))
    assert win._resize_edge is not None, f"{edge_name}: 按下未进入缩放态"
    # moves（多步，模拟真实拖动）
    steps = 6
    for i in range(1, steps + 1):
        cur = QPoint(g0.x() + dxy[0] * i // steps, g0.y() + dxy[1] * i // steps)
        local = win.mapFromGlobal(cur)
        ev = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(local), QPointF(local),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(win, ev)
    # release
    cur = QPoint(g0.x() + dxy[0], g0.y() + dxy[1])
    local = win.mapFromGlobal(cur)
    ev = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(local), QPointF(local),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(win, ev)
    after = win.frameGeometry().getRect()
    return before, after

def main() -> int:
    app = QApplication(sys.argv)
    ctx = AppContext()
    win = MainWindow(ctx)
    win.show()
    win.resize(1000, 700)
    app.processEvents()

    W, H = 1000, 700
    m = RESIZE_MARGIN
    cases = [
        ("左",        QPoint(2, H // 2),        (-80, 0)),
        ("右",        QPoint(W - 2, H // 2),    (90, 0)),
        ("上",        QPoint(W // 2, 2),        (0, -60)),
        ("下",        QPoint(W // 2, H - 2),    (0, 70)),
        ("左上角",    QPoint(2, 2),             (-70, -50)),
        ("右上角",    QPoint(W - 2, 2),         (60, -50)),
        ("左下角",    QPoint(2, H - 2),         (-60, 60)),
        ("右下角",    QPoint(W - 2, H - 2),     (80, 70)),
    ]

    results = []
    # ── 回归核心：同一侧连拖两次，第二次必须仍然生效 ──
    for name, start, dxy in cases:
        b1, a1 = drag_edge(win, name, start, dxy)
        ok1 = a1 != b1
        # 第二次拖（用户场景：拖一遍再拖）
        W2, H2 = a1[2], a1[3]
        start2 = QPoint(W2 - 2, H2 // 2) if "右" in name else QPoint(2, H2 // 2)
        if "上" in name:
            start2 = QPoint(W2 // 2, 2)
        elif "下" in name:
            start2 = QPoint(W2 // 2, H2 - 2)
        dxy2 = (40, 40) if "角" in name else ((50, 0) if name in ("左", "右") else (0, 50))
        b2, a2 = drag_edge(win, name + "·第二次", start2, dxy2)
        ok2 = a2 != b2
        results.append((f"{name} 第1拖{'生效' if ok1 else '无效'} / 第2拖{'生效' if ok2 else '无效'}", ok1 and ok2))
        # 复位，保持后续用例起点一致
        win.setGeometry(100, 100, W, H)
        app.processEvents()

    # ── 最大化：内缩归零 + 属性切换（offscreen 下 showMaximized 不改尺寸，
    #    用 setWindowState 显式切状态，走 changeEvent 兜底路径）──
    win.setWindowState(Qt.WindowState.WindowMaximized)
    app.processEvents()
    cw = win.centralWidget()
    cw_rect = cw.geometry().getRect()
    max_ok = cw_rect == (0, 0, win.width(), win.height())
    # PyQt6 会把 "true"/"false" 动态属性规范化为 bool；QSS 选择器
    # [maximized="true"] 对 bool 属性同样匹配（Qt 文档规定），两种都接受
    prop_val = win.property("maximized")
    prop_ok = prop_val in (True, "true")
    results.append((f"最大化 centralWidget 铺满 {cw_rect}", max_ok))
    results.append(("最大化属性=true", prop_ok))
    # 最大化时按边缘不应进入缩放态
    QApplication.sendEvent(win, mouse(QEvent.Type.MouseButtonPress, QPoint(2, 300)))
    no_resize_max = win._resize_edge is None
    QApplication.sendEvent(win, mouse(QEvent.Type.MouseButtonRelease, QPoint(2, 300)))
    results.append(("最大化时边缘不可拖拽", no_resize_max))

    # ── 恢复正常：margin 回 8 且可再次拖拽 ──
    win.setWindowState(Qt.WindowState.WindowNoState)
    win.setGeometry(100, 100, 1000, 700)
    app.processEvents()
    # offscreen 平台 setWindowState 异步生效：resizeEvent 先跑而 state 后到。
    # 真实 Windows 平台状态与尺寸同步变化（changeEvent/resizeEvent 双入口），
    # 不存在此洞；此处显式收尾只为让断言可测。
    win._sync_frame_state()
    cw_rect = cw.geometry().getRect()
    restore_ok = cw_rect == (m, m, 1000 - 2 * m, 700 - 2 * m)
    results.append((f"恢复后内缩 {m}px {cw_rect}", restore_ok))
    b, a = drag_edge(win, "恢复后右下角", QPoint(998, 698), (50, 40))
    results.append(("恢复后仍可拖拽", a != b))

    # ── grab 状态健康度：一轮拖拽后无残留 ──
    clean = win._resize_edge is None and win._resize_start_pos is None and win._resize_start_geom is None
    results.append(("拖拽状态机无残留", clean))

    failed = [n for n, ok in results if not ok]
    for n, ok in results:
        print(("PASS " if ok else "FAIL ") + n)
    print(f"\n{'ALL PASS' if not failed else 'FAILED: ' + '; '.join(failed)}")
    QTimer.singleShot(200, app.quit)
    app.exec()
    return 0

if __name__ == "__main__":
    main()
