"""内嵌浏览器一体化安装器 —— 登录、网盘、下载、交给 Blender 安装，一个窗口搞定。

为什么需要它（而不是用 requests 抓链再下）：
budeco.top 的下载按钮由 erphpdown 插件生成，未登录时是 `javascript:;` 占位，
登录后可能是**直链**，也可能是**百度网盘分享页**（还需网盘登录 + 提取码），
而且网盘真实下载链接要带 Referer/Cookie/UA 才有权限。用 requests 复刻这一整套
鉴权既脆弱又不可能通用。

内嵌 QWebEngine 的思路反过来：让用户在我们自己的浏览器里点，点击产物（下载请求）
由我们截获 —— `QWebEngineProfile.downloadRequested` 拿到的已是浏览器带齐 Cookie
和 UA 的真实下载，我们只需落盘，然后把 zip 交给安装流程。整个过程与用户手动
下载完全等价，但不需要用户去文件管理器里找文件。

两种失败兜底：
1. 网盘强制走客户端（baiduyun:// 协议）→ 顶部「手动选择 zip」按钮。
2. 环境没有 WebEngine（命令行 / 无显示）→ 直接降级为该按钮 + 外部浏览器提示。
"""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from core import theme  # pyright: ignore[reportImplicitRelativeImport]
from core.services.browser_cookie_import import (  # pyright: ignore[reportImplicitRelativeImport]
    collect_firefox_cookies,
    inject_into_webengine,
)
from modules.addon_store.download_watch import DownloadWatcher

# 浏览器里可接受的压缩包后缀（Blender 的 Install from Disk 只吃 zip，rar/7z 需先转)
_ARCHIVE_SUFFIXES = {".zip"}


