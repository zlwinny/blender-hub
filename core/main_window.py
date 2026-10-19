"""主窗口 — 无边框自定义标题栏 + 侧边导航 + 页面栈 + 底部下载条 + Toast。"""

from __future__ import annotations

import os
import sys

from typing import Callable

from PyQt6.QtCore import QEvent, QObject, Qt, QPoint, QSettings, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWidgets import QMainWindow

from . import theme
from .icons import brand_pixmap, caption_icon, caption_icon_size, nav_icon, nav_icon_size
from .widgets import Toast


# 侧栏导航项：图标上、文字下的堆叠尺寸（对齐 FlClash 的窄条侧栏比例）
NAV_ICON_PX = 22
NAV_ITEM_H = 52
BRAND_LOGO_PX = 40

# ── 无边框窗口边缘缩放（Qt 原生 QWindow.startSystemResize）──
# 说明：早期版本用 Windows 的 WM_NCHITTEST + ctypes 解指针实现，但那条路径
# 只在真实消息循环下才触发、且指针操作有崩溃风险（实测导致程序无法启动）。
# 现改用 Qt 官方 API，不碰 ctypes，跨平台且安全。
# 应急开关：设 BLENDERHUB_NO_EDGE_RESIZE=1 可完全禁用边缘缩放。
EDGE_RESIZE_ENABLED = os.environ.get("BLENDERHUB_NO_EDGE_RESIZE") != "1"
RESIZE_MARGIN = 8          # 边界可拖拽宽度（px）。
# 6px 实测偏窄、鼠标不好抓，放宽到 8px 手感明显改善。


class _ToastBridge(QObject):
    """跨线程安全桥：EventBus 的订阅者在**发送线程**里同步执行，
    工作线程 emit 的 ui.toast 不能直接碰 UI，统一经信号排队回主线程。
    """

    show = pyqtSignal(str, bool)


