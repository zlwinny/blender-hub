"""
插件模块 — 在线目录浏览 + 智能卡片布局 + 安装

数据源：BudecoTop（在线目录）
UI：自适应网格布局 + 详情预览面板 + 兼容性检测
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import requests
from PyQt6.QtCore import QObject, QRectF, Qt, QThread, QTimer, QUrl, pyqtSignal
from typing_extensions import override
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Protocol

    from PyQt6.QtCore import QEvent

    class BlenderVersionInfo(Protocol):
        """Blender 版本信息类型 stub（避免 Any 类型）"""
        label: str
        version: str
        path: Path

from core import theme  # pyright: ignore[reportImplicitRelativeImport]
from core.api.module_api import BaseModule  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
from core.services.addon_service import AddonInfo, AddonService  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
from core.services.budeco_auth import (  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
    BudecoAuthService,
    DownloadTarget,
)
from modules.addon_store.qq_login_dialog import QQLoginDialog  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
from modules.addon_store.install_browser import BrowserInstallDialog  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
from core.services.addon_verifier import AddonVerifier  # pyright: ignore[reportImplicitRelativeImport]
from core.services.log_service import redact_url  # pyright: ignore[reportImplicitRelativeImport]


# 插件安装包大小上限：防止超大文件/无限流把内存打满（200 MB）
MAX_ADDON_BYTES = 200 * 1024 * 1024


# ============================================================
# AddonCard — 智能自适应卡片
# ============================================================

class _ThumbFrame(QFrame):
    """缩略图容器 — 自绘圆角底 + 边框。

    为什么自绘：QSS 的 border-radius 画不出圆角边框（直角背景问题见
    AddonCard 注释），且用户要求图块有可见边框（"图块边框没有了"）。
    WA_TranslucentBackground 阻止 Qt 预填 palette 直角底色。
    """

    RADIUS = 6

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, a0) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 0.5px 内缩：1px 描边居内，不被父卡片圆角裁掉外侧
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.fillPath(path, QColor(theme.Tokens.OVERLAY_BG))
        p.setPen(QPen(QColor(theme.Tokens.BORDER), 1.0))
        p.drawPath(path)


class AddonCard(QFrame):
    """插件卡片 — 固定宽度、缩略图等比半尺寸 + 自适应高度（文字完整显示）"""

    CARD_WIDTH: int = 240    # 卡片固定宽度
    CARD_HEIGHT: int = 216   # 卡片固定高度（缩略图半尺寸 + 信息区；用户要求固定，
                             # 不接受最大化时被网格行高拉长。名称限单行后从 232 收紧）
    THUMB_SIZE: int = 106    # 缩略图尺寸：原 212px 等比例缩小一半（用户要求）
    CARD_RADIUS: int = 14    # 与 theme.Tokens.R_CARD 一致；QSS 的 border-radius
                             # 不裁剪子控件，必须用 paintEvent 裁剪才真正生效

    install_clicked: pyqtSignal = pyqtSignal(str)
    details_clicked: pyqtSignal = pyqtSignal(str)

    def __init__(
        self,
        addon: AddonInfo,
        is_installed: bool = False,
        thumbnail_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.addon: AddonInfo = addon
        self._is_installed: bool = is_installed
        self._thumbnail_path: Path | None = thumbnail_path

        self.setObjectName("card")
        # WA_TranslucentBackground + 自绘 paintEvent 组合：
        # 透明属性负责"Qt 不预填 palette 直角底色"（否则圆角外露出
        # BG_ROOT 直角块，实测角 alpha=255）；自绘负责画圆角背景。
        # 注意：不能只开透明依赖 QSS 背景 —— 那条路实测 QSS 背景整体丢失。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 固定宽高：窗口最大化时卡片不再被网格行高拉长（用户 2026-10-16
        # 反馈"最大化后插件块也跟随拉长"）。文字完整性由描述区 3 行固定
        # 高度 + toolTip 全文保证。
        self.setFixedSize(self.CARD_WIDTH, self.CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._hover = False

        self._build_ui()
        self._update_thumbnail()

    def _build_ui(self) -> None:
        self._layout: QVBoxLayout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 10, 14, 12)
        self._layout.setSpacing(5)

        # === 缩略图区域（等比半尺寸：106x106，有图显示图，无图显示图标） ===
        thumb_size = self.THUMB_SIZE  # 106px（原 212 的一半）

        self._thumb_container: _ThumbFrame = _ThumbFrame()
        self._thumb_container.setObjectName("thumbContainer")
        self._thumb_container.setFixedSize(thumb_size, thumb_size)
        self._thumb_container.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._thumb_layout: QVBoxLayout = QVBoxLayout(self._thumb_container)
        # 上下左右留 2px：子 pixmap 不贴容器边，容器自身的 6px 圆角才不被
        # label 的直角内容顶掉（QSS 圆角同样不裁剪子控件）
        self._thumb_layout.setContentsMargins(2, 2, 2, 2)
        self._thumb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._thumb_label: QLabel = QLabel()
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setScaledContents(False)
        self._thumb_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._thumb_layout.addWidget(self._thumb_label)

        # 无图时的大图标（字号随缩略图等比缩小：48 → 32）
        self._icon_label: QLabel = QLabel(self.addon.icon)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setStyleSheet("font-size: 32px;")
        self._thumb_layout.addWidget(self._icon_label)

        self._layout.addWidget(self._thumb_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # === 名称 + 来源标记 ===
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self._name_label: QLabel = QLabel(self.addon.name)
        self._name_label.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.Tokens.ON_CARD}; background: transparent;")
        # 名称限单行（用户 2026-10-17"减少一行"：budeco 标题常把版本号+
        # 描述塞进名称，两行换行把卡片撑高）。固定单行高度 + 悬停看全名。
        self._name_label.setWordWrap(False)
        self._name_label.setFixedHeight(18)
        self._name_label.setToolTip(self.addon.name)
        name_row.addWidget(self._name_label, 1)

        # 来源标记
        source_icon = {"github": "\U0001f419", "gitee": "\U0001f3ee", "blender_org": "\U0001f512",
                       "community": "\U0001f465", "builtin": "\u2b50", "budeco": "\U0001f9e9",
                       "local": "\U0001f4c1"}
        src = self.addon.source
        self._source_tag: QLabel = QLabel(source_icon.get(src, "\U0001f4e6"))
        self._source_tag.setToolTip(
            {"github": "GitHub 开源", "gitee": "Gitee 国内镜像",
             "blender_org": "Blender 官方", "community": "社区推荐",
             "builtin": "内置精选", "budeco": "BudecoTop 中文站"}.get(src, src)
        )
        self._source_tag.setStyleSheet("font-size: 14px; background: transparent;")
        name_row.addWidget(self._source_tag)
        self._layout.addLayout(name_row)

        # === 作者 ===
        self._author_label: QLabel = QLabel(self.addon.author)
        self._author_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 11px; background: transparent;")
        self._layout.addWidget(self._author_label)

        # 描述文字已按用户要求移除（2026-10-17"描述文字直接不要"）；
        # 完整描述见详情弹窗（AddonDetailDialog 仍保留简介区）。

        self._layout.addStretch()

        # === 标签行 ===
        if self.addon.tags:
            tags_row = QHBoxLayout()
            tags_row.setSpacing(4)
            for tag in self.addon.tags[:3]:
                tag_label = QLabel(tag)
                tag_label.setStyleSheet(
                    f"background-color: {theme.Tokens.HIGHLIGHT_BLUE_DIM}; color: {theme.Tokens.HIGHLIGHT_BLUE}; "
                    + "border-radius: 3px; padding: 1px 6px; font-size: 10px;"
                )
                tags_row.addWidget(tag_label)
            tags_row.addStretch()
            self._layout.addLayout(tags_row)

        # === 底部信息行 ===
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        # 评分
        rating_text = f"\u2b50 {self.addon.rating:.1f}"
        self._rating_label: QLabel = QLabel(rating_text)
        self._rating_label.setStyleSheet(f"color: {theme.Tokens.ACCENT_LEGACY}; font-size: 11px; background: transparent;")
        bottom.addWidget(self._rating_label)

        # 下载量
        dl = self.addon.downloads
        if dl >= 1000:
            dl_text = f"\U0001f4e5 {dl/1000:.0f}k" if dl < 1000000 else f"\U0001f4e5 {dl/1000000:.1f}M"
        else:
            dl_text = f"\U0001f4e5 {dl}"
        self._downloads_label: QLabel = QLabel(dl_text)
        self._downloads_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 11px; background: transparent;")
        bottom.addWidget(self._downloads_label)

        bottom.addStretch()

        # 价格 / 安装按钮
        if self.addon.free:
            if self._is_installed:
                btn = QPushButton("\u2713 已安装")
                btn.setEnabled(False)
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {theme.Tokens.SUCCESS_BG}; color: {theme.Tokens.SUCCESS_BRIGHT}; border: none; "
                    + "border-radius: 5px; padding: 4px 12px; font-size: 11px; }"
                )
            else:
                btn = QPushButton("安装")
                btn.setObjectName("primaryButton")
                btn.setFixedHeight(26)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)  # QSS 无 cursor，须代码设置
                _ = btn.clicked.connect(lambda: self.install_clicked.emit(self.addon.id))  # pyright: ignore[reportUnknownMemberType]
            bottom.addWidget(btn)
        else:
            price_label = QLabel(self.addon.price or "付费")
            price_label.setStyleSheet(f"color: {theme.Tokens.ACCENT_LEGACY}; font-weight: bold; font-size: 12px;")
            bottom.addWidget(price_label)

        self._layout.addLayout(bottom)

    @override
    def mousePressEvent(self, a0: QMouseEvent | None) -> None:  # noqa: N802
        """点击整张卡片查看详情"""
        self.details_clicked.emit(self.addon.id)
        super().mousePressEvent(a0)

    def enterEvent(self, a0) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(a0)

    def leaveEvent(self, a0) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(a0)

    def paintEvent(self, a0) -> None:  # noqa: N802
        """卡片自绘：圆角背景 + 1px 边框（hover 变色）。

        为什么不用 QSS（两条路都实测失败）：
        1. 不开 WA_TranslucentBackground：QSS border-radius 只画直角
           背景，圆角区被填充（角 alpha=255，用户看"没有圆角"）；
        2. 开 WA_TranslucentBackground：QSS 背景**整体丢失**，卡片区域
           露出窗口根底色，缩略图块边界消失（"图块边框没有了"）。
        自绘 fillPath 圆角矩形是唯一可控路径；不调 super().paintEvent，
        避免 QSS/全局 QWidget 规则再把直角背景画回来。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.CARD_RADIUS, self.CARD_RADIUS)
        bg = theme.Tokens.BG_CARD_HOVER if self._hover else theme.Tokens.BG_CARD
        p.fillPath(path, QColor(bg))
        border = theme.Tokens.BORDER_STRONG if self._hover else theme.Tokens.BORDER
        p.setPen(QPen(QColor(border), 1.0))
        p.drawPath(path)

    def _update_thumbnail(self) -> None:
        """更新缩略图：106px 等比显示，保持宽高比裁剪居中"""
        thumb_size = self.THUMB_SIZE  # 106px

        if self._thumbnail_path and self._thumbnail_path.exists():
            pixmap = QPixmap(str(self._thumbnail_path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    thumb_size, thumb_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._thumb_label.setPixmap(scaled)
                self._thumb_label.setFixedSize(thumb_size - 4, thumb_size - 4)  # 留 2px 边距给容器圆角
                self._icon_label.hide()
            else:
                self._thumb_label.clear()
                self._icon_label.show()
        else:
            self._thumb_label.clear()
            self._icon_label.show()

    def set_thumbnail(self, path: Path | None) -> None:
        self._thumbnail_path = path
        self._update_thumbnail()


# ============================================================
# AddonDetailDialog — 详情弹窗（含缩略图）
# ============================================================

class AddonDetailDialog(QDialog):
    """插件详情对话框"""
    install_requested: pyqtSignal = pyqtSignal(str)

    def __init__(
        self,
        addon: AddonInfo,
        is_installed: bool = False,
        thumbnail_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.addon: AddonInfo = addon
        self._is_installed: bool = is_installed
        self._thumbnail_path: Path | None = thumbnail_path

        self.setWindowTitle(f"{addon.name} - 插件详情")
        self.setMinimumSize(480, 520)
        self.setMaximumSize(600, 700)
        # 注意：f-string 里的 CSS 大括号必须 {{ }} 转义！旧实现直接写
        # { background-color: ... } 被 Python 当表达式解析，点击卡片开详情
        # 必抛 NameError（PyQt 槽函数异常直接终止程序 = 用户看到"点击崩溃"）。
        self.setStyleSheet(f"""
            QDialog {{ background-color: {theme.Tokens.ROW_ALT5}; }}
            QLabel {{ color: {theme.Tokens.ON_CARD_SUB}; }}
        """)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # === 顶部缩略图（1:1 正方形） ===
        if self._thumbnail_path and self._thumbnail_path.exists():
            thumb_frame = QFrame()
            thumb_frame.setObjectName("detailThumb")
            thumb_frame.setStyleSheet(
                f"#detailThumb {{ background-color: {theme.Tokens.OVERLAY_BG_ALT}; border-radius: 8px; }}"
            )
            thumb_frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            thumb_frame.setFixedSize(220, 220)
            thumb_layout = QVBoxLayout(thumb_frame)
            thumb_layout.setContentsMargins(0, 0, 0, 0)
            thumb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_img = QLabel()
            thumb_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(str(self._thumbnail_path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    212, 212,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                thumb_img.setPixmap(scaled)
                thumb_img.setFixedSize(212, 212)
            thumb_layout.addWidget(thumb_img)
            layout.addWidget(thumb_frame, alignment=Qt.AlignmentFlag.AlignCenter)

        # === 标题行 ===
        title_row = QHBoxLayout()
        icon_label = QLabel(self.addon.icon)
        icon_label.setStyleSheet("font-size: 32px;")
        title_row.addWidget(icon_label)

        name_label = QLabel(self.addon.name)
        name_font = QFont()
        name_font.setPointSize(16)
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD};")
        title_row.addWidget(name_label, 1)

        # 来源标记
        source_names = {"github": "GitHub", "gitee": "Gitee 码云", "blender_org": "Blender 官方",
                        "community": "社区推荐", "builtin": "内置精选", "budeco": "BudecoTop 中文站"}
        src_tag = QLabel(source_names.get(self.addon.source, self.addon.source))
        src_tag.setObjectName("tagMuted")
        title_row.addWidget(src_tag)
        layout.addLayout(title_row)

        # === 基本信息网格 ===
        info_grid = QGridLayout()
        info_grid.setSpacing(8)

        info_items: list[tuple[str, str]] = [
            ("作者", self.addon.author),
            ("分类", self.addon.category),
            ("评分", f"\u2b50 {self.addon.rating:.1f}"),
            ("下载量", f"{self.addon.downloads:,}"),
            ("兼容版本", f"Blender {self.addon.min_blender} - {self.addon.max_blender}"),
            ("许可证", self.addon.license),
            ("价格", "免费" if self.addon.free else (self.addon.price or "付费")),
            ("来源", source_names.get(self.addon.source, self.addon.source)),
        ]
        for i, (label, value) in enumerate(info_items):
            lbl = QLabel(f"{label}:")
            lbl.setObjectName("muted")
            val = QLabel(value)
            val.setObjectName("body")
            row, col = divmod(i, 2)
            info_grid.addWidget(lbl, row, col * 2)
            info_grid.addWidget(val, row, col * 2 + 1)

        layout.addLayout(info_grid)

        # === 描述 ===
        desc_title = QLabel("简介")
        desc_title.setObjectName("title")
        layout.addWidget(desc_title)

        desc_text = QTextEdit()
        desc_text.setReadOnly(True)
        desc_text.setPlainText(self.addon.description)
        desc_text.setMaximumHeight(100)
        desc_text.setStyleSheet(
            f"QTextEdit {{ background-color: {theme.Tokens.OVERLAY_BG_ALT}; color: {theme.Tokens.CHIP_TEXT}; border: 1px solid {theme.Tokens.ROW_ALT}; "
            + "border-radius: 6px; padding: 10px; font-size: 12px; }"
        )
        layout.addWidget(desc_text)

        # === 标签 ===
        if self.addon.tags:
            tags_layout = QHBoxLayout()
            tags_layout.setSpacing(6)
            tags_layout.addWidget(QLabel("标签:"))
            for tag in self.addon.tags:
                tag_lbl = QLabel(tag)
                tag_lbl.setObjectName("tagLink")
                tags_layout.addWidget(tag_lbl)
            tags_layout.addStretch()
            layout.addLayout(tags_layout)

        # === 项目链接 ===
        if self.addon.source_url:
            link_label = QLabel(
                "\U0001f517 <a href='" + self.addon.source_url
                + f"' style='color: {theme.Tokens.HIGHLIGHT_BLUE};'>" + self.addon.source_url + "</a>"
            )
            link_label.setOpenExternalLinks(True)
            link_label.setStyleSheet("font-size: 11px;")
            layout.addWidget(link_label)

        layout.addStretch()

        # === 底部按钮 ===
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if self.addon.free and not self._is_installed:
            install_btn = QPushButton("\u2b07 安装此插件")
            # 大按钮用 #primary 规则（12.5px 字号 + 7px  padding）；
            # 卡片小按钮那条 #primaryButton（11px）不适合 36px 高的大按钮
            install_btn.setObjectName("primary")
            install_btn.setFixedHeight(36)
            install_btn.setFixedWidth(160)
            install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            _ = install_btn.clicked.connect(lambda: self.install_requested.emit(self.addon.id))  # pyright: ignore[reportUnknownMemberType]
            _ = install_btn.clicked.connect(self.accept)  # pyright: ignore[reportUnknownMemberType]
            btn_row.addWidget(install_btn)
        elif self._is_installed:
            installed_label = QLabel("\u2713 已安装")
            installed_label.setObjectName("tagInstalled")
            btn_row.addWidget(installed_label)
        else:
            price_btn = QPushButton(f"\U0001f6d2 {self.addon.price or '付费插件'}")
            price_btn.setObjectName("price")
            price_btn.setFixedHeight(36)
            btn_row.addWidget(price_btn)

        close_btn = QPushButton("关闭")
        close_btn.setFixedHeight(36)
        close_btn.setFixedWidth(80)
        _ = close_btn.clicked.connect(self.reject)  # pyright: ignore[reportUnknownMemberType]
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)


# ============================================================
# AddonStoreModule — 插件市场主模块
# ============================================================

class _ResizeEventFilter(QObject):
    """内部 QObject 代理，用于监听 widget resize 事件"""

    def __init__(self, parent_module: AddonStoreModule, timer: QTimer) -> None:
        super().__init__()
        self._module: AddonStoreModule = parent_module
        self._timer: QTimer = timer

    @override
    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:  # noqa: N802
        if a1 is not None and a0 is not None:
            if a1.type() == a1.Type.Resize:
                self._timer.start(150)
        return super().eventFilter(a0, a1)


class _ThumbBridge(QObject):
    """跨线程信号中继。

    BaseModule 是纯 ABC（非 QObject），无法直接声明 pyqtSignal；
    而缩略图下载跑在 threading.Thread 中，必须经由 QObject 信号以
    QueuedConnection 投递回主线程——直接用 QTimer.singleShot 在无 Qt
    事件循环的线程里调用，回调永远不会执行（缩略图永远不更新）。
    """
    thumb_ready = pyqtSignal(str, str)  # (addon_id, thumbnail_path)


class _FetchThread(QThread):
    """后台抓取 budeco 下载链接，结果通过信号回主线程（避免界面卡死）。"""

    result_ready = pyqtSignal(object)  # url 字符串或 None

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.result_ready.emit(self._fn())
        except Exception as e:  # noqa: BLE001
            # 旧实现直接 emit(None)，超时/解析失败的原因完全丢失；
            # 这里至少把异常写进日志，便于用户报障时定位。
            try:
                from core.services.log_service import LogService

                LogService.instance().warning(
                    f"后台任务失败: {e}", module="addon_store", exc_info=True
                )
            except Exception:  # noqa: BLE001
                pass
            self.result_ready.emit(None)


class _AddonDownloadWorker(QThread):
    """插件包下载线程：只负责下载与进度，安装仍在主线程（涉及弹窗确认）。"""

    progress = pyqtSignal(int, int, int)          # 已下载, 总字节(0=未知), 百分比
    finished = pyqtSignal(bool, object, str)      # ok, content(bytes)/None, 错误信息

    def __init__(self, fetcher, parent=None) -> None:
        super().__init__(parent)
        self._fetcher = fetcher

    def run(self) -> None:
        try:
            content = self._fetcher(lambda d, t, p: self.progress.emit(d, t, p))
        except Exception as e:  # noqa: BLE001
            self.finished.emit(False, None, str(e))
            return
        self.finished.emit(True, content, "")


class AddonStoreModule(BaseModule):
    """插件市场模块 v2.0 — 多源数据 + 智能布局"""

    def __init__(self) -> None:
        super().__init__()
        self.id: str = "addon_store"
        self.name: str = "插件"
        self.icon: str = "\U0001f6d2"
        self._widget: QWidget | None = None
        self._grid_layout: QGridLayout | None = None
        self._current_category: str = "全部"
        self._installed_addons: set[str] = set()
        self._category_buttons: dict[str, QPushButton] = {}
        self._addon_service: AddonService | None = None
        self._budeco_auth: BudecoAuthService | None = None
        self._qq_login_btn: QPushButton | None = None
        self._login_thread: _FetchThread | None = None
        self._cookie_harvester = None
        self._resolve_thread = None
        self._dl_worker: _AddonDownloadWorker | None = None
        self._resolve_progress = None
        self._resolve_result = None
        self._trash = None  # 待删除卡片的隐藏回收容器（避免变成顶层窗口）
        self._fetch_thread: _FetchThread | None = None
        self._cards_container: QWidget | None = None
        self._cols: int = 4  # 默认列数
        self._resize_timer: QTimer | None = None
        self._stats_label: QLabel | None = None
        self._search_input: QLineEdit | None = None
        self._category_group: QButtonGroup | None = None
        self._scroll: QScrollArea | None = None
        self._resize_filter: _ResizeEventFilter | None = None
        # 搜索防抖定时器（textChanged -> 300ms 静默期 -> 实际重建卡片）
        self._search_debounce: QTimer | None = None
        # 在线刷新按钮引用与刷新状态轮询定时器
        self._refresh_btn: QPushButton | None = None
        self._refresh_poll_timer: QTimer | None = None
        self._refresh_wait_ms: int = 0
        # 分页状态（只实例化当前页卡片，避免上千控件堆积）
        # 注意：必须是实例属性。写成 PAGE_ROWS = 2（局部量）会让 _refresh_cards 里的
        # self.PAGE_ROWS 抛 AttributeError —— 而 addon_store 是首页，进入即触发，
        # 槽函数内异常会让 PyQt 直接 abort，表现为"启动后立刻静默退出"。
        # 每页行数：2 → 5（用户反馈"卡片只显示 2 行"；4 列 × 5 行 = 20 张/页，
        # 2891 条全量目录约 145 页，翻页节奏合适；控件量 ~400/页不卡顿）
        self.PAGE_ROWS = 5
        self._page_index: int = 0
        self._total_pages: int = 1
        self._pager_widget: QWidget | None = None
        self._page_label: QLabel | None = None
        self._prev_btn: QPushButton | None = None
        self._next_btn: QPushButton | None = None
        # 跨线程信号中继：后台下载线程通过它把结果投递回主线程
        self._thumb_bridge = _ThumbBridge()
        self._thumb_bridge.thumb_ready.connect(self._on_thumb_ready)

    @override
    def get_widget(self) -> QWidget:
        if self._widget:
            return self._widget

        # 初始化插件服务
        if self.ctx is not None:
            cache_dir: Path = self.ctx.config_service.base_dir / "addons_cache"  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportUnknownVariableType]
        else:
            cache_dir = Path("addons_cache")
        self._addon_service = AddonService(
            cache_dir=cache_dir,  # pyright: ignore[reportUnknownArgumentType]
            log_service=self.ctx.log_service if self.ctx else None,  # pyright: ignore[reportUnknownMemberType]
        )
        # BudecoTop QQ 登录服务（cookie 持久化 + 下载链解析）
        self._budeco_auth = self.ctx.budeco_auth if self.ctx else None
        # 恢复上次安装的插件状态（旧实现只放内存，重启后全丢）
        self._restore_installed_states()  # pyright: ignore[reportUnknownMemberType]


        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        # === 标题行 + 搜索 + 统计 ===
        header = QHBoxLayout()
        title = QLabel("\U0001f6d2 插件")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet(f"color: {theme.Tokens.ON_CARD};")
        header.addWidget(title)

        # 统计信息
        assert self._addon_service is not None  # 上面已赋值
        stats = self._addon_service.get_stats()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        self._stats_label = QLabel(
            f"共 {stats['total']} 个插件 | 官方 {stats['official']} | 免费 {stats['free']}"
        )
        self._stats_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD_DISABLED}; font-size: 11px;")
        header.addWidget(self._stats_label)
        header.addStretch()


        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索插件名称、描述、标签...")
        self._search_input.setFixedWidth(240)
        self._search_input.setFixedHeight(32)
        # 防抖：旧实现 textChanged 直连 _refresh_cards，每敲一个字符就销毁并重建
        # 全部卡片（55 卡 × ~20 控件 ≈ 1100 widgets），搜索过程严重卡顿。
        # 改为 300ms 无输入后才真正刷新。
        self._search_debounce = QTimer(self._widget)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(300)
        self._search_debounce.timeout.connect(self._on_search)
        _ = self._search_input.textChanged.connect(  # pyright: ignore[reportUnknownMemberType]
            lambda: self._search_debounce.start()
        )
        header.addWidget(self._search_input)

        # 刷新在线数据按钮
        refresh_btn = QPushButton("\U0001f504 在线刷新")
        refresh_btn.setObjectName("secondaryButton")
        refresh_btn.setFixedHeight(32)
        refresh_btn.setToolTip("从 BudecoTop 获取最新插件列表")
        _ = refresh_btn.clicked.connect(self._on_refresh)  # pyright: ignore[reportUnknownMemberType]
        header.addWidget(refresh_btn)
        self._refresh_btn = refresh_btn  # 供刷新状态切换（旧实现靠 findChildren 找按钮）

        # BudecoTop 登录按钮（下载需登录态；已登录则显示状态）
        qq_btn = QPushButton()
        qq_btn.setObjectName("secondaryButton")
        qq_btn.setFixedHeight(32)
        qq_btn.setMinimumWidth(104)
        qq_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_qq_login_button(qq_btn)
        _ = qq_btn.clicked.connect(self._on_qq_login)  # pyright: ignore[reportUnknownMemberType]
        header.addWidget(qq_btn)
        self._qq_login_btn = qq_btn

        layout.addLayout(header)

        # === 分类标签栏 ===

        cat_layout = QHBoxLayout()
        cat_layout.setSpacing(4)
        self._category_group = QButtonGroup(self._widget)
        self._category_group.setExclusive(True)
        for i, cat in enumerate(self._addon_service.CATEGORIES):
            btn = QPushButton(cat)
            btn.setCheckable(True)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {theme.Tokens.GREY_3}; border: none; "
                + "padding: 6px 14px; border-radius: 14px; font-size: 12px; }"
                + f"QPushButton:hover {{ color: {theme.Tokens.ON_CARD_SUB}; background-color: {theme.Tokens.CHIP_BG}; }}"
                + f"QPushButton:checked {{ background-color: {theme.Tokens.HIGHLIGHT_BLUE_DIM}; color: {theme.Tokens.HIGHLIGHT_BLUE}; }}"
            )
            self._category_group.addButton(btn, i)
            _ = btn.clicked.connect(  # pyright: ignore[reportUnknownMemberType]
                lambda _checked, c=cat: self._on_category_changed(c)  # pyright: ignore[reportUnknownLambdaType]
            )
            cat_layout.addWidget(btn)
            self._category_buttons[cat] = btn
            if i == 0:
                btn.setChecked(True)

        cat_layout.addStretch()
        layout.addLayout(cat_layout)

        # === 滚动卡片区域（仅纵向滚动，横向自适应列数） ===
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background-color: transparent; }"
            + "QScrollArea > QWidget > QWidget { background-color: transparent; }"
        )

        self._cards_container = QWidget()
        self._cards_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self._grid_layout = QGridLayout(self._cards_container)
        self._grid_layout.setSpacing(10)
        self._grid_layout.setContentsMargins(0, 6, 0, 6)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._scroll.setWidget(self._cards_container)
        layout.addWidget(self._scroll, 1)

        # ── 分页控件（仅当插件数超过一页时可见）──
        pager = QWidget()
        pager.setFixedHeight(40)
        pager_lay = QHBoxLayout(pager)
        pager_lay.setContentsMargins(8, 4, 8, 4)
        pager_lay.addStretch()

        prev_btn = QPushButton("← 上一页")
        prev_btn.setObjectName("secondaryButton")
        prev_btn.clicked.connect(lambda: self._goto_page(-1))
        pager_lay.addWidget(prev_btn)

        page_label = QLabel("")
        page_label.setStyleSheet(f"color: {theme.Tokens.PAGER_TEXT}; font-size: 12px; padding: 0 12px;")
        pager_lay.addWidget(page_label)

        next_btn = QPushButton("下一页 →")
        next_btn.setObjectName("secondaryButton")
        next_btn.clicked.connect(lambda: self._goto_page(1))
        pager_lay.addWidget(next_btn)

        pager_lay.addStretch()
        layout.addWidget(pager)
        pager.setVisible(False)
        self._pager_widget = pager
        self._page_label = page_label
        self._prev_btn = prev_btn
        self._next_btn = next_btn

        # 监听 resize 事件 — 同时监听 widget 和 scroll viewport
        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        _ = self._resize_timer.timeout.connect(self._on_resize_timeout)  # pyright: ignore[reportUnknownMemberType]
        self._resize_filter = _ResizeEventFilter(self, self._resize_timer)
        assert self._widget is not None
        self._widget.installEventFilter(self._resize_filter)
        self._scroll.viewport().installEventFilter(self._resize_filter)  # pyright: ignore[reportOptionalMemberAccess]

        return self._widget

    def _on_resize_timeout(self) -> None:
        """窗口/视口大小改变后重新计算列数并刷新布局。

        ⚠ 必须延迟到下一个事件循环再重建：滚动时滚动条出现会让 viewport 触发
        resize，此刻仍在滚动事件/绘制的分发过程中，直接销毁并重建卡片
        （setParent + deleteLater）会在 Windows 上崩溃（关闭详情弹窗后滚动
        浏览页必崩就是这个原因）。singleShot(0) 把重建挪出当前分发栈。
        """
        if not self._cards_container:
            return
        new_cols = self._calculate_columns()
        if new_cols != self._cols:
            self._cols = new_cols
            QTimer.singleShot(0, self._refresh_cards)

    def _calculate_columns(self) -> int:
        """根据可视区域宽度计算最佳列数（卡片 240px + 间距 14px）"""
        if not self._cards_container or self._grid_layout is None:
            return 4
        card_w = AddonCard.CARD_WIDTH   # 240
        gap = self._grid_layout.spacing()

        # 使用 scroll viewport 宽度（实际可见区域），而非 widget 宽度
        viewport_w = self._scroll.viewport().width() if self._scroll else 0  # pyright: ignore[reportOptionalMemberAccess]
        usable = viewport_w if viewport_w > 0 else self._cards_container.width()

        cols = max(1, (usable + gap) // (card_w + gap))
        return cols

    def _download_thumbnail_async(self, addon: AddonInfo) -> None:
        """后台下载缩略图，下载完成后更新对应卡片"""
        addon_id = addon.id
        thumb_url = addon.thumbnail_url
        svc = self._addon_service

        def _download() -> None:
            if svc is None:
                return
            try:
                path = svc.download_thumbnail(addon_id, thumb_url)
                if path and path.exists():
                    # 旧实现用 QTimer.singleShot(0, ...) 从 threading.Thread 里回调，
                    # 但该线程没有 Qt 事件循环，回调永远不会执行 —— 缩略图永远不更新。
                    # 正确做法：用跨线程信号（QueuedConnection）投递回主线程。
                    self._thumb_bridge.thumb_ready.emit(addon_id, str(path))
            except Exception as e:  # noqa: BLE001
                if self.ctx:
                    self.ctx.log_service.debug(
                        f"thumbnail download failed for {addon_id}: {e}", module="addon_store"
                    )

        t = threading.Thread(target=_download, daemon=True)
        t.start()

    @override
    def _on_thumb_ready(self, addon_id: str, path_str: str) -> None:
        """缩略图下载完成的信号处理（运行在主线程）"""
        path = Path(path_str)
        if path.exists():
            self._update_card_thumbnail(addon_id, path)

    def _update_card_thumbnail(self, addon_id: str, path: Path) -> None:
        """在主线程更新卡片的缩略图"""
        if not self._grid_layout:
            return
        try:
            for i in range(self._grid_layout.count()):
                item = self._grid_layout.itemAt(i)
                if item is None:
                    continue
                widget = item.widget()
                if isinstance(widget, AddonCard) and widget.addon.id == addon_id:
                    widget.set_thumbnail(path)
                    break
        except RuntimeError:
            # 卡片已被 deleteLater 回收（刷新/重建期间），忽略即可
            pass

    @override
    def on_navigate_to(self) -> None:
        """进入插件市场时刷新"""
        # 顺手清理上次安装可能残留的"正在解析下载地址"小窗口
        # （它的 parent 是本页面，所以只会在切到插件市场时露出来）
        _thread = getattr(self, "_resolve_thread", None)
        if _thread is None or not _thread.isRunning():
            self._close_resolve_progress()
        self._refresh_cards()
        # 后台静默刷新在线数据
        if self._addon_service:
            _ = self._addon_service.refresh_cache()
            # 刷新完成也要重建卡片（旧实现只延迟更统计，卡片区停留空白）
            if self._addon_service.is_refreshing():
                self._start_refresh_polling()
            else:
                # 节流命中（刚刷过）：立即用现有数据刷新一次 UI
                self._update_stats()
                self._refresh_cards()

    def on_shutdown(self) -> None:
        """退出时停掉后台线程，避免 QThread 在解释器退出时被强杀。"""
        for attr in ("_fetch_thread", "_login_thread", "_resolve_thread", "_dl_worker"):
            thread = getattr(self, attr, None)
            if thread is None or not thread.isRunning():
                continue
            try:
                thread.requestInterruption()
                thread.wait(3000)
            except RuntimeError:
                pass

    def _update_stats(self) -> None:
        if self._addon_service and self._stats_label:
            stats = self._addon_service.get_stats()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            self._stats_label.setText(
                f"共 {stats['total']} 个插件 | 官方 {stats['official']} | 免费 {stats['free']}"
            )

    def _refresh_cards(self) -> None:
        if self._grid_layout is None or self._addon_service is None:
            return

        self._clear_cards()
        self._cols = self._calculate_columns()

        search_text = self._search_input.text().strip() if self._search_input else ""

        # 从 AddonService 获取筛选结果
        all_addons = self._addon_service.search(keyword=search_text, category=self._current_category)

        # 分页：旧实现全量实例化（55 卡 × ~20 控件 ≈ 1100 widgets），
        # 搜索每次重建全部卡片，控件堆积且卡顿。这里只渲染当前页，
        # 每页上限 =列数 × 每页行数，控件量降到 1/3 以下。
        per_page = max(1, self._cols * self.PAGE_ROWS)
        self._total_pages = max(1, (len(all_addons) + per_page - 1) // per_page)
        if self._page_index >= self._total_pages:
            self._page_index = self._total_pages - 1
        if self._page_index < 0:
            self._page_index = 0
        start = self._page_index * per_page
        addons = all_addons[start:start + per_page]

        for i, addon in enumerate(addons):
            # 检查缩略图缓存，若没有但存在 thumbnail_url 则触发后台下载
            thumb_path = self._addon_service.get_thumbnail_path(addon.id)
            if thumb_path is None and addon.thumbnail_url:
                self._download_thumbnail_async(addon)

            is_installed = addon.id in self._installed_addons
            # 必须显式给 parent：创建后到 addWidget 之前这段空窗期里，卡片是
            # 没有父级的顶层 widget，一旦被 show 就会浮成独立小窗。
            card = AddonCard(
                addon, is_installed=is_installed, thumbnail_path=thumb_path,
                parent=self._cards_container,
            )
            _ = card.install_clicked.connect(self._install_addon)  # pyright: ignore[reportUnknownMemberType]
            _ = card.details_clicked.connect(self._show_details)  # pyright: ignore[reportUnknownMemberType]
            row, col = divmod(i, self._cols)
            self._grid_layout.addWidget(card, row, col)

        # 在最后一行的剩余列添加空白占位，保持卡片左对齐
        if addons:
            last_row_count = len(addons) % self._cols
            if last_row_count > 0:
                last_row = len(addons) // self._cols
                for col in range(last_row_count, self._cols):
                    spacer = QWidget()
                    spacer.setFixedSize(AddonCard.CARD_WIDTH, AddonCard.CARD_HEIGHT)
                    self._grid_layout.addWidget(spacer, last_row, col)

        self._update_pager(start, len(all_addons), per_page)

        # 空状态
        if not addons:
            empty_widget = QWidget()
            empty_layout = QVBoxLayout(empty_widget)
            empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_icon = QLabel("\U0001f50d")
            empty_icon.setStyleSheet("font-size: 48px;")
            empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(empty_icon)
            empty_text = QLabel("没有找到匹配的插件\n\n试试其他关键词或分类")
            empty_text.setObjectName("hint")
            empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(empty_text)
            self._grid_layout.addWidget(empty_widget, 0, 0, 1, self._cols)

    def _update_pager(self, start: int, total: int, per_page: int) -> None:
        """更新分页控件状态"""
        if self._pager_widget is None:
            return
        has_pages = total > per_page
        self._pager_widget.setVisible(has_pages)
        if not has_pages:
            return

        end = min(start + per_page, total)
        self._page_label.setText(f"第 {self._page_index + 1}/{self._total_pages} 页  ·  显示 {start + 1}-{end}，共 {total} 个")
        self._prev_btn.setEnabled(self._page_index > 0)
        self._next_btn.setEnabled(self._page_index < self._total_pages - 1)

    def _goto_page(self, delta: int) -> None:
        """翻页（delta: -1 上一页, +1 下一页）"""
        new_index = self._page_index + delta
        if 0 <= new_index < self._total_pages:
            self._page_index = new_index
            self._refresh_cards()

    def _clear_cards(self) -> None:
        if self._grid_layout is None:
            return
        # 旧实现只用 deleteLater()（延迟删除），紧接着就重建卡片，
        # 旧卡片在事件循环清空前仍存活 —— 叠加搜索框每键重建会堆积上千控件。
        # 先 setParent(None) 立即脱离父级，再 deleteLater 交回 Qt 回收。
        #
        # ⚠ setParent(None) 会把 widget 变成**顶层窗口**：一旦可见，就变成一叠
        # 脱离主窗口的"空白小窗"飘在屏幕上（240x216、标题栏退化成 exe 产品名）
        # —— 用户报的"每次切到插件市场都弹出空白窗口"就是它。
        # 解法：不要置为无父级，而是转移到**隐藏的回收容器**，卡片始终是
        # 子控件，绝无机会变成独立窗口；既立即脱离布局，又照常延迟回收。
        trash = getattr(self, "_trash", None)
        if trash is None:
            trash = QWidget(self._widget)
            trash.hide()
            self._trash = trash

        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(trash)
                w.deleteLater()

    def _on_category_changed(self, category: str) -> None:
        self._current_category = category
        self._page_index = 0  # 切换分类回到第一页
        self._refresh_cards()

    def _on_search(self, _text: str) -> None:
        self._page_index = 0  # 新搜索回到第一页
        self._refresh_cards()

    def _on_refresh(self) -> None:
        """手动刷新在线数据"""
        if not self._addon_service or not self._widget:
            return
        # 用按钮引用而非 findChildren + 中文文案匹配（旧实现改文案即失效）
        if self._refresh_btn is not None:
            self._refresh_btn.setText("⏳ 刷新中...")
            self._refresh_btn.setEnabled(False)

        _ = self._addon_service.refresh_cache()
        self._start_refresh_polling()

    def _start_refresh_polling(self) -> None:
        """轮询 service 刷新状态，真实完成后才更新 UI（手动/自动刷新共用）。

        旧实现用 QTimer.singleShot(2000) 猜刷新完成，会在 2 秒后就恢复
        并谎报"刷新完成"；且自动刷新（on_navigate_to）路径**只更统计数字、
        不重建卡片**，用户进页面看到空白——爬完的插件不显示。
        """
        if not self._widget or not self._addon_service:
            return
        self._refresh_poll_timer = QTimer(self._widget)
        self._refresh_poll_timer.setInterval(700)
        self._refresh_poll_timer.timeout.connect(self._poll_refresh_done)
        self._refresh_poll_timer.start()
        self._refresh_wait_ms = 0

    def _poll_refresh_done(self) -> None:
        """轮询 AddonService 刷新状态，真实完成后才恢复 UI"""
        svc = self._addon_service
        if svc is None:
            self._refresh_poll_timer.stop()
            return

        self._refresh_wait_ms += 700
        # 超过 45s（服务端 40s 超时 + 余量）强制结束等待，避免卡死 UI
        # 等待上限跟随 service 的 REFRESH_TIMEOUT（全量爬取 2891 条约 3 分钟，
        # 旧值 45s 会在全量途中强制恢复按钮，之后爬完也不再更新 UI）
        wait_cap = int(getattr(svc, "REFRESH_TIMEOUT", 240.0) * 1000) + 15_000
        if svc.is_refreshing() and self._refresh_wait_ms < wait_cap:
            return

        self._refresh_poll_timer.stop()
        self._on_refresh_complete()

    def _on_refresh_complete(self) -> None:
        """在线刷新完成后更新 UI"""
        if not self._widget:
            return
        # 直接操作按钮引用，不再用 findChildren + 中文文案匹配
        # （旧实现改文案或加 i18n 就会失效）
        if self._refresh_btn is not None:
            self._refresh_btn.setText("\U0001f504 在线刷新")
            self._refresh_btn.setEnabled(True)
        self._update_stats()
        self._refresh_cards()
        # 后台复验登录态（不阻塞 UI）
        self._verify_login_async()

    # === 安装逻辑 ===

    def _install_addon(self, addon_id: str) -> None:
        if self.ctx is None or self._addon_service is None:
            return

        addon = self._addon_service.get_addon(addon_id)
        if addon is None:
            return

        # 获取已安装的 Blender 版本
        versions: list[BlenderVersionInfo] = self.ctx.blender_service.detect_installed()  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportUnknownVariableType]
        if not versions:
            _ = QMessageBox.warning(self._widget, "提示", "请先在「更新」中安装一个 Blender 版本")
            return

        # 兼容性检测
        compat_issues = self._check_compatibility(addon, versions)  # pyright: ignore[reportUnknownArgumentType]

        msg = f"安装 {addon.name}\n\n"
        if compat_issues:
            msg += "\u26a0\ufe0f 兼容性警告:\n"
            for issue in compat_issues:
                msg += f"  \u2022 {issue}\n"
            msg += "\n"

        if addon.source_url:
            msg += f"来源: {addon.source_url}\n\n"

        msg += "请选择目标 Blender 版本:"

        version_names: list[str] = [v.label for v in versions]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        item, ok = QInputDialog.getItem(  # pyright: ignore[reportUnknownMemberType]
            self._widget, "安装插件", msg,
            version_names, 0, False,
        )
        if not ok:
            return

        target: BlenderVersionInfo = versions[version_names.index(item)]  # pyright: ignore[reportUnknownVariableType]

        # ── 安装决策链（2026-10-17：消灭"占位安装"误导）──
        # 1. Blender 已捆绑（addons_core/addons，目录或单文件 .py）→ 直接算已安装，
        #    提示去偏好设置启用（rigify/node_wrangler/旧版 io_import_images_as_planes 走这条）
        # 2. 有 download_url（官方 API 回填后的 looptools 等）→ 真实下载安装
        # 3. 都没有 → mock 兜底（文案如实说明）
        bundled_dir = self._find_bundled(addon, target)
        if bundled_dir is not None:
            self._installed_addons.add(addon.id)
            if self.ctx:
                self.ctx.log_service.info(  # pyright: ignore[reportOptionalMemberAccess]
                    f"Addon '{addon.id}' already bundled in Blender {target.version} "
                    f"({bundled_dir.name})", module="addon_store",
                )
            _ = QMessageBox.information(
                self._widget, "已随 Blender 内置",
                f"{addon.name} 已随 Blender {target.version} 捆绑提供，无需下载。\n\n"
                f"位置：{bundled_dir}\n\n"
                "请在 Blender 中启用：编辑 → 偏好设置 → 插件 → "
                f"搜索 \"{addon.name}\" 勾选。",
            )
            self._refresh_cards()
            return

        if addon.download_url:
            self._download_and_install(addon, target)  # pyright: ignore[reportUnknownArgumentType]
        elif addon.source == "budeco":
            # budeco 爬取时 download_url 置空（需登录才能拿到真实下载链），
            # 这里登录后补取再走真实安装；拿不到则如实提示，绝不占位安装。
            self._install_budeco(addon, target)
        else:
            # 没有可用下载地址时如实失败，不再生成占位插件并标记「已安装」
            self._report_install_error(
                addon,
                RuntimeError("该插件没有可用的下载地址，无法安装（未写入占位文件）"),
            )
            self._refresh_cards()

    # ── BudecoTop QQ 登录 + 下载链补取 ──────────────────────────────────────

    def _update_qq_login_button(self, btn: QPushButton) -> None:
        if self._budeco_auth is not None and self._budeco_auth.is_logged_in():
            btn.setText("\u2713 Budeco 已登录")
            btn.setToolTip("已登录 BudecoTop，可下载需登录的插件")
        else:
            btn.setText("登录 Budeco")
            btn.setToolTip(
                "登录 BudecoTop 以解锁需登录的插件下载。\n"
                "首选：一键从本机 Firefox 同步登录态（无需扫码）。"
            )

    def _verify_login_async(self) -> None:
        """后台复验登录态：cookie 名字存在 ≠ 服务端仍认这份登录。

        过期 cookie 会让顶栏一直显示"已登录"、但下载链永远抓不到（13:12 那轮
        "未能解析到下载链接"的另一种形态）。校验有 5 分钟 TTL，不频繁打网络。
        """
        auth = self._budeco_auth
        if auth is None or not auth.is_logged_in():
            return
        if self._login_thread is not None and self._login_thread.isRunning():
            return
        thread = _FetchThread(lambda: bool(auth.verify_login()))
        thread.result_ready.connect(self._on_login_verified)
        self._login_thread = thread
        thread.start()

    def _on_login_verified(self, ok: object) -> None:
        if self._qq_login_btn is not None:
            self._update_qq_login_button(self._qq_login_btn)
        if not ok and self.ctx is not None:
            self.ctx.log_service.warning(  # pyright: ignore[reportOptionalMemberAccess]
                "budeco 登录态已失效（站点返回未登录页），请重新登录",
                module="addon_store",
            )

    def _on_qq_login(self) -> None:
        if self._login_budeco() and self._qq_login_btn is not None:
            self._update_qq_login_button(self._qq_login_btn)
            self._refresh_cards()

    @staticmethod
    def _is_pan_link(url: str) -> bool:
        return BudecoAuthService.is_pan_link(url)

    def _install_budeco(self, addon: AddonInfo, target: BlenderVersionInfo) -> None:
        """budeco 插件安装：解析真实下载链 → 直链直接装 / 网盘引导下载。

        实测（2026-10-17）budeco 的 erphpdown 下载链最终**一律落到网盘分享页**
        （``pan.baidu.com/share/init?surl=…&pwd=…``），不是可直接下载的 zip。
        旧实现默认弹「内嵌浏览器」，而内嵌浏览器在打包环境下会
        ``0xc0000409`` 把整个应用带崩（13:41/13:42 两次真机崩溃）。
        因此默认路径改成零风险的纯 requests 解析 + 外部浏览器下载 + 选回 zip，
        内嵌浏览器降级为用户主动点「用内嵌浏览器下载」才走的可选项。
        """
        if self._budeco_auth is None:
            self._report_install_error(addon, RuntimeError("登录服务未初始化"))
            return

        # 登录态失效 → 先弹登录（Firefox 同步优先），成功后自动继续
        if not self._budeco_auth.is_logged_in() or not self._budeco_auth.verify_login():
            if not self._login_budeco():
                return
            if not self._budeco_auth.verify_login(force=True):
                self._report_install_error(
                    addon, RuntimeError("登录后仍无法访问站点，请确认账号可用")
                )
                return

        # 旧实现：无标题 + NonModal + show() + AutoClose(False)，
        # 而且只在 thread.finished 里 close。只要有一次点安装后线程没正常收尾
        # （或用户连点几次、旧实例被覆盖），这个空 QProgressDialog 就会以
        # 「标题栏显示 exe 产品名 Blender Hub、内容空白的小窗口」常驻，
        # 又因为 parent 是插件市场页面 —— 于是"每次切到插件市场都弹窗"。
        self._close_resolve_progress()  # 先清掉上一次可能残留的窗口
        progress = QProgressDialog("正在解析下载地址…", "", 0, 0, self._widget)
        progress.setWindowTitle(f"正在准备安装 {addon.name}")  # 必须有标题
        progress.setWindowModality(Qt.WindowModality.NonModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setCancelButton(None)  # busy 状态本来就取消不了，别给假按钮
        progress.show()

        def _work():  # noqa: ANN202
            try:
                return self._budeco_auth.resolve_download(addon.source_url or "")
            except Exception as e:  # noqa: BLE001
                return e

        thread = _FetchThread(_work)
        thread.result_ready.connect(self._on_resolve_fetched)
        # 直接用跨线程信号回收结果（queued 连接会回到主线程执行），
        # 不再用 progress.exec() 的嵌套事件循环，也不需要定时轮询：
        # exec() 一旦后面接模态登录窗，没人点就永久卡死。
        thread.result_ready.connect(
            lambda res: self._safe_on_resolved(res, addon, target)  # pyright: ignore[reportUnknownArgumentType]
        )
        # 无论成功/失败/异常，都走同一个收尾函数（幂等），不留孤儿窗口
        thread.finished.connect(self._close_resolve_progress)
        self._resolve_thread = thread
        self._resolve_progress = progress
        thread.start()

    def _close_resolve_progress(self) -> None:
        """关掉"正在解析下载地址"的进度框（幂等，可在任意路径重复调用）。

        必须显式 close + deleteLater：NonModal 的 QProgressDialog 若只是被
        变量覆盖而没人 close，会作为顶层窗口一直挂在插件市场页面上。
        """
        p = getattr(self, "_resolve_progress", None)
        self._resolve_progress = None
        if p is None:
            return
        try:
            p.close()
            p.deleteLater()
        except Exception:  # noqa: BLE001
            pass

    def _on_resolve_fetched(self, res) -> None:  # noqa: ANN001
        self._resolve_result = res

    def _safe_on_resolved(self, res, addon: AddonInfo, target: BlenderVersionInfo) -> None:  # noqa: ANN001
        """跨线程槽的异常兜底。

        PyQt 里 slot 抛出的未捕获异常会走 abort() 直接终止进程（无 traceback，
        表现为退出码 127/"应用闪退"）。安装链路里任何一步出错都只能弹框告知，
        绝不能把整个应用带走。
        """
        # 结果已到 → 先收掉"正在解析"进度框（不等 finished 信号），
        # 避免它在这一步之后又停留一段时间
        self._close_resolve_progress()
        try:
            self._on_budeco_resolved(res, addon, target)
        except Exception as e:  # noqa: BLE001
            self._report_install_error(addon, e)

    def _on_budeco_resolved(self, res, addon: AddonInfo, target: BlenderVersionInfo) -> None:  # noqa: ANN001
        """后台解析完成后在主线程分流：直链 / 网盘 / 失败。"""
        if isinstance(res, Exception):
            self._report_install_error(addon, res)
            return
        target_info = res
        if not isinstance(target_info, DownloadTarget):
            self._report_install_error(addon, RuntimeError("解析下载地址失败"))
            return
        if not target_info.ok:
            if target_info.kind == "login":
                if self._login_budeco() and self._budeco_auth.verify_login(force=True):
                    self._install_budeco(addon, target)
                    return
            self._report_install_error(
                addon, RuntimeError(target_info.note or "未能解析到下载地址")
            )
            return

        if target_info.is_direct_zip:
            addon.download_url = target_info.url
            self._download_and_install(addon, target)
            return

        # 网盘分享：交给用户下载，选回 zip 后走与在线安装相同的流程
        from modules.addon_store.pan_download_dialog import PanDownloadDialog

        dlg = PanDownloadDialog(
            addon.name, target_info.url, target_info.code, self._widget
        )

        def _on_zip(path: str) -> None:
            self._install_from_zip_file(path, addon, target)  # pyright: ignore[reportUnknownArgumentType]

        dlg.zip_ready.connect(_on_zip)
        try:
            dlg.embedded_requested.connect(
                lambda: self._open_embedded_browser(addon, target)  # pyright: ignore[reportUnknownArgumentType]
            )
        except AttributeError:
            pass  # 兜底实现（测试替身）可能没有这个信号
        _ = dlg.exec()  # pyright: ignore[reportUnknownMemberType]

    def _open_embedded_browser(self, addon: AddonInfo, target: BlenderVersionInfo) -> None:
        """可选路径：应用内嵌浏览器里点下载（用户主动选择才走）。"""
        dlg = BrowserInstallDialog(
            addon.source_url or "", self._widget, addon.name, auth=self._budeco_auth
        )
        dlg.zip_ready.connect(
            lambda path: self._install_from_zip_file(  # pyright: ignore[reportUnknownArgumentType]
                path, addon, target
            )
        )
        _ = dlg.exec()  # pyright: ignore[reportUnknownMemberType]

    def _login_budeco(self) -> bool:
        """弹登录窗（Firefox 同步优先），返回是否成功登录。"""
        if self._budeco_auth is None:
            return False
        dlg = QQLoginDialog(self._budeco_auth, self._widget)
        ok = dlg.exec() == QDialog.DialogCode.Accepted  # pyright: ignore[reportUnknownMemberType]
        if ok and self._qq_login_btn is not None:
            self._update_qq_login_button(self._qq_login_btn)
        return ok

    def _sync_budeco_login_state(self) -> None:
        """把内嵌浏览器 profile 里的 budeco 登录 Cookie 同步给 budeco_auth（仅影响顶栏状态）。

        Qt6 的 :class:`QWebEngineCookieStore` **没有** ``cookieList``（既不是方法
        也不是信号），Qt5 那种 ``cookieStore.cookieList.connect(...)`` 写法在 PyQt6
        下必然抛 AttributeError——旧实现的同步逻辑等于从未真正执行过。现改用
        :class:`BudecoCookieHarvester`（cookieAdded + loadAllCookies 补发）。
        """
        if self._budeco_auth is None:
            return
        try:
            from modules.addon_store.cookie_harvest import BudecoCookieHarvester

            harvester = BudecoCookieHarvester(timeout_ms=3000, on_done=self._on_profile_cookies)
            self._cookie_harvester = harvester  # 防 GC
        except Exception:  # noqa: BLE001
            pass

    def _on_profile_cookies(self, cookies: dict) -> None:
        if not cookies or self._budeco_auth is None:
            return
        try:
            self._budeco_auth.ingest_cookies(cookies)
            if self._qq_login_btn is not None:
                self._update_qq_login_button(self._qq_login_btn)
        except Exception:  # noqa: BLE001
            pass

    # 内置 blender_org 条目的捆绑目录名（Blender 发行版内的实际目录/文件名）
    BUNDLED_ALIASES: dict[str, list[str]] = {
        "rigify": ["rigify"],
        "node_wrangler": ["node_wrangler"],
        "loop_tools": ["looptools"],
        "import_images_as_planes": ["io_import_images_as_planes", "import_images_as_planes"],
    }

    def _find_bundled(self, addon: AddonInfo, target: BlenderVersionInfo) -> Optional[Path]:
        """在目标 Blender 里查找该插件是否已捆绑。

        官方安装包有两层 scripts：安装根 `scripts/`（放用户插件）和版本号
        子目录 `scripts/`（系统捆绑在 addons_core/）。旧版形态还可能是
        `addons/` 下的单文件 .py（如 3.6 的 io_import_images_as_planes.py）。
        遍历所有 scripts 根 × 三个插件目录 × 目录/单文件两种形态。
        """
        names = self.BUNDLED_ALIASES.get(addon.id, [addon.id, addon.id.replace("_", "-")])
        for root in self._scripts_roots(target):
            for base in ("addons_core", "addons", "addons_contrib"):
                for name in names:
                    for cand in (root / "scripts" / base / name,
                                 root / "scripts" / base / f"{name}.py"):
                        if cand.exists():
                            return cand
        return None

    @staticmethod
    def _scripts_roots(target: BlenderVersionInfo) -> list[Path]:
        """所有含 scripts 的目录（安装根本身 + 版本号子目录），浅层优先。"""
        base = Path(target.path)
        roots: list[Path] = []
        if (base / "scripts").is_dir():
            roots.append(base)
        try:
            for sub in sorted(base.iterdir()):
                if sub.is_dir() and (sub / "scripts").is_dir() and sub not in roots:
                    roots.append(sub)
        except OSError:
            pass
        return roots or [base]

    @staticmethod
    def _blender_scripts_root(target: BlenderVersionInfo) -> Path:
        """定位 Blender 的 scripts 目录（**插件安装目标**）。

        2026-10-18 用 Blender 4.2.23 实测 ``bpy.utils.script_paths()`` +
        ``addon_utils.paths()``，官方安装包真正被扫描的只有两处：

            ① <安装根>/<版本号>/scripts/addons_core   —— 系统捆绑，只读
            ② %APPDATA%/Blender Foundation/Blender/<版本号>/scripts/addons
                                                      —— 用户插件（Philogix PBR
                                                      Painter / SimpleBake 就在这，
                                                      能被正常识别）

        **安装根下的 ``<安装根>/scripts`` 根本不在这两个路径里**——旧实现判断
        "base/scripts 存在就返回 base"，结果把插件装到 Blender 从不扫描的位置，
        表现为"安装成功但插件不出现"。同时 Program Files 受 UAC 保护，往里写
        文件还会被重定向/拒绝。

        所以安装目标固定取②（用户目录）；该版本从未启动过、目录不存在时，
        退回①所在层并 mkdir 建出来。
        """
        # 测试/特殊场景可显式指定安装层（避免测试写进真实用户配置）
        override = getattr(target, "user_scripts_override", None)
        if override is not None:
            return Path(override)

        # ② 用户插件目录（首选：Blender 一定扫、无需管理员权限）
        # ⚠ 目录名必须是 **major.minor**（如 4.2），不是完整版本号 4.2.23：
        # Blender 的 user_resource("SCRIPTS") 实测返回
        #   %APPDATA%\Blender Foundation\Blender\4.2\scripts
        # 用完整版本号会建出 4.2.23 这种 Blender 从不读取的目录。
        minor = ".".join(str(target.version).split(".")[:2])
        user_layer = (
            Path.home() / "AppData" / "Roaming" / "Blender Foundation"
            / "Blender" / minor / "scripts"
        )
        if user_layer.is_dir():
            return user_layer

        base = Path(target.path)
        # 该版本的用户目录还没建（Blender 没启动过）→ 仍然建它，别退回去装到
        # 无效的外层 scripts 里。
        try:
            (user_layer / "addons").mkdir(parents=True, exist_ok=True)
            return user_layer
        except OSError:
            pass

        # 兜底：安装目录的版本号子层
        ver_layer = base / target.version / "scripts"
        if ver_layer.is_dir():
            return ver_layer
        return base

    def _download_addon_bytes(self, url: str, on_progress=None) -> bytes:
        """同步下载插件包：流式读取 + 大小上限。

        抽成独立方法的原因：下载放到后台线程跑，但大小上限这条安全逻辑必须
        能被单测直接覆盖（见 tests/regression/_test_addon_download_limit.py）。
        """
        with requests.get(url, timeout=30, stream=True) as resp:
            if resp.status_code != 200:
                # 旧实现在此回退到 _mock_install：源站返回 404/403 时仍向用户弹"安装完成"，
                # 用户以为装好了插件，实际只写了个占位文件。失败必须如实上报。
                raise RuntimeError(f"下载返回 HTTP {resp.status_code}（{url}）")
            declared = resp.headers.get("Content-Length")
            total = int(declared) if declared and declared.isdigit() else 0
            if total and total > MAX_ADDON_BYTES:
                raise RuntimeError(
                    f"安装包过大（{total} 字节，上限 {MAX_ADDON_BYTES} 字节）"
                )
            buf = bytearray()
            done = 0
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                buf.extend(chunk)
                done += len(chunk)
                if len(buf) > MAX_ADDON_BYTES:
                    raise RuntimeError(f"安装包超过上限 {MAX_ADDON_BYTES} 字节")
                if on_progress is not None:
                    on_progress(done, total, int(done * 100 / total) if total else 0)
            return bytes(buf)

    def _download_and_install(self, addon: AddonInfo, target: BlenderVersionInfo) -> None:
        """下载插件包（后台线程 + 底部进度条），下载完成后在主线程安装。"""
        if self.ctx is None or not addon.download_url:
            return
        self._start_addon_download(addon, target)

    def _start_addon_download(self, addon: AddonInfo, target: BlenderVersionInfo) -> None:
        """启动后台下载并显示进度；安装仍在主线程（涉及风险确认弹窗）。"""
        main_window = getattr(self.ctx, "main_window", None)

        def fetch(on_progress):
            return self._download_addon_bytes(addon.download_url, on_progress)  # pyright: ignore[reportUnknownArgumentType]

        worker = _AddonDownloadWorker(fetch, self._widget)
        self._dl_worker = worker

        def on_progress(_done: int, _total: int, pct: int) -> None:
            if main_window is not None and hasattr(main_window, "show_download"):
                main_window.show_download(f"⬇ 下载插件 {addon.name}  {pct}%", pct)

        def on_finished(ok: bool, content, err: str) -> None:
            self._dl_worker = None
            if main_window is not None and hasattr(main_window, "hide_download"):
                main_window.hide_download()
            if not ok:
                self._report_install_error(addon, RuntimeError(err or "下载失败"))
                self._refresh_cards()
                return
            self._install_content(
                content=content,  # pyright: ignore[reportArgumentType]
                addon=addon,
                target=target,
                content_type="",
                content_length=len(content) if content else 0,
                status_code=200,
            )
            self._refresh_cards()

        worker.progress.connect(on_progress)  # pyright: ignore[reportUnusedCallResult]
        worker.finished.connect(on_finished)  # pyright: ignore[reportUnusedCallResult]
        if main_window is not None and hasattr(main_window, "show_download"):
            main_window.show_download(f"⬇ 准备下载 {addon.name}", 0)
        worker.start()

    def _install_from_zip_file(self, path: str, addon: AddonInfo, target: BlenderVersionInfo) -> None:
        """把已下载到本地的 zip 走与在线下载完全相同的校验 + 安装流程。"""
        try:
            size = Path(path).stat().st_size
            if size > MAX_ADDON_BYTES:
                _ = QMessageBox.critical(
                    self._widget, "安装失败",
                    f"安装包过大：{size} 字节（上限 {MAX_ADDON_BYTES} 字节）\n\n{path}",
                )
                return
            content = Path(path).read_bytes()
        except OSError as e:
            _ = QMessageBox.critical(
                self._widget, "安装失败",
                f"无法读取下载到的安装包：\n{path}\n\n{e}",
            )
            return
        if not content:
            _ = QMessageBox.critical(
                self._widget, "安装失败", f"下载到的安装包为空：\n{path}"
            )
            return
        self._install_content(content, addon, target, "application/zip", len(content))
        self._refresh_cards()

    def _report_install_error(self, addon: AddonInfo, e: Exception) -> None:
        if self.ctx:
            self.ctx.log_service.error(  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
                f"Failed to install {addon.name}: {e}", module="addon_store"
            )
        # 旧实现在此回退到 _mock_install，导致真实安装失败却向用户弹"安装完成"，
        # 是全项目唯一会主动误导用户的缺陷。失败必须如实上报并中止。
        _ = QMessageBox.critical(
            self._widget, "安装失败",
            f"{addon.name} 安装失败：\n\n{e}\n\n"
            f"来源: {addon.source_url}\n"
            "如为网络问题，请稍后重试或改用其它插件源。"
        )

    def _install_content(
        self,
        content: bytes,
        addon: AddonInfo,
        target: BlenderVersionInfo,
        content_type: str = "",
        content_length: Optional[int] = None,
        status_code: int = 200,
    ) -> None:
        """对已获取的安装包字节做校验 + 解压 + 安装。

        在线下载（requests）与内嵌浏览器下载共用这一条路径，保证两种入口的
        安全校验、路径净化和错误提示行为完全一致。
        """
        if self.ctx is None:
            return

        addon_src_dir = Path(self.ctx.config_service.base_dir) / "addons_cache" / "downloads" / self._safe_slug(addon.id)  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportUnknownArgumentType]

        try:
            addon_src_dir.mkdir(parents=True, exist_ok=True)

            # ── 安装前完整性校验（方案 A：纵深校验，不做签名）──
            # 插件包会在 Blender 里执行任意 Python 代码，这条链路必须校验。
            verify = AddonVerifier()
            base_dir = Path(self.ctx.config_service.base_dir)
            vresult = verify.verify_response(
                status_code=status_code,
                content=content,
                content_type=content_type,
                content_length=content_length,
            )
            if not vresult.ok:
                verify.record_audit(base_dir, addon.id, addon.name, addon.source_url, vresult, target.version)
                if self.ctx:
                    self.ctx.log_service.error(
                        f"Addon package verification failed for '{addon.id}': {vresult.errors}",
                        module="addon_store",
                    )
                _ = QMessageBox.critical(
                    self._widget, "安装失败 — 安装包未通过安全校验",
                    f"{addon.name} 的安装包未通过完整性校验，安装已中止。\n\n"
                    f"原因：\n• " + "\n• ".join(vresult.errors) +
                    f"\n\n来源：{addon.source_url}\n"
                    "若你确信该插件可信，可能是下载被截断或源站临时异常，请稍后重试。"
                )
                self._refresh_cards()
                return

            # 命中风险特征：弹确认框让用户知情决定
            if vresult.requires_user_confirmation:
                reply = QMessageBox.warning(
                    self._widget, "安装确认 — 该插件包存在风险特征",
                    f"{addon.name} 的安装包在校验中发现需要你留意的地方：\n\n"
                    + vresult.summary()
                    + f"\n\n来源：{addon.source_url}\n\n"
                    "Blender 插件可以执行任意 Python 代码。请确认你信任这个来源。\n"
                    "是否仍要继续安装？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    verify.record_audit(
                        base_dir, addon.id, addon.name, addon.source_url, vresult, target.version
                    )
                    self._refresh_cards()
                    return

            verify.record_audit(base_dir, addon.id, addon.name, addon.source_url, vresult, target.version)
            if self.ctx:
                self.ctx.log_service.info(
                    f"Verified addon '{addon.id}': sha256={vresult.sha256[:16]}…, "
                    f"{vresult.entry_count} entries, {vresult.size_bytes/1024:.0f}KB",
                    module="addon_store",
                )

            # 解压 zip
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                zf.extractall(addon_src_dir)

            # 找到插件目录（通常是解压后的第一个目录）
            extracted_dirs = [d for d in addon_src_dir.iterdir() if d.is_dir()]
            if not extracted_dirs:
                if self.ctx:
                    self.ctx.log_service.error(  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
                        f"Addon archive has no plugin directory: {redact_url(addon.source_url)}",
                        module="addon_store",
                    )
                _ = QMessageBox.critical(
                    self._widget, "安装失败",
                    f"{addon.name} 的安装包结构异常：压缩包内未找到插件目录。\n\n"
                    f"来源: {addon.source_url}\n"
                    "该插件源提供的可能不是标准 Blender 插件包（需包含 __init__.py 的目录）。"
                )
                self._refresh_cards()
                return

            plugin_dir = extracted_dirs[0]

            # 复制到 Blender addons 目录（先解析真实 scripts 根：Windows 官方
            # 安装包的 scripts 在多一层版本号目录下，直接拼会装到无效位置）
            # _blender_scripts_root 返回的**就是** scripts 目录（别再拼 /scripts）
            scripts_dir = self._blender_scripts_root(target) / "addons"
            scripts_dir.mkdir(parents=True, exist_ok=True)

            # 目录名用**压缩包内的真实插件目录名**（如 kaboom），而不是远端 id
            # （budeco_44968）。旧实现按远端 id 命名，文件虽然装对了，但用户在
            # Blender 插件目录 / 文件管理器里看到的是一串看不懂的数字，会直接
            # 误判"没装上"；Blender 侧的显示名取自 bl_info，与目录名无关，
            # 所以改成真实名对识别零影响、对可读性是质变。
            dir_name = self._safe_slug(plugin_dir.name)
            dest_dir = scripts_dir / dir_name
            resolved_dest = dest_dir.resolve()
            if not str(resolved_dest).startswith(str(scripts_dir.resolve())):
                if self.ctx:
                    self.ctx.log_service.error(  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
                        f"Blocked addon path traversal attempt: id={addon.id!r}",
                        module="addon_store",
                    )
                _ = QMessageBox.critical(
                    self._widget, "安装失败",
                    "插件标识包含非法路径字符，安装已中止。"
                )
                self._refresh_cards()
                return

            # 原子替换：先复制到临时目录，再把旧目录改名备份，最后替换。
            # 任一步失败都回滚，避免旧实现「先删后拷」在失败时把用户现有插件弄丢。
            tmp_dir = scripts_dir / f".{dir_name}.tmp-{uuid.uuid4().hex[:8]}"
            backup_dir = scripts_dir / f".{dir_name}.bak-{uuid.uuid4().hex[:8]}"
            try:
                if tmp_dir.exists():
                    shutil.rmtree(tmp_dir)
                shutil.copytree(plugin_dir, tmp_dir)  # pyright: ignore[reportUnusedCallResult]
                if dest_dir.exists():
                    dest_dir.rename(backup_dir)
                tmp_dir.rename(dest_dir)
            except OSError as e:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                if backup_dir.exists() and not dest_dir.exists():
                    try:
                        backup_dir.rename(dest_dir)
                    except OSError:
                        pass
                if self.ctx:
                    self.ctx.log_service.error(  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
                        f"Failed to install addon '{addon.name}' atomically: {e}",
                        module="addon_store",
                    )
                _ = QMessageBox.critical(
                    self._widget, "安装失败",
                    f"写入插件目录失败：{e}\n\n已尝试保留原插件，请检查目录权限后重试。",
                )
                self._refresh_cards()
                return
            else:
                shutil.rmtree(backup_dir, ignore_errors=True)

            self._installed_addons.add(addon.id)
            self._record_installed(addon, target, dir_name)

            if self.ctx:
                self.ctx.log_service.info(  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
                    f"Installed addon '{addon.name}' from {addon.source}", module="addon_store"
                )
                self.ctx.event_bus.emit("addon.installed", {  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
                    "addon_id": addon.id,
                    "addon_name": addon.name,
                    "blender_version": target.version,
                    "source": addon.source,
                })

            _ = QMessageBox.information(
                self._widget, "安装完成",
                f"{addon.name} 已成功安装到 Blender {target.version}\n\n"
                f"安装位置：{dest_dir}\n\n"
                "在 Blender 中启用：\n"
                "编辑 → 偏好设置 → 插件 / 扩展 → 搜索 "
                f"\"{addon.name}\" 勾选\n"
                "（带 blender_manifest.toml 的新式插件会出现在「扩展」标签页，"
                "旧式 bl_info 插件在「插件」标签页）\n\n"
                + f"来源: {addon.source_url}"
            )

        except Exception as e:  # noqa: BLE001
            self._report_install_error(addon, e)
            self._refresh_cards()
            return

    # ── 安装状态持久化（重启不再丢） ──────────────────────────────────────

    def _installed_map_path(self) -> Optional[Path]:
        """安装记录文件位置：<config>/addons_cache/installed_map.json。"""
        if self.ctx is None:
            return None
        base = Path(self.ctx.config_service.base_dir)  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]
        d = base / "addons_cache"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return d / "installed_map.json"

    def _record_installed(self, addon: AddonInfo, target: BlenderVersionInfo, dir_name: str) -> None:
        """记录「远端 id → 实际安装目录 / Blender 版本」，供重启后恢复状态。"""
        path = self._installed_map_path()
        if path is None:
            return
        data = self._read_installed_map(path)
        data[addon.id] = {
            "dir": dir_name,
            "blender": target.version,
            "name": addon.name,
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            # 原子写：先写临时文件再替换，避免崩溃/断电留下截断的 JSON
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            os.replace(tmp_path, path)
        except OSError as e:
            if self.ctx:
                self.ctx.log_service.warning(  # pyright: ignore[reportOptionalMemberAccess]
                    f"写入安装记录失败: {e}", module="addon_store"
                )

    @staticmethod
    def _read_installed_map(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _restore_installed_states(self) -> None:
        """启动时恢复"已安装"状态。

        旧实现把已安装集合只放在内存里，重启 HUB 后全部丢失——用户明明装好了，
        界面却显示未安装，点安装又会重下一遍。这里读安装记录，逐条核对目标
        Blender 的插件目录里那个目录是否还在；用户手动删掉插件 → 状态自动回退。
        """
        path = self._installed_map_path()
        if path is None:
            return
        data = self._read_installed_map(path)
        if not data:
            return
        if self.ctx is None:
            return
        try:
            versions = {v.version: v for v in self.ctx.blender_service.detect_installed()}  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType, reportUnknownVariableType]
        except Exception:  # noqa: BLE001
            return
        changed = False
        for addon_id, info in list(data.items()):
            ver = versions.get(info.get("blender", ""))
            if ver is None:
                continue  # 那个 Blender 版本不在机器上了，跳过
            scripts = self._blender_scripts_root(ver) / "addons" / info.get("dir", "")
            if scripts.is_dir():
                self._installed_addons.add(addon_id)
            else:
                # 插件目录被删了（用户手动清理 / Blender 重装）→ 状态如实回退
                data.pop(addon_id, None)
                changed = True
        if changed:
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
        if self._installed_addons and self.ctx:
            self.ctx.log_service.info(  # pyright: ignore[reportOptionalMemberAccess]
                f"恢复已安装状态 {len(self._installed_addons)} 项", module="addon_store"
            )

    def _safe_slug(self, raw: str) -> str:
        """把远端派生的 id 净化为安全的单级目录名。

        addon.id 来自远端（budeco.top 抓取的标题等），可能含 "/"、".." 或盘符，
        直接拼进文件系统路径会写出 Blender 安装目录。非法字符统一替换为 "_"，
        并保证结果非空且不含路径分隔符。
        """
        s = re.sub(r"[^A-Za-z0-9_.\-]", "_", str(raw))
        s = s.strip(".") or "addon"
        # 防止 "..." / ".." 这类纯点号退化结果
        if set(s) <= {"."}:
            s = "addon"
        return s

    def _check_compatibility(
        self, addon: AddonInfo, versions: Sequence[BlenderVersionInfo]
    ) -> list[str]:
        """双层兼容性检测"""
        issues: list[str] = []
        for v in versions:
            try:
                ver: str = v.version
                if self._version_lt(ver, addon.min_blender):
                    issues.append(
                        f"Blender {ver} 低于插件最低要求 ({addon.min_blender})"
                    )
                if self._version_gt(ver, addon.max_blender):
                    issues.append(
                        f"Blender {ver} 可能不完全兼容（插件最高支持 {addon.max_blender}）"
                    )
            except (ValueError, IndexError):
                pass
        return issues

    @staticmethod
    def _version_lt(v1: str, v2: str) -> bool:
        parts1 = [int(x) for x in v1.replace("-beta", "").split(".")]
        parts2 = [int(x) for x in v2.replace("-beta", "").split(".")]
        return parts1 < parts2

    @staticmethod
    def _version_gt(v1: str, v2: str) -> bool:
        parts1 = [int(x) for x in v1.replace("-beta", "").split(".")]
        parts2 = [int(x) for x in v2.replace("-beta", "").split(".")]
        return parts1 > parts2

    # === 详情弹窗 ===

    def _show_details(self, addon_id: str) -> None:
        if self._addon_service is None:
            return

        addon = self._addon_service.get_addon(addon_id)
        if addon is None:
            return

        thumb_path = self._addon_service.get_thumbnail_path(addon_id)
        is_installed = addon_id in self._installed_addons

        dialog = AddonDetailDialog(
            addon, is_installed=is_installed,
            thumbnail_path=thumb_path, parent=self._widget,
        )
        _ = dialog.install_requested.connect(self._install_addon)  # pyright: ignore[reportUnknownMemberType]
        _ = dialog.exec()

        # 弹窗关闭后刷新（可能安装了插件）。
        # 延迟一个事件循环：此刻仍在卡片点击信号的分发栈里，立即销毁该卡片
        # （_clear_cards）会在 Windows 上崩溃。
        QTimer.singleShot(0, self._refresh_cards)

    # === 状态快照 ===

    @override
    def get_state_snapshot(self) -> dict[str, object]:
        return {
            "current_category": self._current_category,
            "search_text": self._search_input.text() if self._search_input else "",
        }

    @override
    def restore_state(self, state: dict[str, object]) -> None:
        if "current_category" in state:
            self._current_category = str(state["current_category"])
        if "search_text" in state and self._search_input:
            self._search_input.setText(str(state["search_text"]))
