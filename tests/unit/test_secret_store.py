"""secret_store（Windows DPAPI 加解密）单元测试。

- 空串 / 无前缀视为旧明文，原样返回（兼容迁移）
- DPAPI 可用时加密结果带 dpapi:v1: 前缀，且能解密回原文
- 非 Windows 环境跳过（本应用仅 Windows）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.services import secret_store

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


check("空串原样返回", secret_store.protect("") == "")
check("无前缀视为旧明文", secret_store.unprotect('{"a":1}') == '{"a":1}')

if secret_store.available():
    text = "hello 世界 - cookie value"
    try:
        enc = secret_store.protect(text)
    except OSError as e:
        check(f"DPAPI 加密可用（异常: {e}）", False)
    else:
        check("加密带 dpapi 前缀", enc.startswith("dpapi:v1:"), enc[:12] + "...")
        check("解密还原", secret_store.unprotect(enc) == text)
else:
    print("SKIP 当前非 Windows，跳过 DPAPI 加解密")

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    raise SystemExit(1)
print("ALL PASS")
sys.stdout.flush()
