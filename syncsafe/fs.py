"""Operaciones de archivos con reintentos, atomicidad y proteccion de metadatos."""

from __future__ import annotations

import errno
import os
import random
import shutil
import stat as stat_module
import threading
import time
import uuid
from pathlib import Path, PureWindowsPath
from collections.abc import Callable

from .domain import Entry, EntryKind


class FileOperationError(RuntimeError):
    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


def as_path(value: PureWindowsPath) -> Path:
    return Path(str(value))


def _error_is_transient(exc: OSError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror in {3, 5, 32, 33, 53, 59, 64, 67, 1231}:
        return True
    if exc.errno in {
        errno.EAGAIN,
        errno.EBUSY,
        errno.ENODEV,
        errno.ENOENT,
        errno.ENOSPC,
        errno.ETIMEDOUT,
    }:
        return True
    return isinstance(exc, (PermissionError, TimeoutError))


class FileOps:
    def __init__(
        self,
        reintentos: int = 8,
        espera_inicial: float = 0.5,
        espera_maxima: float = 10.0,
    ):
        self.reintentos = max(1, reintentos)
        self.espera_inicial = max(0.05, espera_inicial)
        self.espera_maxima = max(self.espera_inicial, espera_maxima)

    def _wait(self, attempt: int) -> None:
        delay = min(self.espera_maxima, self.espera_inicial * (2 ** attempt))
        time.sleep(delay + random.uniform(0, 0.15))

    def stat(self, path: PureWindowsPath) -> Entry | None:
        target = as_path(path)
        try:
            if target.is_symlink():
                return None
            st = target.stat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise FileOperationError(f"No se puede leer {target}: {exc}", transient=_error_is_transient(exc)) from exc
        if stat_module.S_ISDIR(st.st_mode):
            return Entry(EntryKind.DIRECTORY, 0, st.st_mtime_ns)
        if stat_module.S_ISREG(st.st_mode):
            return Entry(EntryKind.FILE, st.st_size, st.st_mtime_ns)
        return Entry(EntryKind.OTHER, st.st_size, st.st_mtime_ns)

    def exists(self, path: PureWindowsPath) -> bool:
        return self.stat(path) is not None

    def is_dir(self, path: PureWindowsPath) -> bool:
        entry = self.stat(path)
        return bool(entry and entry.is_dir)

    def mkdir(self, path: PureWindowsPath) -> None:
        as_path(path).mkdir(parents=True, exist_ok=True)

    def ensure_parent(self, path: PureWindowsPath) -> None:
        as_path(path).parent.mkdir(parents=True, exist_ok=True)

    def list_dir(self, path: PureWindowsPath) -> list[tuple[str, bool]]:
        target = as_path(path)
        result: list[tuple[str, bool]] = []
        try:
            with os.scandir(target) as iterator:
                for item in iterator:
                    try:
                        if item.is_symlink():
                            continue
                        result.append((item.name, item.is_dir(follow_symlinks=False)))
                    except OSError:
                        continue
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise FileOperationError(f"No se puede listar {target}: {exc}", transient=_error_is_transient(exc)) from exc
        return result

    def _clear_readonly(self, path: Path) -> None:
        try:
            mode = path.stat().st_mode
            if not mode & stat_module.S_IWRITE:
                os.chmod(path, mode | stat_module.S_IWRITE)
        except OSError:
            pass

    def copy_atomic(
        self,
        source: PureWindowsPath,
        destination: PureWindowsPath,
        *,
        expected: Entry | None = None,
        progress: Callable[[], None] | None = None,
    ) -> None:
        src = as_path(source)
        dst = as_path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        temp = dst.with_name(f".sync-{uuid.uuid4().hex}.tmp")
        last_error: Exception | None = None
        monitor_stop = threading.Event()
        monitor_thread: threading.Thread | None = None
        if progress is not None:
            def monitor() -> None:
                last_size = -1
                while not monitor_stop.wait(2.0):
                    try:
                        size = temp.stat().st_size
                    except OSError:
                        continue
                    if size != last_size:
                        last_size = size
                        progress()
            monitor_thread = threading.Thread(target=monitor, daemon=True)
            monitor_thread.start()

        for attempt in range(self.reintentos):
            try:
                before = src.stat()
                shutil.copy2(src, temp)
                after = src.stat()
                copied = temp.stat()
                if copied.st_size != after.st_size:
                    raise FileOperationError("La copia quedo incompleta.", transient=True)
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                    raise FileOperationError("La fuente cambio durante la copia.", transient=True)
                if dst.exists():
                    self._clear_readonly(dst)
                os.replace(temp, dst)
                if progress:
                    progress()
                monitor_stop.set()
                if monitor_thread is not None:
                    monitor_thread.join(timeout=3)
                return
            except FileNotFoundError as exc:
                last_error = exc
                break
            except OSError as exc:
                last_error = exc
                try:
                    if temp.exists():
                        temp.unlink()
                except OSError:
                    pass
                if not _error_is_transient(exc) or attempt == self.reintentos - 1:
                    break
                self._wait(attempt)
            except FileOperationError as exc:
                last_error = exc
                try:
                    if temp.exists():
                        temp.unlink()
                except OSError:
                    pass
                if not exc.transient or attempt == self.reintentos - 1:
                    break
                self._wait(attempt)

        monitor_stop.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=3)
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        raise FileOperationError(
            f"No se pudo copiar {source} a {destination}: {last_error}",
            transient=bool(last_error and _error_is_transient(last_error) if isinstance(last_error, OSError) else False),
        )

    def move(self, source: PureWindowsPath, destination: PureWindowsPath) -> None:
        src = as_path(source)
        dst = as_path(destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if dst.is_dir() and not any(dst.iterdir()):
                dst.rmdir()
            else:
                dst = dst.with_name(dst.name + "." + uuid.uuid4().hex[:8])
        last_error: Exception | None = None
        for attempt in range(self.reintentos):
            try:
                shutil.move(str(src), str(dst))
                return
            except OSError as exc:
                last_error = exc
                if not _error_is_transient(exc) or attempt == self.reintentos - 1:
                    break
                self._wait(attempt)
        raise FileOperationError(f"No se pudo mover {source} a {destination}: {last_error}")

    def delete_file(self, path: PureWindowsPath) -> None:
        target = as_path(path)
        last_error: Exception | None = None
        for attempt in range(self.reintentos):
            try:
                self._clear_readonly(target)
                target.unlink()
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc
                if not _error_is_transient(exc) or attempt == self.reintentos - 1:
                    break
                self._wait(attempt)
        raise FileOperationError(f"No se pudo borrar {path}: {last_error}")

    def delete_empty_dir(self, path: PureWindowsPath) -> None:
        target = as_path(path)
        try:
            target.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise FileOperationError(f"No se pudo quitar el directorio {path}: {exc}") from exc

    def disk_free_gb(self, path: PureWindowsPath) -> float:
        target = as_path(path)
        probe = target
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        try:
            return shutil.disk_usage(probe).free / (1024 ** 3)
        except OSError:
            return 0.0

    def cleanup_temp_files(self, root: PureWindowsPath) -> int:
        base = as_path(root)
        removed = 0
        if not base.exists():
            return 0
        now = time.time()
        try:
            for item in base.rglob(".sync-*.tmp"):
                try:
                    if item.is_file() and now - item.stat().st_mtime > 3600:
                        item.unlink()
                        removed += 1
                except OSError:
                    continue
        except OSError:
            pass
        return removed
