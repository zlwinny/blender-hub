"""设置 — 下载镜像源、本地目录与关于。"""

from __future__ import annotations

import subprocess
import sys

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core import theme
from core.services.download_service import DownloadService
from core.widgets import badge, page_header, section_title

# 镜像源可用性（2026-10 实测）
MIRROR_OK = {"nju": True, "tsinghua": False, "ustc": False, "official": False}
MIRROR_LABEL = {
    "nju": "南京大学镜像（推荐）",
    "tsinghua": "清华镜像 TUNA",
    "ustc": "中科大镜像 USTC",
    "official": "官方源（国内常被拦截）",
}


class _LinkRow(QFrame):
    """「更多」列表里的一行：整行可点击，右侧带箭头/外链标记。"""

    clicked = pyqtSignal()

    def __init__(self, text: str, external: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("linkRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 9, 4, 9)
        lay.setSpacing(8)

        label = QLabel(text)
        label.setObjectName("linkRowText")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(label)
        lay.addStretch()

        arrow = QLabel("↗" if external else "›")
        arrow.setObjectName("linkRowArrow")
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(arrow)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class SettingsModule(QWidget):
    def __init__(self, ctx, show_header: bool = True) -> None:
        super().__init__()
        self.ctx = ctx
        self._show_header = show_header
        self._build()

    def _build(self) -> None:
        # 内容放进滚动区：关于面板较高，小窗口下也能完整浏览
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        if self._show_header:  # 嵌入配置中心标签页时不再重复标题
            root.addWidget(page_header("设置", "下载镜像源、安装位置与本地数据"))
        root.addWidget(self._row_mirror())
        root.addWidget(self._row_install_dir())
        root.addWidget(self._row_dirs())
        root.addWidget(self._row_about())
        root.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _panel(self, title: str) -> tuple[QFrame, QHBoxLayout]:
        f = QFrame()
        f.setObjectName("panel")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(section_title(title))
        return f, lay

    def _row_mirror(self) -> QWidget:
        f, lay = self._panel("下载镜像源")
        self.box = QComboBox()
        for k, base in DownloadService.MIRRORS.items():
            self.box.addItem(f"{MIRROR_LABEL.get(k, k)}  ·  {base}", k)
        cur = self.ctx.config.get("settings", "mirror", DownloadService.DEFAULT_MIRROR)
        keys = list(DownloadService.MIRRORS)
        self.box.setCurrentIndex(keys.index(cur) if cur in keys else 0)
        self.box.setFixedWidth(440)
        self.box.currentIndexChanged.connect(self._change_mirror)
        lay.addWidget(self.box)
        self.state = badge("可用" if MIRROR_OK.get(cur, False) else "当前不可用",
                           "ok" if MIRROR_OK.get(cur, False) else "warn")
        lay.addWidget(self.state)
        lay.addStretch()
        return f

    def _change_mirror(self) -> None:
        key = self.box.currentData()
        self.ctx.config.set("settings", "mirror", key)
        self.state.setText("可用" if MIRROR_OK.get(key, False) else "当前不可用")
        self.state.setObjectName("badgeOk" if MIRROR_OK.get(key, False) else "badgeWarn")
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self.ctx.info(f"镜像源切换为 {key}", module="settings")
        self.window().notify(f"已切换到 {MIRROR_LABEL.get(key, key)}")

    def _row_install_dir(self) -> QWidget:
        """Blender 安装位置 — 改动后必须点「保存」才落盘（设置过就要 save）。"""
        f, lay = self._panel("Blender 安装位置")
        self._install_edit = QLineEdit()
        self._install_edit.setPlaceholderText("新版本将安装到「该目录/<版本号>」")
        self._install_edit.setFixedWidth(380)
        self._install_edit.setText(self._install_root())
        self._install_edit.textChanged.connect(self._mark_install_dirty)
        lay.addWidget(self._install_edit)

        b = QPushButton("浏览...")
        b.setObjectName("ghost")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self._browse_install_dir)
        lay.addWidget(b)

        self._install_save = QPushButton("保存")
        self._install_save.setObjectName("primaryButton")
        self._install_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_save.setEnabled(False)
        self._install_save.clicked.connect(self._save_install_dir)
        lay.addWidget(self._install_save)

        self._install_state = badge("", "ok")
        self._install_state.setVisible(False)
        lay.addWidget(self._install_state)
        lay.addStretch()
        return f

    # ── 安装位置读写（与版本库共用同一份 QSettings） ──

    def _install_root(self) -> str:
        try:
            from modules.version_library.module import get_install_root

            base = str(Path(self.ctx.config.base_dir) / "blender_versions")
            return get_install_root(base)
        except Exception:  # noqa: BLE001
            return ""

    def _mark_install_dirty(self, *_a) -> None:
        self._install_save.setEnabled(True)
        self._install_state.setVisible(False)

    def _browse_install_dir(self) -> None:
        start = self._install_edit.text().strip() or str(Path.home())
        picked = QFileDialog.getExistingDirectory(self, "选择 Blender 安装根目录", start)
        if picked:
            self._install_edit.setText(picked)
            self._install_save.setEnabled(True)

    def _notify(self, text: str, ok: bool = True) -> None:
        """通知（未挂到窗口时静默，便于单测直接构造本模块）"""
        w = self.window()
        if w is not None and hasattr(w, "notify"):
            w.notify(text, ok=ok)

    def _save_install_dir(self) -> None:
        from modules.version_library.module import save_install_root

        root = self._install_edit.text().strip()
        if not root:
            self._notify("请先选择安装目录", ok=False)
            return
        try:
            Path(root).mkdir(parents=True, exist_ok=True)
            probe = Path(root) / ".bh_write_test"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
        except Exception as e:  # noqa: BLE001
            self._notify(f"目录不可写：{e}", ok=False)
            return
        save_install_root(root)
        self._install_save.setEnabled(False)
        self._install_state.setText("已保存")
        self._install_state.setVisible(True)
        self.ctx.info(f"Blender 安装位置已保存：{root}", module="settings")
        self._notify("安装位置已保存")

    def _row_dirs(self) -> QWidget:
        f, lay = self._panel("本地数据")
        for txt, path in (
            ("数据目录", self.ctx.config.base_dir),
            ("日志目录", self.ctx.config.base_dir / "logs"),
        ):
            lay.addWidget(QLabel(txt))
            b = QPushButton()
            b.setObjectName("ghost")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            # 长路径省略显示，完整路径放 tooltip，避免撑出横向滚动条
            b.setText(QFontMetrics(b.font()).elidedText(
                str(path), Qt.TextElideMode.ElideMiddle, 420,
            ))
            b.setToolTip(str(path))
            b.setMaximumWidth(420)
            b.clicked.connect(lambda _, p=path: subprocess.Popen(["explorer", str(p)]))
            lay.addWidget(b)

            copy_btn = QPushButton("复制")
            copy_btn.setObjectName("ghost")
            copy_btn.setFixedWidth(56)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            copy_btn.setToolTip(f"复制完整路径：{path}")
            copy_btn.clicked.connect(lambda _, p=path: self._copy_path(p))
            lay.addWidget(copy_btn)
        lay.addStretch()
        return f

    def _row_about(self) -> QWidget:
        """关于：图标 + 名称/版本 + 描述 + 维护者 + 更多入口（参考 FlClash 布局）。"""
        f = QFrame()
        f.setObjectName("panel")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(20, 18, 20, 14)
        lay.setSpacing(10)

        # ── 头部：应用图标 + 名称 / 版本 ──
        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        app = QApplication.instance()
        icon = app.windowIcon() if app is not None else None
        if icon is not None and not icon.isNull():
            logo.setPixmap(icon.pixmap(56, 56))
        else:
            logo.setText("B")
            logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            logo.setFixedSize(56, 56)
            logo.setStyleSheet(
                f"background-color: {theme.Tokens.ACCENT}; color: #ffffff;"
                "border-radius: 14px; font-size: 26px; font-weight: 700;"
            )
        head.addWidget(logo)

        names = QVBoxLayout()
        names.setSpacing(2)
        name = QLabel("Blender Hub")
        name.setStyleSheet(f"color: {theme.Tokens.TEXT}; font-size: 19px; font-weight: 650;")
        version = QLabel("v1.0")
        version.setObjectName("badgeMuted")
        names.addWidget(name)
        names.addWidget(version)
        head.addLayout(names)
        head.addStretch()
        lay.addLayout(head)

        desc = QLabel("Blender 版本 / 项目 / 插件的一体化桌面管理工具。本地优先，开源无广告。")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.Tokens.TEXT_SUB}; font-size: 12.5px;")
        lay.addWidget(desc)

        lay.addWidget(section_title("维护者"))
        maintainer = QLabel("BlenderHub Team")
        maintainer.setStyleSheet(f"color: {theme.Tokens.TEXT}; font-size: 13px;")
        lay.addWidget(maintainer)

        lay.addWidget(section_title("更多"))
        rows = (
            ("免责声明", self._show_disclaimer_dialog, False),
            ("开源许可证 MIT", self._show_license_dialog, False),
            ("Blender 官网", lambda: self._open_url("https://www.blender.org/"), True),
            ("Blender 手册", lambda: self._open_url("https://docs.blender.org/"), True),
            ("打开日志目录", self._open_log_dir, True),
        )
        for text, handler, external in rows:
            row = _LinkRow(text, external=external)
            row.clicked.connect(handler)
            lay.addWidget(row)
        return f

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _copy_path(self, path: Path) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(path))
        self._notify(f"已复制路径：{path}")

    def _open_log_dir(self) -> None:
        logs = Path(self.ctx.config.base_dir) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(logs)])

    def _show_disclaimer_dialog(self) -> None:
        from core.disclaimer import show_disclaimer

        show_disclaimer(self, first_run=False)

    def _show_license_dialog(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("开源许可证")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(self._license_text())
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    @staticmethod
    def _license_text() -> str:
        roots: list[Path] = []
        if getattr(sys, "frozen", False):
            roots.append(Path(getattr(sys, "_MEIPASS", "")))
        roots.append(Path(__file__).resolve().parents[1])
        for root in roots:
            candidate = root / "LICENSE"
            if candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8")
                except OSError:
                    break
        return (
            "MIT License\n\nCopyright (c) 2026 BlenderHub Team\n\n"
            "本软件基于 MIT 许可开源：允许在保留版权声明的前提下自由使用、修改与再分发。"
        )
