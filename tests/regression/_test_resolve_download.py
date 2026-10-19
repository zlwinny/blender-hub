"""BudecoAuthService.resolve_download 解析链回归（打桩网络，不发真实请求）。

守住两个已实测踩过的坑：
1. erphpdown 的 href **没有引号**：``<a href=https://…/download.php?postid=128769 class="down">``，
   只认 ``href="..."`` 的解析器永远返回 None。
2. 终点是**网盘分享页**而不是 zip 直链，且提取码藏在地址栏 ``pwd`` 参数里；
   百度网盘的 ``share/init?surl=`` 还要再规范成用户实际用的 ``/s/1<surl>``。
"""
import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)  # noqa: F841

import core.services.budeco_auth as _ba_mod  # noqa: E402
from core.services.budeco_auth import BudecoAuthService  # noqa: E402


class _MemSettings:
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


class _Resp:
    def __init__(self, url, text="", status=200, headers=None):
        self.url = url
        self.text = text
        self.status_code = status
        self.headers = headers or {}
        self.content = text.encode("utf-8", "replace")


class _FakeSession:
    """按 (精确URL, 响应) 优先、子串兜底匹配返回预置响应。

    必须是 list 而不是 dict：解析链里 ``download.php?postid=128769`` 是
    ``…&key=1&index=0`` 的前缀子串，dict 的插入顺序会让精确的那条永远匹配不到。
    """

    def __init__(self):
        self.exact = {}
        self.contains = []
        self.calls = []

    def add_exact(self, url, resp):
        self.exact[url] = resp

    def add_match(self, pat, resp):
        self.contains.append((pat, resp))

    def get(self, url, timeout=None, allow_redirects=True, **kw):
        self.calls.append(url)
        if url in self.exact:
            r = self.exact[url]
            return r(url) if callable(r) else r
        for pat, r in self.contains:
            if pat in url:
                return r(url) if callable(r) else r
        return _Resp(url, "", 404)


ARTICLE_HTML = (
    '<div class="erphpdown-cart">'
    '<a href=https://budeco.top/wp-content/plugins/erphpdown/download.php?postid=128769'
    ' target="_blank" class="down">立即下载</a></div>'
)
DOWNLOAD_PAGE_HTML = (
    "<p><span class='tit'>链接</span>"
    "<a href='download.php?postid=128769&key=1&index=' class='link erphpdown-down-btn'>立即下载</a>"
    "提取码: <span class='erphpdown-code'>bude</span></p>"
)
LOGGED_OUT_HTML = '<span class="nav-login no"></span>'
VIP_ONLY_HTML = '<div class="down-left">升级VIP享受精彩下载！</div>'


def make_auth(logged_in=True):
    # 每个用例从干净状态开始：BudecoAuthService 构造时会从 QSettings 恢复 cookie，
    # 而 QSettings 已被打桩成进程内内存实现，不清空会跨用例串味。
    _MemSettings._store = {}
    auth = BudecoAuthService(log_service=None)
    if logged_in:
        auth.ingest_cookies({"wordpress_logged_in_x": "T"})
    auth._session = _FakeSession()  # type: ignore[assignment]
    return auth


ART = "https://budeco.top/128769.html"
ENTRY = "https://budeco.top/wp-content/plugins/erphpdown/download.php?postid=128769"
# 站点真实输出是 index= （空值），不是 index=0
SECOND = ENTRY + "&key=1&index="

# ── 1. 完整链路 → 百度网盘分享链接 + 提取码 ──
auth = make_auth()
auth._session.add_exact(ART, _Resp(ART, ARTICLE_HTML))
auth._session.add_exact(ENTRY, _Resp(ENTRY, DOWNLOAD_PAGE_HTML))
auth._session.add_exact(SECOND, _Resp(
    "https://pan.baidu.com/share/init?surl=Glap2MQ1xBrV--WUY230Ng&pwd=bude",
    "", headers={"Content-Type": "text/html"},
))
t = auth.resolve_download(ART)
check("识别为网盘分享", t.kind == "pan" and t.ok)
check("提取码取自地址栏 pwd", t.code == "bude")
check(
    "share/init 规范为 /s/1<surl>",
    t.url == "https://pan.baidu.com/s/1Glap2MQ1xBrV--WUY230Ng?pwd=bude",
)

# ── 2. 直链 zip（Content-Type 命中） ──
auth2 = make_auth()
auth2._session.add_exact(ART, _Resp(ART, ARTICLE_HTML))
auth2._session.add_exact(ENTRY, _Resp(ENTRY, DOWNLOAD_PAGE_HTML))
auth2._session.add_exact(SECOND, _Resp(
    "https://budeco.top/dl/foo.zip", "", headers={"Content-Type": "application/zip"}
))
t2 = auth2.resolve_download(ART)
check("直链 zip 被识别", t2.kind == "zip" and t2.is_direct_zip)
check("直链地址正确", t2.url.endswith("foo.zip"))

# ── 3. 未登录 ──
auth3 = make_auth()
auth3._session.add_exact(ART, _Resp(ART, LOGGED_OUT_HTML))
t3 = auth3.resolve_download(ART)
check("未登录页被识别", t3.kind == "login")
check("未登录提示可读", "重新登录" in t3.note)

# ── 4. 无 cookie 时直接判定需登录 ──
auth4 = make_auth(logged_in=False)
auth4._session.add_exact(ART, _Resp(ART, ARTICLE_HTML))
t4 = auth4.resolve_download(ART)
check("无 cookie 判定 login", t4.kind == "login")

# ── 5. 无 erphpdown 入口 → 回退通用解析（网盘分享直给） ──
auth5 = make_auth()
auth5._session.add_exact(ART, _Resp(ART, '<a href="https://pan.quark.cn/s/abc">网盘下载</a>'))
t5 = auth5.resolve_download(ART)
check("无入口时回退解析", t5.kind == "pan" and "quark" in t5.url)

# ── 6. VIP 限制页 → 如实报错，不伪造链接 ──
auth6 = make_auth()
auth6._session.add_exact(ART, _Resp(ART, VIP_ONLY_HTML))
t6 = auth6.resolve_download(ART)
check("无入口且无链接时不编造", not t6.ok)
check("给出原因", bool(t6.note))

# ── 7. 网络异常 ──
class _Boom:
    def get(self, *a, **k):
        raise OSError("network down")


auth7 = make_auth()
auth7._session = _Boom()  # type: ignore[assignment]
t7 = auth7.resolve_download(ART)
check("网络异常不崩且有原因", not t7.ok and "失败" in t7.note)

# ── 8. _normalize_pan 边界 ──
u, c = BudecoAuthService._normalize_pan(
    "https://pan.baidu.com/share/init?surl=1ABCDEF&pwd=xy9", ""
)
check("surl 已带 1 前缀不重复", u == "https://pan.baidu.com/s/1ABCDEF?pwd=xy9" and c == "xy9")
u2, c2 = BudecoAuthService._normalize_pan("https://www.lanzoux.com/s/abc", "t1x2")
check("非百度网盘补 pwd", "pwd=t1x2" in u2 and c2 == "t1x2")
u3, _ = BudecoAuthService._normalize_pan("https://pan.baidu.com/s/1ZZZ?pwd=kkkk", "")
check("已带 pwd 不重复追加", u3.count("pwd=") == 1)

print()
if failures:
    print(f"FAILED {len(failures)}: " + ", ".join(failures))
    sys.exit(1)
print("ALL PASS")