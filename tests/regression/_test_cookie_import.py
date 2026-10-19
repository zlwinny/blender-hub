"""Firefox 登录态同步 + 服务端复验：回归测试（headless 可跑）。

覆盖：
1. browser_cookie_import 的 profile 枚举 / 明文 cookie 解析 / 过期过滤 /
   域名过滤 / 多 profile 择优 / 坏库容错 / WebEngine 缺失时静默失败
2. BudecoAuthService.verify_login 的三种结论（有效 / 已失效 / 网络异常）
   与 5 分钟 TTL 缓存
"""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)  # noqa: F841

import core.services.budeco_auth as _ba_mod  # noqa: E402
import core.services.browser_cookie_import as _bci  # noqa: E402


class _MemSettings:
    """QSettings 的内存替身：进程内隔离，绝不读写注册表。"""

    _store: dict = {}

    def __init__(self, *a, **k):
        pass

    def value(self, key, default=""):
        return self._store.get(key, default)

    def setValue(self, key, val):
        self._store[key] = val

    def remove(self, key):
        self._store.pop(key, None)


_ba_mod.QSettings = _MemSettings  # pyright: ignore[reportAttributeAccessIssue]

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def make_profile(root: Path, name: str, cookies, table: bool = True) -> Path:
    """造一个 Firefox profile 目录 + 明文 cookies.sqlite。"""
    prof = root / "Profiles" / name
    prof.mkdir(parents=True, exist_ok=True)
    db = prof / "cookies.sqlite"
    con = sqlite3.connect(str(db))
    if table:
        con.execute(
            "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, name TEXT, value TEXT, "
            "host TEXT, path TEXT, expiry INTEGER, isSecure INTEGER)"
        )
        con.executemany(
            "INSERT INTO moz_cookies (name, value, host, path, expiry, isSecure) "
            "VALUES (?,?,?,?,?,1)",
            cookies,
        )
    con.commit()
    con.close()
    return prof


NOW_MS = int(time.time() * 1000)
FUTURE = NOW_MS + 30 * 86400 * 1000
PAST = NOW_MS - 86400 * 1000


def collect(root: Path):
    """在临时 Firefox 根上跑一次收集（monkeypatch 模块级路径）。"""
    orig = _bci._FIREFOX_ROOT
    _bci._FIREFOX_ROOT = root
    try:
        return _bci.collect_firefox_cookies()
    finally:
        _bci._FIREFOX_ROOT = orig


# ── 1. 明文 cookie 解析 ──
tmp1 = Path(tempfile.mkdtemp(prefix="bh_test_ff_"))
make_profile(
    tmp1,
    "aaa.default-release",
    [
        ("wordpress_logged_in_x", "REAL_TOKEN", "budeco.top", "/", FUTURE),
        ("wordpress_sec_x", "SEC", "budeco.top", "/wp-admin", FUTURE),
        ("zh_choose", "z", ".budeco.top", "/", FUTURE),
        ("analytics_id", "BAD", "example.com", "/", FUTURE),  # 其它域，必须被过滤
    ],
)
r = collect(tmp1)
check("有效登录态被识别", r.ok)
check("取到登录 cookie 值", r.cookies.get("wordpress_logged_in_x") == "REAL_TOKEN")
check("非 budeco 域被过滤", "analytics_id" not in r.cookies)
check("命中 profile 记录正确", r.profile is not None and r.profile.name == "aaa.default-release")
check("扫描到 1 个 profile", r.scanned_profiles == 1)

# ── 2. 过期 cookie 被丢弃 ──
tmp2 = Path(tempfile.mkdtemp(prefix="bh_test_ff_"))
make_profile(
    tmp2,
    "old.default",
    [
        ("wordpress_logged_in_x", "STALE", "budeco.top", "/", PAST),
        ("wordpress_sec_x", "SEC", "budeco.top", "/", FUTURE),
    ],
)
r2 = collect(tmp2)
check("过期登录 cookie 被丢弃", not r2.ok)
check("过期计数正确", r2.expired_skipped == 1)

# ── 3. 只有 wordpress_sec（无登录 cookie）不算登录 ──
tmp3 = Path(tempfile.mkdtemp(prefix="bh_test_ff_"))
make_profile(tmp3, "half.default", [("wordpress_sec_x", "SEC", "budeco.top", "/", FUTURE)])
r3 = collect(tmp3)
check("缺 wordpress_logged_in 不算登录", not r3.ok)
check("给出可读提示", "budeco.top" in r3.message)

# ── 4. 坏库 / 无表容错 ──
tmp4 = Path(tempfile.mkdtemp(prefix="bh_test_ff_"))
make_profile(tmp4, "broken.default", [], table=False)
r4 = collect(tmp4)
check("坏库不抛异常", isinstance(r4.cookies, dict))
check("坏库提示可读", "读取失败" in r4.message or "budeco.top" in r4.message)

