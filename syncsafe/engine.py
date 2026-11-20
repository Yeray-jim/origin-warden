"""Motor de reconciliacion, guardias y recuperacion del servicio."""

from __future__ import annotations

import logging
import ntpath
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from .config import AppConfig
from .domain import (
    Action,
    AppPaths,
    Entry,
    IgnoreMatcher,
    Observation,
    PathMapper,
    Source,
    entries_equal,
    resolve,
)
from .fs import FileOperationError, FileOps
from .security import IdentityManager, SafetyController
from .status import StatusStore, utc_now
from .watchers import WatchManager


class ManagedIndex:
    def __init__(self, path: Path, origin: str, destination: str):
        self.path = path
        self.origin = origin
        self.destination = destination
        self._values: set[str] = set()
        self._lock = threading.RLock()
        self._dirty = False
        self.metadata_ok = True
        self._load()

    def _load(self) -> None:
        try:
            import json
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                isinstance(raw, dict)
                and raw.get("origin") == self.origin
                and raw.get("destination") == self.destination
                and isinstance(raw.get("keys"), list)
            ):
                self._values = {str(item) for item in raw["keys"]}
                self.metadata_ok = True
            else:
                self._values = set()
                self._dirty = True
                self.metadata_ok = False
        except FileNotFoundError:
            self._values = set()
            self.metadata_ok = True
        except (OSError, ValueError):
            self._values = set()
            self.metadata_ok = False

    def key(self, value: PureWindowsPath) -> str:
        return ntpath.normcase(str(value))

    def contains(self, value: PureWindowsPath) -> bool:
        with self._lock:
            return self.key(value) in self._values

    def add(self, value: PureWindowsPath) -> None:
        with self._lock:
            key = self.key(value)
            if key not in self._values:
                self._values.add(key)
                self._dirty = True

    def discard(self, value: PureWindowsPath) -> None:
        with self._lock:
            key = self.key(value)
            if key in self._values:
                self._values.discard(key)
                self._dirty = True

    def save(self) -> None:
        import json
        with self._lock:
            if not self._dirty:
                return
            values = sorted(self._values)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {
            "origin": self.origin,
            "destination": self.destination,
            "keys": values,
        }
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass


class Debouncer:
    def __init__(self, delay: float):
        self.delay = max(0.05, delay)
        self._items: dict[str, float] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._stopped = False

    def put(self, key: str) -> None:
        with self._condition:
            self._items[key] = time.monotonic() + self.delay
            self._condition.notify_all()

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def get_due(self, timeout: float = 1.0) -> list[str]:
        with self._condition:
            while not self._stopped:
                if not self._items:
                    self._condition.wait(timeout)
                    continue
                now = time.monotonic()
                due = [key for key, when in self._items.items() if when <= now]
                if due:
                    for key in due:
                        self._items.pop(key, None)
                    return due
                wait = max(0.02, min(timeout, min(self._items.values()) - now))
                self._condition.wait(wait)
            return []

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()


class DeadLetter:
    def __init__(self):
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def failed(self, key: str, error: str) -> bool:
        with self._lock:
            item = self._items.setdefault(key, {"attempts": 0, "next": 0.0, "error": error})
            item["attempts"] += 1
            item["error"] = error
            delay = min(3600.0, 30.0 * (2 ** min(7, item["attempts"])))
            item["next"] = time.time() + delay
            return item["attempts"] <= 8

    def should_skip(self, key: str) -> bool:
        with self._lock:
            item = self._items.get(key)
            return bool(item and item["next"] > time.time())

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def size(self) -> int:
        with self._lock:
            return len(self._items)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {key: dict(value) for key, value in self._items.items()}


