"""Control de la tarea programada de Windows."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from syncsafe import TASK_NAME


@dataclass(frozen=True)
class TaskState:
    exists: bool
    state: str = "Desconocido"
    last_result: int | None = None
    last_run: str | None = None
    next_run: str | None = None
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.state.lower() == "running"

    @property
    def disabled(self) -> bool:
        return self.state.lower() == "disabled"


def _creation_flags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _escape(value: str) -> str:
    return value.replace("'", "''")


class TaskController:
    def __init__(self, task_name: str = TASK_NAME, runtime_dir: Path | None = None):
        self.task_name = task_name
        self.runtime_dir = runtime_dir
        self.stop_request = (
            runtime_dir / "control" / "stop.request" if runtime_dir is not None else None
        )

    def _run_ps(self, command: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_creation_flags(),
        )

    def get_state(self) -> TaskState:
        if os.name != "nt":
            return TaskState(False, error="Task Scheduler solo esta disponible en Windows.")
        name = _escape(self.task_name)
        command = (
            f"$name='{name}';"
            "$task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue;"
            "if($null -eq $task){@{exists=$false}|ConvertTo-Json -Compress; exit 0};"
            "$info=Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue;"
            "$obj=[ordered]@{exists=$true;state=$task.State.ToString();last_result=$null;last_run=$null;next_run=$null};"
            "if($info){$obj.last_result=[int64]$info.LastTaskResult;$obj.last_run=$info.LastRunTime.ToString('o');$obj.next_run=$info.NextRunTime.ToString('o')};"
            "$obj|ConvertTo-Json -Compress"
        )
        try:
            result = self._run_ps(command)
        except Exception as exc:
            return TaskState(False, error=str(exc))
        if result.returncode != 0:
            return TaskState(False, error=(result.stderr or result.stdout).strip())
        try:
            raw = json.loads(result.stdout.strip() or "{}")
        except ValueError:
            return TaskState(False, error=result.stdout.strip() or "Respuesta invalida")
        return TaskState(
            exists=bool(raw.get("exists")),
            state=str(raw.get("state") or "Desconocido"),
            last_result=raw.get("last_result"),
            last_run=raw.get("last_run"),
            next_run=raw.get("next_run"),
        )

    def _action(self, verb: str) -> tuple[bool, str]:
        if os.name != "nt":
            return False, "Task Scheduler solo esta disponible en Windows."
        name = _escape(self.task_name)
        command = (
            f"$name='{name}';"
            "try{"
            f"Get-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null;"
            f"{verb};"
            "@{ok=$true}|ConvertTo-Json -Compress"
            "}catch{@{ok=$false;error=$_.Exception.Message}|ConvertTo-Json -Compress; exit 1}"
        )
        try:
            result = self._run_ps(command)
        except Exception as exc:
            return False, str(exc)
        try:
            raw = json.loads(result.stdout.strip() or "{}")
        except ValueError:
            raw = {"ok": result.returncode == 0, "error": (result.stderr or result.stdout).strip()}
        return bool(raw.get("ok")), str(raw.get("error") or "")

    def enable(self) -> tuple[bool, str]:
        return self._action("Enable-ScheduledTask -TaskName $name | Out-Null")

    def disable(self) -> tuple[bool, str]:
        return self._action("Disable-ScheduledTask -TaskName $name | Out-Null")

    def start(self) -> tuple[bool, str]:
        return self._action("Start-ScheduledTask -TaskName $name")

    def stop(self) -> tuple[bool, str]:
        return self._action("Stop-ScheduledTask -TaskName $name")

    def request_graceful_stop(self) -> bool:
        if self.stop_request is None:
            return False
        try:
            self.stop_request.parent.mkdir(parents=True, exist_ok=True)
            self.stop_request.write_text(str(time.time()), encoding="utf-8")
            return True
        except OSError:
            return False

    def turn_on(self) -> tuple[bool, str]:
        state = self.get_state()
        if not state.exists:
            return False, "La tarea no esta instalada. Usa la pestana Instalacion."
        ok, error = self.enable()
        if not ok:
            return False, error or "No se pudo habilitar la tarea."
        ok, error = self.start()
        if not ok:
            return False, error or "No se pudo iniciar la tarea."
        return True, "Servicio iniciado."

    def turn_off(self, disable: bool = True) -> tuple[bool, str]:
        state = self.get_state()
        if not state.exists:
            return False, "La tarea no esta instalada."
        requested = self.request_graceful_stop() if state.running else False
        if requested:
            for _ in range(20):
                time.sleep(0.5)
                current = self.get_state()
                if not current.running:
                    break
        current = self.get_state()
        if current.running:
            ok, error = self.stop()
            if not ok:
                return False, error or "No se pudo detener la tarea."
        if disable:
            ok, error = self.disable()
            if not ok:
                return False, error or "Se detuvo, pero no se pudo deshabilitar el autoarranque."
        return True, "Servicio detenido."

    def restart(self) -> tuple[bool, str]:
        ok, error = self.turn_off(disable=False)
        if not ok:
            return False, error
        time.sleep(1)
        return self.turn_on()
