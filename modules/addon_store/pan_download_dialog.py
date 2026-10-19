"""网盘分享下载对话框 —— budeco 的默认安装路径（不依赖内嵌浏览器）。

为什么需要它：实测 budeco.top 的 erphpdown 下载链最终**一律落到网盘分享页**
（`pan.baidu.com/share/init?surl=…&pwd=…`，提取码就在地址栏 `pwd` 参数里），
不是可直接下载的 zip 直链。任何内嵌浏览器方案都要面对"网盘分享页 + 提取码
+ 可能的客户端强校验"，而内嵌浏览器在打包环境下还伴随崩溃风险。

所以默认路径改成最稳的一条：
    纯 requests 解析出网盘链接与提取码（``BudecoAuthService.resolve_download``）
    → 打开系统浏览器（用户已有网盘登录态/大文件下载器接管）
    → 用户下载完点「我已下载，选择 zip 文件」
    → 走与在线安装**完全相同**的校验/解压/落位流程

内嵌浏览器仍保留为可选项（主对话框上的「用内嵌浏览器下载」），但不再挡在
默认路径前面——它崩了也不影响下载。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from core import theme  # pyright: ignore[reportImplicitRelativeImport]
from modules.addon_store.download_watch import DownloadWatcher


class PanDownloadDialog(QDialog):
    """展示网盘链接 + 提取码，引导用户下载后选回 zip 文件。"""

    zip_ready = pyqtSignal(str)  # 用户选好的 zip 绝对路径
    embedded_requested = pyqtSignal()  # 用户改走内嵌浏览器（可选路径，由外部接管）

    def __init__(self, addon_name: str, pan_url: str, code: str = "", parent=None) -> None:
        super().__init__(parent)
        self._url = pan_url or ""
        self._code = code or ""
        self._downloaded = False
        self._watcher = None
        self.zip_path: Optional[str] = None

        self.setWindowTitle(f"下载 {addon_name} — 网盘分享")
        self.setMinimumSize(560, 260)
        self.setStyleSheet(f"background-color: {theme.Tokens.OVERLAY_BG};")
        self._build_ui(addon_name)

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self, addon_name: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel(f"{addon_name} 的文件放在网盘分享里")
        title.setStyleSheet(
            f"color: {theme.Tokens.ON_CARD}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(title)

        tip = QLabel(
            "① 点「在浏览器中下载」打开分享页（提取码已自动填好，"
            "浏览器若已有网盘登录态会直接开始下载）\n"
            "② 下载完成后点「我已下载，选择 zip 文件」，会自动装进 Blender"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 12px;")
        layout.addWidget(tip)

        # 链接（只读，可复制）+ 复制按钮
        link_row = QHBoxLayout()
        self._link = QLineEdit(self._url)
        self._link.setReadOnly(True)
        self._link.setFixedHeight(30)
        self._link.setStyleSheet(
            f"color: {theme.Tokens.ON_CARD}; font-size: 12px;"
            f" background-color: {theme.Tokens.OVERLAY_BG_ALT};"
            " border: 1px solid rgba(255,255,255,0.10); border-radius: 4px; padding: 0 6px;"
        )
        self._link.setCursorPosition(0)
        self._link.textChanged.connect(self._on_link_changed)  # type: ignore[attr-defined]
        copy_btn = QPushButton("复制链接")
        copy_btn.setObjectName("secondaryButton")
        copy_btn.setFixedHeight(30)
        copy_btn.clicked.connect(self._copy)  # type: ignore[reportUnknownMemberType]
        link_row.addWidget(self._link, 1)
        link_row.addWidget(copy_btn)
        layout.addLayout(link_row)

        # 提取码
        code_row = QHBoxLayout()
        code_label = QLabel("提取码：")
        code_label.setStyleSheet(f"color: {theme.Tokens.ON_CARD}; font-size: 13px;")
        self._code_box = QLabel(self._code or "（链接里可能已自带，无需填写）")
        self._code_box.setStyleSheet(
            f"color: {theme.Tokens.ACCENT}; font-size: 15px; font-weight: bold;"
        )
        code_btn = QPushButton("复制提取码")
        code_btn.setObjectName("secondaryButton")
        code_btn.setFixedHeight(28)
        code_btn.clicked.connect(self._copy_code)  # type: ignore[attr-defined]
        code_row.addWidget(code_label)
        code_row.addWidget(self._code_box)
        code_row.addStretch()
        code_row.addWidget(code_btn)
        layout.addLayout(code_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 12px;")
        layout.addWidget(self._status)

        # 按钮行
        btn_row = QHBoxLayout()
        self._open_btn = QPushButton("在浏览器中下载")
        self._open_btn.setObjectName("primaryButton")
        self._open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_btn.setFixedHeight(34)
        self._open_btn.clicked.connect(self._open_browser)  # type: ignore[reportUnknownMemberType]

        self._pick_btn = QPushButton("我已下载，选择 zip 文件")
        self._pick_btn.setObjectName("secondaryButton")
        self._pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pick_btn.setFixedHeight(34)
        self._pick_btn.clicked.connect(self._pick_zip)  # type: ignore[reportUnknownMemberType]

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setFixedHeight(34)
        self._cancel_btn.clicked.connect(self.reject)  # type: ignore[reportUnknownMemberType]

        # 内嵌浏览器是可选路径：默认不启用（打包环境下曾直接把应用带崩），
        # 只有用户明确想「在应用内点」时才走。
        self._embedded_btn = QPushButton("改用内嵌浏览器下载")
        self._embedded_btn.setObjectName("secondaryButton")
        self._embedded_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._embedded_btn.setFixedHeight(34)
        self._embedded_btn.setToolTip(
            "在应用内置浏览器里点下载（会由程序自动拦截下载文件）。\n"
            "若应用闪退请改用「在浏览器中下载」——那条路径不依赖内嵌浏览器。"
        )
        self._embedded_btn.clicked.connect(self._request_embedded)  # type: ignore[reportUnknownMemberType]

        # 浏览器把文件下到别处时，手动告诉 HUB 去哪里捡
        self._dir_btn = QPushButton("选择下载目录")
        self._dir_btn.setObjectName("secondaryButton")
        self._dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dir_btn.setFixedHeight(34)
        self._dir_btn.setToolTip("浏览器把文件下到了别处？告诉 HUB 去哪里自动捡")
        self._dir_btn.clicked.connect(self._on_pick_watch_dir)  # type: ignore[reportUnknownMemberType]

        btn_row.addWidget(self._open_btn)
        btn_row.addWidget(self._pick_btn)
        btn_row.addWidget(self._embedded_btn)
        btn_row.addWidget(self._dir_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        self._stop_watch()
        super().closeEvent(event)

    def _request_embedded(self) -> None:
        try:
            self.embedded_requested.emit()
        except Exception:  # noqa: BLE001
            pass

    def _on_link_changed(self, text: str) -> None:  # noqa: ANN001
        # 手动粘贴了别家网盘链接时，提取码以链接里的 pwd 为准
        if "?pwd=" in text or "&pwd=" in text:
            self._code_box.setText(self._code or "")

    # ── 动作 ─────────────────────────────────────────────────────────────

    def _open_browser(self) -> None:
        if not self._url:
            return
        # 提取码先进剪贴板：网盘页直接 Ctrl+V，不用来回看窗口
        if self._code:
            QApplication.clipboard().setText(self._code)
        QDesktopServices.openUrl(QUrl(self._url))
        self._downloaded = True
        self._start_watch()
        self._status.setText(
            "已在浏览器打开分享页"
            + (f"，提取码「{self._code}」已复制到剪贴板" if self._code else "")
            + "。\n在浏览器里点「下载」即可——下完后这里会自动检测到并安装，"
            "不需要切回本窗口点按钮。"
        )

    # ── 下载目录守望（自动拾取浏览器下载的 zip）─────────────────────────

    def _start_watch(self) -> None:
        """盯住下载目录，出现「新写完的 zip」就自动接管安装。"""
        if self._watcher is not None and self._watcher.running:
            return
        dirs = [_default_download_dir()]
        # 顺带覆盖「下载」与「桌面」两个默认位置，减少用户手动指定
        home = Path.home()
        for extra in (home / "Downloads", home / "Desktop", home / "下载"):
            if extra.is_dir() and extra not in dirs:
                dirs.append(extra)

        self._watcher = DownloadWatcher(dirs=dirs, parent=self, timeout_s=900)
        self._watcher.found.connect(self._on_watched_zip)
        self._watcher.timed_out.connect(self._on_watch_timeout)
        self._watcher.waiting.connect(self._on_watch_tick)
        self._watcher.start()

    def _on_watched_zip(self, path: str) -> None:
        self._status.setText(f"检测到下载完成的文件：{Path(path).name}，正在安装…")
        self.zip_path = path
        try:
            self.zip_ready.emit(path)
        except Exception:  # noqa: BLE001
            pass
        QTimer.singleShot(200, self.accept)

    def _on_watch_timeout(self) -> None:
        self._status.setText(
            "等待超时（15 分钟）仍未在下载目录发现新的 zip。\n"
            "若浏览器下载到了别处，可点「选择下载目录」；或直接点"
            "「我已下载，选择 zip 文件」手动安装。"
        )

    def _on_watch_tick(self, seconds: int) -> None:
        self._status.setText(
            f"正在等待浏览器下载… 已等待 {seconds // 60:d} 分 {seconds % 60:02d} 秒\n"
            "在浏览器里点「下载」即可，下完自动安装。"
        )

    def _on_pick_watch_dir(self) -> None:
        start = str(_default_download_dir())
        chosen = QFileDialog.getExistingDirectory(self, "选择浏览器的下载目录", start)
        if not chosen:
            return
        if self._watcher is None:
            self._watcher = DownloadWatcher(dirs=[Path(chosen)], parent=self, timeout_s=900)
            self._watcher.found.connect(self._on_watched_zip)
            self._watcher.timed_out.connect(self._on_watch_timeout)
            self._watcher.waiting.connect(self._on_watch_tick)
        else:
            self._watcher.set_dirs([Path(chosen)])
        self._watcher.start()
        self._status.setText(f"已切换监控目录：{chosen}\n正在等待下载完成…")

    def _stop_watch(self) -> None:
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:  # noqa: BLE001
                pass

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._url)
        self._status.setText("链接已复制到剪贴板。")

    def _copy_code(self) -> None:
        if not self._code:
            self._status.setText("该链接未带提取码。")
            return
        QApplication.clipboard().setText(self._code)
        self._status.setText(f"提取码「{self._code}」已复制。")

    def _pick_zip(self) -> None:
        start = str(_default_download_dir())
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择下载好的插件压缩包", start,
            "Blender 插件包 (*.zip);;所有文件 (*)",
        )
        if not chosen:
            return
        if Path(chosen).suffix.lower() != ".zip":
            self._status.setText("选中的不是 .zip。Blender 只能从 zip 安装，请选 zip 文件。")
            return
        self.zip_path: Optional[str] = chosen
        try:
            self.zip_ready.emit(chosen)
        except Exception:  # noqa: BLE001
            pass
        self.accept()


def _default_download_dir() -> Path:
    """优先用浏览器默认下载目录，省得用户每次翻文件夹。"""
    import os

    home = Path.home()
    candidates = [
        home / "Downloads",
        home / "下载",
        home / "Desktop",
    ]
    # Chrome / Edge 的默认下载目录（Preferences 里的 download.default_directory）
    for user_data, profile in (
        (home / "AppData" / "Local" / "Google" / "Chrome" / "User Data", "Default"),
        (home / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data", "Default"),
    ):
        prefs = user_data / profile / "Preferences"
        try:
            import json

            if prefs.is_file():
                data = json.loads(prefs.read_text(encoding="utf-8", errors="replace"))
                d = (data.get("download") or {}).get("default_directory")
                if d:
                    candidates.insert(0, Path(os.path.expandvars(d)))
        except Exception:  # noqa: BLE001
            continue

    for c in candidates:
        if c.is_dir():
            return c
    return home