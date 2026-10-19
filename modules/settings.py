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

from core import i18n, theme
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

        self.text_label = QLabel(text)
        self.text_label.setObjectName("linkRowText")
        self.text_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.text_label)
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
        # 控件 ↔ 词条 绑定表，供 retranslate() 在切换语言时刷新文案
        self._tr_items: list = []
        self._build()

    # ── 多语言支持 ──────────────────────────────────────────────────
    def _bind_text(self, widget, key: str, attr: str = "setText", transform=None) -> None:
        """把控件文案绑定到词条，立即应用并在切换语言时自动刷新。

        transform 用于保留原样式（例如 section_title 的小标题大写）。
        """
        self._tr_items.append((widget, key, attr, transform))
        value = i18n.tr(key)
        getattr(widget, attr)(transform(value) if transform else value)

    def retranslate(self) -> None:
        """语言切换后刷新本页所有已绑定文案（主窗口统一调用）。"""
        for widget, key, attr, transform in self._tr_items:
            try:
                value = i18n.tr(key)
                getattr(widget, attr)(transform(value) if transform else value)
            except RuntimeError:
                pass  # 控件已被销毁（页面重建等），跳过即可

    def _row_language(self) -> QWidget:
        """界面语言：中文 / English 切换，即时生效。"""
        f, lay = self._panel("settings.language.label")
        box = QComboBox()
        box.addItem(i18n.tr("lang.zh"), i18n.LANG_ZH)
        box.addItem(i18n.tr("lang.en"), i18n.LANG_EN)
        box.setFixedWidth(220)
        cur = i18n.current_lang()
        idx = box.findData(cur)
        box.setCurrentIndex(idx if idx >= 0 else 0)

        def on_change() -> None:
            lang = box.currentData()
            if not lang or lang == i18n.current_lang():
                return
            i18n.set_lang(lang)  # 会通知主窗口刷新侧栏与各页
            self._notify(i18n.tr("settings.language.applied"))

        box.currentIndexChanged.connect(on_change)
        lay.addWidget(box)
        lay.addStretch()
        return f

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
            root.addWidget(page_header(
                i18n.tr("settings.header.title"), i18n.tr("settings.header.subtitle")
            ))
        root.addWidget(self._row_language())
        root.addWidget(self._row_mirror())
        root.addWidget(self._row_install_dir())
        root.addWidget(self._row_dirs())
        root.addWidget(self._row_about())
        root.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _panel(self, key: str) -> tuple[QFrame, QHBoxLayout]:
        f = QFrame()
        f.setObjectName("panel")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(16, 14, 16, 14)
        # 注意：section_title() 只是创建 QLabel，必须再 addWidget 才会显示
        title_label = section_title(i18n.tr(key))
        self._bind_text(title_label, key, transform=str.upper)  # section_title 原样为大写
        lay.addWidget(title_label)
        return f, lay

    def _row_mirror(self) -> QWidget:
        f, lay = self._panel("settings.mirror.label")
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
        f, lay = self._panel("settings.install_dir.label")
        self._install_edit = QLineEdit()
        self._install_edit.setPlaceholderText("新版本将安装到「该目录/<版本号>」")
        self._install_edit.setFixedWidth(380)
        self._install_edit.setText(self._install_root())
        self._install_edit.textChanged.connect(self._mark_install_dirty)
        lay.addWidget(self._install_edit)

        b = QPushButton()
        b.setObjectName("ghost")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bind_text(b, "common.browse")
        b.clicked.connect(self._browse_install_dir)
        lay.addWidget(b)

        self._install_save = QPushButton()
        self._bind_text(self._install_save, "common.save")
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
        f, lay = self._panel("settings.dirs.label")
        for key, path in (
            ("settings.dirs.data", self.ctx.config.base_dir),
            ("settings.dirs.logs", self.ctx.config.base_dir / "logs"),
        ):
            lbl = QLabel()
            self._bind_text(lbl, key)
            lay.addWidget(lbl)
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

            copy_btn = QPushButton()
            self._bind_text(copy_btn, "common.copy")
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

        desc = QLabel()
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.Tokens.TEXT_SUB}; font-size: 12.5px;")
        self._bind_text(desc, "settings.about.description")
        lay.addWidget(desc)

        maintainer_title = section_title(i18n.tr("settings.about.maintainer"))
        self._bind_text(maintainer_title, "settings.about.maintainer", transform=str.upper)
        lay.addWidget(maintainer_title)
        maintainer = QLabel("BlenderHub Team")
        maintainer.setStyleSheet(f"color: {theme.Tokens.TEXT}; font-size: 13px;")
        lay.addWidget(maintainer)

        more_title = section_title(i18n.tr("settings.about.more"))
        self._bind_text(more_title, "settings.about.more", transform=str.upper)
        lay.addWidget(more_title)
        rows = (
            ("settings.about.disclaimer", self._show_disclaimer_dialog, False),
            ("settings.about.license", self._show_license_dialog, False),
            ("settings.about.site", lambda: self._open_url("https://www.blender.org/"), True),
            ("settings.about.manual", lambda: self._open_url("https://docs.blender.org/"), True),
            ("settings.about.log_dir", self._open_log_dir, True),
        )
        for key, handler, external in rows:
            row = _LinkRow(i18n.tr(key), external=external)
            self._bind_text(row.text_label, key)
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
