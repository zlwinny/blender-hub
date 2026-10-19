"""免责声明 — 首次启动确认 + 「关于」里的只读查看。

折中口径（与 MIT 许可一致）：
- 不禁止商业使用，只说明「按现状提供、风险自担」；
- 明确通过本工具安装的第三方插件/素材，授权仍归各自作者；
- 应用本体按 MIT 许可开源。

首次启动弹「退出 / 同意」，同意后写入 QSettings，之后不再弹出；
CI / 无显示冒烟通过 BH_OFFSCREEN 或 BH_SKIP_DISCLAIMER 跳过。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core import theme

DISCLAIMER_TITLE = "免责声明"

DISCLAIMER_TEXT = (
    "本软件按「现状」提供，不附带任何明示或暗示的担保；"
    "使用本软件所产生的任何风险与后果由使用者自行承担。\n\n"
    "本软件仅提供 Blender 版本、项目与插件的本地管理能力，不代为下载、分发或授权任何第三方插件与素材。"
    "通过本软件安装的插件、模型、贴图等资源，其版权与授权仍归各自作者所有，"
    "请在遵守相应许可的前提下使用。\n\n"
    "本软件基于 MIT 许可开源，允许在保留版权声明的前提下自由使用、修改与再分发；"
    "请勿将本软件用于违反当地法律法规的用途。"
)

_ORG = "BlenderHub Team"
_APP = "BlenderHub"
_KEY = "disclaimer_accepted"


def is_accepted() -> bool:
    return bool(QSettings(_ORG, _APP).value(_KEY, False, type=bool))


def set_accepted(value: bool = True) -> None:
    settings = QSettings(_ORG, _APP)
    settings.setValue(_KEY, value)
    settings.sync()


def should_skip() -> bool:
    """无显示环境 / CI 冒烟跳过弹窗，避免无人值守时阻塞启动。"""
    return bool(os.environ.get("BH_SKIP_DISCLAIMER") or os.environ.get("BH_OFFSCREEN"))


def build_dialog(parent=None, first_run: bool = True) -> QDialog:
    """构造免责声明对话框（不 exec，便于测试与复用）。"""
    dlg = QDialog(parent)
    dlg.setObjectName("disclaimerDialog")
    dlg.setWindowTitle(DISCLAIMER_TITLE)
    dlg.setModal(True)
    dlg.setMinimumWidth(540)

    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(26, 24, 26, 20)
    lay.setSpacing(14)

    title = QLabel(DISCLAIMER_TITLE)
    title.setObjectName("dialogTitle")
    lay.addWidget(title)

    body = QLabel(DISCLAIMER_TEXT)
    body.setObjectName("dialogBody")
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(body)
    lay.addStretch()

    buttons = QHBoxLayout()
    buttons.setSpacing(10)
    buttons.addStretch()
    if first_run:
        quit_btn = QPushButton("退出")
        quit_btn.setObjectName("ghost")
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.clicked.connect(dlg.reject)
        buttons.addWidget(quit_btn)
    close_btn = QPushButton("同意" if first_run else "关闭")
    close_btn.setObjectName("primary")
    close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    close_btn.clicked.connect(dlg.accept)
    buttons.addWidget(close_btn)
    lay.addLayout(buttons)

    return dlg


def show_disclaimer(parent=None, first_run: bool = True) -> bool:
    """展示对话框；返回 True 表示同意/关闭。首次启动同意时持久化。"""
    dlg = build_dialog(parent, first_run=first_run)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    if first_run and accepted:
        set_accepted(True)
    return accepted


def ensure_accepted(parent=None) -> bool:
    """启动入口调用：已同意或无显示环境直接放行，否则要求确认。"""
    if should_skip() or is_accepted():
        return True
    return show_disclaimer(parent, first_run=True)
