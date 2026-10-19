"""可复用 UI 组件 — 卡片、页头、徽标、Toast、空状态。"""

from __future__ import annotations

from pathlib import Path
from threading import current_thread

from PyQt6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QObject,
    QPropertyAnimation,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .theme import Tokens


def is_main_thread() -> bool:
    """当前是否在 Qt 主线程（GUI 操作必须在主线程执行）"""
    app = QCoreApplication.instance()
    if app is None:
        # 无应用实例时（如单元测试）退化为"是主线程"
        return True
    return current_thread() is app.thread()


class Card(QFrame):
    """圆角卡片，带柔和阴影与 hover 高亮。"""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, shadow: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if shadow:
            eff = QGraphicsDropShadowEffect(self)
            eff.setBlurRadius(18)
            eff.setOffset(0, 3)
            eff.setColor(QColor(0, 0, 0, 110))
            self.setGraphicsEffect(eff)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


def badge(text: str, kind: str = "accent") -> QLabel:
    """kind: accent | muted | ok | warn"""
    obj = {"accent": "badge", "muted": "badgeMuted", "ok": "badgeOk", "warn": "badgeWarn"}[kind]
    lb = QLabel(text)
    lb.setObjectName(obj)
    return lb


def page_header(title: str, hint: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    lay.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setObjectName("pageHint")
        lay.addWidget(h)
    return w


def section_title(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("sectionTitle")
    return lb


def thumb_label(path: Path | None, fallback: str = "◫", size: int = 64) -> QLabel:
    lb = QLabel()
    lb.setFixedSize(size, size)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if path and path.exists():
        pm = QPixmap(str(path))
        if not pm.isNull():
            lb.setPixmap(
                pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
            return lb
    lb.setText(fallback)
    lb.setStyleSheet(f"font-size: {int(size * 0.45)}px; color: {Tokens.TEXT_MUTED};")
    return lb


def empty_state(text: str, hint: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.setSpacing(6)
    icon = QLabel("◍")
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    icon.setStyleSheet(f"font-size: 42px; color: {Tokens.TEXT_MUTED};")
    lay.addWidget(icon)
    t = QLabel(text)
    t.setAlignment(Qt.AlignmentFlag.AlignCenter)
    t.setStyleSheet(f"color: {Tokens.TEXT_SUB}; font-size: 14px; font-weight: 600;")
    lay.addWidget(t)
    if hint:
        h = QLabel(hint)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.setObjectName("pageHint")
        lay.addWidget(h)
    return w


class Toast(QLabel):
    """右下角浮层通知，淡入后自动淡出。

    旧实现在 show_msg 时按父控件当时尺寸定位一次，窗口 resize 后不再更新，
    通知会停留在旧位置（甚至跑到窗口外）。这里改为覆盖父控件的 resizeEvent，
    在通知可见期间持续跟随右下角。
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(
            f"background-color: {Tokens.BG_ELEVATED}; border: 1px solid {Tokens.BORDER_STRONG};"
            f"border-radius: 10px; padding: 10px 16px; color: {Tokens.TEXT}; font-size: 12.5px;"
        )
        self.setWindowOpacity(0.0)
        self.hide()
        self._anim = QPropertyAnimation(self, b"windowOpacity")
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

        # 跟随父窗口尺寸变化重新定位（安装事件过滤器而非继承，
        # 避免 Toast 的 resizeEvent 被自身 adjustSize 触发的递归影响）
        parent.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """父控件 resize 时重新贴到右下角"""
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._reposition()
        return super().eventFilter(obj, event)

    def _reposition(self) -> None:
        p = self.parentWidget()
        if p is not None and self.isVisible():
            self.adjustSize()
            self.move(
                max(0, p.width() - self.width() - 24),
                max(0, p.height() - self.height() - 24),
            )

    def show_msg(self, text: str, duration: int = 2600, ok: bool = True) -> None:
        # GUI 安全：非主线程调用时（如下载工作线程）排队投递，
        # 否则 Qt 会报 "QObject::setParent: Cannot set parent, new parent is in a different thread"
        if not is_main_thread():
            QTimer.singleShot(0, lambda: self.show_msg(text, duration, ok))
            return

        color = Tokens.SUCCESS if ok else Tokens.DANGER
        self.setText(f"<span style='color:{color}'>●</span>&nbsp; {text}")
        self.adjustSize()
        p = self.parentWidget()
        if p:
            self.move(
                max(0, p.width() - self.width() - 24),
                max(0, p.height() - self.height() - 24),
            )
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(duration)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()
        QTimer.singleShot(200, self.hide)
