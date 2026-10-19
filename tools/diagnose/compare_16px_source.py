"""对比 16px 帧的两种来源，决定 ICO 是否保留原生 16px 帧。

A: 直接按 16px 渲染（当前做法）
B: 用 32px 帧平滑下采样到 16px
C: 用 48px 帧平滑下采样到 16px

判据：把结果放大后看环带是否仍可分辨。用 8 倍最近邻放大并排展示。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packaging"))
import make_app_icon as M  # noqa: E402

_APP = QGuiApplication.instance() or QGuiApplication([])


def main(out: Path) -> None:
    direct = M.render(16)
    from32 = M.render(32).scaled(
        16, 16, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    from48 = M.render(48).scaled(
        16, 16, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )

    variants = [
        ("A direct 16", direct),
        ("B from 32", from32),
        ("C from 48", from48),
    ]

    zoom = 16
    cell = 16 * zoom + 24
    pad = 24
    width = pad * 2 + cell * len(variants)
    height = 60 + cell + 40

    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ffffff"))
    p = QPainter(canvas)

    for i, (name, img) in enumerate(variants):
        x = pad + i * cell
        p.fillRect(QRect(x, 50, cell - 8, cell - 8), QColor("#202020"))
        z = img.scaled(16 * zoom, 16 * zoom, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.FastTransformation)
        p.drawImage(x + (cell - 8 - z.width()) // 2, 50 + (cell - 8 - z.height()) // 2, z)
        p.setPen(QColor("#222"))
        p.drawText(QRect(x, 30, cell - 8, 16), Qt.AlignmentFlag.AlignCenter, name)

        # 统计环带区域的不透明度：环越糊，中间态 alpha 越多
        a = img.convertToFormat(QImage.Format.Format_ARGB32)
        mid = 0
        for yy in range(16):
            for xx in range(16):
                al = a.pixelColor(xx, yy).alpha()
                if 20 < al < 235:
                    mid += 1
        p.setPen(QColor("#555"))
        p.drawText(QRect(x, 50 + cell - 4, cell - 8, 16),
                   Qt.AlignmentFlag.AlignCenter, f"semi-alpha px={mid}")
    p.end()
    canvas.save(str(out))
    print(f"saved {out} {canvas.width()}x{canvas.height()}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
