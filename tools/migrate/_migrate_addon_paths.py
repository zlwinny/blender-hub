"""把误装到「安装根/scripts/addons」的插件迁移到 Blender 真正扫描的用户目录。

2026-10-18 实测（Blender 4.2.23）：官方安装包被 addon_utils 扫描的只有
 ① <安装根>/<版本号>/scripts/addons_core（系统捆绑）
 ② %APPDATA%/Blender Foundation/Blender/<版本号>/scripts/addons（用户插件）
安装根下的 <安装根>/scripts/addons 两者都不在——旧版 HUB 装到那里，
Blender 永远看不到。本脚本把这些目录搬到②，并同步 installed_map.json。
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])  # noqa: F841

from core.services.blender_service import BlenderService  # noqa: E402

CONFIG = Path(os.environ["LOCALAPPDATA"]) / "BlenderHub Team" / "BlenderHub"
MAP_PATH = CONFIG / "addons_cache" / "installed_map.json"


def user_scripts(version: str) -> Path:
    """Blender 用户脚本目录用 **major.minor**（4.2），不是完整版本号（4.2.23）。

    实测 bpy.utils.user_resource("SCRIPTS") → ...\\Blender\\4.2\\scripts。
    用完整版本号会建出 Blender 从不读取的 4.2.23 目录。
    """
    minor = ".".join(str(version).split(".")[:2])
    return (Path.home() / "AppData" / "Roaming" / "Blender Foundation"
            / "Blender" / minor / "scripts" / "addons")


def main() -> int:
    versions = BlenderService().detect_installed()
    records = {}
    if MAP_PATH.is_file():
        try:
            records = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            records = {}

    moved = 0
    for v in versions:
        wrong = v.path / "scripts" / "addons"   # 旧实现的目标（无效位置）
        if not wrong.is_dir():
            continue
        right = user_scripts(v.version)
        right.mkdir(parents=True, exist_ok=True)
        for entry in sorted(wrong.iterdir()):
            if not entry.is_dir():
                continue
            target = right / entry.name
            if target.exists():
                print(f"[跳过] 目标已存在: {target}")
                shutil.rmtree(entry, ignore_errors=True)
                continue
            try:
                shutil.move(str(entry), str(target))
                print(f"[迁移] {v.label}: {wrong / entry.name} → {target}")
                moved += 1
            except OSError as e:
                print(f"[失败] {entry}: {e}")
        # 清掉空的无效目录，避免下次再被误判
        try:
            if wrong.is_dir() and not any(wrong.iterdir()):
                wrong.rmdir()
                print(f"[清理] 移除空目录 {wrong}")
        except OSError:
            pass

    # 同步安装记录：目录名不变，但路径语义已由 _blender_scripts_root 接管
    for addon_id, info in records.items():
        info.setdefault("migrated", "2026-10-18")
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完成：迁移 {moved} 个插件目录；安装记录 {len(records)} 条已同步")
    return 0


if __name__ == "__main__":
    sys.exit(main())