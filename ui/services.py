"""Servicios compartidos por el panel."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from syncsafe.config import AppConfig, load_config, save_config_atomic
from syncsafe.security import IdentityManager, SafetyController


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    if (base / "config.json").exists():
        return base
    if (base.parent / "config.json").exists():
        return base.parent
    return base


class StatusReader:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or app_dir()
        self.path = self.base_dir / "runtime" / "status.json"

    def read(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        data["stale"] = self._stale(data.get("heartbeat"))
        return data

    def _stale(self, value: Any) -> bool:
        if not isinstance(value, str):
            return True
        try:
            moment = datetime.fromisoformat(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - moment).total_seconds() > 20
        except ValueError:
            return True


class ConfigService:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or app_dir()
        self.path = self.base_dir / "config.json"

    def load(self) -> AppConfig:
        return load_config(self.path, allow_last_good=True)

    def arm(self, force: bool = False) -> str:
        config = self.load()
        return IdentityManager(config).arm(force=force)

    def is_armed(self) -> bool:
        try:
            return IdentityManager(self.load()).is_armed()
        except Exception:
            return False

    def approval_request(self) -> dict[str, Any] | None:
        try:
            raw = json.loads((self.base_dir / "runtime" / "approval_required.json").read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return None

    def approve(self, token: str | None = None) -> bool:
        return SafetyController(self.load()).approve(token)

    def save_fields(self, values: dict[str, Any]) -> AppConfig:
        config = self.load()
        raw = config.to_json_dict()
        raw["ORIGEN"] = values.get("origen", raw["ORIGEN"])
        raw["DESTINO"] = values.get("destino", raw["DESTINO"])
        raw["APORTES"]["subcarpeta"] = values.get(
            "aportes", raw["APORTES"].get("subcarpeta", "Aportes_Usuarios")
        )
        raw["OBSERVACION"]["modo_origen"] = values.get(
            "modo_origen", raw["OBSERVACION"].get("modo_origen", "auto")
        )
        raw["OBSERVACION"]["modo_destino"] = values.get(
            "modo_destino", raw["OBSERVACION"].get("modo_destino", "auto")
        )
        raw["RECONCILIACION"]["intervalo_min"] = int(
            values.get("intervalo_min", raw["RECONCILIACION"].get("intervalo_min", 20))
        )
        raw["RECONCILIACION"]["restauracion_max_min"] = int(
            values.get(
                "restauracion_max_min",
                raw["RECONCILIACION"].get("restauracion_max_min", 60),
            )
        )
        raw["RECONCILIACION"]["trabajadores"] = int(
            values.get("trabajadores", raw["RECONCILIACION"].get("trabajadores", 2))
        )
        raw["RECONCILIACION"]["debounce_seg"] = float(
            values.get("debounce_seg", raw["RECONCILIACION"].get("debounce_seg", 1.5))
        )
        raw["RECONCILIACION"]["gracia_borrado_seg"] = int(
            values.get(
                "gracia_borrado_seg",
                raw["RECONCILIACION"].get("gracia_borrado_seg", 60),
            )
        )
        raw["SEGURIDAD"]["papelera_activa"] = bool(
            values.get(
                "papelera_activa",
                raw["SEGURIDAD"].get("papelera_activa", True),
            )
        )
        raw["SEGURIDAD"]["max_aporte_mb"] = int(
            values.get("max_aporte_mb", raw["SEGURIDAD"].get("max_aporte_mb", 2048))
        )
        raw["SEGURIDAD"]["requerir_armado"] = bool(
            values.get(
                "requerir_armado",
                raw["SEGURIDAD"].get("requerir_armado", True),
            )
        )
        raw["LOG"]["nivel"] = str(values.get("nivel_log", raw["LOG"].get("nivel", "INFO")))

        temp = self.path.with_name("config.validate.json")
        try:
            temp.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            validated = load_config(temp, allow_last_good=False)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass
        validated = replace(
            validated,
            base_dir=self.base_dir,
        )
        paths_changed = (
            str(validated.origen).lower() != str(config.origen).lower()
            or str(validated.destino).lower() != str(config.destino).lower()
        )
        save_config_atomic(validated, self.path)
        if paths_changed:
            IdentityManager(config).disarm()
            IdentityManager(validated).disarm()
            for name in ("managed.json", "armed.json", "approval_required.json", "approval.json"):
                try:
                    (self.base_dir / "runtime" / name).unlink()
                except OSError:
                    pass
        return validated

    def test_paths(self) -> tuple[bool, str]:
        config = self.load()
        problems: list[str] = []
        for label, value in (("ORIGEN", config.origen), ("DESTINO", config.destino)):
            path = Path(str(value))
            if not path.exists():
                problems.append(f"{label} no existe o no es accesible: {value}")
        if not problems:
            return True, "Ambas rutas son accesibles."
        return False, "\n".join(problems)


class InstallerService:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or app_dir()
        self.script = self.base_dir / "instalar_tarea.ps1"
        self.result = self.base_dir / "runtime" / "install-result.json"

    def _run_ps(self, args: list[str], timeout: int = 240) -> subprocess.CompletedProcess:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script),
            *args,
        ]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )

    def install(self, config_path: Path, apply_permissions: bool = True, start: bool = True) -> tuple[bool, str]:
        if not self.script.exists():
            return False, f"No se encontro {self.script}"
        args = ["-Action", "Install", "-Config", str(config_path)]
        if apply_permissions:
            args.append("-ApplyPermissions")
        if start:
            args.append("-StartService")
        try:
            self.result.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            result = self._run_ps(args)
        except Exception as exc:
            return False, str(exc)
        detail = self.read_result()
        if detail:
            return bool(detail.get("ok")), str(detail.get("message") or detail)
        output = (result.stdout or result.stderr or "").strip()
        return result.returncode == 0, output or "Instalacion finalizada."

    def repair(self, config_path: Path) -> tuple[bool, str]:
        return self.install(config_path, apply_permissions=False, start=True)

    def uninstall(self) -> tuple[bool, str]:
        if not self.script.exists():
            return False, f"No se encontro {self.script}"
        try:
            result = self._run_ps(["-Action", "Uninstall"])
        except Exception as exc:
            return False, str(exc)
        detail = self.read_result()
        if detail:
            return bool(detail.get("ok")), str(detail.get("message") or detail)
        return result.returncode == 0, (result.stdout or result.stderr or "").strip()

    def read_result(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.result.read_text(encoding="utf-8-sig"))
            return raw if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return None


class LogReader:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or app_dir()

    def tail(self, lines: int = 220, level: str | None = None) -> str:
        path = self.base_dir / "logs" / "sync.log"
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                content = handle.readlines()[-max(20, lines) :]
        except OSError:
            return "Todavia no hay registros."
        if level:
            upper = level.upper()
            content = [line for line in content if f"| {upper}" in line]
        return "".join(content).strip() or "Sin lineas para el filtro seleccionado."
