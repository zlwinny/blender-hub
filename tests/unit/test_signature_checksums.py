"""模块签名校验（checksums）单元测试。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.services.signature_service import SignatureService

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


svc = SignatureService()
tmp = Path(tempfile.mkdtemp(prefix="bh_sig_"))
mod_dir = tmp / "mymod"
mod_dir.mkdir()
(mod_dir / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")

base_manifest = {
    "id": "mymod",
    "name": "MyMod",
    "version": "1.0.0",
    "entry_point": "mymod:Mod",
}
manifest_path = mod_dir / "manifest.json"

# 1. 缺 manifest → 拒绝
check("缺 manifest 拒绝加载", svc.verify_module(tmp) is False)

# 2. 无 checksums 但 manifest 合法 → 放行（结构校验）
manifest_path.write_text(json.dumps(base_manifest), encoding="utf-8")
check("无 checksums 时按结构校验放行", svc.verify_module(mod_dir) is True)

# 3. 声明 checksums 且一致 → 通过
base_manifest["checksums"] = svc.generate_checksums(mod_dir)
manifest_path.write_text(json.dumps(base_manifest), encoding="utf-8")
check("checksums 一致时通过", svc.verify_module(mod_dir) is True)

# 4. 篡改文件 → 拒绝
(mod_dir / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
check("文件被篡改后拒绝", svc.verify_module(mod_dir) is False)

# 5. 穿越路径防护
base_manifest["checksums"] = {"../../evil.py": "deadbeef" * 8}
manifest_path.write_text(json.dumps(base_manifest), encoding="utf-8")
check("checksums 路径穿越被拦截", svc.verify_module(mod_dir) is False)

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    raise SystemExit(1)
print("ALL PASS")
sys.stdout.flush()
