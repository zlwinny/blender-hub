"""插件安装包校验器的对抗性测试。

覆盖：正常包 / 截断 / zip bomb / 路径穿越 / 符号链接 / 可执行载荷 / HTML 伪装页 / 损坏 zip。
"""
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.services.addon_verifier import AddonVerifier  # noqa: E402


def make_zip(entries: dict) -> bytes:
    """entries: {归档内路径: (内容bytes, external_attr)}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, (data, attr) in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = attr
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


NORMAL = {
    "my_addon/__init__.py": (b"bl_info = {}", 0o644 << 16),
    "my_addon/ui.py": (b"x = 1", 0o644 << 16),
}


def t(name: str, cond: bool) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    return cond


def main() -> int:
    v = AddonVerifier()
    results = []

    print("=" * 60)
    print("AddonVerifier 对抗性测试")
    print("=" * 60)

    # 1. 正常插件包 -> 通过
    r = v.verify_response(200, make_zip(NORMAL), "application/zip")
    results.append(t("正常插件包被接受", r.ok and r.sha256 and r.entry_count == 2))

    # 2. HTTP 非200 -> 拒绝
    r = v.verify_response(404, make_zip(NORMAL), "application/zip")
    results.append(t("HTTP 404 被拒绝", not r.ok and any("404" in e for e in r.errors)))

    # 3. HTML 伪装页 -> 拒绝
    r = v.verify_response(200, b"<html>404</html>", "text/html; charset=utf-8")
    results.append(t("HTML 伪装页被拒绝", not r.ok))

    # 4. 传输截断（Content-Length 不符）-> 拒绝
    data = make_zip(NORMAL)
    r = v.verify_response(200, data[:-10], "application/zip", content_length=len(data))
    results.append(t("传输截断被拒绝", not r.ok and any("不完整" in e for e in r.errors)))

    # 5. 损坏 zip -> 拒绝
    r = v.verify_response(200, b"PK\x03\x04" + b"\x00" * 50, "application/zip")
    results.append(t("损坏 zip 被拒绝", not r.ok))

    # 6. 缺 __init__.py -> 拒绝
    no_init = {"my_addon/readme.txt": (b"hi", 0o644 << 16)}
    r = v.verify_response(200, make_zip(no_init), "application/zip")
    results.append(t("缺 __init__.py 被拒绝", not r.ok))

    # 7. 路径穿越 -> 拒绝
    evil = {
        "../../../Windows/System32/evil.py": (b"import os", 0o644 << 16),
        "my_addon/__init__.py": (b"", 0o644 << 16),
    }
    r = v.verify_response(200, make_zip(evil), "application/zip")
    results.append(t("路径穿越被拒绝", not r.ok and any("穿越" in e for e in r.errors)))

    # 8. 绝对路径条目 -> 拒绝
    abs_entry = {
        "/etc/passwd": (b"x", 0o644 << 16),
        "my_addon/__init__.py": (b"", 0o644 << 16),
    }
    r = v.verify_response(200, make_zip(abs_entry), "application/zip")
    results.append(t("绝对路径条目被拒绝", not r.ok))

    # 9. 符号链接 -> 拒绝
    symlink = {
        "my_addon/__init__.py": (b"", 0o644 << 16),
        "my_addon/evil.dll": (b"", (0o120777 << 16)),  # 0xA000 标记
    }
    r = v.verify_response(200, make_zip(symlink), "application/zip")
    results.append(t("符号链接条目被拒绝", not r.ok and any("符号链接" in e for e in r.errors)))

    # 10. zip bomb（条目数超限）-> 拒绝
    bomb = {"my_addon/__init__.py": (b"", 0o644 << 16)}
    for i in range(5100):
        bomb[f"my_addon/f{i}.py"] = (b"", 0o644 << 16)
    r = v.verify_response(200, make_zip(bomb), "application/zip")
    results.append(t("条目数超限被拒绝", not r.ok and any("条目数" in e for e in r.errors)))

    # 11. 可执行载荷 -> 接受但告警（需用户确认）
    payload = {
        "my_addon/__init__.py": (b"", 0o644 << 16),
        "my_addon/setup.exe": (b"MZ...", 0o644 << 16),
    }
    r = v.verify_response(200, make_zip(payload), "application/zip")
    results.append(t(
        "可执行载荷：接受但需确认",
        r.ok and r.requires_user_confirmation and len(r.risky_payloads) == 1,
    ))

    # 12. 单条目过大 -> 拒绝
    big = {"my_addon/__init__.py": (b"", 0o644 << 16),
           "my_addon/huge.dat": (b"A" * (65 * 1024 * 1024), 0o644 << 16)}
    r = v.verify_response(200, make_zip(big), "application/zip")
    results.append(t("单条目过大被拒绝", not r.ok and any("过大" in e for e in r.errors)))

    # 13. 空响应 -> 拒绝
    r = v.verify_response(200, b"", "application/zip")
    results.append(t("空响应被拒绝", not r.ok))

    # 14. 审计日志往返
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        good = v.verify_response(200, make_zip(NORMAL), "application/zip")
        v.record_audit(base, "test_addon", "Test", "https://example.com", good, "4.2.0")
        entries = v.read_audit(base)
        results.append(t(
            "审计日志写入并读回",
            len(entries) == 1 and entries[0]["sha256"] == good.sha256,
        ))

    passed = sum(1 for x in results if x)
    print("=" * 60)
    print(f"结果: {passed}/{len(results)} 项通过")
    print("=" * 60)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())