class TrashManager:
    def __init__(self, config: AppConfig, fs: FileOps, logger: logging.Logger):
        self.config = config
        self.fs = fs
        self.logger = logger
        self.root = config.intake_root / config.aportes.papelera
        self._lock = threading.Lock()
        self.files = 0
        self.bytes = 0

    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def _target(self, key: PureWindowsPath, category: str | None) -> PureWindowsPath:
        parts = [self.root]
        if category:
            parts.append(PureWindowsPath(category))
        parts.append(PureWindowsPath(self._stamp()))
        parts.append(key)
        target = parts[0]
        for part in parts[1:]:
            target = target / part
        return target

    def move(self, key: PureWindowsPath, category: str | None = None) -> PureWindowsPath | None:
        source = self.config.destino / key
        target = self._target(key, category)
        with self._lock:
            self.fs.move(source, target)
            self.files += 1
            try:
                if target.exists():
                    self.bytes += sum(
                        item.stat().st_size for item in Path(str(target)).rglob("*") if item.is_file()
                    )
            except OSError:
                pass
        return target

    def preserve_file(self, key: PureWindowsPath, category: str = "_Sobrescritos") -> PureWindowsPath | None:
        source = self.config.destino / key
        entry = self.fs.stat(source)
        if entry is None or not entry.is_file:
            return None
        target = self._target(key, category)
        self.fs.copy_atomic(source, target, expected=entry, progress=None)
        return target

    def cleanup(self) -> None:
        base = Path(str(self.root))
        if not base.exists():
            return
        cutoff = time.time() - max(1, self.config.seguridad.papelera_dias) * 86400
        removed = 0
        for item in sorted(base.glob("**/*"), key=lambda p: len(p.parts), reverse=True):
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink()
                    removed += 1
                elif item.is_dir() and not any(item.iterdir()):
                    item.rmdir()
            except OSError:
                continue
        limit_gb = self.config.seguridad.papelera_max_gb
        if limit_gb > 0:
            files: list[Path] = []
            total = 0
            try:
                for item in base.rglob("*"):
                    if item.is_file():
                        try:
                            total += item.stat().st_size
                            files.append(item)
                        except OSError:
                            continue
                files.sort(key=lambda value: value.stat().st_mtime)
                while files and total > limit_gb * (1024 ** 3):
                    victim = files.pop(0)
                    try:
                        size = victim.stat().st_size
                        victim.unlink()
                        total -= size
                        removed += 1
                    except OSError:
                        continue
            except OSError:
                pass
        if removed:
            self.logger.info("Papelera: %d archivos antiguos eliminados", removed)


@dataclass
class _Runtime:
    copies: int = 0
    ingests: int = 0
    deletes: int = 0
    errors: int = 0


