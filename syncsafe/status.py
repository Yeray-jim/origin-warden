"""Estado compartido entre servicio y panel mediante JSON atomico."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StatusStore:
    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir
        self.path = runtime_dir / "status.json"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "schema": 1,
            "state": "starting",
            "mode": "simulation",
            "armed": False,
            "pid": os.getpid(),
            "started_at": utc_now(),
            "heartbeat": utc_now(),
            "queue": 0,
            "pending_restores": 0,
            "pending_approval": None,
            "last_error": None,
            "last_event": None,
            "last_progress": utc_now(),
            "trash": {"files": 0, "bytes": 0},
            "guards": {},
            "counters": {},
        }

    def set(self, **values: Any) -> None:
        with self._lock:
            self._data.update(values)
            self._data["heartbeat"] = utc_now()

    def update_nested(self, key: str, **values: Any) -> None:
        with self._lock:
            current = self._data.get(key)
            if not isinstance(current, dict):
                current = {}
                self._data[key] = current
            current.update(values)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = dict(self._data)
            if isinstance(data.get("guards"), dict):
                data["guards"] = dict(data["guards"])
            return data

    def write(self) -> None:
        data = self.snapshot()
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

    def read(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return None
