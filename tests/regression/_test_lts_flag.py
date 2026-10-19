"""LTS 判定修正：装了 5.2.2 LTS 却显示"稳定版"的回归。

根因：`BlenderService._is_lts()` 用了一份**与在线版本列表不同步**的硬编码
集合（缺 5.2），而在线列表用的是 `_LTS_MINORS`（含 5.2）。
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from core.app_context import AppContext  # noqa: E402
from core.services.blender_service import BlenderService, BlenderInstallation  # noqa: E402
from modules.version_library.module import VersionLibraryModule, VersionRow  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


svc = BlenderService()

# ── 1. LTS 判定与在线版本列表同源 ──
check("_LTS_MINORS 含 5.2", "5.2" in BlenderService._LTS_MINORS, str(sorted(BlenderService._LTS_MINORS)))
check("5.2.2 判为 LTS", svc._is_lts("5.2.2") is True)
check("5.2.9 判为 LTS", svc._is_lts("5.2.9") is True)
check("4.5.14 判为 LTS", svc._is_lts("4.5.14") is True)
check("4.2.23 判为 LTS", svc._is_lts("4.2.23") is True)
check("3.3.21 判为 LTS", svc._is_lts("3.3.21") is True)
check("5.1.2 判为非 LTS", svc._is_lts("5.1.2") is False)
check("4.4.3 判为非 LTS", svc._is_lts("4.4.3") is False)
check("异常输入不炸", svc._is_lts("") is False and svc._is_lts("abc") is False)

# 在线版本数据里标了 is_lts 的，判定必须一致
avail = BlenderService.get_available_versions()
stated = {v["version"]: bool(v.get("is_lts")) for v in avail.get("stable", [])}
mismatch = [v for v, flag in stated.items() if svc._is_lts(v) != flag]
check("与在线列表的 is_lts 标注完全一致", not mismatch, f"不一致: {mismatch}")
print("    在线列表 LTS 项:", [v for v, f in stated.items() if f])

# ── 2. 列表行徽章：5.2 LTS 不能显示成稳定版 ──
row_lts = VersionRow(BlenderInstallation("5.2.2", Path("/x"), Path("/x/blender.exe"), is_lts=True))
row_stable = VersionRow(BlenderInstallation("5.1.2", Path("/x"), Path("/x/blender.exe"), is_lts=False))
from PyQt6.QtWidgets import QLabel  # noqa: E402


def tags(row):
    return [lab.text() for lab in row.findChildren(QLabel)
            if lab.text() in ("🏷 LTS", "✅ 稳定", "🧪 测试")]


check("5.2.2 行显示 LTS 徽章", "🏷 LTS" in tags(row_lts), str(tags(row_lts)))
check("5.1.2 行显示稳定徽章", "✅ 稳定" in tags(row_stable), str(tags(row_stable)))

# ── 3. 已缓存记录的 LTS 标记会被重算（修用户已有的 5.2.2）──
tmp = Path(tempfile.mkdtemp(prefix="bh_lts_"))
ctx = AppContext()
ctx.config._base_dir = tmp / "config"  # type: ignore[union-attr]
base = ctx.config._base_dir / "blender_versions"  # type: ignore[union-attr]
inst_dir = base / "5.2.2" / "blender-5.2.2-windows-x64"
inst_dir.mkdir(parents=True, exist_ok=True)
exe = inst_dir / "blender.exe"
exe.write_bytes(b"MZ fake")

mod = VersionLibraryModule()
mod.ctx = ctx
# 手工写一份"旧规则"缓存：5.2.2 被记成非 LTS
import json  # noqa: E402

cache = mod._cache_path()
cache.write_text(json.dumps([{
    "version": "5.2.2", "path": str(inst_dir), "executable": str(exe),
    "is_lts": False, "source": "managed",
}], ensure_ascii=False), encoding="utf-8")

loaded = mod._load_installed_cache()
check("缓存里 is_lts=False 被重算为 True",
      bool(loaded) and loaded[0].is_lts is True,
      str([(i.version, i.is_lts) for i in loaded]))

# 渲染后行上的徽章也应当是 LTS
mod._installations = loaded
mod.get_widget()
mod._clear_rows()
mod._render_rows()
found = []
for i in range(mod._rows_layout.count()):  # type: ignore[union-attr]
    w = mod._rows_layout.itemAt(i).widget()  # type: ignore[union-attr]
    if w is None:
        continue
    for lab in w.findChildren(QLabel):
        if lab.text() in ("🏷 LTS", "✅ 稳定", "🧪 测试"):
            found.append(lab.text())
check("渲染出来的徽章是 LTS", "🏷 LTS" in found, str(found))

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
