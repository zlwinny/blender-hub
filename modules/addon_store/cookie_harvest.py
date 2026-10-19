"""从 QWebEngineProfile 收割 budeco.top cookie（Qt6 专用）。

为什么需要这个文件：
PyQt6 的 :class:`QWebEngineCookieStore` **没有** ``cookieList()`` 方法——它既不是
方法也不是信号，调用直接 ``AttributeError``。Qt5 时代的
``cookieStore.cookieList.connect(...)`` 写法在 PyQt6 下必然抛异常，等于登录检测
从未生效。可用 API 只有：

* 信号 ``cookieAdded`` / ``cookieRemoved``
* 方法 ``loadAllCookies()`` / ``setCookie()`` / ``deleteCookie()`` ...

Qt 6.2 起 ``loadAllCookies()`` 是异步的，并会为每个已存 cookie 补发一次
``cookieAdded``。所以正确读法是：先连信号，再调 ``loadAllCookies()`` 触发补发，
本类负责累积、过滤域、去重，并在超时后一次性交出结果。
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

# Qt6 的 cookieAdded 有「单个 cookie」与「cookie 列表」两个重载，
# PyQt 会按实际发射的类型投递，这里统一归一成列表。
def _as_list(payload) -> list:  # noqa: ANN001
    if payload is None:
        return []
    if hasattr(payload, "domain"):  # QNetworkCookie 实例
        return [payload]
    try:
        return list(payload)
    except TypeError:
        return [payload]


class BudecoCookieHarvester(QObject):
    """一次性收割 profile 里的 budeco cookie。"""

    found = pyqtSignal(dict)  # {name: value}

    def __init__(
        self,
        parent=None,
        domain: str = "budeco.top",
        timeout_ms: int = 2500,
        on_done: Optional[Callable[[Dict[str, str]], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._domain = domain
        self._cookies: Dict[str, str] = {}
        self._done = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish)

        try:
            from PyQt6.QtWebEngineCore import QWebEngineProfile

            self._store = QWebEngineProfile.defaultProfile().cookieStore()
            self._store.cookieAdded.connect(self._on_added)  # type: ignore[attr-defined]
            self._store.loadAllCookies()  # 触发对已存 cookie 的补发
        except Exception:  # noqa: BLE001
            self._store = None

        if on_done is not None:
            self.found.connect(on_done)
        self._timer.start(timeout_ms)

    @property
    def cookies(self) -> Dict[str, str]:
        return dict(self._cookies)

    def _on_added(self, *args) -> None:  # noqa: ANN001
        for c in _as_list(args[0] if len(args) == 1 else args):
            try:
                dom = bytes(c.domain()).decode("utf-8", "replace")
                if self._domain not in dom:
                    continue
                name = bytes(c.name()).decode("utf-8", "replace")
                value = bytes(c.value()).decode("utf-8", "replace")
                if name:
                    self._cookies[name] = value
            except Exception:  # noqa: BLE001
                continue
        # 注意：这里刻意**不**因命中登录 cookie 就提前收工。
        # Qt 补发是逐条 cookie 到达的，一收到 wordpress_logged_in_* 就 finish，
        # 后面陆续补发的 wordpress_sec_* / 站点 session 会被丢掉——而下载链解析
        # 恰恰依赖 sec cookie。统一等超时（默认 2.5s）一次性交出完整集合。

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        try:
            if self._store is not None:
                self._store.cookieAdded.disconnect(self._on_added)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        try:
            self.found.emit(dict(self._cookies))
        except Exception:  # noqa: BLE001
            pass