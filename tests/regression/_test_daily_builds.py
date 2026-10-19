"""测试版通道（官方每日构建）+ 手动刷新版本列表。

背景：官方 release 目录里只有正式版，「测试版」标签页因此一直是空的。
builder.blender.org 的每日构建才有 alpha/beta（如 5.3 / 5.4 alpha）。
"""
import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BH_DISABLE_WEBENGINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import QSettings as _QS  # noqa: E402

_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "daily.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
_QS_ORIG_INIT = _QS.__init__


def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)


_QS.__init__ = _qs_patched

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QTabWidget  # noqa: E402

app = QApplication(sys.argv)

from core.services.blender_service import BlenderService  # noqa: E402
import modules.version_library.module as vlm  # noqa: E402
from modules.version_library.module import VersionSelectorDialog  # noqa: E402

failures = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        failures.append(name)


# ── 1. 每日构建抓取（真实网络，抓不到就跳过）──
daily = BlenderService.fetch_daily_builds(timeout=12)
print("daily builds:", [(d["version"], d.get("date", "")) for d in daily])

if daily:
    check("能抓到每日构建", len(daily) >= 1, str(len(daily)))
    check("版本号带 alpha/beta 后缀",
          all("-alpha" in d["version"] or "-beta" in d["version"] for d in daily),
          str([d["version"] for d in daily]))
    check("带 CDN 直链", all(str(d.get("url", "")).startswith("http") for d in daily))
    check("按版本降序", [d["version"] for d in daily] ==
          sorted([d["version"] for d in daily],
                 key=lambda v: BlenderService._version_tuple(v), reverse=True))
    # 测试版必须能拿到可下载地址（release 目录拼不出来）
    v0 = daily[0]["version"]
    url = BlenderService().get_download_url(v0)
    check("测试版能解析出下载地址", bool(url) and "cdn.builder.blender.org" in str(url), str(url))
else:
    print("    （本次没抓到每日构建，跳过网络相关断言）")

# ── 2. 刷新按钮（打桩网络，只验证列表重建逻辑）──
fake = {
    "stable": [
        {"version": "6.0.1", "label": "Blender 6.0.1", "is_lts": True, "size": "360 MB", "date": "2027-11-03"},
        {"version": "5.2.2", "label": "Blender 5.2.2 LTS", "is_lts": True, "size": "350 MB", "date": ""},
    ],
    "beta": [
        {"version": "6.1.0-alpha", "label": "Blender 6.1.0 Alpha", "is_lts": False, "size": "", "date": "abc123"},
    ],
    "lts": [
        {"version": "6.0.1", "label": "Blender 6.0.1", "is_lts": True, "size": "360 MB", "date": ""},
    ],
}
orig = BlenderService.get_available_versions
BlenderService.get_available_versions = staticmethod(lambda force=False: fake)  # type: ignore[assignment]

dlg = VersionSelectorDialog(None, blender_service=None, default_install_dir="",
                            installed_versions={"5.2.2"})
check("对话框有刷新按钮", hasattr(dlg, "refresh_btn"))

before = dlg._version_list["stable"].count()
dlg._refresh_version_lists()
after = dlg._version_list["stable"].count()
check("刷新后稳定版列表被重建", after == 1, f"before={before} after={after}")
check("刷新后能出现 6.0.1",
      dlg._version_list["stable"].item(0).data(Qt.ItemDataRole.UserRole) == "6.0.1",
      str(dlg._version_list["stable"].item(0).text()) if after else "")
check("刷新后仍过滤已装版本（5.2.2 不在）",
      [dlg._version_list["stable"].item(i).data(Qt.ItemDataRole.UserRole) for i in range(after)] == ["6.0.1"])
check("测试版列表有内容", dlg._version_list["beta"].count() >= 1,
      str(dlg._version_list["beta"].count()))
check("刷新后给出最新提示", "已更新" in dlg._online_hint.text(), dlg._online_hint.text())
check("刷新后按钮恢复可用", dlg.refresh_btn.isEnabled())

tabs = dlg.tabs.widget(0).findChild(QTabWidget)
check("标签标题带数量", tabs is not None and "(" in tabs.tabText(0),
      tabs.tabText(0) if tabs else "")

BlenderService.get_available_versions = orig  # type: ignore[assignment]

print()
if failures:
    print("FAILED", len(failures), failures)
    sys.stdout.flush()
    os._exit(1)
print("ALL PASS")
sys.stdout.flush()
os._exit(0)
