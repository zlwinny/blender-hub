"""内嵌浏览器安装器 + zip 安装链路回归测试（headless，可 CI 跑）。

覆盖：
1. 浏览器下载到的 zip → 走真实校验/解压/落位（复用在线安装同一条路径）
2. 非 zip / 文件缺失 → 如实报错而不是假装成功
3. BrowserInstallDialog 在无 WebEngine 环境下正确降级
4. downloadRequested 截获逻辑：接管目录、净化文件名、记录落点
5. _install_budeco 路由：取消不装、确认则装
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 无显示环境 WebEngine 会 spawn 失败直接退出（127），测试走降级分支
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import modules.addon_store.module as M  # noqa: E402
from modules.addon_store.install_browser import BrowserInstallDialog  # noqa: E402
from core.services.addon_service import AddonInfo  # noqa: E402

app = QApplication(sys.argv)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


# ── 所有模态框打桩（headless 下没人点按钮会永久阻塞）──
msgs: list[tuple[str, str]] = []
QMessageBox.information = staticmethod(lambda *a, **k: msgs.append(("info", str(a[1]) if len(a) > 1 else "")))
QMessageBox.critical = staticmethod(lambda *a, **k: msgs.append(("critical", str(a[1]) if len(a) > 1 else "")))
QMessageBox.warning = staticmethod(lambda *a, **k: msgs.append(("warning", str(a[1]) if len(a) > 1 else "")))

tmp = Path(tempfile.mkdtemp(prefix="bh_test_"))
try:
    # 伪造 Blender 安装目录：官方安装包布局是 <base>/<版本号>/scripts/addons
    blender_root = tmp / "blender"
    (blender_root / "4.2" / "scripts" / "addons").mkdir(parents=True)

    class _Cfg:
        base_dir = str(tmp / "cfg")

    class _Log:
        def info(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

    class _Bus:
        def emit(self, *a, **k):
            pass

    class _Ctx:
        config_service = _Cfg()
        log_service = _Log()
        event_bus = _Bus()

    class _Target:
        label = "4.2"
        version = "4.2"
        path = blender_root
        # 测试隔离：把安装层指到临时目录，绝不写进真实用户配置
        user_scripts_override = str(blender_root / "4.2" / "scripts")

    addon = AddonInfo(
        id="my_test_addon",
        name="My Test Addon",
        author="tester",
        category="建模",
        description="d",
        source_url="https://budeco.top/128740.html",
    )

    def make_zip(path: Path) -> Path:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "MyTestAddon/__init__.py",
                'bl_info = {"name": "My Test Addon", "version": (1, 0), "blender": (4, 2, 0)}\n',
            )
            zf.writestr("MyTestAddon/operators.py", "def register():\n    pass\n")
        path.write_bytes(buf.getvalue())
        return path

    zip_path = make_zip(tmp / "my_test_addon.zip")

    mod = M.AddonStoreModule()
    mod.ctx = _Ctx()  # pyright: ignore[reportAttributeAccessIssue]
    mod._widget = None
    mod._refresh_cards = lambda: None  # pyright: ignore[reportAttributeAccessIssue]

    # ── 1. 浏览器下载到 zip → 真实安装 ──
    # 目录名必须是**压缩包内的真实插件目录名**（MyTestAddon），而不是远端 id
    # （my_test_addon）——旧行为让用户在 Blender 目录里看到 budeco_44968 这类
    # 看不懂的名字，直接误判"没装上"。
    mod._install_from_zip_file(str(zip_path), addon, _Target())
    dest = blender_root / "4.2" / "scripts" / "addons" / "MyTestAddon"
    check("zip 安装：按 zip 内真实目录名落位", dest.is_dir(), str(dest))
    check("zip 安装：不再使用远端 id 命名",
          not (blender_root / "4.2" / "scripts" / "addons" / "my_test_addon").exists())
    check("zip 安装：__init__.py 已落位", (dest / "__init__.py").is_file())
    check("zip 安装：operators.py 已落位", (dest / "operators.py").is_file())
    check("zip 安装：加入已安装集合", "my_test_addon" in mod._installed_addons)
    check("zip 安装：安装记录已持久化",
          isinstance(getattr(mod, "_installed_map_path", lambda: None)(), Path)
          and json.loads(mod._installed_map_path().read_text(encoding="utf-8"))
          .get("my_test_addon", {}).get("dir") == "MyTestAddon")
    check("zip 安装：提示安装完成", any(k == "info" for k, _ in msgs))

    # ── 2. 非 zip 内容 → 必须如实报错 ──
    msgs.clear()
    bad = tmp / "bad.zip"
    bad.write_bytes(b"<html>not a zip</html>")
    mod._install_from_zip_file(str(bad), addon, _Target())
    check("非 zip 被拒绝", any(k == "critical" for k, _ in msgs))

    # ── 3. 文件不存在 → 如实报错 ──
    msgs.clear()
    mod._install_from_zip_file(str(tmp / "nope.zip"), addon, _Target())
    check("文件缺失被拒绝", any(k == "critical" for k, _ in msgs))

    # ── 4. 无 WebEngine 环境降级（WebEngine 已在 showEvent 里延后创建） ──
    d = BrowserInstallDialog("https://budeco.top/128740.html", None, "My Test Addon")
    check("降级：构造期不创建内嵌视图", d._view is None)
    d.show()  # 触发 showEvent → 走降级分支
    QCoreApplication.processEvents()
    check("降级：show 后仍无内嵌视图", d._view is None)
    check("降级：提示手动选择 zip", "内嵌浏览器" in d._status.text() or "外部浏览器" in d._status.text())
    check("降级：初始未取得 zip", d.zip_path is None)
    d.close()

    # ── 5. downloadRequested 截获逻辑 ──
    class _Sig:
        def connect(self, fn):
            pass

    class _FakeDownload:
        def __init__(self):
            self.dir = None
            self.name = None
            self.accepted = False

        def downloadFileName(self):
            return "plugin_v1.2.zip"

        def setDownloadDirectory(self, p):
            self.dir = p

        def setDownloadFileName(self, n):
            self.name = n

        def accept(self):
            self.accepted = True

        def receivedBytes(self):
            return 100

        def totalBytes(self):
            return 400

        receivedBytesChanged = _Sig()
        stateChanged = _Sig()

    d2 = BrowserInstallDialog("https://budeco.top/128740.html", None, "X")
    fd = _FakeDownload()
    d2._on_download(fd)
    check("下载被接管（accept 已调用）", fd.accepted)
    check("下载目录为暂存目录", bool(fd.dir) and Path(fd.dir).is_dir(), str(fd.dir))
    check("文件名保留原名", fd.name == "plugin_v1.2.zip")
    check("记录下载落点", d2._download_path == Path(str(fd.dir)) / "plugin_v1.2.zip")
    d2.close()

    # 百度网盘下载常不带扩展名/带怪字符，验证净化
    class _FakeDownload2(_FakeDownload):
        def downloadFileName(self):
            return 'bad:name*?.zip'

    d3 = BrowserInstallDialog("https://budeco.top/128740.html", None, "X")
    fd2 = _FakeDownload2()
    d3._on_download(fd2)
    check("文件名非法字符被净化", fd2.name == "badname.zip", str(fd2.name))
    d3.close()

    # ── 6. _install_budeco 路由 ──
    calls: list[str] = []
    mod._install_from_zip_file = lambda p, a, t: calls.append(p)  # pyright: ignore[reportAttributeAccessIssue]

    # _install_budeco 的默认路径在 2026-10-17 已改为「resolve_download →
    # 直链/网盘/报错」三分流（内嵌浏览器降级为可选，见 _test_qq_login.py）；
    # 本文件只验证**可选的内嵌浏览器路径**：zip_ready 拿到 zip 后必须走安装。
    calls.clear()

    class _S:
        """pyqtSignal 的最小替身。"""

        def __init__(self):
            self.fn = None

        def connect(self, fn):  # noqa: ANN001
            self.fn = fn


    class _DlgCancel:
        def __init__(self, *a, **k):
            self.zip_path = None
            self.zip_ready = _S()

        def exec(self):
            return QDialog.DialogCode.Rejected

    orig_dialog = M.BrowserInstallDialog
    M.BrowserInstallDialog = _DlgCancel  # pyright: ignore[reportAttributeAccessIssue]
    mod._open_embedded_browser(addon, _Target())
    check("内嵌浏览器取消 → 不安装", calls == [])

    class _DlgOK:
        def __init__(self, *a, **k):
            self.zip_path = str(zip_path)
            self.zip_ready = _S()

        def exec(self):
            # 模拟：浏览器里下载完成 → zip_ready 发射
            if self.zip_ready.fn is not None:
                self.zip_ready.fn(str(zip_path))
            return QDialog.DialogCode.Accepted

    M.BrowserInstallDialog = _DlgOK  # pyright: ignore[reportAttributeAccessIssue]
    mod._open_embedded_browser(addon, _Target())
    check("内嵌浏览器拿到 zip → 触发安装", calls == [str(zip_path)], str(calls))
    M.BrowserInstallDialog = orig_dialog  # pyright: ignore[reportAttributeAccessIssue]

    print(f"\n{'ALL PASS' if FAIL == 0 else 'FAILED'}  {PASS} passed, {FAIL} failed")
    sys.exit(0 if FAIL == 0 else 1)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
