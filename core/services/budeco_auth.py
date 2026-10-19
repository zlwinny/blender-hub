"""BudecoTop QQ 登录服务（内嵌 QWebEngine 完成 QQ 互联 OAuth 后注入 cookie）。

背景：budeco.top 的插件下载有登录墙——文章页"立即下载"按钮未登录时是
``javascript:;`` 伪链接，拿不到真实地址。站点支持 QQ 互联 OAuth 登录，
登录后文章页才会暴露真实下载地址。

登录流程（详见 ``modules/addon_store/qq_login_dialog.QQLoginDialog``）：

1. 内嵌浏览器（QWebEngineView）加载 OAuth 入口，页面渲染出 QQ 登录二维码
2. 手机 QQ 扫码并确认 → 重定向发生在**内嵌浏览器的 profile** 内
3. 从该 profile 提取 ``wordpress_logged_in_*`` 等 cookie
4. :meth:`ingest_cookies` 把 cookie 注入本服务的 ``requests.Session``
   并持久化到 QSettings（下次启动免登录）
5. :meth:`fetch_download_url` 带登录态解析文章页的真实下载链

注意：QQ 互联是标准 Web 重定向 OAuth，重定向发生在**完成授权的浏览器**
里。因此必须用应用内嵌浏览器拿 cookie——纯 ``requests`` 轮询、或手机扫码
后重定向到手机浏览器，都无法把登录态带回到本应用的会话（旧方案走不通）。
"""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from PyQt6.QtCore import QSettings

from core.services import secret_store
from core.services.log_service import redact_url


@dataclass
class DownloadTarget:
    """一次下载解析的结果。"""

    kind: str = "unknown"  # zip(直链) / pan(网盘分享) / login(未登录) / vip / unknown
    url: str = ""  # 直链 zip，或网盘分享地址（已带提取码）
    code: str = ""  # 网盘提取码
    note: str = ""  # 给人看的原因/补充信息
    page_url: str = ""  # 来源文章页

    @property
    def ok(self) -> bool:
        return self.kind in ("zip", "pan") and bool(self.url)

    @property
    def is_direct_zip(self) -> bool:
        return self.kind == "zip"


