"""从 PE 文件回读 RT_ICON / RT_GROUP_ICON，用于核对打包后的图标是否为预期版本。

用法:
    python read_pe_icons.py <exe> [<exe> ...]

PE32 与 PE32+ 的 Optional Header 布局不同（PE32+ 少 BaseOfData），
DataDirectory 起始偏移分别为 96 与 112，Resource 项再加 2*8。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

RT_ICON = 3
RT_GROUP_ICON = 14


def _sections(pe: bytes) -> list[tuple[int, int, int, int]]:
    pe_off = struct.unpack_from("<I", pe, 0x3C)[0]
    nsec = struct.unpack_from("<H", pe, pe_off + 0x06)[0]
    optsz = struct.unpack_from("<H", pe, pe_off + 0x14)[0]
    base = pe_off + 0x18 + optsz
    out = []
    for i in range(nsec):
        o = base + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", pe, o + 8)
        out.append((vaddr, max(vsize, rawsize), rawptr, rawsize))
    return out


def _rva_to_off(secs, rva: int) -> int | None:
    for vaddr, span, rawptr, rawsize in secs:
        if vaddr <= rva < vaddr + span:
            delta = rva - vaddr
            return rawptr + delta if delta < rawsize else None
    return None


def read_resources(path: str | Path) -> dict[int, bytes]:
    """读取资源树中每种类型的第一份数据。

    PE 资源是三层目录树：Type -> Name(资源 ID) -> Language，
    每层的 entry 高位 bit31 表示"指向子目录"而非数据。
    目录的根节点位于 .rsrc 段起始处的 IMAGE_RESOURCE_DIRECTORY。
    """
    pe = Path(path).read_bytes()
    pe_off = struct.unpack_from("<I", pe, 0x3C)[0]
    magic = struct.unpack_from("<H", pe, pe_off + 0x18)[0]
    dd_start = 96 if magic == 0x10B else 112  # PE32 vs PE32+
    dir_rva = struct.unpack_from("<I", pe, pe_off + 0x18 + dd_start + 2 * 8)[0]

    secs = _sections(pe)
    root = _rva_to_off(secs, dir_rva)
    if root is None:
        raise ValueError(f"资源目录 RVA 0x{dir_rva:X} 无法映射到文件偏移")

    found: dict[int, bytes] = {}

    def walk(dir_off: int, depth: int, type_id: int | None) -> None:
        """dir_off 是文件偏移。子目录偏移相对资源段根节点，不是 RVA。

        只有叶子节点指向的 IMAGE_RESOURCE_DATA_ENTRY 里存的 OffsetToData
        才是真正的 RVA，需要再做一次段映射。
        """
        if dir_off is None or depth > 3:
            return
        named, by_id = struct.unpack_from("<HH", pe, dir_off + 12)
        for i in range(named + by_id):
            entry = dir_off + 16 + i * 8
            name_id, off_field = struct.unpack_from("<II", pe, entry)
            rel = off_field & 0x7FFFFFFF
            if off_field & 0x80000000:          # 指向下一层目录（相对 root）
                walk(root + rel, depth + 1, type_id if depth else name_id)
                continue
            data_entry = root + rel
            # IMAGE_RESOURCE_DATA_ENTRY: OffsetToData(RVA) 在前，Size 在后
            content_rva, size = struct.unpack_from("<II", pe, data_entry)
            content_off = _rva_to_off(secs, content_rva)
            if content_off is None:
                continue
            tid = type_id if type_id is not None else name_id
            found.setdefault(tid, pe[content_off: content_off + size])

    walk(root, 0, None)
    return found


def report(path: str | Path) -> None:
    p = Path(path)
    print(f"\n{p}  ({p.stat().st_size:,} bytes)")
    try:
        res = read_resources(p)
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: {exc}")
        return

    grp = res.get(RT_GROUP_ICON, b"")
    if len(grp) < 6:
        print("  未找到 RT_GROUP_ICON")
        return

    count = struct.unpack_from("<H", grp, 4)[0]
    print(f"  RT_ICON {len(res.get(RT_ICON, b'')):,}B   帧数={count}")
    total = 0
    for i in range(count):
        o = 6 + i * 14
        w, h, _cc, _res, _planes, bpp, size, rid = struct.unpack_from(
            "<BBBBHHIH", grp, o
        )
        total += size
        print(
            f"    {w or 256:>3}x{h or 256:<3} bpp={bpp:<2} "
            f"size={size:>6}B  id={rid}"
        )
    print(f"    合计 {total:,}B")


if __name__ == "__main__":
    targets = sys.argv[1:]
    if not targets:
        print(__doc__)
        raise SystemExit(1)
    for t in targets:
        report(t)
