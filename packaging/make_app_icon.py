"""生成应用图标 packaging/icon.ico（矢量绘制，无外部素材依赖）。

设计：橙色圆角方块 + 六向辐射「枢纽」图案，图案部分**挖空透明**
（深色任务栏下呈深色、浅色下呈白色，自动适配主题）。

几何取自原始设计稿的像素测量，归一化到 22x22 网格：
  方块 22x22、圆角 4.6；中心橙圆 r=3.0；深色环 3.0..5.0；
  6 条辐条宽 2.0、从 3.0 到 7.5、间隔 60°、首条朝上。

用法：
    python packaging/make_app_icon.py
输出：同目录 icon.ico（256/128/96/64/48/32/24/16 共 8 帧）
"""
from __future__ import annotations

import math
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

# ---------------------------------------------------------------- 设计参数
# 由 22x22 像素级对拍 + 网格搜索拟合（深色像素 IoU ≈ 0.90，
# 剩余差异来自 22px 图上抗锯齿边缘的灰阶过渡，无法再提升）。
ORANGE = "#D97722"      # 橙色主色（原稿实测 #D37221~#DF7D2E 中值）
PATTERN = "#110C08"     # 图案颜色：深色实心（原稿实测 rgb(17,12,8)，非镂空）
GRID = 22.0             # 设计网格边长
R_CORNER = 7.5          # 圆角半径（原稿 y=0 行深色占 0-4，对应圆角约 7.5）
R_INNER = 3.0           # 中心橙色圆半径
R_OUTER = 4.7           # 环外径
R_TIP = 9.0             # 辐条端点距中心
SPOKE_W = 1.8           # 辐条宽度
SPOKES = 6              # 辐条数量
FIRST_ANGLE = 90.0      # 首条辐条角度（朝上）
SIZES = (256, 128, 96, 64, 48, 32, 24, 16)

# 所有尺寸共用同一套几何，不做小尺寸简并。
# 靠超采样倍率保证缩小时的采样精度：16px 目标用 1024px 画布（64x），
# 让环带与辐条各自的覆盖面积被准确平均，而不是靠删图层换可读性。
SS_FACTOR = 8
SS_MIN = 1024


def render(size: int) -> QImage:
    """绘制单帧。

    先按 SS_FACTOR 倍超采样（不低于 SS_MIN 像素）绘制，再平滑缩到目标尺寸。
    超采样倍率足够高时，环带与辐条的覆盖率能被准确积分，
    小尺寸下表现为线条变淡/变细，而不是混成一整块深色。
    """
    ss = max(size * SS_FACTOR, SS_MIN)
    k = ss / GRID
    img = QImage(ss, ss, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    body = QPainterPath()
    body.addRoundedRect(QRectF(0, 0, ss, ss), R_CORNER * k, R_CORNER * k)
    p.fillPath(body, QColor(ORANGE))

    c = ss / 2.0
    # 完整画法：环 + 辐条，辐条从环外缘起始（不穿过环带）
    ring = QPainterPath()
    ring.addEllipse(QPointF(c, c), R_OUTER * k, R_OUTER * k)
    ring.addEllipse(QPointF(c, c), R_INNER * k, R_INNER * k)
    ring.setFillRule(Qt.FillRule.OddEvenFill)
    p.fillPath(ring, QColor(PATTERN))

    pen = QPen(QColor(PATTERN))
    pen.setWidthF(SPOKE_W * k)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    for i in range(SPOKES):
        a = math.radians(FIRST_ANGLE + i * (360.0 / SPOKES))
        dx, dy = math.cos(a), -math.sin(a)      # 屏幕坐标 y 轴向下
        p.drawLine(QPointF(c + R_OUTER * k * dx, c + R_OUTER * k * dy),
                   QPointF(c + R_TIP * k * dx, c + R_TIP * k * dy))
    p.end()

    if ss != size:
        img = img.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


def build_ico(frames: list[tuple[int, bytes]]) -> bytes:
    """组装 ICO：ICONDIR + 每帧 ICONDIRENTRY + 内嵌 PNG 数据。

    Vista 以后 256x256 帧必须以 PNG 内嵌（BMP 也行但体积大 20 倍）；
    这里统一用 PNG，Windows 10/11 全部支持。
    """
    header = struct.pack("<HHH", 0, 1, len(frames))   # reserved, type=1(icon), count
    entries, blob = b"", b""
    offset = 6 + 16 * len(frames)
    for size, png in frames:
        entries += struct.pack("<BBBBHHII",
                               size if size < 256 else 0,   # 0 表示 256
                               size if size < 256 else 0,
                               0,                            # 调色板数
                               0,                            # reserved
                               1,                            # 色彩平面
                               32,                           # 位深
                               len(png), offset)
        offset += len(png)
        blob += png
    return header + entries + blob


def main() -> int:
    out_dir = Path(__file__).resolve().parent
    frames = [(s, png_bytes(render(s))) for s in SIZES]
    ico = build_ico(frames)
    target = out_dir / "icon.ico"
    target.write_bytes(ico)
    print(f"已生成 {target}  ({len(ico)} 字节)")
    for s, b in frames:
        print(f"  {s:3d}x{s:<3d} {len(b):6d} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
