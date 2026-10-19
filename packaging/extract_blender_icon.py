"""从 Blender 官方 blender.exe 提取应用图标（技术示例脚本，勿用于发布）。

⚠️ 商标提醒：Blender logo 是 Blender Foundation 的注册资产，
官方指南明确「Do not use the Blender logo as your own logo」，第三方只能把它
当**次级品牌**用于指代 Blender 产品或致谢。所以本脚本产出的图标**不能**作为
Blender Hub 自己的应用图标发布。
本项目的图标由 `packaging/make_app_icon.py` 矢量绘制生成，与本脚本无关。

保留本脚本的价值：演示如何解析 Windows PE 的图标资源，供需要从 exe 提图时参考。

Windows PE 的资源结构：
  RT_ICON (type=3)        —— 一帧帧图像数据（PNG 或 BMP）
  RT_GROUP_ICON (type=14) —— GRPICONDIR，描述各帧的尺寸/位深/顺序

本脚本解析 PE 的 .rsrc 段，按 GRPICONDIR 把各帧拼成标准 ICO 文件。
（REA 的 inspect_pe_resources 有 64MB 上限，blender.exe 超限，故自己解析。）
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

RT_ICON = 3
RT_GROUP_ICON = 14


class PEIcons:
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        self._parse_headers()
        self._sections = self._read_sections()
        # 资源表文件偏移：下面每一层的 OffsetToData 都相对它，而不是相对上一层
        # 目录（踩过这个坑：条目数会解析成 6416 之类的垃圾值）。
        self.res_root = self.rva_to_off(self.res_rva)

    # ── PE 头 ────────────────────────────────────────────────────────
    def _parse_headers(self) -> None:
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError("不是 PE 文件（缺少 MZ 头）")
        e_lfanew = struct.unpack_from("<I", d, 0x3C)[0]
        if d[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
            raise ValueError("不是 PE 文件（缺少 PE 签名）")
        self.e_lfanew = e_lfanew

        # COFF 头
        coff = e_lfanew + 4
        self.n_sections = struct.unpack_from("<H", d, coff + 2)[0]
        opt_size = struct.unpack_from("<H", d, coff + 16)[0]
        opt = coff + 20

        magic = struct.unpack_from("<H", d, opt)[0]
        self.is64 = magic == 0x20B
        # 数据目录起始偏移：PE32=96, PE32+=112
        dd_off = opt + (112 if self.is64 else 96)
        # 数据目录第 2 项 = Resource Table
        self.res_rva, self.res_size = struct.unpack_from("<II", d, dd_off + 8 * 2)

    def _read_sections(self) -> list[tuple[int, int, int, int]]:
        """返回 [(VirtualAddress, VirtualSize, PointerToRawData, SizeOfRawData)]"""
        # section 表紧跟可选头
        opt_size_off = self.e_lfanew + 4 + 20 - 2
        opt_size = struct.unpack_from("<H", self.data, self.e_lfanew + 4 + 16)[0]
        sec_off = self.e_lfanew + 4 + 20 + opt_size
        out = []
        for i in range(self.n_sections):
            base = sec_off + i * 40
            _name = self.data[base:base + 8]
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", self.data, base + 8)
            out.append((vaddr, vsize, rawptr, rawsize))
        return out

    def rva_to_off(self, rva: int) -> int:
        for vaddr, vsize, rawptr, rawsize in self._sections:
            if vaddr <= rva < vaddr + max(vsize, rawsize):
                return rawptr + (rva - vaddr)
        raise ValueError(f"RVA 0x{rva:X} 不在任何节区中")

    # ── 资源树 ────────────────────────────────────────────────────────
    def _dir_entries(self, off: int) -> list[tuple[int, int, bool]]:
        """返回 [(id, offset, is_dir)]"""
        named, idcnt = struct.unpack_from("<HH", self.data, off + 12)
        out = []
        for i in range(named + idcnt):
            base = off + 16 + i * 8
            name_or_id, off_to_data = struct.unpack_from("<II", self.data, base)
            is_dir = bool(off_to_data & 0x80000000)
            out.append((name_or_id & 0x7FFFFFFF, off_to_data & 0x7FFFFFFF, is_dir))
        return out

    def find_type(self, res_type: int) -> int | None:
        root = self.res_root
        for tid, off, is_dir in self._dir_entries(root):
            if tid == res_type and is_dir:
                return root + off
        return None

    def collect_icons(self) -> dict[int, tuple[int, int]]:
        """RT_ICON: {资源ID: (数据偏移, 大小)}"""
        type_dir = self.find_type(RT_ICON)
        if type_dir is None:
            return {}
        out = {}
        for _nid, name_off, is_dir in self._dir_entries(type_dir):
            if not is_dir:
                continue
            name_dir = self.res_root + name_off
            for _lang, lang_off, is_dir2 in self._dir_entries(name_dir):
                if is_dir2:
                    continue
                data_entry = self.res_root + lang_off
                data_rva, size = struct.unpack_from("<II", self.data, data_entry)
                out[_nid] = (self.rva_to_off(data_rva), size)
        return out

    def group_icon(self) -> tuple[int, list[tuple]] | None:
        """读第一个 RT_GROUP_ICON，返回 (count, [entries])"""
        type_dir = self.find_type(RT_GROUP_ICON)
        if type_dir is None:
            return None
        for _nid, name_off, is_dir in self._dir_entries(type_dir):
            if not is_dir:
                continue
            name_dir = self.res_root + name_off
            for _lang, lang_off, is_dir2 in self._dir_entries(name_dir):
                if is_dir2:
                    continue
                data_entry = self.res_root + lang_off
                data_rva, size = struct.unpack_from("<II", self.data, data_entry)
                off = self.rva_to_off(data_rva)
                _res, _typ, count = struct.unpack_from("<HHH", self.data, off)
                entries = []
                for i in range(count):
                    e = struct.unpack_from("<BBBBHHIH", self.data, off + 6 + i * 14)
                    entries.append(e)  # (w,h,colorCount,reserved,planes,bitCount,bytesInRes,nId)
                return count, entries
        return None

    # ── 组装 ICO ──────────────────────────────────────────────────────
    def build_ico(self) -> bytes | None:
        icons = self.collect_icons()
        grp = self.group_icon()
        if not icons or not grp:
            return None
        count, entries = grp

        header = struct.pack("<HHH", 0, 1, count)
        offset = 6 + 16 * count
        dir_block = b""
        data_block = b""
        ok = 0
        for (w, h, cc, res, planes, bits, bytes_in_res, nid) in entries:
            if nid not in icons:
                continue
            data_off, data_size = icons[nid]
            blob = self.data[data_off:data_off + data_size]
            dir_block += struct.pack("<BBBBHHII", w, h, cc, res, planes, bits,
                                     len(blob), offset)
            data_block += blob
            offset += len(blob)
            ok += 1

        if ok == 0:
            return None
        # 修正 count（可能有缺失帧）
        header = struct.pack("<HHH", 0, 1, ok)
        return header + dir_block + data_block


def main() -> int:
    candidates = [
        Path(r"D:\Program Files\Blender Foundation\Blender 4.5\blender.exe"),
        Path(r"D:\Program Files\Blender Foundation\Blender 4.2\blender.exe"),
        Path(r"D:\Program Files\Blender Foundation\Blender 3.6\blender.exe"),
    ]
    src = next((p for p in candidates if p.exists()), None)
    if src is None:
        print("未找到 blender.exe")
        return 1

    print(f"源: {src}")
    pe = PEIcons(src)
    print(f"  架构: {'PE32+' if pe.is64 else 'PE32'}  节区: {pe.n_sections}")
    print(f"  资源表: RVA=0x{pe.res_rva:X} size={pe.res_size}")

    grp = pe.group_icon()
    if grp:
        cnt, entries = grp
        print(f"  RT_GROUP_ICON: {cnt} 帧")
        for (w, h, cc, _r, _p, bits, size, nid) in entries:
            dim = 256 if w == 0 else w
            print(f"    {dim}x{dim}  {bits}bit  {size}B  id={nid}")

    ico = pe.build_ico()
    if not ico:
        print("未能组装 ICO")
        return 1

    out = Path(__file__).resolve().parent / "icon.ico"
    out.write_bytes(ico)
    print(f"\n[完成] {out}  {len(ico)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())