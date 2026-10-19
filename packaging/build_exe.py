"""PyInstaller 打包脚本。

遵循两个实测坑：
1. 绝不用 --collect-submodules=PyQt6（analysis 阶段 OOM）
2. 必须排除 PyQt6.QtQml/QtQuick/QtQuickWidgets/QtPositioning（headless 构建死锁）
另排除未使用的 QtWebEngine 等重型模块以显著减小体积。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = BUILD / "dist"
APP_NAME = "BlenderHub"

# 图标与版本资源都在 packaging/（图标由 make_app_icon.py 生成）。
# 历史上这里指向 build/icon.ico，而 build/ 已被清理，导致本脚本构建出的 exe 没有图标。
PKG = ROOT / "packaging"
ICON = PKG / "icon.ico"
VERSION_FILE = PKG / "version_info.txt"

EXCLUDES = [
    "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickWidgets", "PyQt6.QtQuick3D",
    "PyQt6.QtPositioning", "PyQt6.QtMultimedia",
    "PyQt6.QtMultimediaWidgets", "PyQt6.QtBluetooth", "PyQt6.QtNfc",
    "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtSql", "PyQt6.QtTest",
    "PyQt6.QtDesigner", "PyQt6.QtCharts", "PyQt6.QtDataVisualization",
    "PyQt6.Qt3DCore", "PyQt6.Qt3DRender", "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets",
    "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtNetworkAuth", "PyQt6.QtRemoteObjects",
    "PyQt6.QtSpatialAudio", "PyQt6.QtStateMachine", "PyQt6.QtTextToSpeech",
    "PyQt6.QtSvg", "PyQt6.QtSvgWidgets", "PyQt6.QtUiTools", "PyQt6.QtHelp",
    "tkinter", "unittest", "pydoc", "doctest", "numpy", "PIL",
    # 死重（实测零引用，见 HIDDEN 注释）：避免连带 libcrypto/libssl 约 19MB + lxml 6.7MB
    "lxml", "cryptography", "OpenSSL", "setuptools", "pip",
]

HIDDEN = [
    "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
    # lxml / cryptography 已移除：
    # 实测全项目零引用（BeautifulSoup 统一用 "html.parser"，无 Fernet/hazmat 使用），
    # 仅作为 requests 的可选依赖被列入 hiddenimports，却连带打包 libcrypto(7.7M) +
    # libssl(1.6M) + cryptography(9.5M) + _cffi + libffi ≈ 19MB 与 lxml 6.7MB 死重。
    # requests 在缺少它们时正常工作（走纯 Python fallback）。
    "requests", "platformdirs", "bs4",
    # 核心层
    "core.module_manager", "core.api.module_api",
    "core.services.event_bus", "core.services.config_service", "core.services.log_service",
    "core.services.download_service", "core.services.blender_service",
    "core.services.auth_service", "core.services.signature_service", "core.services.addon_service",
    "core.services.addon_verifier",
    # QQ 登录内嵌浏览器（budeco.top 下载需登录态，必须从应用内嵌浏览器取回 cookie）
    # QtNetwork 不能排除：setCookie/cookieAdded 走它，PyQt6.QtWebEngineCore 强依赖。
    "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore", "PyQt6.QtWebChannel",
    "PyQt6.QtNetwork",
    # 功能模块（ModuleManager 动态 importlib 加载）
    "modules.addon_store.module", "modules.addon_store.qq_login_dialog",
    "modules.addon_store.install_browser", "modules.addon_store.cookie_harvest",
    "modules.addon_store.pan_download_dialog", "modules.addon_store.download_watch",
    "core.services.budeco_auth", "core.services.browser_cookie_import",
    "modules.config_center.module", "modules.project_center.module", "modules.version_library.module",
    "modules.settings",
]


KEEP_PREV_DIST = 2  # 旧产物保留份数（每份约 110MB，超过就永久删除）


def cleanup_old_dist(keep: int = KEEP_PREV_DIST) -> None:
    """清理过期的 _prev_* 产物目录。

    旧实现只把 dist 改名成 _prev_<时间戳> 却永不删除，build/dist 已累积 5 份共 679MB。
    这里保留最近 keep 份，其余永久删除。
    """
    if not DIST.exists():
        return
    prevs = sorted(
        (p for p in DIST.glob("_prev_*") if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in prevs[keep:]:
        size_mb = sum(f.stat().st_size for f in old.rglob("*") if f.is_file()) / 1024 / 1024
        print(f"[build] 删除过期产物 {old.name}（{size_mb:.0f}MB）")
        shutil.rmtree(old, ignore_errors=True)


def main() -> int:
    DIST.mkdir(parents=True, exist_ok=True)
    target = DIST / APP_NAME
    if target.exists():  # 旧目录改名避让，避免 COLLECT 批量删除被拦
        target.rename(DIST / f"_prev_{time.strftime('%Y%m%d_%H%M%S')}")
    cleanup_old_dist()

    args = [
        "--name=" + APP_NAME,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--clean",
        f"--distpath={DIST}",
        f"--workpath={BUILD / 'work'}",
        f"--specpath={BUILD}",
        f"--paths={ROOT}",
        f"--paths={ROOT / 'modules'}",
        # 不启用 UPX：压缩 Qt DLL 是 Windows Defender / 第三方杀软误报的高频诱因，
        # 且产物未签名会触发 SmartScreen。体积收益（约 10-15%）远小于误报代价。
        "--noupx",
    ]

    # 应用图标 + PE 版本资源（旧实现两者都缺：exe 无图标、文件属性里无版本信息）
    if ICON.exists():
        args.append(f"--icon={ICON}")
        # 运行时也要能拿到图标：main.py 用 setWindowIcon 设置标题栏/任务栏图标，
        # 只加 --icon 只影响 exe 文件本身的图标，窗口内仍会显示 Qt 默认图标。
        args.append(f"--add-data={ICON}{os.pathsep}.")
        print(f"[build] 使用图标 {ICON}")
    else:
        print("[build] 警告：packaging/icon.ico 不存在，先运行 packaging/make_app_icon.py")
    if VERSION_FILE.exists():
        args.append(f"--version-file={VERSION_FILE}")
        print(f"[build] 使用版本资源 {VERSION_FILE}")
    args += [f"--hidden-import={m}" for m in HIDDEN]
    args += [f"--exclude-module={m}" for m in EXCLUDES]
    # 模块 manifest / __init__ 必须随包分发，否则 ModuleManager 发现不到模块
    for d in sorted((ROOT / "modules").iterdir()):
        if not d.is_dir():
            continue
        for f in ("manifest.json", "__init__.py"):
            if (d / f).exists():
                args.append(f"--add-data={d / f}{os.pathsep}modules/{d.name}")
    args.append(str(ROOT / "main.py"))

    print("[build] 开始打包 …")
    PyInstaller.__main__.run(args)
    exe = target / f"{APP_NAME}.exe"
    print(f"[build] 产物：{exe}  存在={exe.exists()}")
    return 0 if exe.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
