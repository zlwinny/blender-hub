"""BudecoTop 登录对话框 —— 两条登录路径，谁先通用谁。

路径 A（首选，无需任何浏览器内嵌能力）：**从本机 Firefox 同步登录态**。
Firefox 的 cookie 明文存在 ``cookies.sqlite``，只读复制即可解析。用户在
Firefox 里登录过 budeco.top，这里一键就能复用，比扫码快得多，也不受
QtWebEngine 是否可用影响。

路径 B（兜底）：内嵌 QWebEngine 完成 QQ 互联 OAuth 扫码。
budeco 的 QQ 登录是标准 Web 重定向 OAuth，重定向发生在**完成授权的浏览器**
里并种下登录 Cookie。只有把授权页加载进应用自己的 QWebEngineView，Cookie
才落在可控的 profile 内，才能取回登录态（纯 requests 轮询拿不到）。

⚠ 路径 B 曾经 100% 失败，根因不在本文件：``QApplication`` 在 main.py 里先被
创建，而 QtWebEngineWidgets 是运行期才惰性 import 的，Qt 会直接抛
``QtWebEngineWidgets must be imported or Qt.AA_ShareOpenGLContexts must be set
before a QCoreApplication instance is created``。现在 main.py 在创建 app 之前
设置了 ``AA_ShareOpenGLContexts``，这条限制已解除；若仍失败，本文件会把真实
异常原文显示在界面上（不再只给一句"当前环境不支持"，避免把问题指错方向）。
"""

from __future__ import annotations

import os
import webbrowser
from typing import Dict, List, Optional

from PyQt6.QtCore import QTimer, QUrl, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core import theme  # pyright: ignore[reportImplicitRelativeImport]
from core.services.browser_cookie_import import (  # pyright: ignore[reportImplicitRelativeImport]
    FirefoxCookieResult,
    collect_firefox_cookies,
    inject_into_webengine,
)
from modules.addon_store.cookie_harvest import (  # noqa: E402  # pyright: ignore[reportImplicitRelativeImport]
    BudecoCookieHarvester,
)


