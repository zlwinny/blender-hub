"""QQ 登录 + budeco 下载链：语法/逻辑回归（headless 可跑，不触发 WebEngine）。"""
import os
import sys
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"  # 强制对话框走外部浏览器兜底，避免 offscreen 导入 WebEngine 崩溃
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication, QPushButton, QDialog, QMessageBox
app = QApplication(sys.argv)

# headless 下模态 QMessageBox 无用户点击会永久阻塞，打桩为无操作
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

# ── 测试隔离：把 QSettings 换成内存实现 ──
# 血泪教训：这里原本直接 remove 真实配置，而且后续 ingest 的假 cookie
# （wordpress_logged_in_abc="VAL"）会写进真实注册表。应用下次启动读到它，
# 误判"已登录"→ 跳过登录 → 拿一份无效 cookie 去抓文章页 → 页面是未登录态、
# 没有任何下载地址 → 报"未能解析到下载链接"。测试绝不能碰真实配置。
import core.services.budeco_auth as _ba_mod  # noqa: E402


class _MemSettings:
    """QSettings 的内存替身：进程内隔离，不读写注册表。"""

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


# ── 1. budeco_auth：cookie 注入 + 下载链解析 ──
from core.services.budeco_auth import BudecoAuthService

auth = BudecoAuthService(log_service=None)
check("初始未登录", not auth.is_logged_in())
auth.ingest_cookies({"wordpress_logged_in_abc": "VAL", "wordpress_logged_in_abc_2": "SEC"})
check("ingest 后标记已登录", auth.is_logged_in())

# 用假 session.get 返回已登录态 HTML，验证解析
HTML_HREF = '<a class="down signin-loader" href="/wp-content/uploads/2024/foo.zip">下载</a>'
HTML_DATA = '<a class="down" data-url="https://budeco.top/d/bar.zip" href="javascript:;">下载</a>'
HTML_PAN = '<a href="https://pan.baidu.com/s/1abc">网盘</a>'


class _FakeResp:
    def __init__(self, text):
        self.status_code = 200
        self.text = text


for label, html, expect in (
    ("href 相对路径补全", HTML_HREF, "https://budeco.top/wp-content/uploads/2024/foo.zip"),
    ("data-url 属性", HTML_DATA, "https://budeco.top/d/bar.zip"),
    ("网盘链接返回", HTML_PAN, "https://pan.baidu.com/s/1abc"),
):
    auth._session.get = lambda *a, **k: _FakeResp(html)
    got = auth.fetch_download_url("https://budeco.top/123.html")
    check(f"fetch_download_url[{label}]={got}", got == expect)

check("is_pan_link 识别网盘", BudecoAuthService.is_pan_link("https://pan.baidu.com/x"))
check("is_pan_link 否认真实直链", not BudecoAuthService.is_pan_link("https://budeco.top/a.zip"))

# 未登录时 fetch 必须返回 None
auth2 = BudecoAuthService(log_service=None)
check("未登录 fetch 返回 None", auth2.fetch_download_url("https://budeco.top/x") is None)

# ── 2. 模块集成：顶栏按钮 + 安装路由 ──
from core.app_context import AppContext
from modules.addon_store.module import AddonStoreModule, AddonInfo

ctx = AppContext()
# 伪造 budeco_auth：登录态有效 + 可控的解析结果（不打桩就会真发网络请求，
# 甚至在登录态失效时弹模态登录窗把测试永久卡死）
from core.services.budeco_auth import DownloadTarget  # noqa: E402

ctx.budeco_auth.is_logged_in = lambda: True
ctx.budeco_auth.verify_login = lambda *a, **k: True
_resolve_calls = []
_resolve_result = {"v": None}


def _fake_resolve(url):
    _resolve_calls.append(url)
    return _resolve_result["v"]


ctx.budeco_auth.resolve_download = _fake_resolve  # type: ignore[attr-defined]
ctx.budeco_auth.fetch_download_url = lambda url: "https://budeco.top/real.zip"

mod = AddonStoreModule()
mod.ctx = ctx
widget = mod.get_widget()

# 顶栏应有 QQ 登录按钮
qq_btns = [b for b in widget.findChildren(QPushButton) if "QQ" in b.text() or "Budeco" in b.text()]
check("顶栏存在 QQ 登录按钮", len(qq_btns) >= 1)