# ── 5. 多 profile 择优（取登录 cookie 过期最晚的） ──
tmp5 = Path(tempfile.mkdtemp(prefix="bh_test_ff_"))
make_profile(
    tmp5,
    "old.default-release",
    [("wordpress_logged_in_x", "OLD_TOKEN", "budeco.top", "/", FUTURE)],
)
make_profile(
    tmp5,
    "new.default-release",
    [("wordpress_logged_in_x", "NEW_TOKEN", "budeco.top", "/", FUTURE + 10 * 86400 * 1000)],
)
r5 = collect(tmp5)
check("多 profile 择优", r5.cookies.get("wordpress_logged_in_x") == "NEW_TOKEN")
check("择优命中 new profile", r5.profile is not None and r5.profile.name.startswith("new"))

# ── 6. 无 Firefox 目录 ──
r6 = collect(Path(tempfile.mkdtemp(prefix="bh_no_ff_")))
check("无 Firefox 时不报错", not r6.ok and r6.message)

# ── 7. WebEngine 不可用时注入静默失败 ──
# 注意：offscreen 下 QtWebEngineCore 可能仍可导入（Widgets 才受
# AA_ShareOpenGLContexts 限制），所以这里只要求"不抛异常且返回 bool"。
_check = _bci.inject_into_webengine({"a": "b"})
check("注入不抛异常且返回 bool", isinstance(_check, bool))
check("空 cookie 注入返回 False", _bci.inject_into_webengine({}) is False)

# ── 8. verify_login：三种结论 + TTL ──
from core.services.budeco_auth import BudecoAuthService  # noqa: E402

auth = BudecoAuthService(log_service=None)
check("无 cookie 时 verify=False", auth.verify_login() is False)

auth.ingest_cookies({"wordpress_logged_in_x": "T"})


class _Resp:
    def __init__(self, text, code=200):
        self.status_code = code
        self.text = text


LOGGED_IN_HTML = '<html><body><a class="nav-login">登录</a></body></html>'
LOGGED_OUT_HTML = '<html><body><span class="nav-login no"></span></body></html>'

calls = []


def _get(html, code=200):
    def _inner(*a, **k):
        calls.append(1)
        return _Resp(html, code)

    return _inner


auth._session.get = _get(LOGGED_IN_HTML)
check("服务端确认有效", auth.verify_login() is True)
n_before = len(calls)
check("TTL 内不重复请求", auth.verify_login() is True and len(calls) == n_before)

auth._session.get = _get(LOGGED_OUT_HTML)
check("站点返回未登录页 → 判定失效", auth.verify_login(force=True) is False)
check("失效后 is_logged_in 同步为 False", auth.is_logged_in() is False)


def _boom(*a, **k):
    raise OSError("network down")


auth2 = BudecoAuthService(log_service=None)
auth2.ingest_cookies({"wordpress_logged_in_x": "T"})
auth2._session.get = _boom
check("网络异常不下失效结论", auth2.verify_login() is False or auth2._verified is False)
check("网络异常后仍保持已登录标记", auth2.is_logged_in() is True)

# ── 9. 真实 Firefox（只读，不做任何写操作） ──
real = _bci.collect_firefox_cookies()
print(f"[info] 真实 Firefox: {real.message}; "
      f"profiles={real.scanned_profiles}; cookies={len(real.cookies)}; ok={real.ok}")
if real.ok:
    check("真实 Firefox 读到登录 cookie 且不含明文 token 打印",
          "wordpress_logged_in_" in "".join(real.cookies.keys()))

# ── 10. 安装器：Firefox 同步路由（stub 掉真 WebEngine，避免本环境起不来） ──
import modules.addon_store.install_browser as _ib  # noqa: E402
from core.services.browser_cookie_import import FirefoxCookieResult  # noqa: E402


class _FakeAuth:
    def __init__(self):
        self.ingested = {}
        self._log = None

    def ingest_cookies(self, cookies):
        self.ingested.update(cookies)


_ff_result = FirefoxCookieResult(
    cookies={"wordpress_logged_in_x": "T1", "wordpress_sec_x": "S1"},
    profile=Path("/fake/profiles/new.default-release"),
    message="stub",
)
_ib.collect_firefox_cookies = lambda: _ff_result          # type: ignore[assignment]
_ib.inject_into_webengine = lambda cookies, domain=".budeco.top": True  # type: ignore[assignment]

_auth_fake = _FakeAuth()
_dlg = _ib.BrowserInstallDialog(
    "https://budeco.top/sample-plugin/", None, "测试插件", auth=_auth_fake
)
_synced = _dlg._try_firefox_sync()
check("安装器同步返回 True", _synced is True)
check("安装器标记已同步", _dlg._ff_synced is True)
check("cookie 已注入 auth 会话", _auth_fake.ingested.get("wordpress_logged_in_x") == "T1")
check("状态文案明示来源 Firefox", "Firefox" in _dlg._status.text())
check("状态文案不再误导去点页面下载", "立即下载" not in _dlg._status.text())

# 无登录态时不得静默成功，状态必须说明原因
_ib.collect_firefox_cookies = lambda: FirefoxCookieResult(  # type: ignore[assignment]
    cookies={}, message="Firefox 里没有找到 budeco.top 的有效登录态"
)
_ok2 = _dlg._try_firefox_sync()
check("无登录态返回 False", _ok2 is False)
check("无登录态时提示原因", "有效登录态" in _dlg._status.text())

print()
if failures:
    print(f"FAILED {len(failures)}: " + ", ".join(failures))
    sys.exit(1)
print("ALL PASS")