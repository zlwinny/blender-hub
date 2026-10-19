"""生成图标小尺寸对比预览图（旧简化画法 vs 统一完整画法）。

用法:
    python preview_icon_compare.py <old.ico> <new.ico> <out.png>
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

# offscreen 平台下若没有 QGuiApplication 实例，QPainter.setFont / drawText
# 会直接静默终止进程（exit 127，faulthandler 也抓不到）。绘制文字前必须建实例。
_APP = QGuiApplication.instance() or QGuiApplication([])


def load_frames(path: str | Path) -> dict[int, QImage]:
    data = Path(path).read_bytes()
    count = struct.unpack_from("<HHH", data, 0)[2]
    frames: dict[int, QImage] = {}
    for i in range(count):
        w, h, _cc, _r, _pl, _bpp, size, off = struct.unpack_from(
            "<BBBBHHII", data, 6 + i * 16
        )
        img = QImage.fromData(data[off: off + size])
        if not img.isNull():
            frames[w or 256] = img
    return frames


def build(old_path: Path, new_path: Path, out_path: Path) -> None:
    old = load_frames(old_path)
    new = load_frames(new_path)

    sizes = [s for s in (16, 24, 32) if s in old and s in new]
    zoom = 12
    pad = 14
    cell = 32 * zoom
    width = pad * 3 + cell * 2
    height = 40 + (cell + 8) * len(sizes) * 2 + pad

    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ffffff"))
    p = QPainter(canvas)
    p.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
    p.setPen(QColor("#222"))
    p.drawText(
        QRect(0, 6, width, 24),
        Qt.AlignmentFlag.AlignCenter,
        "上 = 旧简化画法   |   下 = 统一完整画法（本次）",
    )
    p.setFont(QFont("Microsoft YaHei", 8))

    y = 34
    for frames, tag in ((old, "旧"), (new, "新")):
        for s in sizes:
            for col, bg, tc in (
                (0, QColor("#ffffff"), "#999999"),
                (1, QColor("#1e1e1e"), "#888888"),
            ):
                x = pad + col * (cell + pad)
                p.fillRect(QRect(x, y, cell, cell), bg)
                p.setPen(QColor(tc))
                p.drawText(
                    QRect(x + 2, y + 1, cell, 14),
                    Qt.AlignmentFlag.AlignLeft,
                    f"{tag} {s}px",
                )
                img = frames[s].scaled(
                    s * zoom,
                    s * zoom,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                p.drawImage(
                    x + (cell - img.width()) // 2,
                    y + (cell - img.height()) // 2,
                    img,
                )
            y += cell + 8
        y += pad
    p.end()
    canvas.save(str(out_path))
    print(f"saved {out_path}  {canvas.width()}x{canvas.height()}")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
