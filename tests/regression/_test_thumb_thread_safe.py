"""缩略图后台解析必须走 QImage（线程安全）的回归测试。

背景：QPixmap 属于 GUI 资源，Qt 要求只能在主线程创建/使用。早先后台任务里
直接 `load_thumbnail()`（内部 QPixmap.fromImage）→ Windows 真机崩溃。
现在后台只产出 QImage，回主线程再转 QPixmap。
"""
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtGui import QImage, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from modules.project_center.module import _ThumbTask, load_thumbnail_image  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


def make_blend(path: Path, w: int, h: int, raw: bytes, with_wh_header: bool) -> None:
    header = b"BLENDER" + b"-" + b"v" + b"\x00\x00\x00"   # 8 字节指针 + 小端
    payload = (struct.pack("<ii", w, h) + raw) if with_wh_header else raw

    def block(code: bytes, data: bytes) -> bytes:
        return (code + struct.pack("<i", len(data)) + b"\x00" * 8
                + struct.pack("<ii", 0, 0) + data)

    path.write_bytes(header + block(b"TEST", payload) + block(b"ENDB", b""))


tmp = Path(tempfile.mkdtemp(prefix="bh_thumb_ts_"))
raw = b"\x00\x00\xff\xff" * (4 * 4)      # 4x4 RGBA
blend = tmp / "proj.blend"
make_blend(blend, 4, 4, raw, with_wh_header=True)

# 1. 后台函数返回 QImage（不是 QPixmap）
img = load_thumbnail_image(blend)
check("返回 QImage", isinstance(img, QImage), type(img).__name__)
check("尺寸正确", img is not None and (img.width(), img.height()) == (4, 4))

# 2. 后台任务真的在线程池里跑，并投递 QImage
got = []
task = _ThumbTask(blend)
task.signals.ready.connect(lambda p, im: got.append((p, im)))
task.run()   # 直接执行 run()，等价于线程池里执行
check("任务投递了结果", len(got) == 1, str(len(got)))
if got:
    check("投递的是 QImage 而非 QPixmap",
          isinstance(got[0][1], QImage) and not isinstance(got[0][1], QPixmap),
          type(got[0][1]).__name__)
    check("路径正确", got[0][0] == str(blend))

# 3. 主线程转换仍可用
pm = QPixmap.fromImage(got[0][1]) if got else QPixmap()
check("主线程可转 QPixmap", not pm.isNull())

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
