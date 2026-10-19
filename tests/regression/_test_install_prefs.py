"""安装位置持久化 + 在线列表隐藏已装版本 + LTS/稳定版徽章区分。"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QSettings as _QS  # noqa: E402

_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "install_prefs.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
if os.path.exists(_TEST_INI):
    os.remove(_TEST_INI)
_QS_ORIG_INIT = _QS.__init__


def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)


_QS.__init__ = _qs_patched

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication(sys.argv)

import modules.version_library.module as vlm  # noqa: E402
from modules.version_library.module import (  # noqa: E402
    VersionLibraryModule, VersionRow, VersionSelectorDialog,
    get_install_root, get_managed_roots, save_install_root,
)
from core.services.blender_service import BlenderInstallation  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


tmp = Path(tempfile.mkdtemp(prefix="bh_prefs_"))

# ── 1. 安装位置：设置过就要 save（且立即落盘，不等点下载）──
check("初始无保存记录时回退默认", get_install_root("DEFAULT") == "DEFAULT", get_install_root("DEFAULT"))
save_install_root(str(tmp / "MyBlender"), "DEFAULT")
check("保存后能读回", get_install_root("DEFAULT") == str(tmp / "MyBlender"), get_install_root("DEFAULT"))
check("同时登记为托管根目录",
      str(tmp / "MyBlender") in get_managed_roots("DEFAULT"), str(get_managed_roots("DEFAULT")))
save_install_root(str(tmp / "Second"), "DEFAULT")
check("第二个目录也进托管列表",
      str(tmp / "Second") in get_managed_roots("DEFAULT") and
      str(tmp / "MyBlender") in get_managed_roots("DEFAULT"))

# 对话框：填好路径点"下载并安装"后，路径应已写入设置
dlg = VersionSelectorDialog(None, blender_service=None, default_install_dir="DEFAULT")
dlg.install_dir_edit.setText(str(tmp / "FromDialog"))
dlg._on_accept()
check("对话框确定后立即保存安装位置",
      get_install_root("DEFAULT") == str(tmp / "FromDialog"), get_install_root("DEFAULT"))

# ── 2. 在线列表不再显示本地已安装的版本 ──
avail = vlm.BlenderService.get_available_versions()
all_versions = [v["version"] for v in avail.get("stable", [])]
print("在线稳定版共", len(all_versions), "条，例如", all_versions[:3])
target = all_versions[0] if all_versions else ""

dlg2 = VersionSelectorDialog(None, blender_service=None, default_install_dir="DEFAULT",
                             installed_versions={target} if target else set())
lw = dlg2._version_list.get("stable")
shown = []
if lw:
    for i in range(lw.count()):
        shown.append(lw.item(i).data(Qt.ItemDataRole.UserRole))
if target:
    check("本地已装版本从在线列表消失", target not in shown, f"{target} / shown={shown[:3]}")
    # isHidden() 而非 isVisible()：对话框在无头测试里没有 show()，
    # isVisible() 恒 False，但控件本身确实是"未隐藏"状态
    check("给出隐藏提示（数量按版本去重）",
          (not dlg2._online_hint.isHidden()) and "已隐藏 1 个" in dlg2._online_hint.text(),
          dlg2._online_hint.text())
else:
    print("    （本地没有可用版本数据，跳过过滤断言）")

# ── 3. LTS / 稳定版 / 测试版 徽章互相区分 ──
def row_for(version: str, is_lts: bool) -> VersionRow:
    return VersionRow(BlenderInstallation(
        version=version, path=Path("/x"), executable=Path("/x/blender.exe"), is_lts=is_lts,
    ))


def tags_of(row: VersionRow) -> list:
    out = []
    for lab in row.findChildren(QLabel):
        t = lab.text()
        if t in ("🏷 LTS", "✅ 稳定", "🧪 测试"):
            out.append((t, lab.styleSheet()))
    return out


r_lts = row_for("4.5.14", True)
r_stable = row_for("5.2.2", False)
r_beta = row_for("5.3.0-beta", False)

t_lts = tags_of(r_lts)
t_stable = tags_of(r_stable)
t_beta = tags_of(r_beta)
print("LTS tag:", t_lts[:1])
print("stable tag:", t_stable[:1])
print("beta tag:", t_beta[:1])

check("LTS 行带 LTS 徽章", any(t[0] == "🏷 LTS" for t in t_lts))
check("稳定版行带稳定徽章", any(t[0] == "✅ 稳定" for t in t_stable))
check("测试版行带测试徽章", any(t[0] == "🧪 测试" for t in t_beta))
check("三类徽章文字互不相同",
      {t_lts[0][0], t_stable[0][0], t_beta[0][0]} == {"🏷 LTS", "✅ 稳定", "🧪 测试"})
check("LTS 与稳定版底色不同", t_lts[0][1] != t_stable[0][1])
check("稳定版与测试版底色不同", t_stable[0][1] != t_beta[0][1])

# 模块类方法仍能取到（转发没坏）
mod = VersionLibraryModule()
check("模块方法转发可用", callable(mod._managed_roots) and isinstance(mod._managed_roots(), list))

# ── 4. 设置页：安装位置 + 保存按钮 ──
from core.app_context import AppContext  # noqa: E402
from modules.settings import SettingsModule  # noqa: E402

ctx = AppContext()
sm = SettingsModule(ctx)
check("设置页有安装位置输入框", hasattr(sm, "_install_edit"))
check("设置页有保存按钮", hasattr(sm, "_install_save"))
check("设置页带出上次保存的位置",
      sm._install_edit.text() == str(tmp / "FromDialog"), sm._install_edit.text())
check("未改动时保存按钮不可用", not sm._install_save.isEnabled())
sm._install_edit.setText(str(tmp / "FromSettings"))
check("改动后保存按钮亮起", sm._install_save.isEnabled())
sm._save_install_dir()
check("设置页保存写入设置",
      get_install_root("DEFAULT") == str(tmp / "FromSettings"), get_install_root("DEFAULT"))
check("保存后按钮回到灰态", not sm._install_save.isEnabled())
check("保存后显示已保存标记",
      sm._install_state.text() == "已保存" and not sm._install_state.isHidden())

# ── 5. 失效路径回退 / 垃圾路径过滤 ──
import shutil  # noqa: E402

missing = str(tmp / "NotExists")
save_install_root(missing)  # 保存会自动建目录
shutil.rmtree(missing, ignore_errors=True)
check("保存的目录被删掉后回退默认", get_install_root("DEFAULT") == "DEFAULT", get_install_root("DEFAULT"))
check("不存在的托管目录被过滤掉", missing not in get_managed_roots("DEFAULT"),
      str(get_managed_roots("DEFAULT")))

# ── 6. 删除保护边界：官方安装目录绝不能删 ──
ctx2 = AppContext()
root = tmp / "BlenderRoot"
official = root / "Blender 4.5"                                   # 官方安装器建的
managed = root / "5.2.2" / "blender-5.2.2-windows-x64"            # HUB 装的
official.mkdir(parents=True, exist_ok=True)
managed.mkdir(parents=True, exist_ok=True)
save_install_root(str(root))

mod2 = VersionLibraryModule()
mod2.ctx = ctx2
mod2._refresh = lambda *a, **k: None  # type: ignore[method-assign]
mod2._installations = [
    BlenderInstallation("4.5.14", official, official / "blender.exe"),
    BlenderInstallation("5.2.2", managed, managed / "blender.exe"),
]

calls = {"warn": [], "question": []}


class _M:
    class StandardButton:
        Yes = 1
        No = 2

    @classmethod
    def warning(cls, *a, **k):
        calls["warn"].append(a[1] if len(a) > 1 else "")
        return 1

    @classmethod
    def question(cls, *a, **k):
        calls["question"].append(a[1] if len(a) > 1 else "")
        return 2  # 一律选"否"，只验证判断结果，不真删

    @classmethod
    def information(cls, *a, **k):
        return 1


vlm.QMessageBox = _M  # type: ignore[attr-defined]

mod2._delete_version("4.5.14")
check("官方安装目录（Blender 4.5）不允许删除",
      any("外部安装" in str(w) for w in calls["warn"]), str(calls["warn"]))
check("官方安装目录没有弹删除确认", not calls["question"], str(calls["question"]))

calls["warn"].clear()
calls["question"].clear()
mod2._delete_version("5.2.2")
check("HUB 安装目录（<root>/5.2.2/...）允许删除",
      len(calls["question"]) >= 1 and not calls["warn"], str(calls))

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
