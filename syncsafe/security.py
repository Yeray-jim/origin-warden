"""Identidad de raices, armado y guardias de operaciones masivas."""

from __future__ import annotations

import json
import os
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import AppConfig
from .domain import (
    MARKER_DESTINATION,
    MARKER_ORIGIN,
)
from .status import utc_now


class SafetyError(RuntimeError):
    pass


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _win(path) -> Path:
    return Path(str(path))


class IdentityManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.origin_marker = _win(config.origen / MARKER_ORIGIN)
        self.destination_marker = _win(config.destino / MARKER_DESTINATION)
        self.arm_file = config.runtime_dir / "armed.json"

    def _read_marker(self, path: Path) -> dict[str, Any] | None:
        return _read_json(path)

    def verify(self) -> tuple[bool, str]:
        origin = self._read_marker(self.origin_marker)
        destination = self._read_marker(self.destination_marker)
        if origin is None:
            return False, "Falta el marcador de ORIGEN."
        if destination is None:
            return False, "Falta el marcador de DESTINO."
        if origin.get("role") != "origin":
            return False, "El marcador de ORIGEN tiene un rol incorrecto."
        if destination.get("role") != "destination":
            return False, "El marcador de DESTINO tiene un rol incorrecto."
        if origin.get("sync_id") != destination.get("sync_id"):
            return False, "Los marcadores no pertenecen al mismo conjunto."
        armed = _read_json(self.arm_file)
        if armed is None or not armed.get("armed"):
            return False, "El sistema no esta armado."
        if armed.get("sync_id") != origin.get("sync_id"):
            return False, "El armado no coincide con los marcadores."
        return True, "ok"

    def arm(self, force: bool = False) -> str:
        if not _win(self.config.origen).exists():
            raise SafetyError(f"No existe ORIGEN: {self.config.origen}")
        if not _win(self.config.destino).exists():
            raise SafetyError(f"No existe DESTINO: {self.config.destino}")
        origin = self._read_marker(self.origin_marker)
        destination = self._read_marker(self.destination_marker)
        if origin and origin.get("role") != "origin" and not force:
            raise SafetyError("El marcador existente en ORIGEN no tiene rol de origen.")
        if destination and destination.get("role") != "destination" and not force:
            raise SafetyError("El marcador existente en DESTINO no tiene rol de destino.")
        ids = {
            value.get("sync_id")
            for value in (origin, destination)
            if value and value.get("sync_id")
        }
        if len(ids) > 1 and not force:
            raise SafetyError("Los marcadores existentes tienen identidades distintas.")
        sync_id = next(iter(ids), secrets.token_hex(16))
        _write_json(
            self.origin_marker,
            {"sync_id": sync_id, "role": "origin", "created": utc_now()},
        )
        _write_json(
            self.destination_marker,
            {"sync_id": sync_id, "role": "destination", "created": utc_now()},
        )
        managed_file = self.config.runtime_dir / "managed.json"
        current = _read_json(managed_file)
        if not (
            isinstance(current, dict)
            and current.get("origin") == self.config.origen_str
            and current.get("destination") == self.config.destino_str
            and isinstance(current.get("keys"), list)
        ):
            _write_json(
                managed_file,
                {
                    "origin": self.config.origen_str,
                    "destination": self.config.destino_str,
                    "keys": [],
                },
            )
        _write_json(
            self.arm_file,
            {"sync_id": sync_id, "armed": True, "armed_at": utc_now()},
        )
        return sync_id

    def disarm(self) -> None:
        _write_json(self.arm_file, {"armed": False, "disarmed_at": utc_now()})

    def is_armed(self) -> bool:
        return self.verify()[0]


