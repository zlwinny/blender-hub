"""BudecoCookieHarvester + PyQt6 CookieStore API 形态回归。

重点守住两个已踩过的坑：
1. Qt5 的 ``cookieStore.cookieList.connect(...)`` / ``cookieList()`` 在 PyQt6 里
   根本不存在（既非方法也非信号）→ 用假 store 验证 harvester 走的是
   ``cookieAdded`` + ``loadAllCookies()`` 正路。
2. cookieAdded 有「单个 cookie」和「列表」两个重载 → 都要能正确归一。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QEventLoop, QObject, QTimer, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)  # noqa: F841

import PyQt6.QtWebEngineCore as _wec  # noqa: E402

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


# ── 假 cookie / 假 store ──
class _C:
    def __init__(self, domain, name, value):
        self._d, self._n, self._v = domain.encode(), name.encode(), value.encode()

    def domain(self):
        return self._d

    def name(self):
        return self._n

    def value(self):
        return self._v


class _FakeStore(QObject):
    cookieAdded = pyqtSignal(object)

    def __init__(self, cookies, list_mode=False):
        super().__init__()
        self._cookies = cookies
        self._list_mode = list_mode
        self.load_calls = 0

    def loadAllCookies(self):
        self.load_calls += 1
        if self._list_mode:
            self.cookieAdded.emit(list(self._cookies))
        else:
            for c in self._cookies:
                self.cookieAdded.emit(c)


class _FakeProfile:
    store = None

    @staticmethod
    def defaultProfile():
        return _FakeProfile()

    def cookieStore(self):
        return _FakeProfile.store


def harvest(cookies, list_mode=False, timeout_ms=150):
    """跑一次收割，返回结果字典。"""
    _FakeProfile.store = _FakeStore(cookies, list_mode)
    _wec.QWebEngineProfile = _FakeProfile
    from modules.addon_store.cookie_harvest import BudecoCookieHarvester

    got = {}
    loop = QEventLoop()
    h = BudecoCookieHarvester(timeout_ms=timeout_ms)
    h.found.connect(lambda d: (got.update(d), loop.quit()))
    QTimer.singleShot(1500, loop.quit)
    loop.exec()
    _wec.QWebEngineProfile = _wec.__dict__.get("_orig_profile", _wec.QWebEngineProfile)
    return got, h


# ── 1. 单 cookie 重载 ──
got, h = harvest([
    _C(".budeco.top", "wordpress_logged_in_a", "TOKEN"),
    _C("budeco.top", "wordpress_sec_a", "SEC"),
    _C("example.com", "tracker", "NOPE"),
])
check("收集到登录 cookie", got.get("wordpress_logged_in_a") == "TOKEN")
check("收集到 sec cookie", got.get("wordpress_sec_a") == "SEC")
check("其它域被过滤", "tracker" not in got)
check("确实调用了 loadAllCookies", h._store is not None and h._store.load_calls >= 1)

# ── 2. 列表重载 ──
got2, _ = harvest([
    _C(".budeco.top", "wordpress_logged_in_b", "TOKEN2"),
    _C("other.org", "x", "y"),
], list_mode=True)
check("列表重载也能归一", got2.get("wordpress_logged_in_b") == "TOKEN2")
check("列表重载也过滤域名", "x" not in got2)

# ── 3. 空 profile ──
got3, _ = harvest([])
check("无 cookie 时返回空 dict", got3 == {})

# ── 4. 非 budeco 域的 cookie 不触发提前收工 ──
got4, h4 = harvest([_C("example.com", "tracker", "NOPE")], timeout_ms=80)
check("只有无关 cookie 时结果为空", got4 == {})
check("收割器正常结束", h4._done)

# ── 5. 静态检查：源码里不得再出现 Qt5 的 cookieList 写法 ──
# 注意这里要用项目根（parents[2]），不是脚本所在目录
root = Path(__file__).resolve().parents[2]
for rel in ("modules/addon_store/module.py", "modules/addon_store/qq_login_dialog.py"):
    text = (root / rel).read_text(encoding="utf-8")
    # 去掉注释行后再查，避免把"这里曾经用错"的说明文字当成残留
    code = "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )
    check(f"{rel} 无 cookieList 调用残留", "cookieList(" not in code)

print()
if failures:
    print(f"FAILED {len(failures)}: " + ", ".join(failures))
    sys.exit(1)
print("ALL PASS")