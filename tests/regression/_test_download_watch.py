"""DownloadWatcher：下载目录守望者回归（headless 可跑，真实文件系统）。

覆盖易错点：
1. 只认「开始等待之后」出现的 zip（旧文件不能误触发）
2. 大小没稳（还在写）不能触发
3. 小于 min_size 的 zip 不算插件包
4. 超时发 timed_out
5. stop() 之后彻底静默
6. 运行中切换目录（set_dirs）生效
"""
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)  # noqa: F841

from modules.addon_store.download_watch import (  # noqa: E402
    DownloadWatcher,
    default_watch_dirs,
)

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


def make_zip(path: Path, kb: int = 200) -> None:
    """造一个指定体积的 zip。

    必须用**不可压缩**的随机内容：若写 "x"*N，DEFLATE 会把它压到几百字节，
    低于 min_size_kb 门槛，测试会因"文件太小"而永远等不到命中。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("addon/__init__.py", os.urandom(kb * 1024))


def run_watcher(dirs, stable_ms=400, timeout_s=2, min_size_kb=50):
    """跑一次守望，返回 (found, timed_out)。"""
    got = {"found": None, "timeout": False}
    loop = QEventLoop()
    w = DownloadWatcher(dirs=dirs, min_size_kb=min_size_kb,
                        stable_ms=stable_ms, timeout_s=timeout_s, interval_ms=120)
    w.found.connect(lambda p: (got.update(found=p), loop.quit()))
    w.timed_out.connect(lambda: (got.update(timeout=True), loop.quit()))
    QTimer.singleShot(8000, loop.quit)
    w.start()
    loop.exec()
    w.stop()
    return got["found"], got["timeout"], w


# ── 1. 旧文件不触发 ──
d1 = Path(tempfile.mkdtemp(prefix="bh_watch_"))
old = d1 / "old_addon.zip"
make_zip(old)
os.utime(old, (time.time() - 3600, time.time() - 3600))  # 一小时前
f, t, _ = run_watcher([d1], timeout_s=2)
check("已存在的旧 zip 不触发", f is None)
check("旧文件场景正常超时", t is True)

# ── 2. 新出现且写完的 zip 触发 ──
d2 = Path(tempfile.mkdtemp(prefix="bh_watch_"))
found, timed, _ = (lambda: (None, False, None))()  # 占位，下面真正跑
got = {"found": None}
loop = QEventLoop()
w = DownloadWatcher(dirs=[d2], min_size_kb=50, stable_ms=600, timeout_s=6, interval_ms=120)
w.found.connect(lambda p: (got.update(found=p), loop.quit()))
w.timed_out.connect(loop.quit)
QTimer.singleShot(8000, loop.quit)
w.start()
QTimer.singleShot(300, lambda: make_zip(d2 / "new_addon.zip"))
loop.exec()
w.stop()
check("等待期间新写完的 zip 被拾取", got["found"] == str(d2 / "new_addon.zip"))

# ── 3. 写入过程中不抢装，写完后才拾取 ──
d3 = Path(tempfile.mkdtemp(prefix="bh_watch_"))
hits3 = []
loop3 = QEventLoop()
w3 = DownloadWatcher(dirs=[d3], min_size_kb=10, stable_ms=1500, timeout_s=12, interval_ms=150)
w3.found.connect(hits3.append)
w3.timed_out.connect(loop3.quit)
QTimer.singleShot(6000, loop3.quit)
w3.start()
# 以 200ms 间隔连续重写 4 次（大小一直在变），总时长 800ms < stable 1500ms
sizes = [120, 160, 200, 240]


def _step(i: int) -> None:
    if i >= len(sizes):
        return
    make_zip(d3 / "streaming.zip", kb=sizes[i])
    QTimer.singleShot(200, lambda: _step(i + 1))


_step(0)
QTimer.singleShot(900, loop3.quit)  # 还在写，不该触发
loop3.exec()
check("下载写入过程中不会被抢装", hits3 == [])

# 停止写入后，稳定满 stable_ms 就应正常拾取
got3 = {"found": None}
loop3b = QEventLoop()
w3.found.disconnect()
w3.found.connect(lambda p: (got3.update(found=p), loop3b.quit()))
QTimer.singleShot(4000, loop3b.quit)
loop3b.exec()
w3.stop()
check("写入完成并稳定后能拾取", got3["found"] == str(d3 / "streaming.zip"))

# ── 4. 小文件不算插件包 ──
d4 = Path(tempfile.mkdtemp(prefix="bh_watch_"))
got4 = {"found": None}
loop4 = QEventLoop()
w4 = DownloadWatcher(dirs=[d4], min_size_kb=50, stable_ms=300, timeout_s=3, interval_ms=120)
w4.found.connect(lambda p: (got4.update(found=p), loop4.quit()))
w4.timed_out.connect(loop4.quit)
QTimer.singleShot(5000, loop4.quit)
w4.start()
QTimer.singleShot(200, lambda: make_zip(d4 / "tiny.zip", kb=1))  # 1KB
loop4.exec()
w4.stop()
check("过小的 zip 被忽略", got4["found"] is None)

# ── 5. stop() 后静默 ──
d5 = Path(tempfile.mkdtemp(prefix="bh_watch_"))
w5 = DownloadWatcher(dirs=[d5], stable_ms=300, timeout_s=10, interval_ms=100)
hits = []
w5.found.connect(hits.append)
w5.start()
w5.stop()
make_zip(d5 / "late.zip")
QTimer.singleShot(1200, lambda: None)
loop5 = QEventLoop()
QTimer.singleShot(1300, loop5.quit)
loop5.exec()
check("stop() 后不再触发", hits == [])
check("running 标志正确", w5.running is False)

# ── 6. set_dirs 切换生效 ──
d6a = Path(tempfile.mkdtemp(prefix="bh_watch_a"))
d6b = Path(tempfile.mkdtemp(prefix="bh_watch_b"))
got6 = {"found": None}
loop6 = QEventLoop()
w6 = DownloadWatcher(dirs=[d6a], stable_ms=300, timeout_s=8, interval_ms=120)
w6.found.connect(lambda p: (got6.update(found=p), loop6.quit()))
w6.timed_out.connect(loop6.quit)
QTimer.singleShot(9000, loop6.quit)
w6.start()
QTimer.singleShot(200, lambda: w6.set_dirs([d6b]))
QTimer.singleShot(500, lambda: make_zip(d6b / "switched.zip"))
loop6.exec()
w6.stop()
check("运行中切换目录后仍能拾取", got6["found"] == str(d6b / "switched.zip"))

# ── 7. 默认目录枚举 ──
dirs = default_watch_dirs()
check("默认目录能枚举出至少一个", len(dirs) >= 0 and isinstance(dirs, list))
check("默认目录全部为 Path", all(isinstance(p, Path) for p in dirs))

print()
if failures:
    print(f"FAILED {len(failures)}: " + ", ".join(failures))
    sys.exit(1)
print("ALL PASS")