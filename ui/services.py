"""Servicios compartidos por el panel."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from syncsafe import __version__
from syncsafe.config import AppConfig, default_raw_config, load_config, save_config_atomic
from syncsafe.path_setup import PathSetupService, RootAnalysis
from syncsafe.security import IdentityManager, SafetyController
from ui.release_client import default_install_root
from ui.task_control import TaskController, TaskState


def ensure_panel_copy() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    current = Path(sys.executable).resolve()
    target = default_install_root() / "OriginWardenPanel.exe"
    if current == target or target.exists():
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(current, target)
        return target
    except OSError:
        return None


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        candidates = [base, base.parent, default_install_root()]
    else:
        base = Path(__file__).resolve().parent.parent
        candidates = [base]
    for candidate in candidates:
        if (candidate / "config.json").exists():
            return candidate
    return default_install_root() if getattr(sys, "frozen", False) else base


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
        raw["RAIZ"] = values.get("raiz", raw.get("RAIZ", ""))
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
            str(validated.raiz).lower() != str(config.raiz).lower()
            or str(validated.origen).lower() != str(config.origen).lower()
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

    def analyze_root(self, root: str) -> RootAnalysis:
        return PathSetupService(self.base_dir / "runtime").analyze(PureWindowsPath(root))

    def apply_paths(
        self,
        root: str,
        origin: str,
        destination: str,
        mode: str,
        *,
        service_installed: bool = False,
    ) -> AppConfig:
        root_path = PureWindowsPath(root)
        origin_path = PureWindowsPath(origin)
        destination_path = PureWindowsPath(destination)
        if service_installed:
            raise ValueError("Desinstala el servicio antes de cambiar la configuracion de rutas.")
        try:
            current = self.load()
            raw = current.to_json_dict()
        except Exception:
            current = None
            raw = default_raw_config()
        raw["RAIZ"] = root
        raw["ORIGEN"] = origin
        raw["DESTINO"] = destination
        temp = self.path.with_name("config.paths.validate.json")
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
        validated = replace(validated, base_dir=self.base_dir)
        setup = PathSetupService(self.base_dir / "runtime")
        if mode == "create":
            setup.create_structure(root_path, origin_path, destination_path)
        elif mode == "adopt":
            setup.adopt_existing(root_path, origin_path, destination_path)
        elif mode == "engulf":
            setup.engulf(
                root_path,
                origin_path,
                destination_path,
                service_installed=service_installed,
            )
        else:
            raise ValueError(f"Modo de rutas desconocido: {mode}")
        paths_changed = (
            current is None
            or str(validated.raiz).lower() != str(current.raiz).lower()
            or str(validated.origen).lower() != str(current.origen).lower()
            or str(validated.destino).lower() != str(current.destino).lower()
        )
        save_config_atomic(validated, self.path)
        if paths_changed:
            if current is not None:
                IdentityManager(current).disarm()
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
        for label, value in (("RAIZ", config.raiz), ("ORIGEN", config.origen), ("DESTINO", config.destino)):
            path = Path(str(value))
            if not path.exists():
                problems.append(f"{label} no existe o no es accesible: {value}")
        if not problems:
            return True, "La raiz, el origen y la copia son accesibles."
        return False, "\n".join(problems)



@dataclass(frozen=True)
class InstallAudit:
    status: str
    installed: bool
    message: str
    task: TaskState
    receipt: dict[str, Any] | None = None


def _same_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return str(left).replace("/", "\\").lower() == str(right).replace("/", "\\").lower()


class InstallationAuditor:
    def __init__(self, base_dir: Path, task_controller: TaskController):
        self.base_dir = base_dir
        self.task_controller = task_controller
        self.receipt_path = base_dir / "runtime" / "installation.json"

    def receipt(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.receipt_path.read_text(encoding="utf-8-sig"))
            return raw if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return None

    def audit(self) -> InstallAudit:
        task = self.task_controller.get_state()
        receipt = self.receipt()
        if task.error and not task.exists:
            return InstallAudit("verification_failed", False, task.error, task, receipt)
        installed_receipt = bool(receipt and receipt.get("installed"))
        if not task.exists and not installed_receipt:
            return InstallAudit("not_installed", False, "Origin Warden no esta instalado.", task, receipt)
        if not task.exists and installed_receipt:
            return InstallAudit(
                "installed_incomplete",
                True,
                "Hay un recibo de instalacion pero la tarea programada no existe. Repara la instalacion.",
                task,
                receipt,
            )
        if task.exists and not receipt:
            return InstallAudit(
                "installed_legacy",
                True,
                "Existe una tarea de Origin Warden sin recibo de auditoria. Usa Adoptar/Reparar.",
                task,
                receipt,
            )
        expected = receipt.get("service_executable") if receipt else None
        package_dir = receipt.get("package_dir") if receipt else None
        action = task.action_execute or ""
        arguments = task.action_arguments or ""
        action_ok = False
        if action:
            if str(action).lower().endswith("originwardenservice.exe"):
                action_ok = _same_path(action, expected) or _same_path(str(Path(action).parent), str(self.base_dir))
            elif "pythonw.exe" in str(action).lower():
                action_ok = "sincronizador.pyw" in arguments.lower()
        if not action_ok and action:
            return InstallAudit(
                "installed_foreign",
                True,
                "Existe una tarea con el mismo nombre pero apunta a otro ejecutable o carpeta.",
                task,
                receipt,
            )
        if expected and not Path(str(expected)).exists():
            return InstallAudit(
                "installed_incomplete",
                True,
                f"No existe el ejecutable instalado: {expected}",
                task,
                receipt,
            )
        if package_dir and not _same_path(str(package_dir), str(self.base_dir)):
            return InstallAudit(
                "installed_foreign",
                True,
                f"La instalacion pertenece a otra carpeta: {package_dir}",
                task,
                receipt,
            )
        return InstallAudit(
            "installed_ok",
            True,
            "Origin Warden esta instalado y la auditoria es correcta.",
            task,
            receipt,
        )


class InstallerService:
    def __init__(
        self,
        base_dir: Path | None = None,
        task_controller: TaskController | None = None,
    ):
        self.base_dir = base_dir or app_dir()
        self.script = self.base_dir / "instalar_tarea.ps1"
        self.result = self.base_dir / "runtime" / "install-result.json"
        self.task_controller = task_controller or TaskController(runtime_dir=self.base_dir / "runtime")
        self.auditor = InstallationAuditor(self.base_dir, self.task_controller)

    def audit(self) -> InstallAudit:
        return self.auditor.audit()

    def receipt(self) -> dict[str, Any] | None:
        return self.auditor.receipt()

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

    def install(
        self,
        config_path: Path,
        apply_permissions: bool = True,
        start: bool = True,
        service_exe: Path | None = None,
        version: str | None = None,
    ) -> tuple[bool, str]:
        if not self.script.exists():
            return False, f"No se encontro {self.script}"
        audit = self.audit()
        if audit.installed:
            return False, f"Ya esta instalado. {audit.message}"
        args = ["-Action", "Install", "-Config", str(config_path), "-Version", version or __version__]
        if service_exe is not None:
            args.extend(["-ServiceExe", str(service_exe)])
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

    def repair(
        self,
        config_path: Path,
        service_exe: Path | None = None,
        version: str | None = None,
    ) -> tuple[bool, str]:
        if not self.script.exists():
            return False, f"No se encontro {self.script}"
        args = ["-Action", "Repair", "-Config", str(config_path), "-Version", version or __version__]
        if service_exe is not None:
            args.extend(["-ServiceExe", str(service_exe)])
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
        return result.returncode == 0, (result.stdout or result.stderr or "").strip()

    def update(
        self,
        config_path: Path,
        service_exe: Path,
        version: str | None = None,
    ) -> tuple[bool, str]:
        if not self.script.exists():
            return False, f"No se encontro {self.script}"
        args = [
            "-Action",
            "Update",
            "-Config",
            str(config_path),
            "-ServiceExe",
            str(service_exe),
            "-Version",
            version or __version__,
            "-StartService",
        ]
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
        return result.returncode == 0, (result.stdout or result.stderr or "").strip()

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
