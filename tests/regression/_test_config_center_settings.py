"""设置页并入配置中心的回归测试。

验证：
1. 侧边栏模块顺序为 项目 / 更新 / 插件 / 配置（设置不再单独成页）
2. 配置中心存在两个标签页：Blender 配置 / 应用设置
3. 应用设置标签页里的设置项（镜像源下拉、安装位置输入）已正常构建
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit, QTabWidget  # noqa: E402

app = QApplication(sys.argv)

from core.app_context import AppContext  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


ctx = AppContext()
mm = ctx.module_manager
mm.discover()
mm.load_all()

# ── 1. 模块顺序与名称 ──
mods = [(i.id, i.name) for i in mm.get_all_modules()]
expect = [
    ("project_center", "项目"),
    ("version_library", "更新"),
    ("addon_store", "插件"),
    ("config_center", "配置"),
]
check("模块顺序符合侧边栏", mods == expect, str(mods))

# ── 2. 配置中心标签页 ──
widget = mm.get_module_widget("config_center")
check("配置中心能提供界面", widget is not None)
tabs = widget.findChild(QTabWidget) if widget is not None else None
check("存在标签页控件", tabs is not None)
if tabs is not None:
    labels = [tabs.tabText(i) for i in range(tabs.count())]
    check("两个标签页", tabs.count() == 2, str(labels))
    check("标签页名称正确", labels == ["Blender 配置", "应用设置"], str(labels))

    settings_page = tabs.widget(1)
    combos = settings_page.findChildren(QComboBox) if settings_page is not None else []
    edits = settings_page.findChildren(QLineEdit) if settings_page is not None else []
    check("应用设置含镜像源下拉", any(c.count() > 0 for c in combos), f"{len(combos)} 个下拉")
    check("应用设置含安装位置输入框", len(edits) >= 1, f"{len(edits)} 个输入框")

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