class BudecoAuthService:
    """BudecoTop QQ 登录态管理（cookie 持久化 + 文章页下载链解析）。"""

    OAUTH_ENTRY = "https://budeco.top/oauth/socialogin/?act=login&type=qq"
    HOME = "https://budeco.top/"
    COOKIE_KEY = "budeco_auth_cookies"

    # 登录态复验间隔：cookie 名字存在 ≠ 服务端仍认这份登录态，
    # 过期 cookie 会让 UI 显示"已登录"但实际拿不到任何下载地址。
    VERIFY_TTL = 300.0
    # 未登录页面的特征（budeco 主题 + erphpdown 插件一致）
    _LOGGED_OUT_MARKERS = ("nav-login no", "signin-loader")

    # 网盘域名（命中即视为"需手动下载"，不直接下载）
    _PAN_PATTERNS = (
        r"pan\.baidu\.com",
        r"www\.aliyundrive\.com",
        r"aliyundrive\.com",
        r"pan\.quark\.cn",
        r"www\.lanzou",
        r"lanzoux\.com",
        r"lanzoui\.com",
        r"www\.123pan\.com",
        r"123pan\.com",
    )

    def __init__(self, log_service=None) -> None:
        self._log = log_service
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"),
            "Referer": "https://budeco.top/",
        })
        self._logged_in = False
        self._verified = False
        self._verified_at = 0.0
        self._load_cookies()

    # ── 登录态持久化 ──────────────────────────────────────────────────────

    def _load_cookies(self) -> None:
        """从 QSettings 恢复 cookie 并检测登录态（DPAPI 加密，兼容旧明文）。"""
        raw = QSettings("BlenderHub Team", "BlenderHub").value(self.COOKIE_KEY, "")
        if not raw:
            return
        try:
            # 旧版本是 JSON 明文（无前缀），unprotect 会原样返回；下次保存自动升级
            raw = secret_store.unprotect(str(raw))
            cookies = json.loads(raw)
            for name, value in cookies.items():
                self._session.cookies.set(name, value, domain=".budeco.top")
            self._logged_in = self._check_login_cookie()
            if self._log:
                self._log.info(f"budeco cookies restored (logged_in={self._logged_in})",
                               module="budeco_auth")
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.warning(f"budeco cookie restore failed: {e}", module="budeco_auth")

    def _save_cookies(self) -> None:
        cookies = {c.name: c.value for c in self._session.cookies}
        payload = json.dumps(cookies, ensure_ascii=False)
        try:
            payload = secret_store.protect(payload)
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.warning(
                    f"cookie 加密失败，降级为明文存储: {e}", module="budeco_auth"
                )
        settings = QSettings("BlenderHub Team", "BlenderHub")
        settings.setValue(self.COOKIE_KEY, payload)
        settings.sync()

    def logout(self) -> None:
        self._session.cookies.clear()
        self._logged_in = False
        settings = QSettings("BlenderHub Team", "BlenderHub")
        settings.remove(self.COOKIE_KEY)
        settings.sync()

    def is_logged_in(self) -> bool:
        return self._logged_in

    def verify_login(self, force: bool = False) -> bool:
        """向站点确认这份 cookie 是否**真的**还有效。

        为什么必须有这一步：本服务判断"已登录"只检查 ``wordpress_logged_in_*``
        这个 cookie 名是否存在。但 cookie 过期后名字仍在，UI 会一直显示"已登录"，
        而服务端返回的是未登录页面 → 下载链解析必然失败（这正是 13:12 那轮
        "抓不到链接"的另一种形态）。这里真发一次请求验证，结果缓存
        ``VERIFY_TTL`` 秒，避免每次刷新卡片都打一次网络。

        网络异常时**不下结论**（保持原状态），避免把"没网"误报成"登录失效"。
        """
        if not self._logged_in:
            return False
        now = time.time()
        if not force and self._verified_at and now - self._verified_at < self.VERIFY_TTL:
            return self._verified

        try:
            resp = self._session.get(self.HOME, timeout=15)
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.warning(f"budeco 登录态校验网络异常，保留原状态: {e}",
                                  module="budeco_auth")
            self._verified_at = now
            return self._verified

        text = resp.text if resp.status_code == 200 else ""
        ok = bool(text) and not any(m in text for m in self._LOGGED_OUT_MARKERS)
        self._verified = ok
        self._verified_at = now
        if not ok and self._logged_in:
            self._logged_in = False
            if self._log:
                self._log.warning(
                    "budeco cookie 已失效（站点返回未登录页），需重新登录",
                    module="budeco_auth",
                )
        elif ok and self._log:
            self._log.info("budeco 登录态校验通过", module="budeco_auth")
        return ok

    def get_cookies(self) -> dict:
        return {c.name: c.value for c in self._session.cookies}

    def _check_login_cookie(self) -> bool:
        return any(c.name.startswith("wordpress_logged_in")
                   for c in self._session.cookies)

    def ingest_cookies(self, cookies: dict) -> None:
        """把内嵌浏览器提取的 cookie 字典注入会话并持久化。

        ``cookies`` 形如 ``{"wordpress_logged_in_xxx": "yyy", ...}``，
        均按 ``.budeco.top`` 域写入。
        """
        for name, value in cookies.items():
            self._session.cookies.set(name, value, domain=".budeco.top")
        self._logged_in = self._check_login_cookie()
        self._verified_at = 0.0  # 新 cookie 必须重新向站点复验
        self._save_cookies()
        if self._log:
            self._log.info(f"budeco cookies ingested (logged_in={self._logged_in})",
                           module="budeco_auth")

    # ── 文章页下载链解析 ──────────────────────────────────────────────────

    # erphpdown 输出的 href **没有引号**（非标准 HTML）：
    #   <a href=https://budeco.top/wp-content/plugins/erphpdown/download.php?postid=128769
    #      target="_blank" class="down">立即下载</a>
    # 旧解析器只认 href="..."（带引号），因此永远匹配不到 → 一律返回 None。
    _ERPHP_ENTRY = re.compile(
        r"erphpdown/download\.php\?postid=(\d+)", re.IGNORECASE
    )
    # 下载页里的第二级入口：href='download.php?postid=...&key=1&index='
    _ERPHP_SECOND = re.compile(
        r"href=[\"']?(download\.php\?[^\"'\s>]+)", re.IGNORECASE
    )
    _ERPHP_CODE = re.compile(
        r"erphpdown-code[\"']?>([^<]{1,12})<", re.IGNORECASE
    )

    def resolve_download(self, article_url: str) -> DownloadTarget:
        """从文章页一路跟进到最终下载地址（纯 requests，不依赖内嵌浏览器）。

        实测链路（budeco.top / erphpdown）：
            文章页  →  erphpdown/download.php?postid=NNNN
                     →  download.php?…&key=1&index=   （页面内「立即下载」按钮）
                     →  302 → downloadfile.php?…&md5key=…&times=…&session_name=…
                     →  百度网盘分享页  https://pan.baidu.com/share/init?surl=…&pwd=提取码

        提取码就藏在最后这个地址的 ``pwd`` 参数里（站点自己的提示原文：
        「如无提取码'点击下载'看浏览器地址栏最后4位字母如：pwd=bude」）。
        """
        target = DownloadTarget(page_url=article_url)
        if not article_url:
            target.note = "缺少文章地址"
            return target

        try:
            resp = self._session.get(article_url, timeout=25)
        except Exception as e:  # noqa: BLE001
            target.note = f"打开文章页失败：{e}"
            return target

        if resp.status_code != 200:
            target.note = f"文章页返回 {resp.status_code}"
            return target

        html = resp.text
        if any(m in html for m in self._LOGGED_OUT_MARKERS):
            target.kind = "login"
            target.note = "登录态已失效，请重新登录 BudecoTop"
            return target
        if not self._logged_in:
            target.kind = "login"
            target.note = "尚未登录 BudecoTop"
            return target

        # 1) 文章页里的一级入口
        m = self._ERPHP_ENTRY.search(html)
        if m is None:
            # 非 erphpdown 结构时退回通用抓取（直链 zip / 网盘分享）
            legacy = self.fetch_download_url(article_url)
            if legacy:
                target.url = legacy
                target.kind = "pan" if self.is_pan_link(legacy) else "zip"
                return target
            target.note = "页面里没有找到下载入口（站点结构可能已变）"
            return target

        host = urlparse(article_url).netloc or "budeco.top"
        entry = f"https://{host}/wp-content/plugins/erphpdown/download.php?postid={m.group(1)}"
        try:
            page2 = self._session.get(entry, timeout=25)
        except Exception as e:  # noqa: BLE001
            target.note = f"打开下载页失败：{e}"
            return target

        code = ""
        m2 = self._ERPHP_CODE.search(page2.text)
        if m2 is not None:
            code = m2.group(1).strip()

        # 2) 下载页里的第二级入口（index 从 0 开始逐个试）
        m3 = self._ERPHP_SECOND.search(page2.text)
        second = (entry.rsplit("/", 1)[0] + "/" + m3.group(1)) if m3 else entry
        for index in range(3):
            url = second if index == 0 else re.sub(r"index=\d*", f"index={index}", second)
            try:
                resp2 = self._session.get(url, timeout=25, allow_redirects=True)
            except Exception as e:  # noqa: BLE001
                if index == 2:
                    target.note = f"请求下载入口失败：{e}"
                continue

            ctype = (resp2.headers.get("Content-Type") or "").lower()
            if "zip" in ctype or "octet-stream" in ctype:
                target.kind = "zip"
                target.url = resp2.url
                return target

            final = resp2.url
            if any(re.search(pat, final) for pat in self._PAN_PATTERNS):
                target.kind = "pan"
                target.url, target.code = self._normalize_pan(final, code)
                return target

            # 最后一跳仍停在 budeco：页面里直接写了网盘链接
            for pat in self._PAN_PATTERNS:
                mm = re.search(rf'https?://[^\s"\'<>]*{pat}[^\s"\'<>]*', resp2.text)
                if mm:
                    target.kind = "pan"
                    target.url, target.code = self._normalize_pan(mm.group(0), code)
                    return target

        target.note = "未能从站点解析出下载链接（可能需要 VIP 或结构已变）"
        return target

    def download_to(self, url: str, dest: Path, timeout: int = 180) -> bool:
        """把解析出的直链下载到本地（流式，带登录态 Cookie/Referer）。

        为什么需要它：真机（PyInstaller 打包）创建 QWebEngineView 会 native crash，
        所以"浏览器内点下载"这条主路径不可用。改用已带登录态的 requests 会话
        直接下——erphpdown 的最终下载链只认 Cookie/Referer，本会话两者都有。
        """
        if not url:
            return False
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with self._session.get(url, timeout=timeout, stream=True,
                                   allow_redirects=True) as resp:
                if resp.status_code != 200:
                    if self._log:
                        self._log.warning(
                            f"budeco 下载返回 {resp.status_code}: {redact_url(url)}",
                            module="budeco_auth",
                        )
                    return False
                ctype = (resp.headers.get("Content-Type") or "").lower()
                total = int(resp.headers.get("Content-Length") or 0)
                written = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        if total and written > total * 4:
                            # 明显异常（比如下回来的是 HTML 错误页却谎称超大）
                            break
            if written <= 0:
                dest.unlink(missing_ok=True)
                return False
            if total and written < total:
                if self._log:
                    self._log.warning(
                        f"budeco 下载不完整 {written}/{total}", module="budeco_auth")
                return False
            if self._log:
                self._log.info(
                    f"budeco 下载完成: {dest.name} ({written} bytes, ctype={ctype})",
                    module="budeco_auth")
            return True
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.warning(f"budeco 下载失败: {e}", module="budeco_auth")
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @staticmethod
    def _normalize_pan(url: str, code: str = "") -> tuple[str, str]:
        """把网盘分享页地址整理成用户可直接用的形式，并取出提取码。

        budeco 最终落点是 ``pan.baidu.com/share/init?surl=<surl>&pwd=<码>``，
        用户实际要访问的是 ``pan.baidu.com/s/1<surl>?pwd=<码>``。
        """
        p = urlparse(url)
        q = parse_qs(p.query)
        pwd = (q.get("pwd") or [""])[0] or code
        surl = (q.get("surl") or [""])[0]
        host = p.netloc.lower()
        if "pan.baidu.com" in host and surl:
            short = surl[1:] if surl.startswith("1") else surl
            out = f"https://pan.baidu.com/s/1{short}"
            if pwd:
                out += f"?pwd={pwd}"
            return out, pwd
        if pwd and "pwd=" not in url:
            joiner = "&" if "?" in url else "?"
            return url + joiner + f"pwd={pwd}", pwd
        return url, pwd

    def fetch_download_url(self, article_url: str) -> Optional[str]:
        """登录态请求文章页，解析真实下载地址。

        返回：
        - 直接下载链接（http/https，相对路径会自动补全为 budeco.top 绝对路径）
        - 网盘链接（pan.baidu.com 等；调用方需提示用户手动下载）
        - ``None``：解析失败（未登录 / 页面结构变化 / 网络异常）
        """
        if not self._logged_in:
            return None
        try:
            resp = self._session.get(article_url, timeout=20)
            if resp.status_code != 200:
                return None
            html = resp.text

            # 1) 下载按钮的 data-url / data-href / href（class 含 down）
            for attr in ("data-url", "data-href", "href"):
                m = re.search(
                    rf'<a[^>]*class="[^"]*\bdown\b[^"]*"[^>]*{attr}="([^"]+)"', html
                )
                if not m:  # href 在 class 之前的形态
                    m = re.search(
                        rf'<a[^>]*{attr}="([^"]+)"[^>]*class="[^"]*\bdown\b[^"]*"', html
                    )
                if m:
                    href = m.group(1).strip()
                    if href and not href.startswith(("javascript:", "#", "about:")):
                        return self._normalize(href)

            # 2) 其它下载相关按钮（download / data-download 属性）
            m = re.search(r'<a[^>]*(?:download|data-download)[^>]*href="([^"]+)"', html)
            if m:
                href = m.group(1).strip()
                if href and not href.startswith(("javascript:", "#", "about:")):
                    return self._normalize(href)

            # 3) 网盘直链（不能直接下载，但告知用户）
            for pat in self._PAN_PATTERNS:
                m = re.search(rf'href="(https?://[^"]*{pat}[^"]*)"', html)
                if m:
                    return m.group(1)
        except Exception as e:  # noqa: BLE001
            if self._log:
                self._log.warning(
                    f"budeco download parse failed for {redact_url(article_url)}: {e}",
                    module="budeco_auth",
                )
        return None

    @staticmethod
    def _normalize(href: str) -> str:
        """补全相对路径为 budeco.top 绝对地址。"""
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return "https://budeco.top" + href
        return href

    @staticmethod
    def is_pan_link(url: str) -> bool:
        return any(re.search(pat, url) for pat in BudecoAuthService._PAN_PATTERNS)
