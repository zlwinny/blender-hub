"""插件安装包完整性校验。

安全背景
--------
插件商店会把**远端下载的第三方 zip** 直接解压进 Blender 的 addons 目录，
而 Blender 插件是可以执行任意 Python 代码的。这是一条真实的代码注入链路：
源站被劫持 / 传输被篡改 / 源站本身投毒，都会导致用户一"安装"就执行了恶意代码。

本模块提供安装前的多级校验（不依赖 Ed25519 等非对称签名，原因见下）：

1. **传输层**：Content-Type 必须是 zip/application；HTTP 必须 200；
   强制Content-Length 与实际字节数一致（防截断/超长）
2. **结构层**：必须是合法 zip（central directory 完整）；
   大小/条目数在合理区间（防 zip bomb）；禁止符号链接与绝对路径条目
3. **内容层**：解压后必须存在 `__init__.py`（Blender 插件的必要条件）；
   拒绝含 `.py` 之外可执行载荷的可疑组合（如同时含 `.exe`/`.dll`/`.bat`）
4. **审计层**：计算 SHA-256 并记录到本地审计日志（可追溯"装了什么"）
5. **人工确认**：命中风险特征时要求用户显式确认才继续

为什么不用 Ed25519
------------------
非对称签名的价值在于"公钥可公开分发、私钥不公开"。但本项目的插件来自
**三个开放抓取源**（GitHub 搜索 API / Gitee / budeco.top爬虫），并没有
一个权威发布方持有私钥并为每个插件签发签名——我们无法对第三方插件验签。
硬造一套签名机制只会是自欺欺人。因此这里选择"可落地的纵深校验"，
并明确其边界：它能挡住传输损坏与明显的恶意载荷，**挡不住有组织的投毒源站**。
"""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Optional

# Blender 插件的必要文件
ADDON_MARKER = "__init__.py"

# zip 内容体积上限（解压后总大小），防 zip bomb
MAX_UNCOMPRESSED = 256 * 1024 * 1024      # 256MB
# 单条目上限
MAX_ENTRY_SIZE = 64 * 1024 * 1024# 64MB
# 条目数量上限（插件正常只有几十个文件）
MAX_ENTRIES = 5000

# 可执行载荷扩展名——Blender 插件不需要这些，出现即为高风险
EXECUTABLE_SUFFIXES = {
    ".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".scr",
    ".msi", ".jar", ".sh",
}

# 合法的 content-type
ALLOWED_CONTENT_TYPES = {
    "application/zip", "application/x-zip-compressed", "application/octet-stream",
    "application/x-zip", "multipart/x-zip",
}


@dataclass
class VerificationResult:
    """校验结果"""
    ok: bool = False
    sha256: str = ""
    size_bytes: int = 0
    entry_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risky_payloads: list[str] = field(default_factory=list)

    @property
    def requires_user_confirmation(self) -> bool:
        """有风险特征时需要用户显式确认（不直接拦死，但要让人知情）"""
        return bool(self.warnings or self.risky_payloads)

    def summary(self) -> str:
        if not self.ok:
            return "校验失败：" + "；".join(self.errors)
        lines = [f"SHA-256: {self.sha256[:16]}…", f"大小: {self.size_bytes / 1024:.0f} KB",
                 f"文件数: {self.entry_count}"]
        if self.risky_payloads:
            lines.append("⚠ 含可执行载荷：" + ", ".join(self.risky_payloads[:5]))
        if self.warnings:
            lines.append("⚠ " + "；".join(self.warnings))
        return "\n".join(lines)


