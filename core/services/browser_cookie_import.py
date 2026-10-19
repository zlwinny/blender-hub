"""从本机 Firefox 同步 budeco.top 登录态（只读，不碰浏览器数据）。

为什么这条路比「内嵌浏览器扫码」更可靠：

* Firefox 把 cookie 明文存在 ``<profile>/cookies.sqlite`` 的 ``moz_cookies`` 表里，
  只需要**复制一份**再读 sqlite 就能拿到登录 cookie，不需要任何解密或密钥注入。
* budeco.top 的 QQ 登录一旦完成，``wordpress_logged_in_*`` 有效期很长（实测月级以上），
  用户在 Firefox 里登录过，应用就直接复用这份登录态，根本不用再扫一次码。
* Chromium 系（Chrome/Edge）把 cookie 用 DPAPI + AES-GCM 加密在 ``Network/Cookies``，
  需要额外解密链路且随版本变化，暂不支持；Firefox 明文库已覆盖当前使用场景。

注意：全程只读——把 cookies.sqlite（含 -wal/-shm）复制到临时目录后再解析，
不打开、不写回、不加锁，Firefox 正在运行也不影响。
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Firefox profile 根目录（Windows 默认位置）
_FIREFOX_ROOT = Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox"

# moz_cookies 里我们关心的列（新版多出的 originAttributes 等列不 SELECT，兼容老库）
_SQL = (
    "SELECT name, value, host, path, expiry, isSecure "
    "FROM moz_cookies WHERE host LIKE ? ORDER BY path"
)


@dataclass
class FirefoxCookieResult:
    """一次 Firefox 同步的结果。"""

    cookies: Dict[str, str] = field(default_factory=dict)
    profile: Optional[Path] = None
    expired_skipped: int = 0
    scanned_profiles: int = 0
    message: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.cookies) and any(
            n.startswith("wordpress_logged_in") for n in self.cookies
        )


def _profile_dirs() -> List[Path]:
    """枚举 Firefox profile 目录（含 profiles.ini 里自定义路径的 profile）。"""
    found: List[Path] = []

    profiles_dir = _FIREFOX_ROOT / "Profiles"
    if profiles_dir.is_dir():
        for p in sorted(profiles_dir.iterdir()):
            if p.is_dir() and (p / "cookies.sqlite").exists():
                found.append(p)

    ini = _FIREFOX_ROOT / "profiles.ini"
    if ini.exists():
        try:
            text = ini.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        is_relative = True
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("IsRelative="):
                is_relative = line.split("=", 1)[1].strip() == "1"
            elif line.startswith("Path=") and is_relative is False:
                cand = Path(line.split("=", 1)[1].strip())
                if cand.is_dir() and (cand / "cookies.sqlite").exists() and cand not in found:
                    found.append(cand)
    return found


def _copy_db(src: Path, work_dir: Path, tag: str) -> Path:
    """把 cookies.sqlite 及 -wal/-shm 复制到临时目录（只读解耦，避免锁库）。"""
    dst = work_dir / f"cookies_{tag}.sqlite"
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        side = Path(str(src) + suffix)
        if side.exists():
            try:
                shutil.copy2(side, Path(str(dst) + suffix))
            except OSError:
                pass  # 热点文件偶发占用，主库副本已够用
    return dst


def _read_one(profile: Path, domain: str, work_dir: Path) -> tuple[Dict[str, str], int, str]:
    """读单个 profile，返回 (cookie 字典, 过期丢弃数, 错误信息)。"""
    src = profile / "cookies.sqlite"
    try:
        dst = _copy_db(src, work_dir, profile.name.replace(".", "_"))
    except OSError as e:
        return {}, 0, f"复制失败: {e}"

    now_ms = int(time.time() * 1000)
    cookies: Dict[str, str] = {}
    expired = 0
    con: Optional[sqlite3.Connection] = None
    try:
        con = sqlite3.connect(str(dst))
        rows = con.execute(_SQL, (f"%{domain}%",)).fetchall()
    except sqlite3.DatabaseError as e:
        return {}, 0, f"读取失败: {e}"
    finally:
        if con is not None:
            con.close()

    for name, value, _host, _path, expiry, _secure in rows:
        try:
            exp = int(expiry or 0)
        except (TypeError, ValueError):
            exp = 0
        # expiry=0 是会话 cookie，浏览器关掉才失效；有值且已过期则丢弃
        if exp and exp < now_ms:
            expired += 1
            continue
        if not name or value is None:
            continue
        cookies[str(name)] = str(value)
    return cookies, expired, ""


def collect_firefox_cookies(domain: str = "budeco.top") -> FirefoxCookieResult:
    """从所有 Firefox profile 里挑一份**最新有效**的 budeco 登录 cookie。

    选择规则：优先含 ``wordpress_logged_in_*`` 的 profile；都含则取
    ``wordpress_logged_in_*`` 过期时间最晚的那个（通常是最新登录的 profile）。
    """
    result = FirefoxCookieResult()
    profiles = _profile_dirs()
    result.scanned_profiles = len(profiles)
    if not profiles:
        result.message = "未找到 Firefox 用户配置目录（本机可能没装 Firefox）"
        return result

    best_expiry = -1
    errors: List[str] = []
    work_dir = Path(tempfile.mkdtemp(prefix="bh_ff_cookie_"))
    try:
        for profile in profiles:
            cookies, expired, err = _read_one(profile, domain, work_dir)
            if err:
                errors.append(f"{profile.name}: {err}")
                continue
            result.expired_skipped += expired
            if not cookies:
                continue
            logged = [n for n in cookies if n.startswith("wordpress_logged_in")]
            if not logged:
                continue
            # 用该 profile 里登录 cookie 的过期时间做"新鲜度"排序
            expiry = _login_expiry(profile, logged[0], work_dir)
            if expiry > best_expiry:
                best_expiry = expiry
                result.cookies = cookies
                result.profile = profile
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    if result.ok:
        result.message = f"已从 Firefox（{result.profile.name}）读取到登录 Cookie"  # type: ignore[union-attr]
    elif errors:
        result.message = "；".join(errors[:2])
    else:
        result.message = "Firefox 里没有找到 budeco.top 的有效登录态，请先在 Firefox 登录该站点"
    return result


def _login_expiry(profile: Path, cookie_name: str, work_dir: Path) -> int:
    """取某个登录 cookie 的过期毫秒值（用于挑最新 profile）。"""
    src = profile / "cookies.sqlite"
    con: Optional[sqlite3.Connection] = None
    try:
        dst = _copy_db(src, work_dir, profile.name.replace(".", "_") + "_e")
        con = sqlite3.connect(str(dst))
        row = con.execute(
            "SELECT MAX(expiry) FROM moz_cookies WHERE name = ?", (cookie_name,)
        ).fetchone()
        return int(row[0] or 0) if row else 0
    except (OSError, sqlite3.DatabaseError, TypeError, ValueError):
        return 0
    finally:
        if con is not None:
            con.close()


def inject_into_webengine(cookies: Dict[str, str], domain: str = ".budeco.top") -> bool:
    """把 cookie 灌进内嵌浏览器（QWebEngineProfile），让内嵌页一开始就是已登录态。

    没有 WebEngine（无显示环境 / 未打包）时返回 False，调用方可忽略——
    这条路径只影响"内嵌浏览器是否需要重新扫码"，不影响 requests 侧的下载链解析。
    """
    if not cookies:
        return False
    try:
        from PyQt6.QtNetwork import QNetworkCookie
        from PyQt6.QtWebEngineCore import QWebEngineProfile
    except Exception:  # noqa: BLE001
        return False

    try:
        store = QWebEngineProfile.defaultProfile().cookieStore()
        origin = "https://" + domain.lstrip(".")
        from PyQt6.QtCore import QUrl

        ok_any = False
        for name, value in cookies.items():
            c = QNetworkCookie(name.encode("utf-8"), str(value).encode("utf-8"))
            c.setDomain(domain)
            c.setPath("/")
            c.setSecure(True)
            c.setHttpOnly(True)
            try:
                store.setCookie(c, QUrl(origin + "/"))
                ok_any = True
            except Exception:  # noqa: BLE001
                continue
        return ok_any
    except Exception:  # noqa: BLE001
        return False
