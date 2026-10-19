"""生成版本资源 version_info.txt（以及 legacy 图标）。

不依赖 Pillow（打包已排除），用标准库直接写 ICO 与 PE version resource。

⚠️ 正式应用图标由 make_app_icon.py 生成到 packaging/icon.ico，**不要用本脚本覆盖它**：
本脚本画的是旧的「橙色方块 + 白色点阵 B」方案，曾因输出路径指向 packaging/icon.ico
而把新图标覆盖回旧 logo（2026-10-19 排查"界面 logo 还是旧的"时的根因之一）。
因此这里只往 legacy_icon.ico 写，正式图标请运行 make_app_icon.py。

绘制方案（全部纯几何，逐像素判定，无抗锯齿）：
- 圆角矩形：到最近角的距离判定（简单可靠，不会像复杂布尔式那样出反）
- 字形：5x7 点阵字模（B），按目标尺寸整数倍放大，任何尺寸都清晰

图标：Blender 橙 (#E87D0D) 圆角方块 + 深色底 + 白色点阵 "B"。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

BUILD = Path(__file__).resolve().parent
OUT_ICO = BUILD / "legacy_icon.ico"   # 不能叫 icon.ico：那是 make_app_icon.py 的产物
OUT_TXT = BUILD / "version_info.txt"

ORANGE = (0xE8, 0x7D, 0x0D)
ORANGE_DEEP = (0xC4, 0x62, 0x06)
DARK = (0x1A, 0x1E, 0x26)
WHITE = (0xFF, 0xFF, 0xFF)

VERSION = "1.0.0"
COMPANY = "BlenderHub Team"
PRODUCT = "Blender Hub"
DESCRIPTION = "Blender 版本 / 项目 / 插件 一体化管理工具"

# 5x7 点阵字模：大写 B
GLYPH_B = [
    "11110",
    "10001",
    "10001",
    "11110",
    "10001",
    "10001",
    "11110",
]


def _lerp(a, b, t: float) -> tuple[int, int, int]:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def render_icon(size: int) -> bytes:
    """绘制一枚图标并编码为 PNG（RGBA）。"""
    # 圆角矩形几何
    radius = max(2.0, size * 0.22)
    left = top = 0.0
    right = bottom = float(size - 1)

    # 字形布局（居中）
    gw, gh = 5, 7
    scale = max(1, int(size * 0.52) // gh)
    font_w = gw * scale
    font_h = gh * scale
    fx0 = (size - font_w) // 2
    fy0 = (size - font_h) // 2

    # 内框（深色底），给白字留出对比空间
    inset = size * 0.14
    inner_r = max(1.0, radius * 0.6)

    rows: list[bytes] = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            fx, fy = float(x), float(y)

            # ── 圆角矩形判定 ──
            # 到最近角的圆心距离，超出半径则在外
            ccx = left + radius if fx < left + radius else (
                right - radius if fx > right - radius else fx
            )
            ccy = top + radius if fy < top + radius else (
                bottom - radius if fy > bottom - radius else fy
            )
            if (fx - ccx) ** 2 + (fy - ccy) ** 2 > radius * radius:
                row += bytes((0, 0, 0, 0))
                continue

            # ── 底色：橙色垂直渐变 ──
            t = fy / max(1.0, size - 1)
            color = _lerp(ORANGE, ORANGE_DEEP, t)

            # ── 内框深色区域（字形的衬底）──
            il, it = inset, inset
            ir, ib = size - inset, size - inset
            icx = il + inner_r if fx < il + inner_r else (
                ir - inner_r if fx > ir - inner_r else fx
            )
            icy = it + inner_r if fy < it + inner_r else (
                ib - inner_r if fy > ib - inner_r else fy
            )
            in_inner = (fx - icx) ** 2 + (fy - icy) ** 2 <= inner_r * inner_r
            if in_inner:
                color = _lerp(DARK, (0x24, 0x29, 0x33), (fy - it) / max(1.0, ib - it))

                # ── 白色点阵字形 B ──
                gx = int((fx - fx0) // scale)
                gy = int((fy - fy0) // scale)
                if 0 <= gx < gw and 0 <= gy < gh:
                    if GLYPH_B[gy][gx] == "1":
                        color = WHITE

            row += bytes((color[0], color[1], color[2], 255))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def build_ico() -> None:
    sizes = [256, 128, 64, 48, 32, 16]
    images = [render_icon(s) for s in sizes]

    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    data = b""
    for s, png in zip(sizes, images):
        dim = 0 if s >= 256 else s  # 256 用 0 表示
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    OUT_ICO.write_bytes(header + entries + data)


def build_version_info() -> None:
    """生成 PyInstaller --version-file 用的 version info。

    注意：PyInstaller 的 load_version_info_from_text_file() 是用 **eval()** 解析这个文件的，
    所以格式必须是 VSVersionInfo 类的 Python 字面量（str(VSVersionInfo(...))），
    而不是常见的 C 风格 VS_VERSIONINFO 文本 —— 用后者会报
    "Failed to deserialize VSVersionInfo from text-based representation!"
    """
    from PyInstaller.utils.win32 import versioninfo

    ffi = versioninfo.VSVersionInfo(
        ffi=versioninfo.FixedFileInfo(
            filevers=(1, 0, 0, 0),
            prodvers=(1, 0, 0, 0),
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            versioninfo.StringFileInfo([
                versioninfo.StringTable(
                    '040904B0',
                    [versioninfo.StringStruct('CompanyName', COMPANY),
                     versioninfo.StringStruct('FileDescription', DESCRIPTION),
                     versioninfo.StringStruct('FileVersion', VERSION),
                     versioninfo.StringStruct('InternalName', 'BlenderHub'),
                     versioninfo.StringStruct('LegalCopyright', f'Copyright (c) {COMPANY} 2026'),
                     versioninfo.StringStruct('OriginalFilename', 'BlenderHub.exe'),
                     versioninfo.StringStruct('ProductName', PRODUCT),
                     versioninfo.StringStruct('ProductVersion', VERSION)],
                ),
            ]),
            versioninfo.VarFileInfo([versioninfo.VarStruct('Translation', [1033, 1200])]),
        ],
    )
    OUT_TXT.write_text(str(ffi), encoding="utf-8")


if __name__ == "__main__":
    build_ico()
    build_version_info()
    print(f"[icon]  {OUT_ICO}  ({OUT_ICO.stat().st_size} bytes)")
    print(f"[ver ]  {OUT_TXT}  ({OUT_TXT.stat().st_size} bytes)")