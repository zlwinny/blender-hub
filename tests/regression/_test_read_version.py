"""验证版本检测不再启动进程（= 深度扫描不再弹黑窗）。

旧实现：每个候选 exe 都 subprocess 跑一次 `blender.exe --version` →
Windows 上控制台子系统程序会弹黑窗，且每个最多等 10s。
新实现：目录名 → PE 版本资源 → 最后才 subprocess（带 CREATE_NO_WINDOW）。
"""
import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import subprocess  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


from core.services.blender_service import (  # noqa: E402
    BlenderService, _pe_file_version, _version_from_name,
)

# ── 1. 纯字符串 / PE 解析 ──
check("目录名解析 blender-4.2.23-windows-x64",
      _version_from_name("blender-4.2.23-windows-x64") == "4.2.23",
      str(_version_from_name("blender-4.2.23-windows-x64")))
check("目录名解析 'Blender 4.2'",
      _version_from_name("Blender 4.2") == "4.2", str(_version_from_name("Blender 4.2")))
check("目录名解析 'blender' → None", _version_from_name("blender") is None)
check("目录名解析 'bin' → None", _version_from_name("bin") is None)

# 系统 python.exe 一定有版本资源，用来验证 PE 读取
import shutil  # noqa: E402
py = shutil.which("python") or sys.executable
pe_ver = _pe_file_version(str(py))
check("PE 版本资源可读", pe_ver is not None and "." in pe_ver, f"{py} -> {pe_ver}")
check("PE 读取对不存在的文件返回 None", _pe_file_version("C:/no/such/file.exe") is None)

# ── 2. 真实安装：不得启动任何进程 ──
svc = BlenderService()
insts = svc.detect_installed(deep_scan=False)
print(f"本机检测到 {len(insts)} 个安装")

calls = {"n": 0}
_orig_run = subprocess.run


def counting_run(*a, **k):
    calls["n"] += 1
    return _orig_run(*a, **k)


subprocess.run = counting_run  # type: ignore[assignment]

ok = True
for inst in insts:
    v = svc._read_version(inst.executable)
    print(f"    {inst.executable} -> {v}")
    if not v:
        ok = False

subprocess.run = _orig_run  # type: ignore[assignment]

if insts:
    check("所有已安装版本都能读出版本号", ok)
    check("读版本**没有启动任何进程**（不再弹黑窗）", calls["n"] == 0, f"subprocess.run 调用 {calls['n']} 次")
else:
    print("    （本机没检测到 Blender，跳过真实安装断言）")

# ── 3. 兜底路径必须隐藏窗口 ──
import inspect  # noqa: E402

src = inspect.getsource(BlenderService._read_version_by_process)
check("兜底 subprocess 带 CREATE_NO_WINDOW", "CREATE_NO_WINDOW" in src,
      "即便真要跑进程也不弹黑窗")
check("_read_version 优先用零进程方案",
      "_pe_file_version" in inspect.getsource(BlenderService._read_version)
      and "_version_from_name" in inspect.getsource(BlenderService._read_version))

# ── 4. 添加版本对话框里的本地目录浏览也不再直接 subprocess ──
vlm_src = ""
try:
    import modules.version_library.module as vlm  # noqa: E402
    vlm_src = inspect.getsource(vlm.VersionSelectorDialog._browse_directory)
except Exception as e:  # noqa: BLE001
    print("    skip:", e)
if vlm_src:
    check("本地目录浏览改用 BlenderService 读版本",
          "subprocess.run" not in vlm_src and "svc._read_version" in vlm_src)

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.exit(1)
print("ALL PASS")
