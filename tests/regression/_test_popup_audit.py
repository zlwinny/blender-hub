"""验证弹窗审计：装了之后，弹窗/浮层都会留痕，且不改变原有行为。"""
import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

app = QApplication(sys.argv)

from core.popup_audit import install  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


# 弹窗/exec 在 offscreen 下会开模态事件循环、没人点就永久卡住（本项目已知坑），
# 所以先把它们的真实实现换成桩，只验证审计的包装与记录逻辑。
QMessageBox.information = staticmethod(lambda *a, **k: 1024)
QMessageBox.warning = staticmethod(lambda *a, **k: 1024)
QDialog.exec = lambda self: 1  # type: ignore[assignment]

hits = []
ok = install(lambda title, kind, where: hits.append((title, kind, where)))
check("审计安装成功", ok)

# 1) QMessageBox 类方法（标题是第 2 个参数）
ret = QMessageBox.information(None, "测试标题", "内容")
check("QMessageBox.information 被记录",
      any(h[0] == "测试标题" and "QMessageBox.information" in h[1] for h in hits),
      str(hits[:2]))
check("记录了调用位置（本测试文件）",
      any("test_popup_audit" in h[2] for h in hits), str(hits[:2]))
check("原行为未变（仍有返回值）", ret is not None, str(ret))

# 2) 自定义对话框 exec（标题取 windowTitle）
hits.clear()
w = QDialog()
w.setWindowTitle("我的对话框")
w.exec()
check("QDialog.exec 被记录",
      any(h[0] == "我的对话框" and "QDialog.exec" in h[1] for h in hits), str(hits[:2]))

# 3) 重复安装不应重复包装（否则日志翻倍）
before = len(hits)
install(lambda title, kind, where: hits.append((title, kind, where)))
hits.clear()
QMessageBox.warning(None, "第二次", "内容")
check("重复安装不会重复包装", len(hits) == 1, str(hits))

# 4) 全局 show 监听：任何顶层窗口被 show 都要留痕
from core.popup_audit import install_show_watcher  # noqa: E402

watch_hits = []
ok2 = install_show_watcher(
    app, lambda title, cls, extra: watch_hits.append((title, cls, extra))
)
check("show 监听安装成功", ok2)
d2 = QDialog()
d2.setWindowTitle("被监听的窗口")
d2.show()
app.processEvents()
check("顶层窗口 show 被记录",
      any(h[0] == "被监听的窗口" and "QDialog" in h[1] for h in watch_hits),
      str(watch_hits[:2]))
d2.close()

# 子控件 show 不应淹没日志（只记录顶层窗口）
watch_hits.clear()
child = QDialog()
host = QDialog()
child.setParent(host)
child.show()
app.processEvents()
check("非顶层子窗口不会被记录", len(watch_hits) == 0, str(watch_hits[:2]))
host.close()

# 5) 孤儿守卫：无标题的顶层窗口会被自动关掉；有标题的不能碰
from core.popup_audit import install_orphan_guard  # noqa: E402

guard_hits = []
ok3 = install_orphan_guard(app, lambda m: guard_hits.append(m), interval_ms=1000)
check("孤儿守卫安装成功", ok3)

orphan = QDialog()          # 没有 setWindowTitle → 标题为空
orphan.show()
app.processEvents()
QEventLoopWait = None
import time as _t  # noqa: E402

_t.sleep(1.6)
app.processEvents()
check("无标题顶层窗口被守卫关掉", not orphan.isVisible(), str(orphan.isVisible()))
check("关掉时留了日志", any("orphan closed" in h for h in guard_hits), str(guard_hits[:1]))

named = QDialog()
named.setWindowTitle("正常窗口")
named.show()
app.processEvents()
_t.sleep(1.6)
app.processEvents()
check("有标题的窗口不受影响", named.isVisible())
named.close()

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.exit(1)
print("ALL PASS")