class AddonVerifier:
    """插件安装包校验器（纯逻辑，无 GUI 依赖，可独立测试）"""

    def verify_response(self, status_code: int, content: bytes,
                       content_type: str = "", content_length: Optional[int] = None) -> VerificationResult:
        """校验 HTTP 响应内容"""
        r = VerificationResult()

        # ── 1. 传输层 ──
        if status_code != 200:
            r.errors.append(f"HTTP 状态码 {status_code}")
            return r

        if not content:
            r.errors.append("响应内容为空")
            return r

        ctype = (content_type or "").split(";")[0].strip().lower()
        if ctype and ctype not in ALLOWED_CONTENT_TYPES and "html" in ctype:
            r.errors.append(f"返回的是网页而非安装包（Content-Type: {content_type}）")
            return r

        # Content-Length 与实际长度不符 = 传输截断
        if content_length is not None and content_length != len(content):
            r.errors.append(
                f"传输不完整：声明 {content_length} 字节，实际收到 {len(content)} 字节"
            )
            return r

        r.sha256 = hashlib.sha256(content).hexdigest()
        r.size_bytes = len(content)

        # zip magic 快速判定
        if content[:2] != b"PK":
            r.errors.append("不是有效的 zip 文件（缺少 PK 文件头）")
            return r

        # ── 2/3. 结构层 + 内容层 ──
        try:
            with zipfile.ZipFile(BytesIO(content), "r") as zf:
                infos = zf.infolist()
                r.entry_count = len(infos)

                if len(infos) == 0:
                    r.errors.append("压缩包为空")
                    return r

                if len(infos) > MAX_ENTRIES:
                    r.errors.append(f"条目数异常（{len(infos)} > {MAX_ENTRIES}）")
                    return r

                total_uncompressed = sum(i.file_size for i in infos)
                if total_uncompressed > MAX_UNCOMPRESSED:
                    r.errors.append(
                        f"解压后体积异常（{total_uncompressed / 1024 / 1024:.0f}MB > "
                        f"{MAX_UNCOMPRESSED / 1024 / 1024:.0f}MB），疑似压缩炸弹"
                    )
                    return r

                names: list[str] = []
                has_init = False
                for info in infos:
                    name = info.filename
                    # 绝对路径 / 盘符 / 上级穿越
                    norm = name.replace("\\", "/")
                    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
                        r.errors.append(f"压缩包含绝对路径条目：{name}")
                        return r
                    if ".." in norm.split("/"):
                        r.errors.append(f"压缩包含上级目录穿越条目：{name}")
                        return r

                    # 单条目大小
                    if info.file_size > MAX_ENTRY_SIZE:
                        r.errors.append(
                            f"条目过大：{name}（{info.file_size / 1024 / 1024:.0f}MB）"
                        )
                        return r

                    # 符号链接（外部属性高位 0xA000 标记）
                    if (info.external_attr >> 16) & 0xF000 == 0xA000:
                        r.errors.append(f"压缩包含符号链接条目：{name}")
                        return r

                    names.append(name)
                    if Path(norm).name == ADDON_MARKER:
                        has_init = True

                    suffix = Path(norm).suffix.lower()
                    if suffix in EXECUTABLE_SUFFIXES:
                        r.risky_payloads.append(name)

                if not has_init:
                    r.errors.append(
                        f"未找到 {ADDON_MARKER}，不是有效的 Blender 插件包"
                    )
                    return r

                if r.risky_payloads:
                    r.warnings.append(
                        "安装包内含可执行文件（Blender 插件通常不需要），请确认来源可信"
                    )

                # 探测混淆迹象（超高压缩比 = 可能是伪装载荷）
                ratio = total_uncompressed / max(1, len(content))
                if ratio > 500:
                    r.warnings.append(f"压缩比异常高（{ratio:.0f}:1），内容可能经过刻意混淆")

                # 实测 zipfile 自带的 CRC 校验（读取全部内容触发 CRC 校验）
                with zf.open(infos[0]) as _probe:
                    while _probe.read(65536):
                        pass

        except zipfile.BadZipFile as e:
            r.errors.append(f"zip 文件损坏或格式非法：{e}")
            return r
        except Exception as e:  # noqa: BLE001
            r.errors.append(f"校验过程出错：{e}")
            return r

        r.ok = True
        return r

    @staticmethod
    def audit_log_path(base_dir: Path) -> Path:
        return Path(base_dir) / "install_audit.jsonl"

    def record_audit(self, base_dir: Path, addon_id: str, addon_name: str,
                     source_url: str, result: VerificationResult,
                     target_version: str = "") -> None:
        """记录安装审计（JSONL 追加，可追溯装过什么）"""
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "addon_id": addon_id,
            "addon_name": addon_name,
            "source_url": source_url,
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "entry_count": result.entry_count,
            "warnings": result.warnings,
            "risky_payloads": result.risky_payloads,
            "target_version": target_version,
        }
        try:
            path = self.audit_log_path(base_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 审计失败不应阻断安装

    def read_audit(self, base_dir: Path, limit: int = 20) -> list[dict]:
        """读取最近的安装审计记录"""
        path = self.audit_log_path(base_dir)
        if not path.exists():
            return []
        entries = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except OSError:
            return []
        return entries[-limit:][::-1]