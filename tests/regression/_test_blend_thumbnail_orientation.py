"""项目中心缩略图上下颠倒的回归测试。

Blender 把视口预览写进 .blend 的 TEST 块，像素按 OpenGL 习惯**自下而上**存储。
旧实现直接交给 QImage(Format_RGBA8888) 按自上而下解释，卡片上的缩略图就是倒的。
本脚本构造一个上红下蓝的 TEST 块，验证读出后顶部确实是红色。

同时覆盖两条解析分支：
- 普通尺寸：payload = int32 w + int32 h + RGBA
- 256×256：payload 直接是 RGBA（无宽高头）
"""
import os
import struct
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from modules.project_center.module import _read_blend_preview  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


RED = (220, 30, 30, 255)
BLUE = (30, 30, 220, 255)


def bottom_up_raw(w, h):
    """构造自下而上存储的 RGBA：逻辑上半红、下半蓝。"""
    rows = []
    for stored_y in range(h):
        logical_y = h - 1 - stored_y          # 存储第 0 行 = 逻辑最后一行（底部）
        color = RED if logical_y < h // 2 else BLUE
        rows.append(bytes(color) * w)
    return b"".join(rows)


def make_blend(path, w, h, raw, with_wh_header):
    """写一个只含 TEST + ENDB 两个块的最小 .blend。"""
    ptr = 8
    header = b"BLENDER" + b"-" + b"v" + b"\x00\x00\x00"   # 8 字节指针 + 小端
    payload = (struct.pack("<ii", w, h) + raw) if with_wh_header else raw

    def block(code, data):
        return (code + struct.pack("<i", len(data)) + b"\x00" * ptr
                + struct.pack("<ii", 0, 0) + data)

    path.write_bytes(header + block(b"TEST", payload) + block(b"ENDB", b""))


tmp = Path(tempfile.mkdtemp(prefix="bh_thumb_"))

# ── 1. 普通尺寸（带宽高头）──────────────────────────────────────────────
p1 = tmp / "normal.blend"
make_blend(p1, 4, 4, bottom_up_raw(4, 4), with_wh_header=True)
img1 = _read_blend_preview(p1)
check("普通尺寸能读出预览", img1 is not None)
if img1 is not None:
    check("尺寸正确", (img1.width(), img1.height()) == (4, 4), f"{img1.width()}x{img1.height()}")
    top = img1.pixelColor(0, 0)
    bottom = img1.pixelColor(0, 3)
    check("顶部是红色（已垂直翻转）", top.red() > 200 and top.blue() < 60, top.name())
    check("底部是蓝色", bottom.blue() > 200 and bottom.red() < 60, bottom.name())

# ── 2. 256×256 分支（payload 无宽高头）─────────────────────────────────
p2 = tmp / "thumb256.blend"
make_blend(p2, 256, 256, bottom_up_raw(256, 256), with_wh_header=False)
img2 = _read_blend_preview(p2)
check("256×256 能读出预览", img2 is not None)
if img2 is not None:
    check("256 尺寸正确", (img2.width(), img2.height()) == (256, 256), f"{img2.width()}x{img2.height()}")
    top = img2.pixelColor(0, 0)
    bottom = img2.pixelColor(0, 255)
    check("256 顶部是红色（已垂直翻转）", top.red() > 200 and top.blue() < 60, top.name())
    check("256 底部是蓝色", bottom.blue() > 200 and bottom.red() < 60, bottom.name())

# ── 3. 非 blend / 损坏文件不抛异常 ─────────────────────────────────────
p3 = tmp / "not_a_blend.blend"
p3.write_bytes(b"NOTBLENDER" * 8)
check("非 blend 返回 None", _read_blend_preview(p3) is None)

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
