"""配置中心：快照记录真实清单 + 恢复/迁移置灰的回归测试。

背景：旧实现快照写死一套假配置数据，恢复/迁移只弹「已完成」却不写任何文件。
修复后：快照只读采集 Blender 用户配置目录的文件清单与插件目录名，恢复/迁移按钮置灰。
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

app = QApplication(sys.argv)

from modules.config_center.module import ConfigCenterModule  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


# ── 1. 未安装的版本 → 清单标记不存在 ──
m = ConfigCenterModule()
inv = m._collect_config_inventory("9.9.9")
check("清单类型为 inventory", inv.get("kind") == "inventory")
check("不存在的版本目录标记 exists=False", inv.get("exists") is False, str(inv))

# ── 2. 用临时 APPDATA 模拟真实配置目录 ──
tmp = Path(tempfile.mkdtemp(prefix="bh_cfg_"))
os.environ["APPDATA"] = str(tmp)
cfg = tmp / "Blender Foundation" / "Blender" / "4.2.0" / "config"
cfg.mkdir(parents=True)
(cfg / "userpref.blend").write_bytes(b"BLENDER")
(cfg / "bookmarks.txt").write_text("x", encoding="utf-8")
addons = cfg.parent / "scripts" / "addons"
addons.mkdir(parents=True)
(addons / "node_wrangler").mkdir()
(addons / "__pycache__").mkdir()  # 应被过滤

inv2 = m._collect_config_inventory("4.2.0")
check("真实配置目录存在", inv2.get("exists") is True)
files = {f["path"] for f in inv2.get("files", [])}
check("采集到配置文件清单", {"userpref.blend", "bookmarks.txt"} <= files, str(files))
check("采集到插件目录名", inv2.get("addons") == ["node_wrangler"], str(inv2.get("addons")))

# ── 3. 恢复/迁移按钮置灰 ──
widget = m.get_widget()
buttons = {b.text(): b for b in widget.findChildren(QPushButton)}
restore = next((b for t, b in buttons.items() if t.startswith("📥 恢复配置")), None)
migrate = next((b for t, b in buttons.items() if t.startswith("🔄 跨版本迁移")), None)
check("恢复按钮已置灰", restore is not None and not restore.isEnabled())
check("迁移按钮已置灰", migrate is not None and not migrate.isEnabled())

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
