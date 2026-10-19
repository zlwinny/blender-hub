"""
签名服务 — 模块文件完整性校验

当前实现：
1. 校验 manifest.json 存在且包含必需字段；
2. 如果 manifest 声明了 checksums，逐文件校验 SHA-256（路径限制在模块目录内）。

说明：
- 内置模块随 exe 一起分发，其代码完整性由打包产物本身保证；
- 第三方/自定义模块可用 tools/gen_module_checksums.py 生成 checksums 后再加载；
- Ed25519 仅保留实验实现（core/services/ed25519_pure.py），尚未接入产品代码，
  不要在 UI/文档中宣称已启用 Ed25519 签名。
"""

import hashlib
import json
from pathlib import Path


class SignatureService:
    """模块签名校验服务"""

    def __init__(self, log_service=None):
        self._log = log_service

    def verify_module(self, module_path: Path) -> bool:
        """
        校验模块完整性

        1. 检查 manifest.json 存在且包含必需字段；
        2. 可选：校验模块文件 SHA-256（如果 manifest 中包含 checksums）。

        注意：checksums 是「可选增强」，未声明时只能保证 manifest 结构合法；
        内置模块的代码完整性由打包产物保证，不要把它当作 Ed25519 签名。
        """
        manifest_path = module_path / "manifest.json"
        if not manifest_path.exists():
            if self._log:
                self._log.error(f"Module integrity failed: {module_path.name} missing manifest.json", module="signature")
            return False

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            # 必需字段检查
            required_fields = ["id", "name", "version", "entry_point"]
            for field in required_fields:
                if field not in manifest:
                    if self._log:
                        self._log.error(
                            f"Module '{module_path.name}' missing required field: {field}",
                            module="signature",
                        )
                    return False

            # v1.5: 如果有 checksums，校验文件哈希
            checksums = manifest.get("checksums", {})
            if checksums:
                module_root = module_path.resolve()
                for rel_path, expected_hash in checksums.items():
                    # 安全：rel_path 来自 manifest.json，若 manifest 被篡改可写入
                    # "../../Windows/System32/xxx" 之类的穿越路径。校验解析结果仍在模块目录内。
                    file_path = (module_path / rel_path).resolve()
                    if not file_path.is_relative_to(module_root):
                        if self._log:
                            self._log.error(
                                f"Blocked checksum path traversal in '{module_path.name}': {rel_path}",
                                module="signature",
                            )
                        return False
                    if not file_path.exists():
                        if self._log:
                            self._log.error(f"Missing file: {file_path}", module="signature")
                        return False
                    actual_hash = self._sha256_file(file_path)
                    if actual_hash != expected_hash:
                        if self._log:
                            self._log.error(
                                f"Hash mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}",
                                module="signature",
                            )
                        return False

            return True

        except (json.JSONDecodeError, IOError, OSError) as e:
            if self._log:
                self._log.error(f"Failed to parse manifest for '{module_path.name}': {e}", module="signature")
            return False

    def verify_all_modules(self, modules_dir: Path) -> dict:
        """
        校验所有模块
        Returns: {module_id: True/False}
        """
        results = {}
        if not modules_dir.exists():
            return results

        for item in sorted(modules_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue
            manifest_path = item / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    module_id = manifest.get("id", item.name)
                    results[module_id] = self.verify_module(item)
                except Exception as e:  # noqa: BLE001
                    # 旧实现 except: pass 到 results[False]，无法定位是哪个模块解析失败
                    if self._log:
                        self._log.error(
                            f"verify_all_modules failed for '{item.name}': {e}", module="signature"
                        )
                    results[item.name] = False

        return results

    @staticmethod
    def _sha256_file(file_path: Path) -> str:
        """计算文件 SHA-256"""
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()

    def generate_checksums(self, module_path: Path) -> dict:
        """为模块生成所有 Python 文件的 SHA-256 哈希"""
        checksums = {}
        for py_file in sorted(module_path.rglob("*.py")):
            rel_path = py_file.relative_to(module_path)
            checksums[str(rel_path).replace("\\", "/")] = self._sha256_file(py_file)
        return checksums
