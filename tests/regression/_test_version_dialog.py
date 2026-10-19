"""VersionSelectorDialog 主按钮分发 + 下载安装链路回归。

守住两个已踩的坑：
1. 底部按钮曾**固定**连到本地逻辑（_on_local_accept），在线下载标签页下点了
   毫无反应，用户只能靠双击列表项才能触发下载。
2. 在线列表选中时按钮从不启用（只有本地选择变化才会启用）。
"""
import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)  # noqa: F841

from modules.version_library.module import (  # noqa: E402
    DownloadWorker,
    InstallWorker,
    VersionSelectorDialog,
)

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


def make_dialog():
    dlg = VersionSelectorDialog(blender_service=None)
    return dlg


# ── 1. 在线 tab：按钮文案与启用状态 ──
d = make_dialog()
check("初始在在线 tab", d.tabs.currentIndex() == 0)
check("在线 tab 未选中时按钮禁用", not d.add_local_btn.isEnabled())
check("在线 tab 按钮文案为下载并安装", "下载并安装" in d.add_local_btn.text(),
      d.add_local_btn.text())

# 选中一个在线版本 → 按钮应启用
lw = d._online_current_list()
check("能取到当前在线列表", lw is not None)
lw.setCurrentRow(0)
d._update_primary_button()
check("选中在线版本后按钮启用", d.add_local_btn.isEnabled())

# 点按钮 → 应走在线分支（设 selected_version 并 accept）
accepted = []
d.accept = lambda: accepted.append(True)  # type: ignore[method-assign]
d._on_primary_clicked()
check("点按钮触发在线下载分支", accepted == [True])
check("selected_version 被正确设置", bool(d.selected_version), str(d.selected_version))

# ── 2. 本地 tab：按钮文案与启用状态 ──
d.tabs.setCurrentIndex(1)
d._update_primary_button()
check("本地 tab 按钮文案为添加本地版本", "添加选中的本地版本" in d.add_local_btn.text(),
      d.add_local_btn.text())
check("本地 tab 未选中时按钮禁用", not d.add_local_btn.isEnabled())

# ── 3. 下载/安装线程可构造（不启动） ──
dw = DownloadWorker("https://example.invalid/blender.zip", os.devnull)
check("DownloadWorker 可构造", dw.url.endswith("blender.zip"))
check("DownloadWorker cancel 可调用", dw.cancel() is None)

iw = InstallWorker(os.devnull, os.devnull)
check("InstallWorker 可构造", iw.install_dir == os.devnull)

# ── 4. main_window 注入链路（ctx → module_manager → ModuleContext） ──
from PyQt6.QtWidgets import QMainWindow  # noqa: E402

from core.app_context import AppContext  # noqa: E402

ctx = AppContext()
check("ctx.main_window 初始为 None", ctx.main_window is None)
check("ModuleContext.main_window 初始为 None",
      ctx.module_manager._context.main_window is None)
win = QMainWindow()
ctx.set_main_window(win)
check("set_main_window 后 ctx 持有窗口", ctx.main_window is win)
check("set_main_window 后模块上下文同步持有",
      ctx.module_manager._context.main_window is win)

# ── 5. 版本库模块：后台校验必须存在且可达（不能是 _start_scan 这种不存在的方法） ──
import inspect  # noqa: E402

from modules.version_library.module import VersionLibraryModule  # noqa: E402

check("_render_from_cache_then_verify 存在",
      hasattr(VersionLibraryModule, "_render_from_cache_then_verify"))
src = inspect.getsource(VersionLibraryModule._render_from_cache_then_verify)
check("后台校验调用的方法真实存在",
      "_start_scan" not in src, "不得再引用不存在的 _start_scan")
check("后台校验改调 _refresh", "_refresh(" in src)

# ── 6. PyQt6 跨线程回调模式（曾用 invokeMethod+lambda，PyQt6 直接 TypeError） ──
from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402

got6: list = []
QTimer.singleShot(0, lambda: got6.append("singleShot"))
loop6 = QEventLoop()
QTimer.singleShot(300, loop6.quit)
loop6.exec()
check("QTimer.singleShot 跨线程回调可用", got6 == ["singleShot"])

import inspect  # noqa: E402

src_dl = inspect.getsource(VersionLibraryModule._download_version)
check("下载回调不再使用 invokeMethod(lambda)",
      "invokeMethod" not in src_dl, "PyQt6 的 invokeMethod 不接受 Python callable")
check("下载回调改用 pyqtSignal 桥接（唯一可靠路径）",
      "_DownloadBridge" in src_dl and "bridge.progress.emit" in src_dl)
check("下载回调不再依赖 singleShot 切线程",
      "singleShot" not in src_dl, "无 receiver 的 singleShot 在没有事件循环的下载线程里不触发")
check("add_task 传了 on_progress", "on_progress=on_progress" in src_dl)

print()
if failures:
    print(f"FAILED {len(failures)}: " + ", ".join(failures))
    sys.exit(1)
print("ALL PASS")