"""Deteccion automatica de rutas locales, UNC y unidades mapeadas."""

from __future__ import annotations

import os
from pathlib import PureWindowsPath


def _windows_drive_type(drive: str) -> int:
    if os.name != "nt":
        return 3
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(drive + "\\"))
    except Exception:
        return 0


def classify_path(path: PureWindowsPath) -> str:
    raw = str(path)
    upper = raw.upper()
    if upper.startswith("\\\\?\\UNC\\") or raw.startswith("\\\\"):
        return "unc"
    if upper.startswith("\\\\?\\"):
        return "local"
    drive = path.drive
    if not drive:
        return "unknown"
    if len(drive) == 2 and drive[1] == ":":
        drive_type = _windows_drive_type(drive)
        if drive_type == 4:
            return "mapped"
        if drive_type == 3:
            return "local"
        if drive_type in {2, 5, 6}:
            return "removable"
        return "unknown"
    return "unknown"


def observer_mode(path: PureWindowsPath, requested: str) -> str:
    kind = classify_path(path)
    if requested == "nativo":
        return "native"
    if requested == "polling":
        return "polling"
    return "polling" if kind in {"unc", "mapped"} else "native"


def path_kind_label(kind: str) -> str:
    return {
        "local": "Local NTFS",
        "unc": "UNC / SMB",
        "mapped": "Unidad mapeada",
        "removable": "Unidad extraible",
        "unknown": "Desconocida",
    }.get(kind, kind)