class SafetyController:
    def __init__(self, config: AppConfig):
        self.config = config
        self.runtime = config.runtime_dir
        self.request_path = self.runtime / "approval_required.json"
        self.approval_path = self.runtime / "approval.json"
        self.state_path = self.runtime / "safety.json"
        self.mode = config.seguridad.modo_inicial
        self._deletes: deque[float] = deque()
        self._ingests: deque[tuple[float, int]] = deque()
        self._pause_delete = False
        self._pause_ingest = False
        self._request: dict[str, Any] | None = _read_json(self.request_path)
        if self._request and not self._approval_matches(self._request):
            kind = self._request.get("kind")
            if kind == "delete":
                self._pause_delete = True
            if kind == "ingest":
                self._pause_ingest = True
        self.total_managed = 0

    def _window(self) -> float:
        return max(1, self.config.seguridad.ventana_fallos_min * 60)

    def _prune(self, now: float) -> None:
        window = self._window()
        while self._deletes and now - self._deletes[0] > window:
            self._deletes.popleft()
        while self._ingests and now - self._ingests[0][0] > window:
            self._ingests.popleft()

    def _approval_matches(self, request: dict[str, Any]) -> bool:
        approval = _read_json(self.approval_path)
        if not approval or not approval.get("approved"):
            return False
        if approval.get("token") != request.get("token"):
            return False
        created = approval.get("approved_at_ts") or 0
        return time.time() - float(created) < 3600

    def _request_approval(self, kind: str, count: int, sample: list[str]) -> None:
        token = secrets.token_hex(12)
        request = {
            "token": token,
            "kind": kind,
            "count": count,
            "sample": sample[:20],
            "created_at": utc_now(),
            "created_at_ts": time.time(),
            "message": (
                "Operacion masiva pausada. Revisa el lote y aprueba o rechaza."
            ),
        }
        _write_json(self.request_path, request)
        self._request = request
        _write_json(
            self.state_path,
            {
                "mode": self.mode,
                "pause_delete": self._pause_delete,
                "pause_ingest": self._pause_ingest,
                "request": request,
            },
        )

    def _check_approval(self) -> None:
        if self._request and self._approval_matches(self._request):
            kind = self._request.get("kind")
            if kind == "delete":
                self._pause_delete = False
                self._deletes.clear()
            if kind == "ingest":
                self._pause_ingest = False
                self._ingests.clear()
            self._request = None
            try:
                self.request_path.unlink()
            except OSError:
                pass
            try:
                self.approval_path.unlink()
            except OSError:
                pass
            _write_json(
                self.state_path,
                {
                    "mode": self.mode,
                    "pause_delete": self._pause_delete,
                    "pause_ingest": self._pause_ingest,
                    "request": None,
                },
            )

    def approve(self, token: str | None = None, consume: bool = False) -> bool:
        request = self._request or _read_json(self.request_path)
        if not request:
            return False
        if token and token != request.get("token"):
            return False
        _write_json(
            self.approval_path,
            {
                "token": request.get("token"),
                "approved": True,
                "approved_at": utc_now(),
                "approved_at_ts": time.time(),
            },
        )
        if consume:
            self._check_approval()
        return True

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._check_approval()

    @property
    def simulation(self) -> bool:
        return self.mode == "simulation"

    @property
    def mutable(self) -> bool:
        return self.mode in {"normal", "degraded", "approval"}

    @property
    def destructive(self) -> bool:
        return self.mode == "normal" and not self._pause_delete

    @property
    def can_ingest(self) -> bool:
        return self.mode == "normal" and not self._pause_ingest

    def allow_delete(self) -> tuple[bool, str]:
        self._check_approval()
        if self.mode != "normal":
            return False, f"modo {self.mode}"
        if self._pause_delete:
            return False, "aprobacion de borrado pendiente"
        return True, "ok"

    def record_delete(self, sample: str) -> None:
        now = time.time()
        self._prune(now)
        self._deletes.append(now)
        limit = self.config.seguridad.max_borrados_lote
        percent_limit = self.config.seguridad.max_borrados_porcentaje
        percent_hit = (
            self.total_managed >= 20
            and len(self._deletes) * 100 >= self.total_managed * percent_limit
        )
        if len(self._deletes) >= limit or percent_hit:
            self._pause_delete = True
            self._request_approval("delete", len(self._deletes), [sample])

    def record_ingest(self, path: str, size: int) -> None:
        now = time.time()
        self._prune(now)
        mb = max(0, size) // (1024 * 1024)
        self._ingests.append((now, mb))
        count = len(self._ingests)
        total_mb = sum(item[1] for item in self._ingests)
        if (
            count >= self.config.seguridad.max_ingesta_archivos_lote
            or total_mb >= self.config.seguridad.max_ingesta_mb_lote
        ):
            self._pause_ingest = True
            self._request_approval("ingest", count, [path])

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "pause_delete": self._pause_delete,
            "pause_ingest": self._pause_ingest,
            "request": self._request,
            "deletes_window": len(self._deletes),
            "ingests_window": len(self._ingests),
        }
