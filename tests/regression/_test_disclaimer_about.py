"""免责声明与「关于」页的回归测试。

验证：
1. CI/无显示环境下 ensure_accepted 自动放行（不阻塞冒烟）
2. 首次启动对话框有「退出 / 同意」，只读查看只有「关闭」
3. 免责声明采用折中口径：不禁止商业使用，且与 MIT 一致
4. 关于页包含 应用名 / 版本 / 免责声明 / 开源许可证 入口
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_SKIP_DISCLAIMER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

app = QApplication(sys.argv)

from core.disclaimer import (  # noqa: E402
    DISCLAIMER_TEXT,
    build_dialog,
    ensure_accepted,
    should_skip,
)
from core.app_context import AppContext  # noqa: E402
from modules.settings import SettingsModule, _LinkRow  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


# ── 1. 无显示环境自动放行 ──
check("BH_SKIP_DISCLAIMER 生效", should_skip() is True)
check("ensure_accepted 不阻塞", ensure_accepted() is True)

# ── 2. 首次启动与只读两种对话框 ──
first = build_dialog(None, first_run=True)
texts = [b.text() for b in first.findChildren(QPushButton)]
check("首次启动有退出/同意", texts == ["退出", "同意"], str(texts))
check("首次启动标题正确", first.windowTitle() == "免责声明")

readonly = build_dialog(None, first_run=False)
texts2 = [b.text() for b in readonly.findChildren(QPushButton)]
check("只读查看只有关闭", texts2 == ["关闭"], str(texts2))

# ── 3. 折中口径：不禁止商业，保留 MIT ──
check("声明提到 MIT", "MIT" in DISCLAIMER_TEXT)
check("声明不禁止商业", "严禁" not in DISCLAIMER_TEXT and "商业目的" not in DISCLAIMER_TEXT)
check("声明包含第三方授权提示", "第三方" in DISCLAIMER_TEXT)

# ── 4. 关于页内容 ──
ctx = AppContext()
sm = SettingsModule(ctx, show_header=False)
labels = [lb.text() for lb in sm.findChildren(QLabel)]
check("关于页显示应用名", "Blender Hub" in labels)
check("关于页显示版本", "v1.0" in labels)
check("关于页有免责声明入口", "免责声明" in labels)
check("关于页有开源许可入口", "开源许可证 MIT" in labels)
rows = sm.findChildren(_LinkRow)
check("更多列表有 5 行", len(rows) == 5, str(len(rows)))
check("许可文本读取成功", "MIT License" in SettingsModule._license_text())

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
