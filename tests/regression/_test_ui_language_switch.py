"""界面中英文切换的回归测试。

覆盖：
1. tr() 在中/英下取到正确文案，缺失词条回退中文
2. 模块侧栏名（manifest 的 name / name_en）
3. 切换语言后：侧栏按钮、配置页标签、设置页文案即时刷新（无需重启）
测试结束会恢复原来的语言设置，避免污染本机偏好。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_SKIP_DISCLAIMER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from core import i18n  # noqa: E402
from core.app_context import AppContext  # noqa: E402
from core.main_window import MainWindow  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


saved_lang = i18n.current_lang()

ctx = AppContext()
mm = ctx.module_manager
mm.discover()
mm.load_all()
win = MainWindow(ctx)
ctx.set_main_window(win)
for info in mm.get_all_modules():
    win.add_page(info.id, info.display_name(), mm.get_module_widget(info.id),
                 module=mm.load_module(info.id))
win.show()
app.processEvents()

# ── 1. 词条取值 ──
check("中文取到中文", i18n.tr("module.config.title") == "⚙️ 配置", i18n.tr("module.config.title"))
check("缺失词条回退 key", i18n.tr("not.exist.key") == "not.exist.key")

# ── 2. 侧栏中文 ──
nav_zh = [b.text() for b in win._nav_buttons]
check("侧栏中文名", nav_zh == ["项目", "更新", "插件", "配置"], str(nav_zh))

# ── 3. 切到英文 ──
i18n.set_lang(i18n.LANG_EN)
app.processEvents()

nav_en = [b.text() for b in win._nav_buttons]
check("侧栏英文名", nav_en == ["Projects", "Updates", "Addons", "Config"], str(nav_en))
check("英文词条生效", i18n.tr("module.config.title") == "⚙️ Config", i18n.tr("module.config.title"))

cc = mm.load_module("config_center")
tabs = getattr(cc, "_tabs", None)
check("配置页标签已刷新", tabs is not None
      and tabs.tabText(0) == "Blender config"
      and tabs.tabText(1) == "App settings",
      str([tabs.tabText(i) for i in range(tabs.count())] if tabs else None))

# 设置页（嵌入配置页 Tab 2）文案也应刷新
settings_page = tabs.widget(1) if tabs is not None else None
labels = [lb.text() for lb in settings_page.findChildren(type(tabs))] if settings_page else []
from PyQt6.QtWidgets import QLabel  # noqa: E402

labels = [lb.text() for lb in settings_page.findChildren(QLabel)] if settings_page else []
# section_title() 会转大写，断言时忽略大小写
check("设置页文案已刷新",
      any(t.lower() == "interface language" for t in labels), str(labels[:4]))

# ── 4. 切回中文 ──
i18n.set_lang(i18n.LANG_ZH)
app.processEvents()
check("切回中文侧栏", [b.text() for b in win._nav_buttons] == ["项目", "更新", "插件", "配置"],
      str([b.text() for b in win._nav_buttons]))

# 恢复测试前的语言设置，避免污染本机偏好
if i18n.current_lang() != saved_lang:
    i18n.set_lang(saved_lang)

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
