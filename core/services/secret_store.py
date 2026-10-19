"""本地凭据加解密 — Windows DPAPI。

用途：cookie / token 等只保存在本机的敏感数据，写入 QSettings 前先用
DPAPI（CryptProtectData）加密，绑定当前 Windows 用户，其他账户无法解密。

兼容：
- 旧版本直接存 JSON 明文，没有 `dpapi:v1:` 前缀；`unprotect()` 原样返回，
  调用方按旧格式解析后会在下一次保存时自动升级为加密格式。
- 非 Windows 环境（本应用仅支持 Windows）退化为原样返回，便于测试。
"""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes

_PREFIX = "dpapi:v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _make_blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def available() -> bool:
    return os.name == "nt"


def _crypt32():
    lib = ctypes.windll.crypt32  # type: ignore[attr-defined]
    lib.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    lib.CryptProtectData.restype = wintypes.BOOL
    lib.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    lib.CryptUnprotectData.restype = wintypes.BOOL
    return lib


def _kernel32():
    lib = ctypes.windll.kernel32  # type: ignore[attr-defined]
    lib.LocalFree.argtypes = [ctypes.c_void_p]
    lib.LocalFree.restype = ctypes.c_void_p
    return lib


def protect(text: str) -> str:
    """加密字符串；非 Windows 或空串原样返回。"""
    if not text or not available():
        return text
    blob_in, _keep = _make_blob(text.encode("utf-8"))
    blob_out = _DATA_BLOB()
    crypt32 = _crypt32()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), "BlenderHub", None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        raise OSError("CryptProtectData failed")
    try:
        raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32().LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
    return _PREFIX + base64.b64encode(raw).decode("ascii")


def unprotect(value: str) -> str:
    """解密字符串；无 `dpapi:v1:` 前缀视为旧版明文，原样返回。"""
    if not value or not value.startswith(_PREFIX):
        return value
    if not available():
        raise OSError("DPAPI 不可用，无法解密本地凭据")
    raw = base64.b64decode(value[len(_PREFIX):])
    blob_in, _keep = _make_blob(raw)
    blob_out = _DATA_BLOB()
    crypt32 = _crypt32()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
    ):
        raise OSError("CryptUnprotectData failed")
    try:
        data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32().LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
    return data.decode("utf-8")
