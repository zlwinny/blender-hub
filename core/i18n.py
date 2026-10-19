"""应用内中英文切换（i18n）。

用法：
    from core import i18n
    label.setText(i18n.tr("settings.title"))

约定：
- 词条统一放在 core/translations.py（key -> {"zh": ..., "en": ...}）；
- tr() 取不到时回退中文，再回退 key 本身（保证界面不会显示空白）；
- 语言存 QSettings("BlenderHub Team","BlenderHub") 的 ui/language，与窗口几何等偏好一致；
- 切换语言时 set_lang() 会通知所有监听器，界面无需重启即可刷新。
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QSettings

LANG_ZH = "zh"
LANG_EN = "en"
LANGS = (LANG_ZH, LANG_EN)

_ORG = "BlenderHub Team"
_APP = "BlenderHub"
_KEY = "ui/language"

_STRINGS: dict[str, dict[str, str]] = {}
_current: str = LANG_ZH
_loaded = False
_listeners: list[Callable[[str], None]] = []


def register(mapping: dict[str, dict[str, str]]) -> None:
    """注册/合并词条表。"""
    _STRINGS.update(mapping)


def load_lang() -> str:
    """从 QSettings 读取已保存的语言（只加载一次）。"""
    global _current, _loaded
    if not _loaded:
        _loaded = True
        saved = QSettings(_ORG, _APP).value(_KEY, LANG_ZH, type=str)
        _current = saved if saved in LANGS else LANG_ZH
    return _current


def current_lang() -> str:
    return load_lang()


def set_lang(lang: str) -> None:
    """切换语言：持久化 + 通知监听器刷新界面。"""
    global _current
    if lang not in LANGS:
        return
    load_lang()
    if lang == _current:
        return
    _current = lang
    settings = QSettings(_ORG, _APP)
    settings.setValue(_KEY, lang)
    settings.sync()
    for cb in list(_listeners):
        try:
            cb(lang)
        except Exception:  # noqa: BLE001
            pass


def add_listener(callback: Callable[[str], None]) -> None:
    """注册语言变化回调（主窗口用来重刷侧栏与各页文案）。"""
    if callback not in _listeners:
        _listeners.append(callback)


def remove_listener(callback: Callable[[str], None]) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def tr(key: str, **kwargs) -> str:
    """翻译。缺失词条回退中文，再回退 key 本身。"""
    load_lang()
    entry = _STRINGS.get(key)
    if entry:
        text = entry.get(_current) or entry.get(LANG_ZH) or entry.get(LANG_EN)
        if text:
            return text.format(**kwargs) if kwargs else text
    return key


def has(key: str) -> bool:
    return key in _STRINGS


# 词条表在这里注册（translations 不依赖 i18n，无循环导入）
from core.translations import STRINGS  # noqa: E402

register(STRINGS)
