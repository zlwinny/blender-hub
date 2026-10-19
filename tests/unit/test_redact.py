"""日志 URL 脱敏单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.services.log_service import redact_url

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


check(
    "网盘链接去掉 query（提取码）",
    redact_url("https://pan.baidu.com/share/init?surl=abc&pwd=1234")
    == "https://pan.baidu.com/share/init",
)
check(
    "直链保留路径",
    redact_url("https://example.com/foo/addon.zip") == "https://example.com/foo/addon.zip",
)
check(
    "去 fragment",
    redact_url("https://example.com/a#frag") == "https://example.com/a",
)
check("非 URL 原样返回", redact_url("not a url") == "not a url")

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    raise SystemExit(1)
print("ALL PASS")
sys.stdout.flush()
