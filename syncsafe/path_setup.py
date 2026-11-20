"""Analisis y preparacion segura de la raiz, el origen y la copia."""

from __future__ import annotations

import json
import ntpath
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any


SYSTEM_NAMES = {
    "$recycle.bin",
    "system volume information",
    "desktop.ini",
    "thumbs.db",
    "pagefile.sys",
    "hiberfil.sys",
    "swapfile.sys",
}


class PathSetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class RootAnalysis:
    root: str
    exists: bool
    is_dir: bool
    writable: bool
    empty: bool
    entries: int
    files: int
    directories: int
    total_bytes: int
    top_level: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _win(value: PureWindowsPath) -> Path:
    return Path(str(value))


def _norm(value: PureWindowsPath) -> str:
    return ntpath.normcase(str(value))


def _inside(child: PureWindowsPath, parent: PureWindowsPath) -> bool:
    c = _norm(child)
    p = _norm(parent)
    if c == p:
        return True
    try:
        return ntpath.commonpath([c, p]) == p
    except ValueError:
        return False


def _ignored(name: str) -> bool:
    lower = name.lower()
    return (
        lower in SYSTEM_NAMES
        or lower.startswith(".syncsafe.")
        or (lower.startswith(".sync-") and lower.endswith(".tmp"))
    )


def _is_empty_dir(path: Path) -> bool:
    try:
        return not any(path.iterdir())
    except OSError:
        return False


