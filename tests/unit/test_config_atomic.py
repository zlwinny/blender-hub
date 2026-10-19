"""ConfigService 原子写与损坏恢复的单元测试。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.services.config_service import ConfigService

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


cs = ConfigService()
cs._base_dir = Path(tempfile.mkdtemp(prefix="bh_cs_"))

cs.set("demo", "k", "v")
config_path = cs._base_dir / "demo_config.json"
check("写入后文件存在", config_path.exists())
check("不残留临时文件", not (cs._base_dir / "demo_config.json.tmp").exists())
check("读回正确", cs.get("demo", "k") == "v")

# 损坏文件：下次读取回默认值，且原文件被保留为 .corrupt
config_path.write_text("{bad json", encoding="utf-8")
cs._cache.clear()
check("损坏后回默认值", cs.get("demo", "k", "DEFAULT") == "DEFAULT")
check("损坏文件被保留备份", (cs._base_dir / "demo_config.json.corrupt").exists())

# 再次写入应重建可用的 JSON
cs.set("demo", "k", "v2")
check("重写后读回正常", cs.get("demo", "k") == "v2")

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    raise SystemExit(1)
print("ALL PASS")
sys.stdout.flush()
