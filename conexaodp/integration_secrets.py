"""DPAPI protection for SIB and Selecty credentials stored by the V3 backend."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _transform(value: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        raise RuntimeError("A proteção de credenciais de integração requer o Windows DPAPI.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    entropy_bytes = b"ConexaoDP.Integrations.v1"
    entropy_buffer = ctypes.create_string_buffer(entropy_bytes)
    entropy = _DataBlob(len(entropy_bytes), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                                         ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID,
                                         wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
                                           ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID,
                                           wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    source_buffer = ctypes.create_string_buffer(value)
    source = _DataBlob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = _DataBlob()
    if decrypt:
        description = wintypes.LPWSTR()
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy),
                                        None, None, 0x1, ctypes.byref(output))
        if description:
            kernel32.LocalFree(ctypes.cast(description, wintypes.HLOCAL))
    else:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "Conexão DP Integrações", ctypes.byref(entropy),
                                      None, None, 0x1, ctypes.byref(output))
    if not ok:
        raise OSError(ctypes.get_last_error(), "O Windows não conseguiu proteger a credencial da integração.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def protect_secret(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) > 8192 or any(char in value for char in "\r\n\0"):
        raise ValueError("A credencial de integração parece inválida.")
    encoded = base64.b64encode(_transform(value.encode("utf-8"), decrypt=False)).decode("ascii")
    return "dpapi:v1:" + encoded


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:v1:"):
        raise RuntimeError("A credencial de integração está em um formato não reconhecido.")
    try:
        raw = base64.b64decode(value[9:], validate=True)
        return _transform(raw, decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Não foi possível abrir a credencial protegida da integração.") from error