class PathSetupService:
    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir
        self.journal_path = runtime_dir / "path-setup.json"

    def validate_hierarchy(
        self,
        root: PureWindowsPath,
        origin: PureWindowsPath,
        destination: PureWindowsPath,
        *,
        require_existing: bool = False,
    ) -> None:
        if _norm(root) == _norm(origin) or _norm(root) == _norm(destination):
            raise PathSetupError("ORIGEN y DESTINO deben ser subcarpetas de RAIZ.")
        if not _inside(origin, root) or not _inside(destination, root):
            raise PathSetupError("ORIGEN y DESTINO deben estar dentro de RAIZ.")
        if _inside(destination, origin) or _inside(origin, destination):
            raise PathSetupError("ORIGEN y DESTINO no pueden estar anidados entre si.")
        if _norm(origin) == _norm(destination):
            raise PathSetupError("ORIGEN y DESTINO no pueden ser la misma ruta.")
        root_path = _win(root)
        if not root_path.exists() or not root_path.is_dir():
            raise PathSetupError(f"RAIZ no existe o no es una carpeta: {root}")
        if require_existing:
            for label, value in (("ORIGEN", origin), ("DESTINO", destination)):
                target = _win(value)
                if not target.exists() or not target.is_dir():
                    raise PathSetupError(f"{label} no existe o no es una carpeta: {value}")

    def analyze(self, root: PureWindowsPath) -> RootAnalysis:
        root_path = _win(root)
        warnings: list[str] = []
        if not root_path.exists():
            return RootAnalysis(
                root=str(root),
                exists=False,
                is_dir=False,
                writable=False,
                empty=False,
                entries=0,
                files=0,
                directories=0,
                total_bytes=0,
                error="La raiz no existe o no es accesible.",
            )
        if not root_path.is_dir():
            return RootAnalysis(
                root=str(root),
                exists=True,
                is_dir=False,
                writable=False,
                empty=False,
                entries=0,
                files=0,
                directories=0,
                total_bytes=0,
                error="La raiz no es una carpeta.",
            )
        writable = True
        try:
            probe = root_path / ".syncsafe-write-test.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            writable = False
            warnings.append("No se pudo escribir en la raiz.")

        entries = 0
        files = 0
        directories = 0
        total_bytes = 0
        top_level: list[str] = []
        try:
            for item in root_path.iterdir():
                if _ignored(item.name):
                    continue
                entries += 1
                top_level.append(item.name)
                try:
                    if item.is_symlink():
                        warnings.append(f"Enlace o junction omitido: {item.name}")
                        continue
                    if item.is_dir():
                        directories += 1
                        for child in item.rglob("*"):
                            if child.is_file():
                                files += 1
                                total_bytes += child.stat().st_size
                    elif item.is_file():
                        files += 1
                        total_bytes += item.stat().st_size
                except OSError:
                    warnings.append(f"No se pudo leer completamente: {item.name}")
        except OSError as exc:
            return RootAnalysis(
                root=str(root),
                exists=True,
                is_dir=True,
                writable=writable,
                empty=False,
                entries=entries,
                files=files,
                directories=directories,
                total_bytes=total_bytes,
                top_level=top_level,
                warnings=warnings,
                error=str(exc),
            )
        return RootAnalysis(
            root=str(root),
            exists=True,
            is_dir=True,
            writable=writable,
            empty=entries == 0,
            entries=entries,
            files=files,
            directories=directories,
            total_bytes=total_bytes,
            top_level=sorted(top_level),
            warnings=warnings,
        )

    def create_structure(
        self,
        root: PureWindowsPath,
        origin: PureWindowsPath,
        destination: PureWindowsPath,
    ) -> None:
        self.validate_hierarchy(root, origin, destination)
        _win(origin).mkdir(parents=True, exist_ok=True)
        _win(destination).mkdir(parents=True, exist_ok=True)
        self._write_journal(
            {
                "mode": "create",
                "root": str(root),
                "origin": str(origin),
                "destination": str(destination),
                "status": "ok",
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )

    def adopt_existing(
        self,
        root: PureWindowsPath,
        origin: PureWindowsPath,
        destination: PureWindowsPath,
    ) -> None:
        self.validate_hierarchy(root, origin, destination, require_existing=True)
        self._write_journal(
            {
                "mode": "adopt",
                "root": str(root),
                "origin": str(origin),
                "destination": str(destination),
                "status": "ok",
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )

    def engulf(
        self,
        root: PureWindowsPath,
        origin: PureWindowsPath,
        destination: PureWindowsPath,
        *,
        service_installed: bool = False,
    ) -> list[str]:
        if service_installed:
            raise PathSetupError("Desinstala el servicio antes de mover contenido de la raiz.")
        self.validate_hierarchy(root, origin, destination)
        if _norm(PureWindowsPath(origin).parent) != _norm(root):
            raise PathSetupError("En el modo englobar, ORIGEN debe ser un hijo directo de RAIZ.")
        if _norm(PureWindowsPath(destination).parent) != _norm(root):
            raise PathSetupError("En el modo englobar, DESTINO debe ser un hijo directo de RAIZ.")
        origin_path = _win(origin)
        destination_path = _win(destination)
        if origin_path.exists() and not _is_empty_dir(origin_path):
            raise PathSetupError("La carpeta ORIGEN debe estar vacia para englobar la raiz.")
        origin_path.mkdir(parents=True, exist_ok=True)
        root_path = _win(root)
        exclude = {_norm(origin), _norm(destination)}
        moved: list[str] = []
        journal: dict[str, Any] = {
            "mode": "engulf",
            "root": str(root),
            "origin": str(origin),
            "destination": str(destination),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "moved": [],
            "status": "running",
        }
        self._write_journal(journal)
        try:
            for item in list(root_path.iterdir()):
                if _ignored(item.name) or item.is_symlink():
                    continue
                if _norm(PureWindowsPath(str(item))) in exclude:
                    continue
                target = origin_path / item.name
                if target.exists():
                    raise PathSetupError(f"Conflicto: ya existe {target}.")
                self._move_with_retry(item, target)
                moved.append(item.name)
                journal["moved"] = moved
                self._write_journal(journal)
            destination_path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            rollback_errors: list[str] = []
            for name in reversed(moved):
                source = origin_path / name
                target = root_path / name
                try:
                    if source.exists() and not target.exists():
                        self._move_with_retry(source, target)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{name}: {rollback_exc}")
            journal.update(
                {
                    "status": "rolled_back" if not rollback_errors else "rollback_incomplete",
                    "error": str(exc),
                    "rollback_errors": rollback_errors,
                    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            self._write_journal(journal)
            if rollback_errors:
                raise PathSetupError(
                    f"Fallo el movimiento y el rollback quedo incompleto: {exc}"
                ) from exc
            raise
        journal.update(
            {
                "status": "ok",
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "moved_count": len(moved),
            }
        )
        self._write_journal(journal)
        return moved

    def _move_with_retry(self, source: Path, target: Path) -> None:
        last: Exception | None = None
        for attempt in range(4):
            try:
                os.rename(source, target)
                return
            except OSError as exc:
                last = exc
                time.sleep(0.5 * (attempt + 1))
        raise PathSetupError(f"No se pudo mover {source} a {target}: {last}") from last

    def _write_journal(self, data: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.journal_path.with_name(self.journal_path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.journal_path)
        try:
            log_path = self.runtime_dir.parent / "logs" / "path-setup.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        except OSError:
            pass
