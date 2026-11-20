"""Instancia unica por conjunto de raices."""

from __future__ import annotations

import hashlib
import os
from typing import Any


class SingleInstance:
    def __init__(self, key: str):
        digest = hashlib.sha256(key.encode("utf-8", "ignore")).hexdigest()[:24]
        self.name = f"Local\\OriginWarden_{digest}"
        self._handle: Any = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                return True
            self._handle = handle
            return kernel32.GetLastError() != 183
        except Exception:
            return True

    def release(self) -> None:
        if os.name != "nt" or not self._handle:
            return
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(self._handle)
        except Exception:
            pass
        self._handle = None