class QQLoginDialog(QDialog):
    """BudecoTop 登录弹窗（Firefox 同步优先，内嵌浏览器扫码兜底）。"""

    def __init__(self, auth, parent=None) -> None:
        super().__init__(parent)
        self._auth = auth
        self._cookies: Dict[str, str] = {}
        self._done = False
        self._view = None
        self._timer = None
        self._ff: Optional[FirefoxCookieResult] = None
        self._store = None
        self._harvesters: List[BudecoCookieHarvester] = []
        self._harvest_running = False
        self.setWindowTitle("登录 BudecoTop")
        self.setMinimumSize(620, 700)
        self.setStyleSheet(f"background-color: {theme.Tokens.OVERLAY_BG};")
        self._build_ui()
        # 先看 Firefox 有没有现成登录态：命中就把按钮做成主色，用户一眼看到最优解
        self._probe_firefox()
        # 内嵌浏览器**不再自动创建**：
        # 2026-10-18 真机实测（提前 import + AA_ShareOpenGLContexts + 禁用沙箱全齐），
        # 只要构造 QWebEngineView 就是 native crash（0xc0000409），整个进程被拖死，
        # 连"点登录按钮"这个动作本身都活不下来。因此默认只保留 Firefox 同步主路径，
        # 扫码登录改为用户主动点「QQ 扫码登录」按钮时才创建（需要时才冒险）。
        self._web_started = False
        self._web_disabled = bool(os.environ.get("BH_DISABLE_WEBENGINE"))
        if not (self._ff and self._ff.ok):
            self._status.setText(
                "未检测到 Firefox 登录态。\n"
                "方式一：在 Firefox 打开 https://budeco.top/ 登录后点「从 Firefox 同步登录」；\n"
                "方式二：点「QQ 扫码登录」用内嵌浏览器扫码（部分环境会失败）。"
            )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 窗口显示后不再自动建 WebEngine；仅把占位文案更新为可操作提示
        if self._placeholder is not None:
            if self._ff and self._ff.ok:
                self._placeholder.setText(
                    "已从 Firefox 取到登录态，点「从 Firefox 同步登录」即可，无需打开浏览器。"
                )
            else:
                self._placeholder.setText(
                    "内嵌浏览器未启用。\n需要扫码登录时点下方「QQ 扫码登录」按钮手动启动。"
                )

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        title = QLabel("登录 BudecoTop 以下载需登录的插件")
        title.setStyleSheet(f"color: {theme.Tokens.ON_CARD}; font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        self._status = QLabel("正在检测本机 Firefox 的登录状态…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 12px;")
        layout.addWidget(self._status)

        # 占位容器：WebEngine 可用时替换为 QWebEngineView
        self._placeholder = QLabel(
            "（内嵌浏览器未启用，请用下方「从 Firefox 同步登录」）"
        )
        self._placeholder.setWordWrap(True)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)  # type: ignore[attr-defined]
        self._placeholder.setStyleSheet(f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 12px;")
        layout.addWidget(self._placeholder, 1)
        self._web_slot = self._placeholder

        # 按钮行
        btn_row = QHBoxLayout()
        self._ff_btn = QPushButton("从 Firefox 同步登录")
        self._ff_btn.setObjectName("secondaryButton")
        self._ff_btn.setFixedHeight(32)
        self._ff_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # type: ignore[attr-defined]
        self._ff_btn.clicked.connect(self._on_firefox_sync)  # pyright: ignore[reportUnknownMemberType]

        # 扫码登录（内嵌浏览器）：用户主动点才创建 QWebEngineView
        self._qr_btn = QPushButton("QQ 扫码登录")
        self._qr_btn.setObjectName("secondaryButton")
        self._qr_btn.setFixedHeight(32)
        self._qr_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # type: ignore[attr-defined]
        self._qr_btn.setToolTip("打开内嵌浏览器扫码（少数环境可能无法启动内嵌浏览器）")
        self._qr_btn.clicked.connect(self._on_start_webengine)  # pyright: ignore[reportUnknownMemberType]

        self._ext_btn = QPushButton("外部浏览器打开")
        self._ext_btn.setObjectName("secondaryButton")
        self._ext_btn.setFixedHeight(32)
        self._ext_btn.clicked.connect(self._open_external)  # pyright: ignore[reportUnknownMemberType]

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setFixedHeight(32)
        self._cancel_btn.clicked.connect(self.reject)  # pyright: ignore[reportUnknownMemberType]

        btn_row.addWidget(self._ff_btn)
        btn_row.addWidget(self._qr_btn)
        btn_row.addWidget(self._ext_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

    # ── 路径 A：从 Firefox 同步 ────────────────────────────────────────────

    def _probe_firefox(self) -> None:
        """构造时轻量探测一次，仅用于把按钮做成主色 / 提前提示。"""
        try:
            self._ff = collect_firefox_cookies()
        except Exception as e:  # noqa: BLE001
            self._ff = None
            self._status.setText(f"检测 Firefox 登录态失败：{e}")
            return
        if self._ff and self._ff.ok:
            self._ff_btn.setObjectName("primaryButton")
            self._ff_btn.setStyleSheet("")  # 让新 objectName 的 QSS 生效
            self._ff_btn.setText("从 Firefox 同步登录（已检测到登录）")
            self._status.setText(
                "检测到 Firefox 已登录 BudecoTop，点下方按钮即可直接同步，无需扫码。"
            )
        elif self._ff:
            self._status.setText(self._ff.message)
        self._log("debug", f"firefox probe: {self._ff.message if self._ff else 'n/a'}")

    def _apply_cookies(self, cookies: Dict[str, str], extra: str = "") -> None:
        self._cookies = dict(cookies)
        if self._auth is not None:
            self._auth.ingest_cookies(self._cookies)
        # 只在用户已主动启动内嵌浏览器时才灌 profile：
        # `QWebEngineProfile.defaultProfile()` 在打包环境会触发引擎初始化，
        # 真机上这一步就是 native crash（0xc0000409）——Firefox 同步主路径
        # 必须完全不碰 WebEngine，否则"点登录就崩"依然复现。
        if self._view is not None:
            inject_into_webengine(self._cookies)
        self._finish_login(skip_ingest=True, status=f"登录成功！{extra}正在返回…")

    def _on_firefox_sync(self) -> None:
        res = self._ff
        if res is None or not res.ok:
            res = collect_firefox_cookies()
            self._ff = res
        if not res.ok:
            self._status.setText(
                f"{res.message}\n可在 Firefox 打开 https://budeco.top/ 登录后再点此按钮，"
                "或用下方内嵌浏览器扫码。"
            )
            self._log("warning", f"firefox sync failed: {res.message}")
            return
        self._log("info", f"firefox sync ok: {res.profile} ({len(res.cookies)} cookies)")
        self._apply_cookies(res.cookies, extra=f"（来源：Firefox {res.profile.name}）")

    # ── 路径 B：内嵌浏览器扫码（按需创建）──────────────────────────────────

    def _on_start_webengine(self) -> None:
        """用户主动点「QQ 扫码登录」时才创建 QWebEngineView。"""
        if self._web_started:
            self._status.setText("内嵌浏览器已启动，请扫描页面二维码。")
            return
        if self._web_disabled:
            self._show_fallback("已跳过内嵌浏览器（BH_DISABLE_WEBENGINE 已设置）")
            return
        self._status.setText("正在启动内嵌浏览器…")
        self._qr_btn.setEnabled(False)
        try:
            self._init_webengine()
        except Exception as e:  # noqa: BLE001
            # 内嵌浏览器在打包环境下可能 native crash（无法在进程内捕获），
            # 这里至少保证 Python 层异常被记录下来，并给出替代路径。
            self._log("error", f"WebEngine 启动异常: {e}")
            self._show_fallback(f"内嵌浏览器启动失败：{e}")
        finally:
            self._qr_btn.setEnabled(True)

    def _init_webengine(self) -> None:
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWebEngineCore import QWebEngineProfile
        except Exception as e:  # noqa: BLE001
            # 把真实异常原文暴露出来：这句话此前被压成"当前环境不支持内嵌浏览器"，
            # 直接把排查方向带偏到"该换个浏览器内核"。
            self._log("error", f"WebEngine 导入失败: {e}")
            self._show_fallback(f"内嵌浏览器不可用：{e}")
            return

        self._web_started = True
        # 必须给 parent：无 parent 的 QWebEngineView 是**顶层窗口**，被 show 时
        # 就是一个空白小窗（标题栏退化成 exe 产品名，看起来像"又弹了个空窗"）
        self._view = QWebEngineView(self)
        idx = self.layout().indexOf(self._web_slot)
        self.layout().replaceWidget(self._web_slot, self._view, 1)
        self._placeholder.deleteLater()
        self._web_slot = self._view

        profile = QWebEngineProfile.defaultProfile()
        # PyQt6 的 QWebEngineCookieStore 没有 cookieList()（既非方法也非信号，
        # 调用直接 AttributeError）。只能连 cookieAdded + loadAllCookies() 触发
        # 对已存 cookie 的补发，由 BudecoCookieHarvester 负责累积与过滤。
        self._store = profile.cookieStore()

        self._view.loadFinished.connect(self._on_load_finished)  # type: ignore[attr-defined]
        self._view.urlChanged.connect(self._on_url_changed)  # type: ignore[attr-defined]

        # 轮询检测登录 Cookie（重定向 + 种 Cookie 可能跨多次导航，定时器最稳）
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._request_cookies)

        # Firefox 已有登录态：先灌进 profile，打开就是已登录，省掉扫码
        if self._ff is not None and self._ff.ok:
            inject_into_webengine(self._ff.cookies)
            self._view.load(QUrl("https://budeco.top/"))
        else:
            self._view.load(QUrl(self._auth.OAUTH_ENTRY))
        self._timer.start()
        self._log("info", "WebEngine 初始化成功，内嵌登录页已加载")

    def _request_cookies(self) -> None:
        """起一次收割（连 cookieAdded + loadAllCookies），结果并入 self._cookies。"""
        if self._harvest_running:
            return
        self._harvest_running = True
        harvester = BudecoCookieHarvester(self, timeout_ms=1200)
        harvester.found.connect(self._on_harvested)
        self._harvesters.append(harvester)  # 持引用，防被 GC 掉信号源
        QTimer.singleShot(1500, self._release_harvester)

    def _release_harvester(self) -> None:
        self._harvest_running = False
        if len(self._harvesters) > 2:
            self._harvesters.pop(0)

    def _on_harvested(self, cookies: Dict[str, str]) -> None:
        if not cookies:
            return
        self._cookies.update(cookies)
        if any(n.startswith("wordpress_logged_in") for n in self._cookies):
            self._log("info", f"内嵌浏览器检测到登录 cookie（{len(cookies)} 个）")
            self._finish_login()

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._status.setText("请使用手机 QQ 扫描页面二维码完成登录")
        else:
            self._status.setText("登录页加载失败，可改用「从 Firefox 同步登录」或外部浏览器")

    def _on_url_changed(self, url: QUrl) -> None:
        if "budeco.top" in url.toString():
            self._request_cookies()

    def _finish_if_logged(self) -> None:
        if any(n.startswith("wordpress_logged_in") for n in self._cookies):
            self._finish_login()
        else:
            self._status.setText(
                "还没检测到登录 Cookie。请确认页面已显示已登录，或改用 Firefox 同步。"
            )

    def _finish_login(self, skip_ingest: bool = False, status: str = "登录成功！正在返回…") -> None:
        if self._done:
            return
        self._done = True
        if self._timer is not None:
            self._timer.stop()
        if not skip_ingest and self._cookies and self._auth is not None:
            self._auth.ingest_cookies(self._cookies)
        self._status.setText(status)
        QTimer.singleShot(400, self.accept)

    # ── 兜底 ──────────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str) -> None:
        log = getattr(self._auth, "_log", None) if self._auth is not None else None
        if log is None:
            return
        try:
            getattr(log, level)(msg, module="budeco_login")
        except Exception:  # noqa: BLE001
            pass

    def _open_external(self) -> None:
        webbrowser.open(self._auth.OAUTH_ENTRY if self._auth is not None else "https://budeco.top/")
        self._status.setText(
            "已在外部浏览器打开登录页。登录后回到这里点「从 Firefox 同步登录」"
            "（若用的是 Firefox）即可取回登录态。"
        )

    def _show_fallback(self, reason: str = "") -> None:
        if reason and self._placeholder is not None:
            self._placeholder.setText(reason)
            self._placeholder.setStyleSheet(
                f"color: {theme.Tokens.ON_CARD_MUTED}; font-size: 12px;"
            )
        self._qr_btn.setEnabled(False)
        if self._ff is not None and self._ff.ok:
            self._status.setText(
                "内嵌浏览器不可用，但已检测到 Firefox 登录态——"
                "点「从 Firefox 同步登录」即可完成，无需扫码。"
            )
        else:
            self._status.setText(
                "内嵌浏览器不可用，且 Firefox 里未检测到登录态。\n"
                "请在 Firefox 打开 https://budeco.top/ 登录后再点「从 Firefox 同步登录」。"
            )