class MainWindow(QMainWindow):
    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self._drag_pos: QPoint | None = None
        # 边缘缩放状态机：按下记基准，移动算新几何，释放清空
        self._resize_edge: Qt.Edge | None = None
        self._resize_start_pos: QPoint | None = None
        self._resize_start_geom = None
        self._pages: list[tuple[str, str, QWidget, object | None]] = []
        self._modules_by_key: dict[str, object | None] = {}
        self._nav_buttons: list[QToolButton] = []
        self._nav_offset: int = 0
        self._current_key: str | None = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        # 无边框窗口要做圆角，必须开启背景透明：否则 QSS 的 border-radius 裁不到
        # 窗口四角，圆角外面会露出黑色直角。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 紧凑布局：最小窗口尺寸下调（原 1080x680），小屏也能用
        self.setMinimumSize(920, 600)
        # 开启鼠标追踪：不做这一步，只有在按住按键时才会收到 mouseMoveEvent，
        # 边界光标就无法随鼠标位置变化
        self.setMouseTracking(True)
        self.resize(1240, 780)
        self.setObjectName("windowRoot")
        self.setStyleSheet(theme.stylesheet())

        root = QWidget()
        self.setCentralWidget(root)

        # 四周留出 RESIZE_MARGIN：必须设在 **QMainWindow 自己的布局** 上，
        # 让 centralWidget 整体内缩 —— 这样边缘 6px 才属于 QMainWindow 本身，
        # 窗口的 mousePressEvent/mouseMoveEvent 才能收到边界上的鼠标事件。
        # （踩过的坑：设在 centralWidget 的内部布局上没用，centralWidget 仍填满
        #   整个窗口，边缘事件被它吃掉，缩放完全没反应。）
        # 注意：QMainWindow 用的是私有 QMainWindowLayout，对它调
        # setContentsMargins() 无效（实测 centralWidget 仍填满窗口，几何 0,0）。
        # 真正在 resizeEvent 里设置 centralWidget 的几何才能内缩。
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_topbar())

        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.nav = QWidget()
        self.nav.setObjectName("navBar")
        self.nav.setFixedWidth(theme.Tokens.NAV_W)
        self._nav_lay = QVBoxLayout(self.nav)
        # 项间距 0（用户要求行距最小）；侧栏 76px 宽，左右各留 6px 让选中背景块不贴边
        self._nav_lay.setContentsMargins(6, 10, 6, 10)
        self._nav_lay.setSpacing(0)

        # 顶部品牌 logo（参照主流工具类 UI：侧栏顶部放一枚大图标）
        self._nav_logo = QLabel()
        self._nav_logo.setObjectName("navLogo")
        self._nav_logo.setPixmap(brand_pixmap(theme.Tokens.ACCENT, BRAND_LOGO_PX))
        self._nav_logo.setFixedSize(BRAND_LOGO_PX, BRAND_LOGO_PX)
        self._nav_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(0, 6, 0, 14)
        logo_row.addStretch()
        logo_row.addWidget(self._nav_logo)
        logo_row.addStretch()
        self._nav_lay.addLayout(logo_row)
        # logo 占了布局的第 0 项，按钮插入位置必须带这个偏移，
        # 否则 insertWidget(0, b) 会把按钮插到 logo 之上。
        self._nav_offset = self._nav_lay.count()

        # ── 底部：stretch + 分隔线 + 三横展开/合并按钮 ──
        # stretch 必须加：QVBoxLayout 所有项都是 Fixed 策略时，窗口拉高后
        # 多余空间会**均分到各条目之间**（图标越拉越散）—— stretch 把空隙
        # 全部收走，导航组才能保持顶部对齐、三横按钮钉在底部（Clash 布局）。
        self._nav_lay.addStretch(1)
        divider = QFrame()
        divider.setObjectName("navDivider")
        divider.setFrameShape(QFrame.Shape.NoFrame)  # 线条用 QSS 画，避免原生 HLine 双重样式
        divider.setFixedHeight(1)
        self._nav_lay.addWidget(divider)
        self._nav_toggle = QToolButton()
        self._nav_toggle.setObjectName("navToggle")
        self._nav_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_toggle.setToolTip("展开 / 合并侧栏")
        self._nav_toggle.setIcon(nav_icon("menu", theme.Tokens.TEXT_SUB, NAV_ICON_PX))
        self._nav_toggle.setIconSize(nav_icon_size(NAV_ICON_PX))
        self._nav_toggle.clicked.connect(self._toggle_nav)
        # 必须是 self._nav_lay（侧栏布局）！旧代码误写成根布局 lay，三横按钮
        # 被加到 centralWidget 顶层（topbar 下方），侧栏里永远看不到它。
        self._nav_lay.addWidget(self._nav_toggle, alignment=Qt.AlignmentFlag.AlignHCenter)

        # 展开状态持久化（Clash 记忆合并态）
        s = QSettings("BlenderHub Team", "BlenderHub")
        self._nav_collapsed = s.value("nav_collapsed", False, type=bool)

        bl.addWidget(self.nav)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {theme.Tokens.BG_ROOT};")
        bl.addWidget(self.stack, 1)
        lay.addWidget(body, 1)

        lay.addWidget(self._build_download_bar())

        self.toast = Toast(self.stack)

        # 接通 ui.toast：模块侧大量 emit（如扫描完成提示），但 EventBus 此前
        # 零订阅 → 这些提示永远不显示。这里订阅后排队回主线程显示。
        self._toast_bridge = _ToastBridge(self)
        self._toast_bridge.show.connect(self._on_toast_event)  # pyright: ignore[reportUnusedCallResult]
        try:
            self.ctx.bus.subscribe("ui.toast", self._emit_toast)
        except Exception as e:  # noqa: BLE001
            print(f"[app] 订阅 ui.toast 失败（不影响启动）: {e}")

        # 语言切换：侧栏与各页文案即时刷新（无需重启）
        try:
            from core import i18n

            i18n.add_listener(self.apply_language)
        except Exception as e:  # noqa: BLE001
            print(f"[app] 注册语言切换回调失败（不影响启动）: {e}")

        self._restore_geometry()

        # 边缘缩放靠 QApplication 级过滤器捕获：装在 app 上，
        # 边缘事件无论落到哪个子控件都能在分发前拦下。
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def showEvent(self, a0) -> None:  # noqa: N802
        super().showEvent(a0)
        # winId() 在 show 后才稳定；Win11 系统圆角此时生效
        self._apply_win11_corner()

    def _apply_win11_corner(self) -> None:
        """Win11 系统级窗口圆角（DWM 的 DWMWA_WINDOW_CORNER_PREFERENCE）。

        为什么需要它：最大化时内容必须满铺（用户明确要求"最大化还是小了"
        不能接受），而 QSS 圆角 + centralWidget 内缩会牺牲 8px 内容面积，
        且 QSS 圆角不裁剪子控件（topbar 直角会露出角区）。DWM 在**窗口级**
        裁剪圆角，内容满铺与圆角可以兼得 —— Win11 原生最大化也是这个效果。
        安全性：纯值传递（byref int），不碰指针解引用（与崩溃过的
        WM_NCHITTEST 方案本质不同）；非 Windows / Win10 静默失败回退。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            pref = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref),
                ctypes.sizeof(pref),
            )
        except Exception:  # noqa: BLE001
            pass  # 无 DWM（Win7/Win10 早期）：静默回退，QSS 圆角仍覆盖普通态

    # ── 无边框窗口的边缘缩放（自算几何）────────────────────────────────────
    # 为什么不用 QWindow.startSystemResize()：
    #   它靠向窗口投递 WM_NCLBUTTONDOWN 让系统接管缩放，而 FramelessWindowHint
    #   窗口**没有非客户区**，DefWindowProc 不处理这条消息 —— 结果是"调用成功
    #   但毫无反应"，不报错也不崩溃，最难排查。
    # 为什么不用 WM_NCHITTEST + ctypes：
    #   那条路实测让程序启动即崩（C 层崩溃，try 兜不住），已废弃。
    # 现在的方案：只用 Qt 公开 API + 自己按鼠标位移算几何。
    # 事件路由（2026-10-16 修"拖一次就失灵"）：
    #   拖拽的按下/移动/释放走**窗口自身的 mouse*Event** —— centralWidget 已
    #   内缩 RESIZE_MARGIN，窗口边缘 8px 内没有任何子控件，鼠标事件必然落到
    #   QMainWindow 自己。grabMouse() 在窗口自己的 mousePressEvent 里调用是
    #   Qt 的标准安全用法；旧版在 QApplication 级 eventFilter 的调用栈内
    #   grab/releaseMouse，属于 Qt 明确警告的危险场景，grab 状态卡死后
    #   第二次拖拽就再无反应（用户实测"拖一遍再拖不起作用"）。
    #   app 级 eventFilter 只保留 hover 光标提示，永远不吞事件、不 grab。
    def _sync_frame_state(self) -> None:
        """按窗口状态同步内缩与 QSS 属性（resizeEvent / changeEvent 双入口）。"""
        maximized = self.isMaximized() or self.isFullScreen()
        m = 0 if maximized else RESIZE_MARGIN
        cw = self.centralWidget()
        if cw is not None:
            cw.setGeometry(
                m, m,
                max(0, self.width() - 2 * m),
                max(0, self.height() - 2 * m),
            )
        # QSS 属性切换（最大化去圆角/边框），必须 repolish 才生效
        new_val = "true" if maximized else "false"
        if self.property("maximized") != new_val:
            self.setProperty("maximized", new_val)
            st = self.style()
            st.unpolish(self)
            st.polish(self)

    def resizeEvent(self, event) -> None:  # noqa: N802
        """让中央部件内缩 RESIZE_MARGIN，把窗口四边让出来。

        最大化/全屏时必须归零内缩并去掉圆角边框：否则内容四周永远
        留着 8px 空边 + 12px 圆角，看起来"最大化了但窗口还是小一圈"。
        """
        super().resizeEvent(event)
        self._sync_frame_state()

    def changeEvent(self, event) -> None:  # noqa: N802
        # 窗口状态切换（最大化/还原）时尺寸不一定变化（offscreen 等平台
        # showMaximized 不触发 resize），必须在 WindowStateChange 上兜底同步
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_frame_state()
            # 最大化按钮图标随状态切换（双击标题栏/Snap 等外部路径也覆盖）
            self._update_max_icon()
        # 模态弹窗（详情页 exec 等）打开时主窗口失活，抢走鼠标 grab；
        # 此时若正在边缘缩放，必须复位，否则弹窗关闭后鼠标一动就继续拉伸。
        elif event.type() == QEvent.Type.WindowDeactivate and self._resize_edge is not None:
            self._reset_resize()
        super().changeEvent(event)

    def _edge_at(self, pos) -> Qt.Edge | None:
        """判断窗口局部坐标落在哪条边/哪个角；不在边界上返回 None。"""
        if self.isMaximized() or self.isFullScreen():
            return None  # 最大化/全屏不允许缩放

        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        m = RESIZE_MARGIN

        edge = Qt.Edge(0)
        if x < m:
            edge |= Qt.Edge.LeftEdge
        elif x >= w - m:
            edge |= Qt.Edge.RightEdge
        if y < m:
            edge |= Qt.Edge.TopEdge
        elif y >= h - m:
            edge |= Qt.Edge.BottomEdge

        return edge if edge else None

    def _edge_at_global(self, gpos) -> Qt.Edge | None:
        """屏幕坐标版：全局事件过滤器只能拿到 globalPosition。"""
        g = self.frameGeometry()
        return self._edge_at(QPoint(gpos.x() - g.x(), gpos.y() - g.y()))

    @staticmethod
    def _cursor_for(edge: Qt.Edge) -> Qt.CursorShape:
        left = bool(edge & Qt.Edge.LeftEdge)
        right = bool(edge & Qt.Edge.RightEdge)
        top = bool(edge & Qt.Edge.TopEdge)
        bottom = bool(edge & Qt.Edge.BottomEdge)
        if left or right:
            if top:
                return Qt.CursorShape.SizeBDiagCursor
            if bottom:
                return Qt.CursorShape.SizeFDiagCursor
            return Qt.CursorShape.SizeHorCursor
        if top or bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _apply_resize(self, gpos) -> None:
        """按鼠标位移计算新几何（八向：四边 + 四角）。"""
        e = self._resize_edge
        st = self._resize_start_geom
        if e is None or st is None:
            return
        dx = gpos.x() - self._resize_start_pos.x()
        dy = gpos.y() - self._resize_start_pos.y()

        left, top = st.x(), st.y()
        right, bottom = st.x() + st.width(), st.y() + st.height()
        minw, minh = self.minimumWidth(), self.minimumHeight()

        if e & Qt.Edge.LeftEdge:
            left = min(st.x() + dx, right - minw)
        if e & Qt.Edge.RightEdge:
            right = max(st.x() + st.width() + dx, left + minw)
        if e & Qt.Edge.TopEdge:
            top = min(st.y() + dy, bottom - minh)
        if e & Qt.Edge.BottomEdge:
            bottom = max(st.y() + st.height() + dy, top + minh)

        self.setGeometry(left, top, right - left, bottom - top)

    def _reset_resize(self) -> None:
        """复位边缘缩放状态（含鼠标 grab）。

        只靠 mouseReleaseEvent 清理太脆弱：模态弹窗（exec）会抢走鼠标 grab，
        导致释放事件丢失，_resize_edge 卡住后「光移动鼠标就拉伸窗口」。
        这里统一复位，供 mouseRelease / 失活 / 无按键移动时自愈。
        """
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geom = None
        try:
            self.releaseMouse()
        except RuntimeError:
            pass

    # ── 边缘缩放的鼠标事件（窗口自身版）──────────────────────────────────
    # 事件能到达这里的保证：resizeEvent 把 centralWidget 内缩了 RESIZE_MARGIN，
    # 窗口四边 8px 内没有任何子控件，落在这里的鼠标事件必然由 QMainWindow
    # 自己接收（标题栏等子控件都在内缩区域之外，互不冲突）。
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if (
            e.button() == Qt.MouseButton.LeftButton
            and EDGE_RESIZE_ENABLED
        ):
            edge = self._edge_at(e.position().toPoint())
            if edge is not None:
                self._resize_edge = edge
                self._resize_start_pos = e.globalPosition().toPoint()
                self._resize_start_geom = self.frameGeometry()
                # 在窗口自己的事件处理器里 grab 是安全的标准用法：
                # 拖出窗口边界也不中断。旧版在 app 级过滤器的调用栈里
                # grab，grab 状态会卡死，第二次拖拽失灵。
                self.grabMouse()
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._resize_edge is not None:
            # 只有左键按住才算在拖拽缩放；若释放事件丢失（弹窗抢走 grab），
            # buttons 不含左键时立即复位，避免「光移动鼠标就拉伸窗口」
            if not (e.buttons() & Qt.MouseButton.LeftButton):
                self._reset_resize()
                super().mouseMoveEvent(e)
                return
            self._apply_resize(e.globalPosition().toPoint())
            e.accept()
            return
        # 未拖拽时按位置换边缘光标（mouseTracking 已开启）
        if EDGE_RESIZE_ENABLED and not e.buttons():
            edge = self._edge_at(e.position().toPoint())
            if edge is not None:
                self.setCursor(self._cursor_for(edge))
            else:
                self.unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._resize_edge is not None:
            self._reset_resize()
            e.accept()
            return
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """app 级过滤器：只做边缘 hover 光标提示，**永远不吞事件、不 grab**。

        鼠标滑进 centralWidget 里的子控件后，窗口自身收不到 move 事件，
        边缘光标形状会残留 —— 由这个过滤器统一纠正。
        """
        if not EDGE_RESIZE_ENABLED:
            return False
        if event.type() == QEvent.Type.MouseMove:
            if (
                isinstance(obj, QWidget)
                and obj.window() is self
                and self._resize_edge is None
                and not event.buttons()
            ):
                edge = self._edge_at_global(event.globalPosition().toPoint())
                if edge is not None:
                    self.setCursor(self._cursor_for(edge))
                else:
                    self.unsetCursor()
        return False


    # ── 标题栏 ────────────────────────────────────────────────────────────
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(theme.Tokens.TOPBAR_H)
        lay = QHBoxLayout(bar)
        # 左 margin 收到 8：标题栏左侧品牌区（logo + BlenderHub + v2.0）
        # 已按用户要求移除（2026-10-16），品牌标识只保留在侧栏顶部。
        lay.setContentsMargins(8, 0, 6, 0)
        lay.addStretch()

        # 账户入口（沿用 v1 顶栏；v1.0 为占位，v1.5 正式开放账户体系）
        self.register_btn = QPushButton("注册")
        self.register_btn.setObjectName("ghost")
        self.register_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.register_btn.setToolTip("创建 Blender Hub 账户（v1.5 开放）")
        self.register_btn.clicked.connect(self._on_register)
        lay.addWidget(self.register_btn)

        self.login_btn = QPushButton("登录")
        self.login_btn.setObjectName("ghost")
        self.login_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.login_btn.setToolTip("登录以启用云同步（v1.5 开放）")
        self.login_btn.clicked.connect(self._on_login)
        lay.addWidget(self.login_btn)
        lay.addSpacing(6)

        # ── Windows 11 标准 caption 按钮（Microsoft Learn《标题栏设计》：
        #    46x32 热区、矢量图标、还原/最大化图标随窗口状态切换）──
        self._win_min_btn = QPushButton()
        self._win_min_btn.setObjectName("winBtn")
        self._win_min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._win_min_btn.setIcon(caption_icon("min", theme.Tokens.TEXT_SUB))
        self._win_min_btn.setIconSize(caption_icon_size())
        self._win_min_btn.setToolTip("最小化")
        self._win_min_btn.clicked.connect(self.showMinimized)
        lay.addWidget(self._win_min_btn)

        self._win_max_btn = QPushButton()
        self._win_max_btn.setObjectName("winBtn")
        self._win_max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._win_max_btn.setToolTip("最大化")
        self._win_max_btn.clicked.connect(self._toggle_max)
        lay.addWidget(self._win_max_btn)

        self._win_close_btn = QPushButton()
        self._win_close_btn.setObjectName("winBtnClose")
        self._win_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._win_close_btn.setIcon(caption_icon("close", theme.Tokens.TEXT_SUB))
        self._win_close_btn.setIconSize(caption_icon_size())
        self._win_close_btn.setToolTip("关闭")
        self._win_close_btn.clicked.connect(self.close)
        lay.addWidget(self._win_close_btn)

        self._update_max_icon()
        bar.mousePressEvent = self._topbar_press      # type: ignore[assignment]
        bar.mouseMoveEvent = self._topbar_move        # type: ignore[assignment]
        bar.mouseDoubleClickEvent = lambda e: self._toggle_max()  # type: ignore[assignment]
        return bar

    def _topbar_press(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def _topbar_move(self, e: QMouseEvent) -> None:
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    # mouseReleaseEvent 已并入上方的边缘缩放实现（统一处理缩放释放与拖动清理）

    def _toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()
        self._update_max_icon()

    def _update_max_icon(self) -> None:
        """最大化/还原图标随状态切换（Win11 原生行为：最大化显示方框、
        已最大化显示双方框还原图标）。"""
        kind = "restore" if self.isMaximized() else "max"
        self._win_max_btn.setIcon(caption_icon(kind, theme.Tokens.TEXT_SUB))
        self._win_max_btn.setToolTip("向下还原" if self.isMaximized() else "最大化")

    # ── 账户入口 ──────────────────────────────────────────────────────────
    def _on_login(self) -> None:
        auth = getattr(self.ctx, "auth", None)
        if auth is not None and getattr(auth, "is_logged_in", False):
            self.notify(f"已登录：{auth.current_user.username}")
            return
        self.ctx.info("点击登录（账户体系 v1.5 开放）", module="main_window")
        self.notify("账户体系 v1.5 开放，当前本地优先、无需登录")

    def _on_register(self) -> None:
        self.ctx.info("点击注册（账户体系 v1.5 开放）", module="main_window")
        self.notify("注册入口 v1.5 开放")

    # ── 导航 ──────────────────────────────────────────────────────────────
    def add_page(self, key: str, label: str, widget: QWidget, module: object | None = None) -> None:
        # 导航图标由 key 决定（core/icons.py 矢量绘制），不再由调用方传入字符。
        # module: 对应的模块实例，用于页面切换时派发 on_navigate_to / on_navigate_away。
        # 旧实现完全没有派发这两个钩子，模块的"进入即刷新/离开即停"逻辑全部失效。
        self._pages.append((key, label, widget, module))
        self._modules_by_key[key] = module
        self.stack.addWidget(widget)
        idx = len(self._pages) - 1
        # 参照主流深色工具类 UI 的侧栏：图标在上、文字在下（垂直堆叠）。
        # QToolButton + ToolButtonTextUnderIcon 是 Qt 原生支持，比手工拼
        # QVBoxLayout 更可靠（不会在 QSS 与尺寸策略上打架）。
        b = QToolButton()
        b.setObjectName("navItem")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setIcon(nav_icon(key, theme.Tokens.TEXT_SUB, NAV_ICON_PX))
        b.setIconSize(nav_icon_size(NAV_ICON_PX))
        b.setText(label)
        b.setFixedHeight(NAV_ITEM_H)
        b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        b.setProperty("icon_key", key)
        b.clicked.connect(lambda checked=False, i=idx: self.select(i))
        self._nav_lay.insertWidget(idx + self._nav_offset, b)
        self._nav_buttons.append(b)  # 与 _pages 同序，供 select 精确定位
        # 启动时若记忆为合并态，页面加载完立即应用（含最后一个页面）
        self._apply_nav_state()
        if idx == 0:
            self.select(0)

    # ── 侧栏展开 / 合并（Clash 三横按钮交互）──────────────────────────────
    def apply_language(self, _lang: str = "") -> None:
        """切换界面语言：刷新侧栏文案并让各模块重刷自身文案（无需重启）。"""
        mm = getattr(self.ctx, "module_manager", None)
        infos = {i.id: i for i in mm.get_all_modules()} if mm is not None else {}

        for idx, (key, _label, widget, module) in enumerate(self._pages):
            info = infos.get(key)
            label = info.display_name() if info is not None else _label
            self._pages[idx] = (key, label, widget, module)
            if idx < len(self._nav_buttons) and not self._nav_collapsed:
                self._nav_buttons[idx].setText(label)
            # 模块自己实现 retranslate() 来更新已创建控件上的文案
            fn = getattr(module, "retranslate", None)
            if callable(fn):
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    log = getattr(self.ctx, "log_service", None)
                    if log is not None:
                        log.debug(f"retranslate('{key}') failed: {e}", module="main_window")

    def _toggle_nav(self) -> None:
        self._nav_collapsed = not self._nav_collapsed
        QSettings("BlenderHub Team", "BlenderHub").setValue(
            "nav_collapsed", self._nav_collapsed
        )
        self._apply_nav_state()

    def _apply_nav_state(self) -> None:
        """按合并态切换侧栏宽度 / 按钮文字 / logo 尺寸。"""
        w = theme.Tokens.NAV_W_COLLAPSED if self._nav_collapsed else theme.Tokens.NAV_W
        self.nav.setFixedWidth(w)
        for btn, page in zip(self._nav_buttons, self._pages):
            # 合并态只留图标（ToolButtonTextUnderIcon 无文字时图标自动居中）
            btn.setText("" if self._nav_collapsed else page[1])
        logo_px = 28 if self._nav_collapsed else BRAND_LOGO_PX
        self._nav_logo.setPixmap(brand_pixmap(theme.Tokens.ACCENT, logo_px))
        self._nav_logo.setFixedSize(logo_px, logo_px)

    def select(self, idx: int) -> None:
        # 旧实现用 "i - 1 == idx" 判断选中，但 _nav_lay 里只有导航按钮（没有 header/spacer），
        # index 0 就是第一个按钮 —— 于是点击第 N 项会高亮第 N+1 项，最后一项永远高亮不了。
        # 改为按记录下来的按钮列表直接对应，彻底消除偏移。
        for i, btn in enumerate(self._nav_buttons):
            selected = "true" if i == idx else "false"
            if btn.property("selected") != selected:
                btn.setProperty("selected", selected)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
                # 选中态图标换强调色（QIcon 跟随 QSS 的 [selected] 变化，
                # 需手动重设）
                icon_key = btn.property("icon_key")
                if icon_key:
                    color = theme.Tokens.ACCENT if i == idx else theme.Tokens.TEXT_SUB
                    btn.setIcon(nav_icon(icon_key, color))
        self.stack.setCurrentIndex(idx)
        key = self._pages[idx][0]

        # 模块生命周期钩子：旧实现只 emit 一个无人订阅的事件（EventBus 全项目零 subscribe），
        # version_library 的首次扫描与 addon_store 的缓存刷新因此从不触发。
        if self._current_key is not None and self._current_key != key:
            self._dispatch_nav(self._current_key, "on_navigate_away")
        self._current_key = key
        self._dispatch_nav(key, "on_navigate_to")

        # payload 必须是 dict（EventBus 签名为 payload: dict），旧实现传了裸 str
        self.ctx.bus.emit("ui.page_changed", {"key": key})
        self.ctx.info(f"切换到 {self._pages[idx][1]}", module="main_window")

    def _dispatch_nav(self, key: str, hook: str) -> None:
        """派发模块的 on_navigate_to / on_navigate_away 钩子（异常隔离，不阻断切页）"""
        module = self._modules_by_key.get(key)
        if module is None:
            return
        fn = getattr(module, hook, None)
        if not callable(fn):
            return
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            log = getattr(self.ctx, "log_service", None)
            if log is not None:
                log.debug(f"{hook}('{key}') failed: {e}", module="main_window")

    # ── 底部下载条 ────────────────────────────────────────────────────────
    def _build_download_bar(self) -> QWidget:
        w = QWidget()
        w.setObjectName("dlBar")
        w.setFixedHeight(0)
        w.setVisible(False)
        self._dl_widget = w
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)
        self._dl_label = QLabel("下载中…")
        # 旧实现用 TEXT_SUB（灰）12px —— 一行灰字贴在窗口最底部，
        # 用户三次反馈"看不到下载进度"，配色是主因之一。改主色加粗。
        self._dl_label.setStyleSheet(
            f"color: {theme.Tokens.ACCENT}; font-size: 12px; font-weight: 600;"
        )
        lay.addWidget(self._dl_label)
        # 真正的进度条（旧实现只有文字，用户找不到"进度条"）
        self._dl_bar = QProgressBar()
        self._dl_bar.setObjectName("dlBarProgress")
        self._dl_bar.setRange(0, 100)
        self._dl_bar.setValue(0)
        self._dl_bar.setTextVisible(False)
        self._dl_bar.setFixedSize(220, 6)
        self._dl_bar.setVisible(False)
        lay.addWidget(self._dl_bar)
        lay.addStretch()
        w.setStyleSheet(
            f"background-color: {theme.Tokens.BG_PANEL}; border-top: 1px solid {theme.Tokens.BORDER};"
        )
        return w

    def show_download(self, text: str, percent: int | None = None) -> None:
        """显示/更新底部下载条。

        percent: 0-100 的整数；传 None 表示进度未知（隐藏进度条）。
        兼容旧调用（只传文字）。
        """
        self._dl_label.setText(text)
        try:
            if percent is None:
                self._dl_bar.setVisible(False)
            else:
                self._dl_bar.setVisible(True)
                self._dl_bar.setValue(max(0, min(100, int(percent))))
        except Exception:  # noqa: BLE001 — 进度条异常绝不能影响下载主流程
            pass
        if not self._dl_widget.isVisible():
            self._dl_widget.setVisible(True)
            self._dl_widget.setFixedHeight(42)

    def hide_download(self) -> None:
        self._dl_widget.setVisible(False)
        self._dl_widget.setFixedHeight(0)
        try:
            self._dl_bar.setValue(0)
        except Exception:  # noqa: BLE001
            pass

    # ── 通知 ──────────────────────────────────────────────────────────────
    def _emit_toast(self, payload) -> None:
        """EventBus 回调（可能在任意线程）：只做解析，UI 由信号转主线程。"""
        text = ""
        ok = True
        if isinstance(payload, dict):
            text = str(payload.get("message", "") or "")
            ok = bool(payload.get("ok", True))
        elif isinstance(payload, str):
            text = payload
        if not text:
            return
        self._toast_bridge.show.emit(text, ok)

    def _on_toast_event(self, text: str, ok: bool) -> None:
        self.notify(text, ok=ok)

    def notify(self, text: str, ok: bool = True) -> None:
        # 右下角浮层"弹窗"也记一条：排查"哪里总弹提示"时同样需要
        try:
            self.ctx.info(f"toast: {text}", module="main_window")
        except Exception:  # noqa: BLE001
            pass
        self.toast.show_msg(text, ok=ok)

    # ── 记忆几何 ──────────────────────────────────────────────────────────
    def _restore_geometry(self) -> None:
        s = QSettings("BlenderHub Team", "BlenderHub")
        geo = s.value("geometry")
        if geo:
            self.restoreGeometry(geo)  # type: ignore[arg-type]

    def closeEvent(self, e) -> None:  # noqa: N802
        # 退出清理：旧实现只存几何，下载线程 / ScanWorker / InstallWorker 在解释器退出时
        # 被强杀，PyQt 会抛 "QThread: Destroyed while thread is still running"。
        # 正确顺序：先停后台传输，再让模块执行 on_shutdown 停扫描/停线程，最后存几何。
        try:
            self._dispatch_nav(self._current_key or "", "on_navigate_away")
        except Exception:  # noqa: BLE001
            pass

        try:
            self.ctx.download_service.shutdown()
        except Exception as e:  # noqa: BLE001
            log = getattr(self.ctx, "log_service", None)
            if log is not None:
                log.error(f"download_service.shutdown failed: {e}", module="main_window")

        try:
            self.ctx.module_manager.shutdown_all()
        except Exception as e:  # noqa: BLE001
            log = getattr(self.ctx, "log_service", None)
            if log is not None:
                log.error(f"module_manager.shutdown_all failed: {e}", module="main_window")

        # 回收本应用启动的 Blender 子进程（只处理本服务 launch 的，不误杀用户手动开的）
        try:
            self.ctx.blender_service.terminate_all()
        except Exception as e:  # noqa: BLE001
            log = getattr(self.ctx, "log_service", None)
            if log is not None:
                log.error(f"blender_service.terminate_all failed: {e}", module="main_window")

        s = QSettings("BlenderHub Team", "BlenderHub")
        s.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)
