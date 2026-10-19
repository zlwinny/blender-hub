"""复现：真机点「登录 Budeco」崩溃 —— 在源码环境逐步构造 QQLoginDialog。"""
import os
import sys
from pathlib import Path
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

# 与 main.py 相同的初始化顺序
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

app = QApplication(sys.argv)
print("[1] QApplication created", flush=True)

print("[2] importing QtWebEngineWidgets ...", flush=True)
try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    print("[2] import OK", flush=True)
except Exception:
    print("[2] import FAILED:")
    traceback.print_exc()
    sys.exit(10)

print("[3] constructing QWebEngineView ...", flush=True)
try:
    view = QWebEngineView()
    print("[3] construct OK", flush=True)
except Exception:
    print("[3] construct FAILED:")
    traceback.print_exc()
    sys.exit(11)

print("[4] defaultProfile cookieStore ...", flush=True)
try:
    from PyQt6.QtWebEngineCore import QWebEngineProfile
    st = QWebEngineProfile.defaultProfile().cookieStore()
    print("[4] cookieStore OK", flush=True)
except Exception:
    print("[4] cookieStore FAILED:")
    traceback.print_exc()
    sys.exit(12)

print("[5] constructing QQLoginDialog ...", flush=True)
try:
    from modules.addon_store.qq_login_dialog import QQLoginDialog

    class _Auth:
        _log = None
        OAUTH_ENTRY = "https://budeco.top/oauth/socialogin/?act=login&type=qq"

    dlg = QQLoginDialog(_Auth())
    print("[5] dialog OK, ff_synced info:", dlg._status.text()[:80], flush=True)
except Exception:
    print("[5] dialog FAILED:")
    traceback.print_exc()
    sys.exit(13)

print("ALL STEPS OK", flush=True)
sys.exit(0)