class SyncEngine:
    def __init__(self, config: AppConfig, logger: logging.Logger, *, simulate: bool = False):
        self.config = config
        self.logger = logger
        self.paths = AppPaths(
            origin=config.origen,
            destination=config.destino,
            intake=config.intake_root,
            conflictos=config.intake_root / config.aportes.conflictos,
            papelera=config.intake_root / config.aportes.papelera,
            cuarentena=config.intake_root / config.aportes.cuarentena,
        )
        self.mapper = PathMapper(self.paths)
        self.ignore = IgnoreMatcher(
            config.filtros.ignorar_nombres,
            config.filtros.ignorar_directorios,
        )
        self.fs = FileOps(
            config.reconciliacion.reintentos_max,
            config.reconciliacion.espera_inicial_seg,
            config.reconciliacion.espera_maxima_seg,
        )
        self.identity = IdentityManager(config)
        self.safety = SafetyController(config)
        self.status = StatusStore(config.runtime_dir)
        self.trash = TrashManager(config, self.fs, logger)
        self.dead = DeadLetter()
        self.managed = ManagedIndex(
            config.runtime_dir / "managed.json",
            config.origen_str,
            config.destino_str,
        )
        self.runtime = _Runtime()
        self.debouncer = Debouncer(config.reconciliacion.debounce_seg)
        self.blocks: dict[str, float] = {}
        self._blocks_lock = threading.Lock()
        self._delete_candidates: dict[str, float] = {}
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_lock = threading.Lock()
        self._stop = threading.Event()
        self._watchers: WatchManager | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._threads: list[threading.Thread] = []
        self._last_progress = time.monotonic()
        self._active_ops = 0
        self._active_since = 0.0
        self._scanning = False
        self._supervisor_failures = 0
        self._degraded_reason: str | None = None
        self._simulate_forced = simulate
        self._simulation_count = 0

    def _key_lock(self, key: str) -> threading.Lock:
        with self._key_locks_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _progress(self) -> None:
        self._last_progress = time.monotonic()

    def _mark(self, key: str | PureWindowsPath) -> None:
        try:
            value = PureWindowsPath(str(key))
        except Exception:
            return
        if not value.parts or self.mapper.is_reserved_key(value):
            return
        if self.ignore.ignored_relative(value):
            return
        self.debouncer.put(str(value))

    def mark_event(
        self,
        side: str,
        path: PureWindowsPath,
        moved_from: PureWindowsPath | None = None,
    ) -> None:
        self._progress()
        if side == "origin":
            key = self.mapper.key_from_origin(path)
        else:
            key = self.mapper.key_from_destination(path)
        if key is None:
            return
        self.status.set(last_event=utc_now())
        if side == "destination" and moved_from is not None:
            source_key = self.mapper.key_from_destination(moved_from)
            if source_key is not None and self._is_managed(source_key):
                with self._blocks_lock:
                    self.blocks[str(key)] = time.time() + 300
                self.logger.warning(
                    "Renombrado en destino no autorizado; se bloquea ingesta temporal: %s",
                    key,
                )
        self._mark(key)

    def _is_managed(self, key: PureWindowsPath) -> bool:
        return bool(
            self.fs.stat(self.mapper.origin_path(key))
            or self.fs.stat(self.mapper.intake_path(key))
        )

    def _blocked(self, key: PureWindowsPath) -> bool:
        with self._blocks_lock:
            expiry = self.blocks.get(str(key))
            if expiry is None:
                return False
            if expiry <= time.time():
                self.blocks.pop(str(key), None)
                return False
            return True

    def _simulate_log(self, message: str, key: PureWindowsPath) -> None:
        self._simulation_count += 1
        if self._simulation_count <= 100:
            self.logger.info("SIMULACION %s: %s", message, key)
        elif self._simulation_count == 101:
            self.logger.warning("SIMULACION: se omiten mas lineas de plan en el registro.")

    def _set_mode(self, mode: str, reason: str | None = None) -> None:
        if self.safety.mode != mode:
            self.logger.info("Modo de seguridad: %s (%s)", mode, reason or "cambio")
        self.safety.set_mode(mode)
        self.status.set(mode=mode, armed=self.identity.is_armed())
        if reason:
            self.status.set(mode_reason=reason)
        if mode == "degraded":
            self._degraded_reason = reason
        elif mode in {"normal", "simulation"}:
            self._degraded_reason = None

    def _startup_identity(self) -> None:
        ok, reason = self.identity.verify()
        if ok and not self.managed.path.exists():
            ok = False
            reason = "estado de gestion ausente; vuelve a armar el sistema"
        elif ok and not self.managed.metadata_ok:
            ok = False
            reason = "estado de gestion de otra configuracion; vuelve a armar el sistema"
        if self._simulate_forced or not ok:
            self._set_mode("simulation", reason if not ok else "modo simulado solicitado")
        else:
            self._set_mode("normal", "identidad verificada")

    def _ensure_intake(self) -> None:
        if self.safety.simulation:
            return
        try:
            self.fs.mkdir(self.paths.intake)
            self.fs.mkdir(self.paths.conflictos)
            self.fs.mkdir(self.paths.papelera)
            self.fs.mkdir(self.paths.cuarentena)
        except Exception as exc:
            self.logger.error("No se pudo crear la estructura de aportes: %s", exc)
            self._set_mode("degraded", "no se pudo crear aportes")

    def start(self) -> None:
        self.logger.info("Iniciando servicio")
        self.status.set(
            state="starting",
            origen=self.config.origen_str,
            destino=self.config.destino_str,
            base_dir=str(self.config.base_dir),
            log=str(self.config.log_path),
        )
        self._startup_identity()
        self._ensure_intake()
        try:
            self.fs.cleanup_temp_files(self.config.destino)
        except Exception:
            pass
        self._watchers = WatchManager(
            self.mapper,
            self.ignore,
            self.mark_event,
            self.logger,
            self.config.observacion.modo_origen,
            self.config.observacion.modo_destino,
            self.config.observacion.intervalo_polling_seg,
        )
        try:
            self._watchers.start()
        except Exception as exc:
            self.logger.error("No se pudieron iniciar los observadores: %s", exc)
            self._set_mode("degraded", "observadores no disponibles")
        self._executor = ThreadPoolExecutor(
            max_workers=self.config.reconciliacion.trabajadores,
            thread_name_prefix="sync-worker",
        )
        self._threads = [
            threading.Thread(target=self._dispatch_loop, name="sync-dispatch", daemon=True),
            threading.Thread(target=self._heartbeat_loop, name="sync-heartbeat", daemon=True),
            threading.Thread(target=self._scan_loop, name="sync-scanner", daemon=True),
            threading.Thread(target=self._supervisor_loop, name="sync-supervisor", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self.status.set(state="running")
        self.logger.info("Servicio iniciado en modo %s", self.safety.mode)

    def stop(self) -> None:
        self.logger.info("Deteniendo servicio")
        self._stop.set()
        self.debouncer.stop()
        if self._watchers:
            self._watchers.stop()
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
        for thread in self._threads:
            thread.join(timeout=5)
        if self._watchers:
            self._watchers.stop()
        self.status.set(state="stopped")
        self.status.write()
        self.logger.info("Servicio detenido")

    def _dispatch_loop(self) -> None:
        try:
            while not self._stop.is_set():
                due = self.debouncer.get_due(timeout=1.0)
                for key in due:
                    if self._stop.is_set():
                        break
                    if self._executor:
                        self._executor.submit(self._run_key, key)
                self.status.set(queue=self.debouncer.size())
        except Exception:
            self.logger.exception("Fallo el despachador de eventos")
            self._set_mode("degraded", "despachador detenido")
            if not self._stop.is_set():
                self._threads.append(
                    threading.Thread(
                        target=self._dispatch_loop, name="sync-dispatch-restart", daemon=True
                    )
                )
                self._threads[-1].start()

    def _run_key(self, key_value: str) -> None:
        if self._stop.is_set():
            return
        key = PureWindowsPath(key_value)
        lock = self._key_lock(key_value)
        if not lock.acquire(blocking=False):
            self._mark(key)
            return
        self._active_ops += 1
        self._active_since = self._active_since or time.monotonic()
        try:
            self._reconcile(key, 0)
        except FileOperationError as exc:
            self.runtime.errors += 1
            self.status.set(last_error=str(exc))
            retry = self.dead.failed(key_value, str(exc))
            self.logger.warning("Error en %s: %s", key, exc)
            if retry and exc.transient:
                self._mark(key)
            else:
                self._set_mode("degraded", "error repetido de archivos")
        except Exception as exc:
            self.runtime.errors += 1
            self.status.set(last_error=f"{type(exc).__name__}: {exc}")
            self.logger.exception("Error inesperado en %s", key)
            self.dead.failed(key_value, str(exc))
        finally:
            self._progress()
            self._active_ops = max(0, self._active_ops - 1)
            if self._active_ops == 0:
                self._active_since = 0.0
            lock.release()
            self.status.update_nested(
                "counters",
                copies=self.runtime.copies,
                ingests=self.runtime.ingests,
                deletes=self.runtime.deletes,
                errors=self.runtime.errors,
                dead_letter=self.dead.size(),
            )

    def _observe(self, key: PureWindowsPath) -> Observation:
        return Observation(
            origin=self.fs.stat(self.mapper.origin_path(key)),
            intake=self.fs.stat(self.mapper.intake_path(key)),
            destination=self.fs.stat(self.mapper.destination_path(key)),
            blocked=self._blocked(key),
            managed=self.managed.contains(key),
        )

    def _roots_available(self) -> bool:
        return bool(
            Path(str(self.config.origen)).exists()
            and Path(str(self.config.destino)).exists()
        )

    def _reconcile(self, key: PureWindowsPath, depth: int) -> None:
        if depth > 64:
            self.logger.error("Profundidad excesiva en %s", key)
            return
        if not self._roots_available():
            self._set_mode("degraded", "raiz no disponible")
            return
        if self.dead.should_skip(str(key)):
            return
        if self.mapper.is_reserved_key(key) or self.ignore.ignored_relative(key):
            return
        observation = self._observe(key)
        source = observation.origin or observation.intake
        if source is not None:
            self.managed.add(key)
            self._delete_candidates.pop(str(key), None)
        if source is not None and source.is_dir:
            self._reconcile_directory(key, observation, depth)
            return
        if observation.destination is not None and observation.destination.is_dir:
            if source is None:
                if self._blocked(key):
                    return
                self._prune_directory(key, depth)
            else:
                self.logger.error("Conflicto archivo/directorio en %s", key)
                self.status.set(last_error=f"Conflicto archivo/directorio: {key}")
            return
        plan = resolve(
            observation,
            self.config.reconciliacion.tolerancia_mtime_seg * 1_000_000_000,
        )
        if plan.action == Action.COPY and plan.source is not None:
            self._copy_file(key, plan.source, source)
        elif plan.action == Action.INGEST:
            self._ingest(key, observation.destination)
        elif plan.action == Action.DELETE:
            self._delete_file(key)
        elif plan.action == Action.PRUNE_DIR:
            self._prune_directory(key, depth)
        elif plan.action == Action.CONFLICT:
            self.logger.error("Conflicto de tipo en %s: %s", key, plan.reason)
            self.status.set(last_error=f"Conflicto en {key}: {plan.reason}")
        else:
            self.dead.succeeded(str(key))

    def _reconcile_directory(
        self, key: PureWindowsPath, observation: Observation, depth: int
    ) -> None:
        source = observation.origin or observation.intake
        if source is None:
            self._prune_directory(key, depth)
            return
        source_path = (
            self.mapper.origin_path(key)
            if observation.origin is not None
            else self.mapper.intake_path(key)
        )
        destination = self.mapper.destination_path(key)
        if observation.destination is not None and not observation.destination.is_dir:
            self._quarantine_path(destination, key, "tipo_incompatible")
        if self.safety.simulation:
            self._simulate_log("mkdir/copiar arbol", key)
            return
        if not self.safety.mutable:
            return
        try:
            self.fs.mkdir(destination)
        except Exception as exc:
            self.logger.error("No se pudo crear directorio %s: %s", destination, exc)
            raise
        if observation.destination is None:
            self._mirror_tree(source_path, key, depth)

    def _mirror_tree(self, source_root: PureWindowsPath, key: PureWindowsPath, depth: int) -> None:
        if depth > 64:
            return
        for name, is_dir in self.fs.list_dir(source_root):
            child_key = key / name
            if self.mapper.is_reserved_key(child_key) or self.ignore.ignored_relative(child_key):
                continue
            child_source = source_root / name
            child_dest = self.mapper.destination_path(child_key)
            if is_dir:
                if self.safety.simulation:
                    continue
                self.fs.mkdir(child_dest)
                self._mirror_tree(child_source, child_key, depth + 1)
            else:
                entry = self.fs.stat(child_source)
                if entry:
                    self._copy_file(child_key, Source.ORIGIN if self.mapper.is_intake_absolute(child_source) is False else Source.INTAKE, entry)
        self._mark(key)

    def _prune_directory(self, key: PureWindowsPath, depth: int) -> None:
        destination = self.mapper.destination_path(key)
        entry = self.fs.stat(destination)
        if entry is None:
            return
        if not entry.is_dir:
            self._delete_file(key)
            return
        if self.safety.simulation:
            self._simulate_log("podar directorio", key)
            return
        if not self.safety.mutable:
            return
        for name, is_dir in self.fs.list_dir(destination):
            child_key = key / name
            if self.mapper.is_reserved_key(child_key) or self.ignore.ignored_relative(child_key):
                continue
            if is_dir:
                self._prune_directory(child_key, depth + 1)
            else:
                self._reconcile(child_key, depth + 1)
        if self.fs.stat(destination) is None:
            return
        try:
            if not any(self.fs.list_dir(destination)):
                self.fs.delete_empty_dir(destination)
                self.managed.discard(key)
        except OSError:
            pass

    def _copy_file(self, key: PureWindowsPath, source: Source, entry: Entry | None) -> None:
        if entry is None:
            return
        if self.safety.simulation:
            self._simulate_log("copia", key)
            return
        if not self.safety.mutable:
            return
        destination = self.mapper.destination_path(key)
        if self.config.seguridad.conservar_version_destino and self.fs.exists(destination):
            try:
                self.trash.preserve_file(key)
            except Exception as exc:
                self.logger.warning("No se pudo preservar version anterior de %s: %s", key, exc)
        source_path = (
            self.mapper.origin_path(key)
            if source == Source.ORIGIN
            else self.mapper.intake_path(key)
        )
        self.fs.copy_atomic(source_path, destination, expected=entry, progress=self._progress)
        self.managed.add(key)
        self.runtime.copies += 1
        self.dead.succeeded(str(key))
        self.logger.info("COPIADO %s -> %s", source_path, destination)

    def _ingest(self, key: PureWindowsPath, entry: Entry | None) -> None:
        if entry is None:
            return
        if self.safety.simulation:
            self._simulate_log("ingesta", key)
            return
        if not self.safety.can_ingest:
            self.logger.warning("Ingesta pausada por guardia: %s", key)
            return
        size_mb = entry.size / (1024 * 1024)
        if self.config.seguridad.max_aporte_mb and size_mb > self.config.seguridad.max_aporte_mb:
            self.logger.error("Aporte demasiado grande, omitido: %s (%.1f MB)", key, size_mb)
            self.status.set(last_error=f"Aporte demasiado grande: {key}")
            return
        if self.fs.disk_free_gb(self.config.origen) < 1.0:
            self.logger.error("Espacio insuficiente para ingesta: %s", key)
            self._set_mode("degraded", "espacio insuficiente")
            return
        target = self.mapper.intake_path(key)
        if self.fs.exists(target):
            current = self.fs.stat(target)
            if current and entries_equal(
                current, entry, self.config.reconciliacion.tolerancia_mtime_seg * 1_000_000_000
            ):
                return
            target = (
                self.paths.conflictos
                / PureWindowsPath(datetime.now().strftime("%Y%m%d_%H%M%S"))
                / key
            )
        self.fs.copy_atomic(
            self.mapper.destination_path(key),
            target,
            expected=entry,
            progress=self._progress,
        )
        self.managed.add(key)
        self.runtime.ingests += 1
        self.safety.record_ingest(str(key), entry.size)
        self.dead.succeeded(str(key))
        self.logger.info("INGESTADO %s -> %s", key, target)
        self._mark(key)

    def _delete_file(self, key: PureWindowsPath) -> None:
        allowed, reason = self.safety.allow_delete()
        if not allowed:
            if reason == "modo simulation":
                self._simulate_log("borrado", key)
            elif reason != "ok":
                self.logger.info("Borrado diferido en %s: %s", key, reason)
            return
        if self._is_managed(key):
            self._mark(key)
            return
        now = time.monotonic()
        first = self._delete_candidates.get(str(key))
        if first is None:
            self._delete_candidates[str(key)] = now
            self._mark(key)
            return
        if now - first < self.config.reconciliacion.gracia_borrado_seg:
            self._mark(key)
            return
        destination = self.mapper.destination_path(key)
        if self.fs.stat(destination) is None:
            self._delete_candidates.pop(str(key), None)
            return
        try:
            if self.config.seguridad.papelera_activa:
                moved = self.trash.move(key)
                self.logger.info("MOVIDO A PAPELERA %s -> %s", key, moved)
            else:
                self.fs.delete_file(destination)
                self.logger.info("BORRADO %s", key)
        except Exception as exc:
            self.logger.error("No se pudo borrar %s de forma segura: %s", key, exc)
            self._set_mode("degraded", "papelera o borrado no disponible")
            return
        self.managed.discard(key)
        self.runtime.deletes += 1
        self.safety.record_delete(str(key))
        self._delete_candidates.pop(str(key), None)
        self.dead.succeeded(str(key))
        self.status.update_nested("trash", files=self.trash.files, bytes=self.trash.bytes)
        if key.parent != PureWindowsPath("."):
            self._mark(key.parent)

    def _quarantine_path(self, path: PureWindowsPath, key: PureWindowsPath, category: str) -> None:
        if self.safety.simulation or not self.safety.mutable:
            return
        if not self.fs.exists(path):
            return
        try:
            self.trash.move(key, category)
        except Exception as exc:
            self.logger.error("No se pudo mover %s a cuarentena: %s", path, exc)

    def _scan_walk(self, root: PureWindowsPath, mode: str) -> int:
        base = Path(str(root))
        if not base.exists():
            return 0
        count = 0
        for current, dirs, files in os.walk(base):
            dirs[:] = [
                name
                for name in dirs
                if not self.ignore.ignored_directory(name)
                and not (mode == "intake" and name in {
                    self.paths.conflictos.name,
                    self.paths.papelera.name,
                    self.paths.cuarentena.name,
                })
            ]
            current_path = PureWindowsPath(current)
            if mode == "origin":
                if self.mapper.is_intake_absolute(current_path) or self.mapper.is_intake_absolute(
                    current_path / "_"
                ):
                    dirs[:] = []
                    continue
                key = self.mapper.key_from_origin(current_path)
            elif mode == "intake":
                if self.mapper.is_reserved_absolute(current_path):
                    dirs[:] = []
                    continue
                key = self.mapper.key_from_origin(current_path)
            else:
                key = self.mapper.key_from_destination(current_path)
            if key is not None:
                if mode in {"origin", "intake"}:
                    self.managed.add(key)
                self._mark(key)
                count += 1
            self._progress()
            for name in files:
                child = current_path / name
                if self.ignore.ignored_name(name):
                    continue
                if mode == "origin":
                    child_key = self.mapper.key_from_origin(child)
                elif mode == "intake":
                    child_key = self.mapper.key_from_origin(child)
                else:
                    child_key = self.mapper.key_from_destination(child)
                if child_key is not None:
                    if mode in {"origin", "intake"}:
                        self.managed.add(child_key)
                    self._mark(child_key)
                    count += 1
        return count

    def scan(self) -> None:
        self._progress()
        self._scanning = True
        self.logger.info("Iniciando barrido de reconciliacion")
        started = time.monotonic()
        try:
            origin_count = self._scan_walk(self.config.origen, "origin")
            intake_count = self._scan_walk(self.config.intake_root, "intake")
            destination_count = self._scan_walk(self.config.destino, "destination")
            self.safety.total_managed = origin_count + intake_count
            self.managed.save()
            duration = time.monotonic() - started
            self.status.update_nested(
                "scan",
                last_at=utc_now(),
                duration_seconds=round(duration, 2),
                origin_entries=origin_count,
                intake_entries=intake_count,
                destination_entries=destination_count,
            )
            self.logger.info(
                "Barrido completado en %.2fs: origen=%d aportes=%d destino=%d",
                duration,
                origin_count,
                intake_count,
                destination_count,
            )
        except Exception as exc:
            self.runtime.errors += 1
            self.logger.exception("Fallo el barrido de reconciliacion")
            self.status.set(last_error=f"Barrido: {exc}")
            self._set_mode("degraded", "barrido fallido")
        finally:
            self._scanning = False
            self._progress()

    def _scan_loop(self) -> None:
        try:
            if self.config.reconciliacion.inicial:
                self.scan()
            interval = max(60, self.config.reconciliacion.intervalo_min * 60)
            while not self._stop.wait(interval):
                self.scan()
                self.trash.cleanup()
        except Exception:
            self.logger.exception("Fallo el hilo de escaneo")
            if not self._stop.is_set():
                self._threads.append(
                    threading.Thread(target=self._scan_loop, name="sync-scanner-restart", daemon=True)
                )
                self._threads[-1].start()

    def _heartbeat_loop(self) -> None:
        try:
            while not self._stop.wait(max(1, self.config.seguridad.heartbeat_seg)):
                self.safety._check_approval()
                self.status.set(
                    mode=self.safety.mode,
                    armed=self.identity.is_armed(),
                    queue=self.debouncer.size(),
                    pending_approval=self.safety.status().get("request"),
                    guards={
                        "delete": self.safety.status().get("pause_delete"),
                        "ingest": self.safety.status().get("pause_ingest"),
                    },
                    trash={"files": self.trash.files, "bytes": self.trash.bytes},
                    dead_letter=self.dead.size(),
                    active_operations=self._active_ops,
                )
                self.status.write()
                self.managed.save()
                self._check_watchdog()
        except Exception:
            self.logger.exception("Fallo el heartbeat")
            if not self._stop.is_set():
                self._threads.append(
                    threading.Thread(target=self._heartbeat_loop, name="sync-heartbeat-restart", daemon=True)
                )
                self._threads[-1].start()

    def _check_watchdog(self) -> None:
        if self._active_ops <= 0 and not self._scanning:
            return
        limit = max(60, self.config.seguridad.watchdog_sin_progreso_min * 60)
        if time.monotonic() - self._last_progress > limit:
            message = "Watchdog: operacion sin progreso; reinicio controlado solicitado."
            self.logger.critical(message)
            self.status.set(state="watchdog", last_error=message)
            self.status.write()
            os._exit(2)

    def _supervisor_loop(self) -> None:
        try:
            while not self._stop.wait(15):
                roots_ok = self.fs.exists(self.config.origen) and self.fs.exists(self.config.destino)
                identity_ok, identity_reason = self.identity.verify()
                if not identity_ok and self.safety.mode in {"normal", "degraded"}:
                    self._set_mode("simulation", identity_reason)
                if (
                    identity_ok
                    and not self._simulate_forced
                    and self.managed.path.exists()
                    and self.safety.mode == "simulation"
                ):
                    self._set_mode("normal", "identidad recuperada")
                    self._ensure_intake()
                if not roots_ok:
                    self._supervisor_failures += 1
                    self._set_mode("degraded", "raiz no disponible")
                    continue
                if self._watchers and not self._watchers.healthy():
                    self._supervisor_failures += 1
                    self.logger.warning("Observadores no saludables; reiniciando")
                    try:
                        self._watchers.restart()
                        self._supervisor_failures = 0
                        self._set_mode("normal" if self.identity.is_armed() else "simulation", "observadores recuperados")
                    except Exception as exc:
                        self.logger.error("No se pudieron reiniciar observadores: %s", exc)
                        self._set_mode("degraded", "observadores caidos")
                else:
                    self._supervisor_failures = 0
                    if self.safety.mode == "degraded" and self._degraded_reason in {
                        "raiz no disponible",
                        "observadores caidos",
                        "observadores no disponibles",
                    }:
                        self._set_mode(
                            "normal" if self.identity.is_armed() else "simulation",
                            "recuperacion automatica",
                        )
        except Exception:
            self.logger.exception("Fallo el supervisor")
            if not self._stop.is_set():
                self._threads.append(
                    threading.Thread(target=self._supervisor_loop, name="sync-supervisor-restart", daemon=True)
                )
                self._threads[-1].start()
