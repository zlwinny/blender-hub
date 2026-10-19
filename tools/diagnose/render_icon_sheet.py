"""从 exe 提取图标并生成交付验证图（各尺寸实拍 + 放大）。

用法:
    python render_icon_sheet.py <exe> <out.png>
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_pe_icons import _rva_to_off, _sections  # noqa: E402
from read_pe_icons import read_resources  # noqa: E402

_APP = QGuiApplication.instance() or QGuiApplication([])

RT_ICON = 3
RT_GROUP_ICON = 14


def exe_frames(path: str | Path) -> dict[int, QImage]:
    """解析 exe 的 RT_GROUP_ICON，把每个 RT_ICON 解出来。"""
    raw = Path(path).read_bytes()
    res = read_resources(path)
    grp = res.get(RT_GROUP_ICON)
    if not grp:
        raise ValueError("没有 RT_GROUP_ICON")

    pe_off = struct.unpack_from("<I", raw, 0x3C)[0]
    magic = struct.unpack_from("<H", raw, pe_off + 0x18)[0]
    dd = 96 if magic == 0x10B else 112
    dir_rva = struct.unpack_from("<I", raw, pe_off + 0x18 + dd + 16)[0]
    secs = _sections(raw)
    root = _rva_to_off(secs, dir_rva)

    # 收集 type=3 下每个 ID 的数据
    icons: dict[int, bytes] = {}

    def walk(off: int, depth: int, tid: int | None, rid: int | None) -> None:
        named, by_id = struct.unpack_from("<HH", raw, off + 12)
        for i in range(named + by_id):
            name_id, fld = struct.unpack_from("<II", raw, off + 16 + i * 8)
            rel = fld & 0x7FFFFFFF
            if fld & 0x80000000:
                if depth == 0:
                    walk(root + rel, 1, name_id, None)
                elif depth == 1:
                    walk(root + rel, 2, tid, name_id)
                continue
            rva, size = struct.unpack_from("<II", raw, root + rel)
            co = _rva_to_off(secs, rva)
            if tid == RT_ICON and rid is not None and co is not None:
                icons.setdefault(rid, raw[co: co + size])

    walk(root, 0, None, None)

    # group 里每条记录指向一个 RT_ICON id，并声明尺寸
    out: dict[int, QImage] = {}
    count = struct.unpack_from("<H", grp, 4)[0]
    for i in range(count):
        w, h, _cc, _r, _pl, _bpp, _sz, rid = struct.unpack_from("<BBBBHHIH", grp, 6 + i * 14)
        data = icons.get(rid)
        if not data:
            continue
        img = QImage.fromData(data)
        if img.isNull():
            # BMP/DIB 帧：手动补 ICO 头解
            hdr = struct.pack("<HHH", 0, 1, 1) + struct.pack(
                "<BBBBHHII", w or 256, h or 256, 0, 0, 1, 32, len(data), 22
            )
            img = QImage.fromData(hdr + data)
        if not img.isNull():
            out[w or 256] = img
    return out


def build(src: Path, out_path: Path) -> None:
    """生成验证图。标签一律用 ASCII：offscreen 平台上中文字体常缺字变方框。"""
    frames = exe_frames(src)
    sizes = sorted(frames)
    big = frames[max(sizes)]

    smalls = [s for s in sizes if s <= 64]
    zoom = 8
    cell = max(cell_w := max(smalls) * zoom + 16, 120)
    pad = 24
    big_block = 180
    width = max(pad * 2 + big_block * 2 + 20, pad * 2 + cell * len(smalls))
    height = 78 + big_block + 30 + 22 + cell + 40 + pad

    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    canvas.fill(QColor("#ffffff"))
    p = QPainter(canvas)
    p.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
    p.setPen(QColor("#1a1a1a"))
    p.drawText(
        QRect(0, 12, width, 26),
        Qt.AlignmentFlag.AlignCenter,
        "BlenderHub.exe embedded icon (read back from PE resource)",
    )
    p.setFont(QFont("Segoe UI", 9))
    p.setPen(QColor("#666"))
    p.drawText(
        QRect(0, 40, width, 20),
        Qt.AlignmentFlag.AlignCenter,
        f"{src.name}   frames={len(sizes)}   sizes=" + " / ".join(str(s) for s in sizes),
    )

    # 上半：256px 主视觉，白底 + 深底
    y = 70
    for i, bg in enumerate((QColor("#ffffff"), QColor("#1e1e1e"))):
        x = pad + i * (big_block + 20)
        p.fillRect(QRect(x, y, big_block, big_block), bg)
        p.drawImage(
            x, y,
            big.scaled(big_block, big_block, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation),
        )
    p.setFont(QFont("Segoe UI", 9))
    p.setPen(QColor("#888"))
    p.drawText(QRect(pad, y + big_block + 2, big_block, 18),
               Qt.AlignmentFlag.AlignLeft, "256px @1x")

    # 下半：小尺寸实测 + 放大对照
    y2 = y + big_block + 32
    p.setPen(QColor("#555"))
    p.drawText(QRect(pad, y2, 500, 18), Qt.AlignmentFlag.AlignLeft,
               "actual size (top) / zoomed 8x (bottom)")
    top = y2 + 22
    for i, s in enumerate(smalls):
        x = pad + i * cell
        p.fillRect(QRect(x, top, cell - 8, 56), QColor("#f3f3f3"))
        img = frames[s]
        p.drawImage(x + (cell - 8 - s) // 2, top + (56 - s) // 2, img)
        z = img.scaled(s * zoom, s * zoom, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.FastTransformation)
        p.drawImage(x + (cell - 8 - z.width()) // 2, top + 62, z)
        p.setPen(QColor("#333"))
        p.drawText(QRect(x, top + 62 + z.height() + 2, cell - 8, 16),
                   Qt.AlignmentFlag.AlignCenter, f"{s}px")
    p.end()
    canvas.save(str(out_path))
    print(f"saved {out_path}  {canvas.width()}x{canvas.height()}  frames={len(sizes)}")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
