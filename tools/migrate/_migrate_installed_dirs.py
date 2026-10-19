"""一次性迁移：把旧版装的 budeco_<id> 目录改名为压缩包内的真实插件名，并补安装记录。

背景：旧实现按远端 id（budeco:44968）命名安装目录，用户看到
``scripts/addons/budeco_44968`` 完全认不出是什么，误判"没装上"。
同时已安装状态只存内存，重启 HUB 后消失。

本脚本做两件事：
  1. 扫所有本机 Blender 的 addons 目录，把 ``budeco_<数字>`` 目录改名为
     其内部 blender_manifest.toml 的 id（如 kaboom），或 bl_info 的 name；
  2. 把「远端 id → 目录名 / Blender 版本」写进 installed_map.json，
     让新版 HUB 重启后能恢复"已安装"状态。

安全：只重命名以 ``budeco_`` 开头、且内部确认为 Blender 插件的目录；
目标名已存在时跳过并报告（不覆盖）。
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])  # noqa: F841

from core.services.blender_service import BlenderService  # noqa: E402

CONFIG = Path(os.environ["LOCALAPPDATA"]) / "BlenderHub Team" / "BlenderHub"
MAP_PATH = CONFIG / "addons_cache" / "installed_map.json"


def _manifest_id(plugin_dir: Path) -> str | None:
    """优先取 blender_manifest.toml 的 id（扩展格式最权威）。"""
    man = plugin_dir / "blender_manifest.toml"
    if man.is_file():
        try:
            m = re.search(r'^\s*id\s*=\s*"([^"]+)"', man.read_text(encoding="utf-8",
                                                                    errors="replace"),
                          re.M)
            if m:
                return m.group(1).strip().lower()
        except OSError:
            pass
    init = plugin_dir / "__init__.py"
    if init.is_file():
        try:
            txt = init.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'"name"\s*:\s*"([^"]+)"', txt)
            if m:
                # bl_info 的 name 可能含空格/大写，转成安全目录名
                return re.sub(r"[^A-Za-z0-9_.\-]", "_", m.group(1)).strip("_").lower()
        except OSError:
            pass
    return None


def main() -> int:
    versions = BlenderService().detect_installed()
    records = {}
    if MAP_PATH.is_file():
        try:
            records = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records = {}

    renamed, skipped, kept = 0, 0, 0
    for v in versions:
        scripts = v.path / "scripts" / "addons"
        if not scripts.is_dir():
            continue
        for entry in sorted(scripts.iterdir()):
            if not entry.is_dir():
                continue
            m = re.fullmatch(r"budeco_(\d+)", entry.name)
            if not m:
                continue
            budeco_id = f"budeco:{m.group(1)}"
            real = _manifest_id(entry)
            if not real:
                print(f"[跳过] {entry} 内既无 blender_manifest.toml 也无 bl_info，无法判定真实名")
                skipped += 1
                # 仍然登记状态，只是目录名保持原样
                records[budeco_id] = {
                    "dir": entry.name, "blender": v.version,
                    "name": entry.name, "at": "migrated",
                }
                continue
            target = scripts / real
            if target == entry:
                kept += 1
            elif target.exists():
                print(f"[跳过] 目标已存在，不覆盖: {target}")
                skipped += 1
            else:
                try:
                    entry.rename(target)
                    print(f"[改名] {v.label}: {entry.name} → {real}")
                    renamed += 1
                except OSError as e:
                    print(f"[失败] {entry} → {target}: {e}（Blender 可能正开着）")
                    skipped += 1
                    continue
            records[budeco_id] = {
                "dir": real, "blender": v.version, "name": real, "at": "migrated",
            }

    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完成：改名 {renamed}，跳过 {skipped}，保持 {kept}；"
          f"安装记录写入 {MAP_PATH}（共 {len(records)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())