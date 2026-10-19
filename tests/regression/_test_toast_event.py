"""ui.toast 事件接通的回归测试。

背景：EventBus 在生产代码里零订阅，模块侧 emit 的 ui.toast（如扫描完成提示）
永远不显示。MainWindow 现在订阅该事件并转 notify()，且跨线程安全。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_SKIP_DISCLAIMER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from core.app_context import AppContext  # noqa: E402
from core.main_window import MainWindow  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


ctx = AppContext()
win = MainWindow(ctx)
win.show()
app.processEvents()

# 订阅者数量：MainWindow 构造后 ui.toast 至少有一个订阅者
count = ctx.bus.subscriber_count("ui.toast")
check("ui.toast 有订阅者", count >= 1, str(count))

# 替换 notify 捕获输出（避免真的弹 Toast）
captured = []
win.notify = lambda text, ok=True: captured.append((text, ok))

ctx.bus.emit("ui.toast", {"message": "扫描完成，找到 4 个版本"})
app.processEvents()
check("emit 后 notify 被调用", len(captured) == 1, str(captured))
if captured:
    check("提示内容正确", captured[0][0] == "扫描完成，找到 4 个版本", str(captured[0]))

# 纯字符串 payload 也应兼容
ctx.bus.emit("ui.toast", "字符串提示")
app.processEvents()
check("字符串 payload 兼容", len(captured) == 2, str(captured))

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
