"""Blender Hub v1.0 入口 — 新版 UI 外壳 + 全部功能模块（ModuleManager 动态加载）。"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time

# 无显示环境下也能构造窗口（打包冒烟用）
if os.environ.get("BH_OFFSCREEN"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QIcon  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# QtWebEngine 必须**早于 QApplication**完成初始化，两件事缺一不可：
#  ① Chromium flags 要在引擎起来之前设好（打包环境默认沙箱/GPU 沙箱会 fail-fast）
#  ② QtWebEngineWidgets 模块要在 QCoreApplication 之前 import —— 只设
#     AA_ShareOpenGLContexts 只能"绕过检查"，不等于完成初始化，运行期真正
#     创建 QWebEngineView 时会在 Qt6Core.dll 里以 0xc0000409 崩溃
#     （2026-10-17 13:41/13:42 两次真机崩溃即此）。
# 默认保留 Chromium 渲染进程沙箱，只关闭 GPU 沙箱以规避打包环境的 GPU 初始化问题。
# 若某些机器仍出现启动即崩溃（0xc0000409），可显式设置 BH_UNSAFE_NO_SANDBOX=1
# 回退到旧的「全关沙箱」行为——这会降低安全性，仅作为排障兜底。
_flags = "--disable-gpu-sandbox --disable-features=RendererCodeIntegrity"
# 打包运行环境（frozen）已知会因 Chromium 沙箱 fail-fast 直接崩（0xc0000409），
# 原实现无条件关闭沙箱。这里收紧为：仅打包环境或显式 BH_UNSAFE_NO_SANDBOX=1 才关，
# 源码运行保留渲染进程沙箱（安全性更好，出问题可自行加开关排障）。
if getattr(sys, "frozen", False) or os.environ.get("BH_UNSAFE_NO_SANDBOX"):
    _flags = "--no-sandbox " + _flags
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", _flags)

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
try:  # 提前导入以触发 QtWebEngineQuick::initialize()
    from PyQt6 import QtWebEngineWidgets as _QWE  # noqa: F401,E402
except Exception as _e:  # noqa: BLE001
    print(f"[app] 警告：内嵌浏览器不可用（{_e}），登录/下载将走外部浏览器兜底")

# ── 崩溃诊断（frozen windowed 模式没有控制台，异常与 Qt 的 qFatal 会静默丢失）──
# 2026-10-18 真机点「登录 Budeco」整个进程消失（0xc0000409 fail-fast），Python 层
# 抓不到任何信息。这里把 fd 1/2 重定向到日志文件并开 faulthandler：
# 即使 native crash，也能尽量留下 Python 栈供定位。
if getattr(sys, "frozen", False):
    try:
        import faulthandler

        _diag_dir = Path(os.environ.get("TEMP", ".")) / "BlenderHub"
        _diag_dir.mkdir(parents=True, exist_ok=True)
        _diag_fh = open(_diag_dir / "webengine_diag.log", "a", buffering=1,
                        encoding="utf-8", errors="replace")
        os.dup2(_diag_fh.fileno(), 1)
        os.dup2(_diag_fh.fileno(), 2)
        faulthandler.enable(all_threads=True)
        print(f"--- BlenderHub 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} "
              f"(pid={os.getpid()}) ---", flush=True)
    except Exception as _diag_err:  # noqa: BLE001
        print(f"[app] 诊断重定向失败（不影响启动）: {_diag_err}")

# frozen 运行时：把打包内的 modules/ 加入 sys.path，
# 否则 ModuleManager 的 importlib.import_module("addon_store.module") 找不到模块
if getattr(sys, "frozen", False):
    _mei_modules = Path(sys._MEIPASS) / "modules"  # type: ignore[attr-defined]
    if _mei_modules.exists() and str(_mei_modules) not in sys.path:
        sys.path.insert(0, str(_mei_modules))
else:
    _src_modules = Path(__file__).resolve().parent / "modules"
    if str(_src_modules) not in sys.path:
        sys.path.insert(0, str(_src_modules))

from core import theme  # noqa: E402
from core.app_context import AppContext  # noqa: E402
from core.disclaimer import ensure_accepted  # noqa: E402
from core.main_window import MainWindow  # noqa: E402

def _app_icon_path() -> Path | None:
    """定位应用图标文件。

    旧实现从未调用 setWindowIcon，窗口标题栏和任务栏一直显示 Qt 默认图标。
    frozen 运行时图标随 --add-data 放在 _MEIPASS 根目录；
    开发环境在 packaging/ 下（历史上放在 build/，保留兼容）。
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / "icon.ico")  # type: ignore[attr-defined]
    root = Path(__file__).resolve().parent
    candidates.append(root / "packaging" / "icon.ico")
    candidates.append(root / "build" / "icon.ico")   # 旧布局兼容
    for p in candidates:
        if p.exists():
            return p
    return None