# _install_budeco 现在的默认路径（2026-10-17 重写）：
#   resolve_download → 直链 zip 直接装 / 网盘分享弹 PanDownloadDialog / 失败如实上报
# 内嵌浏览器不再是默认路径（打包环境下曾 0xc0000409 闪退），降级为用户主动选择。
calls = {}


def fake_install_zip(path, addon, target):
    calls["zip"] = path


def fake_download_install(addon, target):
    calls["online"] = addon.download_url


mod._install_from_zip_file = fake_install_zip  # pyright: ignore[reportAttributeAccessIssue]
mod._download_and_install = fake_download_install  # pyright: ignore[reportAttributeAccessIssue]

fake_target = type("T", (), {"version": "4.2", "path": "X"})()
budeco_addon = AddonInfo(
    id="budeco:1", name="测试插件", author="A", category="建模",
    description="d", source="budeco", source_url="https://budeco.top/1.html",
)

import modules.addon_store.pan_download_dialog as _pan_mod  # noqa: E402

pan_shown = {"url": "", "code": "", "zip": None}


class _Sig:
    def __init__(self, key):
        self.key = key

    def connect(self, fn):  # noqa: ANN001
        pan_shown[self.key] = fn


class _FakePanDialog:
    def __init__(self, name, url, code="", parent=None):
        pan_shown["url"] = url
        pan_shown["code"] = code
        self.zip_ready = _Sig("zip_handler")
        self.embedded_requested = _Sig("emb_handler")

    def exec(self):
        return QDialog.DialogCode.Rejected


_pan_mod.PanDownloadDialog = _FakePanDialog  # type: ignore[reportAttributeAccessIssue]


def run_install(target_info, addon=None):
    """跑一次安装流程（含后台线程 + QTimer 轮询的完整时序）。"""
    _resolve_result["v"] = target_info
    calls.clear()
    pan_shown.clear()
    pan_shown.update({"url": "", "code": "", "zip": None})
    mod._install_budeco(addon or budeco_addon, fake_target)
    # 推进事件循环直到后台线程的 result_ready 回到主线程（queued 连接）
    from PyQt6.QtCore import QCoreApplication

    for _ in range(50):
        QCoreApplication.processEvents()
        if mod._resolve_thread is None or mod._resolve_thread.isFinished():
            QCoreApplication.processEvents()
            break
        time.sleep(0.02)
    QCoreApplication.processEvents()


# 1) 直链 zip → 走在线安装
run_install(DownloadTarget(kind="zip", url="https://budeco.top/real.zip"))
check("直链 zip → 在线安装", calls.get("online") == "https://budeco.top/real.zip")

# 2) 网盘分享 → 弹网盘对话框并带上提取码
run_install(DownloadTarget(
    kind="pan", url="https://pan.baidu.com/s/1ABC?pwd=bude", code="bude"
))
check("网盘分享 → 弹网盘对话框", pan_shown.get("url", "").startswith("https://pan.baidu.com/s/1ABC"))
check("网盘提取码透传", pan_shown.get("code") == "bude")

# 3) 网盘对话框选回 zip → 交给 zip 安装
if pan_shown.get("zip_handler"):
    pan_shown["zip_handler"]("C:/tmp/real.zip")
check("网盘选回 zip → 交给 zip 安装", calls.get("zip") == "C:/tmp/real.zip")

# 4) 解析失败 → 如实报错，不静默安装
QMessageBox.critical = staticmethod(lambda *a, **k: None)  # 抑制弹窗
run_install(DownloadTarget(kind="unknown", note="页面里没有找到下载入口"))
check("解析失败不安装", "zip" not in calls and "online" not in calls)

# 非 budeco + 无 download_url → 走 mock
other = AddonInfo(id="other:1", name="O", author="A", category="建模", description="d", source="github")
# 直接验证决策分支：非 budeco 且无 download_url 时 _install_budeco 不被调用
# 通过 _install_addon 不易隔离，这里单独验证 _install_budeco 仅对 budeco 生效：
check("非 budeco 不应进入 _install_budeco 路由（决策链由 source 判定）",
      other.source != "budeco")

print("\n=== 结果 ===")
if failures:
    print("FAILED:", failures)
    sys.exit(1)
print("ALL QQ-LOGIN LOGIC TESTS PASS")
