# -*- mode: python ; coding: utf-8 -*-
"""Blender Hub 打包配置（仓库内可复现版本）。

与被 PyInstaller 自动生成的那份不同：这里**没有任何绝对路径**，
全部基于 `SPECPATH`（本文件所在目录）推导，克隆到任何机器都能直接构建：

    pip install -r requirements.txt
    pyinstaller packaging/BlenderHub.spec --noconfirm

产物：dist/BlenderHub/（onedir 便携版，整目录拷贝即用）。
"""
import os

HERE = os.path.abspath(SPECPATH)            # 本 spec 所在目录：packaging/
ROOT = os.path.dirname(HERE)                # 项目根目录
MODULES_DIR = os.path.join(ROOT, "modules")

# 业务模块的清单与包标记必须随包分发，按目录自动收集，
# 新增模块时无需再手工改这份 spec
module_datas = []
for name in sorted(os.listdir(MODULES_DIR)):
    mdir = os.path.join(MODULES_DIR, name)
    if not os.path.isdir(mdir):
        continue
    for fname in ("manifest.json", "__init__.py"):
        fpath = os.path.join(mdir, fname)
        if os.path.isfile(fpath):
            module_datas.append((fpath, f"modules/{name}"))

# 应用图标 + 许可证：关于页的「开源许可证」会优先读取随包分发的 LICENSE
project_datas = [(os.path.join(HERE, "icon.ico"), ".")]
_license = os.path.join(ROOT, "LICENSE")
if os.path.isfile(_license):
    project_datas.append((_license, "."))

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT, MODULES_DIR],
    binaries=[],
    datas=project_datas + module_datas,
    hiddenimports=[
        "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
        "PyQt6.QtNetwork", "PyQt6.QtWebChannel",
        "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore",
        "requests", "platformdirs", "bs4",
        "core.module_manager", "core.api.module_api",
        "core.services.event_bus", "core.services.config_service",
        "core.services.log_service", "core.services.download_service",
        "core.services.blender_service", "core.services.auth_service",
        "core.services.signature_service", "core.services.addon_service",
        "core.services.addon_verifier", "core.services.budeco_auth",
        "core.services.browser_cookie_import",
        "modules.addon_store.module", "modules.addon_store.qq_login_dialog",
        "modules.addon_store.install_browser",
        "modules.addon_store.cookie_harvest",
        "modules.addon_store.pan_download_dialog",
        "modules.addon_store.download_watch",
        "modules.config_center.module", "modules.project_center.module",
        "modules.version_library.module", "modules.settings",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtQuickWidgets",
        "PyQt6.QtQuick3D", "PyQt6.QtPositioning", "PyQt6.QtMultimedia",
        "PyQt6.QtMultimediaWidgets", "PyQt6.QtBluetooth", "PyQt6.QtNfc",
        "PyQt6.QtSensors", "PyQt6.QtSerialPort", "PyQt6.QtSql",
        "PyQt6.QtTest", "PyQt6.QtDesigner", "PyQt6.QtCharts",
        "PyQt6.QtDataVisualization", "PyQt6.Qt3DCore", "PyQt6.Qt3DRender",
        "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets", "PyQt6.QtPdf",
        "PyQt6.QtPdfWidgets", "PyQt6.QtNetworkAuth",
        "PyQt6.QtRemoteObjects", "PyQt6.QtSpatialAudio",
        "PyQt6.QtStateMachine", "PyQt6.QtTextToSpeech",
        "PyQt6.QtSvg", "PyQt6.QtSvgWidgets", "PyQt6.QtUiTools",
        "PyQt6.QtHelp",
        "tkinter", "unittest", "pydoc", "doctest",
        "numpy", "PIL", "lxml", "setuptools", "pip",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlenderHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(HERE, "version_info.txt"),
    icon=[os.path.join(HERE, "icon.ico")],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BlenderHub",
)