def _prewarm_blender_cache(ctx) -> None:
    """后台预热 Blender 版本探测。

    detect_installed()（注册表 + 常见目录 + 子进程 --version）首次调用可能要
    几秒，而「更新 / 项目 / 插件 / 配置」多个模块都会在 UI 线程里调它。
    启动时先跑一遍填缓存，用户第一次点进去就不会卡住。
    失败静默：预热只是优化，出问题不应影响启动。
    """
    try:
        ctx.blender.detect_installed()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    # 注：AA_ShareOpenGLContexts 与 QtWebEngineWidgets 的提前导入已在模块顶层完成，
    # 必须早于这里的 QApplication 创建（Qt 的硬性要求）。
    app = QApplication(sys.argv)
    app.setApplicationName("Blender Hub")
    app.setOrganizationName("BlenderHub Team")
    app.setStyleSheet(theme.stylesheet())

    # 窗口图标（标题栏 + 任务栏 + Alt-Tab）
    icon_path = _app_icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        print("[app] 警告：未找到 packaging/icon.ico，窗口将使用默认图标")

    # 首次启动免责声明：不同意直接退出；BH_OFFSCREEN / BH_SKIP_DISCLAIMER 时跳过
    if not ensure_accepted():
        return 0

    ctx = AppContext()

    # 后台预热版本探测（与窗口构建并行）；无显示环境（CI）跳过
    if not os.environ.get("BH_OFFSCREEN"):
        threading.Thread(target=_prewarm_blender_cache, args=(ctx,), daemon=True).start()

    # ── 弹窗审计：任何对话框弹出都记一条日志（标题 + 调用位置）。
    # 用户报"每次切到插件市场都会弹窗"时，光读代码定位不到触发点，
    # 有了这条日志，复现一次就能从日志里看到是谁弹的。
    try:
        from core.popup_audit import install as _install_popup_audit

        _install_popup_audit(
            lambda title, kind, where: ctx.info(
                f"popup: [{kind}] {title}  <- {where}", module="popup_audit",
            )
        )

        # 任何顶层窗口被 show 都留痕（抓"不知道谁开的空白窗口"）
        from core.popup_audit import install_show_watcher as _install_watcher

        _install_watcher(
            app,
            lambda title, cls, extra: ctx.info(
                f"window shown: {cls} title={title!r} {extra}", module="popup_audit",
            ),
        )

        # 兜底：周期清掉无标题的顶层空窗口（用户报的"空白 Blender Hub 窗"）
        from core.popup_audit import install_orphan_guard as _install_guard

        _install_guard(
            app,
            lambda msg: ctx.info(msg, module="popup_audit"),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[app] 弹窗审计未启用: {e}")

    mm = ctx.module_manager
    mm.discover()
    mm.load_all()

    win = MainWindow(ctx)
    ctx.set_main_window(win)  # 模块通过 ctx.main_window 驱动全局 UI（如底部下载条）
    for info in mm.get_all_modules():
        widget = mm.get_module_widget(info.id)
        if widget is None:
            ctx.warn(f"模块 {info.id} 未能提供界面", module="app")
            continue
        # 传模块实例，供 MainWindow 在页面切换时派发 on_navigate_to / on_navigate_away
        win.add_page(info.id, info.name, widget, module=mm.load_module(info.id))
    # 应用设置已并入「配置中心」的「应用设置」标签页，不再单独占用侧边栏

    win.show()
    ctx.info(f"应用启动完成，已加载 {win.stack.count()} 个页面", module="app")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
