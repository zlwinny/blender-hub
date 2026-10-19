"""边缘缩放卡死的回归测试。

背景：模态弹窗（插件详情页 exec）会抢走窗口的鼠标 grab，导致释放事件丢失，
`_resize_edge` 一直挂着——之后「不按鼠标、光移动鼠标」窗口就被拉伸。

修复：① mouseMoveEvent 里只有按住左键才当缩放，否则复位；② WindowDeactivate（弹窗打开）时复位。
本测试直接驱动这两个自愈路径。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_SKIP_DISCLAIMER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from core.app_context import AppContext  # noqa: E402
from core.main_window import MainWindow  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


ctx = AppContext()
win = MainWindow(ctx)
win.show()
app.processEvents()


def arm_resize_state():
    win._resize_edge = Qt.Edge.RightEdge
    win._resize_start_pos = QPoint(0, 0)
    win._resize_start_geom = win.frameGeometry()


# ── 1. 无按键移动 → 自愈复位 ──
arm_resize_state()
move = QMouseEvent(
    QEvent.Type.MouseMove, QPointF(10, 10), QPointF(10, 10), QPointF(10, 10),
    Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
)
win.mouseMoveEvent(move)
check("无按键移动后复位 _resize_edge", win._resize_edge is None)

# ── 2. 窗口失活（弹窗打开）→ 自愈复位 ──
arm_resize_state()
deactivate = QEvent(QEvent.Type.WindowDeactivate)
win.changeEvent(deactivate)
check("窗口失活后复位 _resize_edge", win._resize_edge is None)

# ── 3. 按住左键时仍正常走缩放路径 ──
arm_resize_state()
press = QMouseEvent(
    QEvent.Type.MouseMove, QPointF(10, 10), QPointF(10, 10), QPointF(10, 10),
    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
)
win.mouseMoveEvent(press)
check("按住左键时保持缩放状态", win._resize_edge is not None)

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
