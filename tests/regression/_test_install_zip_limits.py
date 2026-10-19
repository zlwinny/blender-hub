"""Blender 安装包解压上限 / 路径穿越防护的回归测试。

覆盖 InstallWorker.run 的：
1. 正常 zip 解压成功
2. 条目数超限被拒绝
3. 解压后总大小超限被拒绝
4. 含 `..` 的非法路径被拒绝
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import modules.version_library.module as mod  # noqa: E402
from modules.version_library.module import InstallWorker  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


tmp = Path(tempfile.mkdtemp(prefix="bh_ziplimit_"))


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


def run_install(zip_path, install_dir):
    results: list = []
    w = InstallWorker(str(zip_path), str(install_dir))
    w.finished.connect(lambda ok, msg: results.append((ok, msg)))
    w.run()  # 直接同步调用，不启动事件循环
    return results


# ── 1. 正常解压 ──
ok_zip = make_zip(tmp / "ok.zip", [("blender-4.2.0/blender.exe", b"MZ" * 10)])
r = run_install(ok_zip, tmp / "inst_ok")
check("正常 zip 解压成功", bool(r) and r[0][0] is True, str(r))
check("解压出目标目录", (tmp / "inst_ok" / "blender-4.2.0" / "blender.exe").exists())

# ── 2. 条目数超限 ──
orig_entries = mod._MAX_ZIP_ENTRIES
mod._MAX_ZIP_ENTRIES = 3
many_zip = make_zip(tmp / "many.zip", [(f"f{i}.txt", b"x") for i in range(5)])
r = run_install(many_zip, tmp / "inst_many")
check("条目数超限被拒绝", bool(r) and not r[0][0] and "条目数" in r[0][1], str(r))
mod._MAX_ZIP_ENTRIES = orig_entries

# ── 3. 总大小超限 ──
orig_bytes = mod._MAX_ZIP_BYTES
mod._MAX_ZIP_BYTES = 10
big_zip = make_zip(tmp / "big.zip", [("a.txt", b"x" * 100)])
r = run_install(big_zip, tmp / "inst_big")
check("总大小超限被拒绝", bool(r) and not r[0][0] and "总大小" in r[0][1], str(r))
mod._MAX_ZIP_BYTES = orig_bytes

# ── 4. 路径穿越 ──
evil_zip = make_zip(tmp / "evil.zip", [("../evil.txt", b"boom")])
r = run_install(evil_zip, tmp / "inst_evil")
check("路径穿越被拒绝", bool(r) and not r[0][0] and "非法路径" in r[0][1], str(r))
check("未写出越界文件", not (tmp / "evil.txt").exists())

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
