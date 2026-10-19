import os
import sys

from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── QSettings 测试隔离（monkeypatch）──
# PyQt6 实测坑：setDefaultFormat(IniFormat) 不生效、显式四参数也不走
# setPath 的目录——产品代码里的 QSettings("org", "app") 仍会读写**用户
# 真实注册表**（HKCU\Software\...）。唯一可靠方案是 patch 构造函数，
# 把两参数形态统一重定向到测试 ini。
from PyQt6.QtCore import QSettings as _QS
_TEST_INI = os.path.join(str(Path(__file__).resolve().parents[1]), "_settings", "test.ini")
os.makedirs(os.path.dirname(_TEST_INI), exist_ok=True)
_QS_ORIG_INIT = _QS.__init__
def _qs_patched(self, *a, **k):
    if len(a) == 2 and isinstance(a[0], str) and isinstance(a[1], str):
        _QS_ORIG_INIT(self, _TEST_INI, _QS.Format.IniFormat)
    else:
        _QS_ORIG_INIT(self, *a, **k)
_QS.__init__ = _qs_patched

from PyQt6.QtCore import QEvent, QPointF, QSettings, Qt, QTimer
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QMessageBox

# offscreen 无事件循环：模态弹窗/exec 永久挂起，patch 成 no-op
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: None)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QDialog.exec = lambda self: QDialog.DialogCode.Accepted

from core import theme as _theme_mod
from core.app_context import AppContext
from core.services.addon_service import AddonService, AddonInfo
from modules.addon_store.module import AddonStoreModule, AddonCard, AddonDetailDialog

results = []

def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

def click(widget):
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    for et, btn in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                    (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        QApplication.sendEvent(widget, QMouseEvent(
            et, pos, pos, pos, btn, btn, Qt.KeyboardModifier.NoModifier))

def _theme_stylesheet():
    return _theme_mod.stylesheet()


def main() -> int:
    app = QApplication(sys.argv)

    # ── 1. BudecoTop 唯一数据源 ──
    # 用与产品一致的 config base_dir 缓存（内含预热全量目录）
    from core.services.config_service import ConfigService
    svc = AddonService(cache_dir=ConfigService().base_dir / "addons_cache")
    svc.MIN_REFRESH_INTERVAL = 0
    svc.refresh_cache(wait=True, timeout=300)
    stats = svc.get_stats()
    check(f"budeco 拉取 {stats['total']} 条（>10）", stats["total"] > 10)
    check("sources 只有 budeco", set(stats["sources"]) == {"budeco"}, str(stats["sources"]))
    check("所有条目 source=budeco", all(a.source == "budeco" for a in svc._addons.values()))

    # ── 2. UI：构造 / 卡片固定高 / 无已删渠道残留 ──
    ctx = AppContext()
    mod = AddonStoreModule()
    mod.initialize(ctx)
    w = mod.get_widget()
    mod._addon_service.MIN_REFRESH_INTERVAL = 0
    mod._addon_service.refresh_cache(wait=True, timeout=300)
    mod._refresh_cards()
    app.processEvents()
    cards = w.findChildren(AddonCard)
    check(f"卡片 {len(cards)} 张且固定高 216",
          len(cards) > 0 and all(c.height() == 216 for c in cards))
    check("无镜像复选框残留", not w.findChildren(QCheckBox))

    # ── 3. 连续点击不崩（回归 f-string CSS bug）──
    ok = 0
    for _ in range(3):
        cs = w.findChildren(AddonCard)
        if not cs:
            break
        try:
            click(cs[0])
            app.processEvents()
            ok += 1
        except Exception as e:  # noqa: BLE001
            check(f"点击崩溃: {e}", False)
            break
    check(f"连点 {ok}/3 张卡片不崩", ok == 3)

    # ── 4. bundled 检测（安装质量：用户 Blender 里已装的识别）──
    versions = ctx.blender_service.detect_installed()
    t45 = next((v for v in versions if "4.5" in v.label), None)
    if t45 is not None:
        a = AddonInfo(id="rigify", name="Rigify", author="B", category="动画", description="d")
        found = mod._find_bundled(a, t45)
        check(f"rigify 在 {t45.label} 识别为已捆绑", found is not None,
              str(found.name) if found else "")
        root = mod._blender_scripts_root(t45)
        check("安装目标解析到真实 scripts（用户插件层）",
              root.is_dir() and root.name == "scripts", str(root))

    # ── 5. 安装按钮交互（hover/pressed 三态样式 + 光标）──
    # 模块 widget 挂在全局样式表下（真机中由 MainWindow 沿窗口树继承）
    w.setStyleSheet(_theme_stylesheet())
    app.processEvents()
    install_btns = [b for b in w.findChildren(QCheckBox.__mro__[0].__base__ and __import__("PyQt6.QtWidgets", fromlist=["QPushButton"]).QPushButton)
                    if b.objectName() == "primaryButton" and b.text() == "安装"]
    check(f"安装按钮 {len(install_btns)} 个", len(install_btns) > 0)
    if install_btns:
        ib = install_btns[0]
        img = ib.grab().toImage()
        c = img.pixel(8, 8)
        rgb = ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)
        ok = abs(rgb[0] - 0xE8) < 12 and abs(rgb[1] - 0x7D) < 12
        check(f"安装按钮 normal 态=品牌橙 {rgb}", ok)
        check("安装按钮手指光标", ib.cursor().shape() == Qt.CursorShape.PointingHandCursor)
        check("安装按钮高度 26 不被 padding 撑爆", ib.height() == 26)

    # ── 6. 详情弹窗构造（budeco 条目）──
    sample = next(iter(svc._addons.values()), None)
    if sample is not None:
        try:
            d = AddonDetailDialog(sample, is_installed=False, thumbnail_path=None, parent=w)
            check("budeco 条目详情弹窗构造", True)
        except Exception as e:  # noqa: BLE001
            check(f"详情弹窗构造崩溃: {e}", False)

    failed = [n for n, ok in results if not ok]
    print(f"\n{'ALL PASS' if not failed else 'FAILED: ' + '; '.join(failed)}  "
          f"({len(results) - len(failed)}/{len(results)})")
    QTimer.singleShot(150, app.quit)
    app.exec()
    return 0

if __name__ == "__main__":
    main()