class BrowserInstallDialog(QDialog):
    """在一个内嵌浏览器里完成「登录 → 点下载 → 自动安装」。"""

    zip_ready = pyqtSignal(str)  # 待安装压缩包的绝对路径

    def __init__(
        self,
        article_url: str,
        parent=None,
        addon_name: str = "",
        auth=None,  # noqa: ANN001  # 可选：BudecoAuthService，用于同步登录态
    ) -> None:
        super().__init__(parent)
        self._article_url = article_url or ""
        self._addon_name = addon_name or "该插件"
        self._auth = auth
        self._ff_synced = False
        self._view = None
        self._profile = None
        self._prev_ua: Optional[str] = None
        self._download = None
        self._download_path: Optional[Path] = None
        self._watcher = None
        self.zip_path: Optional[str] = None
        self._out_dir: Optional[Path] = None
        self._login_checked = False
        # 站内登录页带 redirect_to，登录成功后自动回到插件页
        self._login_url: Optional[str] = None
        if self._article_url.startswith("http"):
            self._login_url = (
                "https://budeco.top/login?redirect_to=" + quote(self._article_url, safe=":/?=&")
            )

        self.setWindowTitle(f"安装 {self._addon_name} — 登录并下载")
        self.setMinimumSize(940, 720)
        self.setStyleSheet(f"background-color: {theme.Tokens.OVERLAY_BG};")
        self._build_ui()

        self._web_disabled = bool(os.environ.get("BH_DISABLE_WEBENGINE"))

        # 登录态优先复用 Firefox：成功就完全不需要内嵌浏览器（真机上创建
        # QWebEngineView 会 native crash，见 qq_login_dialog 顶部说明）。
        # 同步成功 → 纯 requests 抓下载链并直接下载安装；失败才按需建内嵌浏览器。
        if not self._web_disabled:
            self._try_firefox_sync(initial=True)
        if self._ff_synced and self._auth is not None:
            self._status.setText(
                "已同步 Firefox 登录态，正在解析下载链接…（无需打开内嵌浏览器）"
            )
            self._placeholder.setText(
                "已从 Firefox 复用 BudecoTop 登录态，正在后台获取下载链接…"
            )
            QTimer.singleShot(0, self._resolve_and_download)
            self._web_started = True  # 不再创建内嵌浏览器
        else:
            # 内嵌浏览器**不在构造期创建**：PyInstaller 打包环境下此时引擎尚未完成
            # 初始化，真机实测会在 Qt6Core.dll 里 fail-fast（0xc0000409）直接把整个
            # 应用带走（2026-10-17 13:41/13:42、2026-10-18 12:47）。改为窗口显示后
            # （事件循环已跑起来）再创建，把崩溃风险隔离在可选路径上。
            self._web_started = False

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._web_started:
            return
        self._web_started = True
        if self._web_disabled:
            self._show_fallback("已跳过内嵌浏览器（BH_DISABLE_WEBENGINE 已设置）")
        else:
            self._init_webengine()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("在下面完成登录与下载，下载完成后会自动装进 Blender")
        title.setStyleSheet(
            f"color: {theme.Tokens.ON_CARD}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(title)

        self._status = QLabel(
            "步骤：① 点页面右上角「登录」(支持 QQ) ② 回到文章点「立即下载」"
            " ③ 若是网盘链接，登录网盘并输入提取码后点下载"
        )
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 12px;"
        )
        layout.addWidget(self._status)

        # 地址显示（只读，仅用于判断当前在哪一步）
        addr_row = QHBoxLayout()
        self._addr = QLineEdit()
        self._addr.setReadOnly(True)
        self._addr.setFixedHeight(26)
        self._addr.setStyleSheet(
            f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 11px;"
            f" background-color: {theme.Tokens.OVERLAY_BG_ALT};"
            " border: 1px solid rgba(255,255,255,0.08); border-radius: 4px;"
            " padding: 0 6px;"
        )
        self._reload_btn = QPushButton("刷新")
        self._reload_btn.setObjectName("secondaryButton")
        self._reload_btn.setFixedHeight(26)
        self._reload_btn.setFixedWidth(56)
        self._reload_btn.clicked.connect(self._on_reload)  # pyright: ignore[reportUnknownMemberType]
        addr_row.addWidget(self._addr, 1)
        addr_row.addWidget(self._reload_btn)
        layout.addLayout(addr_row)

        # 浏览器占位容器（WebEngine 可用时被替换）
        self._placeholder = QLabel(
            "（当前环境没有内嵌浏览器，请用下方按钮手动选插件压缩包）"
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 12px;"
        )
        layout.addWidget(self._placeholder, 1)
        self._web_slot = self._placeholder

        # 下载进度
        self._progress = QProgressBar()
        self._progress.setFixedHeight(8)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        layout.addWidget(self._progress)

        # 按钮行
        btn_row = QHBoxLayout()
        self._ff_btn = QPushButton("从 Firefox 同步登录")
        self._ff_btn.setObjectName("secondaryButton")
        self._ff_btn.setFixedHeight(32)
        self._ff_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ff_btn.clicked.connect(self._on_firefox_sync)  # pyright: ignore[reportUnknownMemberType]

        # 内嵌浏览器按需启动（真机上创建 QWebEngineView 可能 native crash）
        self._qr_btn = QPushButton("QQ 扫码登录")
        self._qr_btn.setObjectName("secondaryButton")
        self._qr_btn.setFixedHeight(32)
        self._qr_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._qr_btn.setToolTip("启动内嵌浏览器扫码登录（部分环境无法启动）")
        self._qr_btn.clicked.connect(self._on_start_webengine)  # pyright: ignore[reportUnknownMemberType]

        self._ext_btn = QPushButton("外部浏览器打开")
        self._ext_btn.setObjectName("secondaryButton")
        self._ext_btn.setFixedHeight(32)
        self._ext_btn.clicked.connect(self._open_external)  # pyright: ignore[reportUnknownMemberType]

        self._pick_btn = QPushButton("手动选择 zip 文件")
        self._pick_btn.setObjectName("secondaryButton")
        self._pick_btn.setFixedHeight(32)
        self._pick_btn.clicked.connect(self._on_pick_zip)  # pyright: ignore[reportUnknownMemberType]

        self._dir_btn = QPushButton("选择下载目录")
        self._dir_btn.setObjectName("secondaryButton")
        self._dir_btn.setFixedHeight(32)
        self._dir_btn.setToolTip("告诉 HUB 你的浏览器把文件下到哪里（默认监控「下载」和「桌面」）")
        self._dir_btn.clicked.connect(self._on_pick_watch_dir)  # pyright: ignore[reportUnknownMemberType]

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setFixedHeight(32)
        self._cancel_btn.clicked.connect(self.reject)  # pyright: ignore[reportUnknownMemberType]

        btn_row.addWidget(self._ff_btn)
        btn_row.addWidget(self._qr_btn)
        btn_row.addWidget(self._ext_btn)
        btn_row.addWidget(self._dir_btn)
        btn_row.addWidget(self._pick_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    # ── 下载暂存目录 ──────────────────────────────────────────────────────

    def _ensure_out_dir(self) -> Path:
        if self._out_dir is not None:
            return self._out_dir
        base = Path(os.environ.get("TEMP") or os.path.expanduser("~")) / "BlenderHub" / "downloads"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = Path.home() / "BlenderHub_downloads"
            base.mkdir(parents=True, exist_ok=True)
        self._out_dir = base
        return base

    # ── 内嵌浏览器 ────────────────────────────────────────────────────────

    # ── 内嵌浏览器按需启动 ────────────────────────────────────────────────

    def _on_start_webengine(self) -> None:
        """用户主动点「QQ 扫码登录」时才创建 QWebEngineView。"""
        if self._web_started:
            return
        if self._web_disabled:
            self._show_fallback("已跳过内嵌浏览器（BH_DISABLE_WEBENGINE 已设置）")
            return
        self._qr_btn.setEnabled(False)
        self._status.setText("正在启动内嵌浏览器…")
        try:
            self._init_webengine()
        except Exception as e:  # noqa: BLE001
            self._log("error", f"WebEngine 启动异常: {e}")
            self._show_fallback(f"内嵌浏览器启动失败：{e}")
        finally:
            self._qr_btn.setEnabled(True)

    def _init_webengine(self) -> None:
        try:
            from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
            from PyQt6.QtWebEngineWidgets import QWebEngineView
        except Exception as e:  # noqa: BLE001
            # 把真实异常原文留给界面/日志：曾被压成"当前环境没有内嵌浏览器"，
            # 把排查方向错误地带到了"换个浏览器内核"。
            self._log("error", f"WebEngine 导入失败: {e}")
            self._show_fallback(f"内嵌浏览器不可用：{e}")
            return

        # 同窗口 Page：erphpdown / 网盘分享页大量使用 window.open，
        # 默认会开一个我们没有句柄的新页面，用户点了像"没反应"，下载也拦不到。
        class _Page(QWebEnginePage):  # type: ignore[misc, valid-type]
            def createWindow(self, _type):  # noqa: N802, ANN001
                return self

        # 必须给 parent：无 parent 的 QWebEngineView 在 replaceWidget 之前是
        # **顶层窗口**，一旦被 show 就是一个空白小窗（标题栏退化成 exe 产品名）
        self._view = QWebEngineView(self)
        try:
            self._view.setPage(_Page(self._view))  # pyright: ignore[reportUnknownMemberType]
        except Exception:  # noqa: BLE001
            pass  # 极少数环境不允许替换 page，退化为默认行为

        idx = self.layout().indexOf(self._web_slot)
        self.layout().replaceWidget(self._web_slot, self._view, 1)
        self._placeholder.deleteLater()
        self._web_slot = self._view

        profile = QWebEngineProfile.defaultProfile()
        self._profile = profile

        # 百度网盘分享页会按 UA 给"请使用最新版浏览器"，用桌面 Chrome UA 规避。
        # 关闭时恢复，避免影响其它内嵌页面（如 QQ 登录）。
        try:
            self._prev_ua = profile.httpUserAgent()
            profile.setHttpUserAgent(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        except Exception:  # noqa: BLE001
            self._prev_ua = None

        # 拦截下载：这是整个方案的核心
        try:
            profile.downloadRequested.connect(self._on_download)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"无法监听下载请求（{e}），请手动选择 zip。")

        try:
            self._view.urlChanged.connect(self._on_url_changed)  # type: ignore[attr-defined]
            self._view.loadFinished.connect(self._on_load_finished)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

        # 先复用 Firefox 已有的 budeco 登录态：灌进 profile 后页面打开即已登录，
        # 用户可以直接点「立即下载」，不必在内嵌窗口里再扫一次码。
        self._try_firefox_sync(initial=True)

        self._view.load(QUrl(self._article_url))

    # ── 网盘：浏览器下载 + 自动拾取 ────────────────────────────────────────

    def _start_pan_wait(self, pan_url: str, code: str = "") -> None:
        """打开系统浏览器下载，同时后台守望下载目录，命中 zip 自动安装。"""
        # 提取码先进剪贴板：用户在网盘页直接 Ctrl+V 即可，不用来回看窗口
        if code:
            try:
                from PyQt6.QtWidgets import QApplication

                QApplication.clipboard().setText(code)
            except Exception:  # noqa: BLE001
                pass

        self._watcher = DownloadWatcher(parent=self)
        self._watcher.found.connect(self._on_watched_zip)
        self._watcher.timed_out.connect(self._on_watch_timeout)
        self._watcher.waiting.connect(self._on_watch_tick)
        self._watcher.start()

        dirs_text = "、".join(str(p) for p in self._watcher.watch_dirs[:2]) or "下载目录"
        self._placeholder.setText(
            "等待浏览器下载完成…\n\n"
            f"监控目录：{dirs_text}\n"
            "下载位置不同可点「选择下载目录」。"
        )
        self._status.setText(
            f"已在系统浏览器打开网盘页面"
            + (f"，提取码 {code} 已复制到剪贴板" if code else "")
            + "。\n请在浏览器里点「下载」，下完后这里会自动安装，无需切回本窗口。"
        )
        self._open_external(pan_url)
        self._log("info", f"pan wait started: {pan_url} code={code or '-'} "
                          f"dirs={[str(d) for d in self._watcher.watch_dirs]}")

    def _on_watched_zip(self, path: str) -> None:
        self._log("info", f"watched zip picked: {path}")
        self._placeholder.setText(f"已拾取下载文件：{Path(path).name}")
        self._finish_download_file(Path(path))

    def _on_watch_timeout(self) -> None:
        self._status.setText(
            "等待超时（15 分钟）仍未在下载目录发现新的 zip。\n"
            "可点「选择下载目录」重新监控，或「手动选择 zip 文件」直接安装。"
        )

    def _on_watch_tick(self, seconds: int) -> None:
        self._status.setText(
            f"等待浏览器下载… 已等待 {seconds // 60:d} 分 {seconds % 60:02d} 秒\n"
            "在浏览器里点「下载」即可，下完自动安装；也可点「手动选择 zip 文件」。"
        )

    def _on_pick_watch_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择浏览器的下载目录")
        if chosen and self._watcher is not None:
            self._watcher.set_dirs([Path(chosen)])
            self._status.setText(f"已切换监控目录：{chosen}\n正在等待下载完成…")

    def _stop_watcher(self) -> None:
        if self._watcher is not None:
            try:
                self._watcher.stop()
            except Exception:  # noqa: BLE001
                pass

    # ── Firefox 登录态同步 ────────────────────────────────────────────────

    def _resolve_and_download(self) -> None:
        """Firefox 已登录：纯 requests 抓下载链并直接下载（不碰内嵌浏览器）。

        链路见 ``BudecoAuthService.resolve_download``：
        文章页 → erphpdown download.php → 最终 zip 直链（或百度网盘分享页）。
        """
        if self._auth is None:
            self._show_fallback("缺少登录服务，无法解析下载链接。")
            return
        try:
            target = self._auth.resolve_download(self._article_url)
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"解析下载链接失败：{e}")
            return

        if not target.ok:
            self._status.setText(
                f"未能解析到下载链接：{target.note}\n"
                "可点「QQ 扫码登录」用内嵌浏览器操作，或「外部浏览器打开」后手动选择 zip。"
            )
            self._log("warning", f"resolve_download failed: {target.note}")
            return

        if not target.is_direct_zip:
            # 网盘：requests 拿不到真实文件。改走「浏览器下载 + 自动拾取」——
            # 复制提取码、打开系统浏览器（用户已登录网盘），后台盯着下载目录，
            # 出现新写完的 zip 就自动接管安装，用户不用切回本窗口点按钮。
            self._start_pan_wait(target.url, target.code)
            return

        out_dir = self._ensure_out_dir()
        safe = "".join(ch for ch in (target.url.rsplit("/", 1)[-1] or "addon.zip")
                       if ch not in '\\/:*?"<>|').strip() or "addon.zip"
        if not safe.lower().endswith(".zip"):
            safe += ".zip"
        dest = out_dir / safe
        self._progress.setRange(0, 0)  # 忙碌指示（requests 无细粒度进度）
        self._progress.show()
        self._status.setText(f"正在下载 {safe} …")

        ok = self._auth.download_to(target.url, dest)
        self._progress.hide()
        self._progress.setRange(0, 100)
        if ok and dest.exists():
            self._finish_download_file(dest)
        else:
            self._status.setText(
                f"下载失败（{dest.name}）。\n可点「QQ 扫码登录」改用内嵌浏览器，"
                "或「外部浏览器打开」后点「手动选择 zip 文件」。"
            )

    def _finish_download_file(self, path: Path) -> None:
        if path.suffix.lower() not in _ARCHIVE_SUFFIXES:
            self._status.setText(
                f"下载的文件不是 zip（{path.name}）。\n已保存到：{path}"
            )
            return
        self._set_zip(str(path))

    def _try_firefox_sync(self, initial: bool = False) -> bool:
        """把 Firefox 的 budeco 登录 cookie 灌进内嵌浏览器 + 同步给 auth。"""
        try:
            res = collect_firefox_cookies()
        except Exception as e:  # noqa: BLE001
            self._log("warning", f"firefox cookie 读取异常: {e}")
            return False

        if not res.ok:
            if not initial:
                self._status.setText(
                    f"{res.message}\n（也可在内嵌页面点右上角登录，用 QQ 扫码）"
                )
            return False

        # 仅在内嵌浏览器已启动时才灌 profile：defaultProfile() 在打包环境会触发
        # 引擎初始化，真机上即 native crash（见 qq_login_dialog 顶部说明）。
        injected = inject_into_webengine(res.cookies) if self._view is not None else False
        if self._auth is not None:
            try:
                self._auth.ingest_cookies(res.cookies)
            except Exception as e:  # noqa: BLE001
                self._log("warning", f"ingest firefox cookies failed: {e}")
        self._ff_synced = True
        name = res.profile.name if res.profile is not None else "?"
        self._log("info", f"firefox login synced from {name}, injected={injected}")
        self._status.setText(f"已从 Firefox（{name}）同步 BudecoTop 登录态。")
        return True

    def _on_firefox_sync(self) -> None:
        """按钮：立即同步 Firefox 登录态，然后按当前模式继续。"""
        if not self._try_firefox_sync():
            self._status.setText(
                f"{collect_firefox_cookies().message}\n"
                "可在 Firefox 打开 https://budeco.top/ 登录后再点此按钮，"
                "或用「QQ 扫码登录」。"
            )
            return
        if self._view is not None:
            try:
                self._view.reload()
            except Exception:  # noqa: BLE001
                pass
            self._login_checked = False  # 允许刷新后重新探测登录态
        else:
            # 没有内嵌浏览器：同步成功即可直接走 requests 抓链下载
            self._status.setText("已同步 Firefox 登录态，正在解析下载链接…")
            QTimer.singleShot(0, self._resolve_and_download)

    def _log(self, level: str, msg: str) -> None:
        log = getattr(self._auth, "_log", None) if self._auth is not None else None
        if log is None:
            return
        try:
            getattr(log, level)(msg, module="budeco_browser")
        except Exception:  # noqa: BLE001
            pass

    def _on_url_changed(self, url: QUrl) -> None:
        try:
            self._addr.setText(url.toString())
            self._addr.setCursorPosition(0)
        except Exception:  # noqa: BLE001
            pass

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self._status.setText("页面加载失败，可点「刷新」重试，或用「外部浏览器打开」。")
            return
        # 首次加载后探测是否未登录：未登录就直接送去登录页（登录后会 redirect 回来），
        # 省掉用户"在页面里找登录按钮"。只做一次，避免误判导致来回跳转。
        if self._login_checked or self._view is None:
            return
        self._login_checked = True
        if not self._login_url:
            return
        try:
            self._view.page().runJavaScript(
                "!!(document.querySelector('.nav-login.no')"
                " || document.querySelector('a.down.signin-loader'))",
                self._maybe_go_login,
            )
        except Exception:  # noqa: BLE001
            pass

    def _maybe_go_login(self, need_login) -> None:  # noqa: ANN001
        if not need_login or self._view is None:
            return
        self._status.setText("需要先登录（支持 QQ 扫码），登录后会自动回到插件页。")
        try:
            self._view.load(QUrl(self._login_url))
        except Exception:  # noqa: BLE001
            pass

    def _on_reload(self) -> None:
        if self._view is not None:
            try:
                self._view.reload()
            except Exception:  # noqa: BLE001
                pass
        else:
            self._open_external()

    # ── 下载拦截 ──────────────────────────────────────────────────────────

    def _on_download(self, download) -> None:  # noqa: ANN001
        """downloadRequested：接管下载，落盘到我们的目录。"""
        try:
            suggested = download.downloadFileName() or "addon.zip"
        except Exception:  # noqa: BLE001
            suggested = "addon.zip"

        try:
            out_dir = self._ensure_out_dir()
            # 同名覆盖：每次安装用独立名字，避免旧文件占位导致新下载失败
            safe = "".join(ch for ch in suggested if ch not in '\\/:*?"<>|').strip() or "addon.zip"
            download.setDownloadDirectory(str(out_dir))
            download.setDownloadFileName(safe)
            download.accept()
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"接管下载失败（{e}），请手动下载后选择 zip。")
            return

        self._download = download
        self._download_path = out_dir / safe
        self._progress.setValue(0)
        self._progress.show()
        self._status.setText(f"正在下载 {safe} …下载完成后会自动安装，请稍等。")

        try:
            download.receivedBytesChanged.connect(self._on_progress)  # type: ignore[attr-defined]
            download.stateChanged.connect(self._on_download_state)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _on_progress(self) -> None:
        if self._download is None:
            return
        try:
            total = self._download.totalBytes()
            got = self._download.receivedBytes()
        except Exception:  # noqa: BLE001
            return
        if total and total > 0:
            self._progress.setValue(int(got * 100 / total))
        else:
            self._progress.setValue(0)

    def _on_download_state(self, state) -> None:  # noqa: ANN001
        try:
            from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest

            completed = QWebEngineDownloadRequest.DownloadState.DownloadCompleted
            cancelled = QWebEngineDownloadRequest.DownloadState.DownloadCancelled
            interrupted = QWebEngineDownloadRequest.DownloadState.DownloadInterrupted
        except Exception:  # noqa: BLE001
            return

        if state == completed:
            self._progress.setValue(100)
            # 文件句柄由浏览器在完成回调后释放，稍等再交给安装流程
            QTimer.singleShot(400, self._finish_download)
        elif state in (cancelled, interrupted):
            self._progress.hide()
            self._status.setText(
                "下载被中断（常见原因：网盘要求安装客户端 / 文件较大需登录网盘）。\n"
                "可点「手动选择 zip 文件」直接选中你下载好的压缩包。"
            )

    def _finish_download(self) -> None:
        path = self._download_path
        if path is None or not path.exists():
            self._status.setText("下载完成但找不到文件，请用「手动选择 zip 文件」。")
            return
        if path.suffix.lower() not in _ARCHIVE_SUFFIXES:
            self._status.setText(
                f"下载的文件不是 zip（{path.name}）。\n"
                f"已保存到：{path}\n"
                "请解压后把里的 zip 选进来（或直接用 Blender 的 Install from Disk）。"
            )
            return
        self._set_zip(str(path))

    # ── 收尾 ──────────────────────────────────────────────────────────────

    def _on_pick_zip(self) -> None:
        start = str(self._ensure_out_dir())
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择插件压缩包", start, "Blender 插件包 (*.zip)"
        )
        if chosen:
            self._set_zip(chosen)

    def _set_zip(self, path: str) -> None:
        self.zip_path = path
        self._status.setText(f"已获取压缩包：{Path(path).name}，正在安装…")
        try:
            self.zip_ready.emit(path)
        except Exception:  # noqa: BLE001
            pass
        QTimer.singleShot(200, self.accept)

    def _open_external(self, url: str = "") -> None:
        webbrowser.open(url or self._article_url)
        self._status.setText(
            "已在外部浏览器打开。下载完成后回到这里点「手动选择 zip 文件」即可自动安装。"
        )

    def _show_fallback(self, reason: str = "") -> None:
        self._ext_btn.setEnabled(True)
        if reason:
            self._placeholder.setText(reason)
        self._qr_btn.setEnabled(False)
        self._status.setText(
            "内嵌浏览器不可用，无法在窗口内点下载。\n"
            "可点「从 Firefox 同步登录」直接带登录态解析下载链，或用「外部浏览器打开」"
            "下载后点「手动选择 zip 文件」安装。"
        )

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        # 停止下载目录守望，避免对话框关掉后还在轮询
        self._stop_watcher()
        # 断开全局 profile 的信号，避免 dialog 关闭后仍被回调（野指针崩溃）
        try:
            if self._profile is not None:
                self._profile.downloadRequested.disconnect(self._on_download)
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._profile is not None and self._prev_ua is not None:
                self._profile.setHttpUserAgent(self._prev_ua)
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
