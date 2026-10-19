"""为 modules/*/manifest.json 生成 / 校验 checksums（SHA-256）。

用法：
    python tools/gen_module_checksums.py           # 生成并写回 manifest.json
    python tools/gen_module_checksums.py --check   # 只校验，不修改

说明：
- 只对模块目录内的 *.py 计算哈希（manifest.json 自身不参与，避免自引用）；
- 声明 checksums 后，ModuleManager 加载模块前会逐文件校验，不匹配即拒绝加载；
- 内置模块随 exe 分发，代码完整性由打包产物保证，通常不需要生成；
  该工具主要面向第三方/自定义模块。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = ROOT / "modules"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_checksums(module_dir: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for py_file in sorted(module_dir.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        rel = str(py_file.relative_to(module_dir)).replace("\\", "/")
        checksums[rel] = sha256_file(py_file)
    return checksums


def main() -> int:
    parser = argparse.ArgumentParser(description="生成/校验模块 manifest 的 checksums")
    parser.add_argument("--check", action="store_true", help="只校验，不写回文件")
    args = parser.parse_args()

    manifests = sorted(MODULES_DIR.glob("*/manifest.json"))
    if not manifests:
        print(f"未找到模块 manifest：{MODULES_DIR}")
        return 1

    failed = False
    for manifest_path in manifests:
        module_dir = manifest_path.parent
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = collect_checksums(module_dir)

        if args.check:
            declared = data.get("checksums") or {}
            if not declared:
                print(f"[SKIP] {module_dir.name}: 未声明 checksums")
                continue
            missing = sorted(set(declared) - set(expected))
            changed = sorted(
                rel for rel in declared
                if rel in expected and declared[rel] != expected[rel]
            )
            if missing or changed:
                failed = True
                print(f"[FAIL] {module_dir.name}: 缺失={missing} 变更={changed}")
            else:
                print(f"[ OK ] {module_dir.name}: {len(declared)} 个文件校验通过")
        else:
            data["checksums"] = expected
            manifest_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"[WROTE] {module_dir.name}: {len(expected)} 个文件")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
