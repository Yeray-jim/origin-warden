"""Observadores nativos y de polling para origen y destino."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from pathlib import PureWindowsPath
from collections.abc import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from .domain import IgnoreMatcher, PathMapper
from .pathkind import observer_mode

EventCallback = Callable[[str, PureWindowsPath, PureWindowsPath | None], None]


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        side: str,
        mapper: PathMapper,
        ignore: IgnoreMatcher,
        callback: EventCallback,
        logger: logging.Logger,
    ):
        self.side = side
        self.mapper = mapper
        self.ignore = ignore
        self.callback = callback
        self.logger = logger

    def _accept(self, raw: str) -> PureWindowsPath | None:
        try:
            path = PureWindowsPath(raw)
            key = (
                self.mapper.key_from_origin(path)
                if self.side == "origin"
                else self.mapper.key_from_destination(path)
            )
            if key is None:
                return None
            if self.mapper.is_reserved_key(key):
                return None
            if self.ignore.ignored_relative(key):
                return None
            return path
        except Exception:
            return None

    def _emit(self, raw: str, moved_from: str | None = None) -> None:
        path = self._accept(raw)
        if path is None:
            return
        source = self._accept(moved_from) if moved_from else None
        try:
            self.callback(self.side, path, source)
        except Exception:
            self.logger.exception("Error procesando evento %s", raw)

    def on_created(self, event):
        self._emit(event.src_path)

    def on_modified(self, event):
        self._emit(event.src_path)

    def on_deleted(self, event):
        self._emit(event.src_path)

    def on_moved(self, event):
        self._emit(event.dest_path, event.src_path)


class _Watcher:
    def __init__(self, mode: str, timeout: float):
        self.observer = (
            PollingObserver(timeout=timeout) if mode == "polling" else Observer()
        )
        self.mode = mode

    def start(self, path: str, handler: FileSystemEventHandler) -> None:
        self.observer.schedule(handler, path, recursive=True)
        self.observer.start()

    def stop(self) -> None:
        try:
            self.observer.stop()
        except Exception:
            pass
        try:
            self.observer.join(timeout=5)
        except Exception:
            pass

    def alive(self) -> bool:
        try:
            return self.observer.is_alive()
        except Exception:
            return False


class WatchManager:
    def __init__(
        self,
        mapper: PathMapper,
        ignore: IgnoreMatcher,
        callback: EventCallback,
        logger: logging.Logger,
        mode_origin: str = "auto",
        mode_destination: str = "auto",
        polling_seg: float = 5.0,
    ):
        self.mapper = mapper
        self.ignore = ignore
        self.callback = callback
        self.logger = logger
        self.mode_origin = mode_origin
        self.mode_destination = mode_destination
        self.polling_seg = max(1.0, polling_seg)
        self._origin: _Watcher | None = None
        self._destination: _Watcher | None = None
        self._lock = threading.RLock()

    def _start_side(self, side: str, path, requested: str) -> _Watcher | None:
        target = Path(str(path))
        if not target.exists():
            self.logger.warning("No existe la raiz %s: %s", side, target)
            return None
        mode = observer_mode(path, requested)
        watcher = _Watcher(mode, self.polling_seg)
        handler = _Handler(side, self.mapper, self.ignore, self.callback, self.logger)
        watcher.start(str(target), handler)
        self.logger.info("Observador %s iniciado en modo %s: %s", side, mode, target)
        return watcher

    def start(self) -> None:
        with self._lock:
            if self._origin is None or not self._origin.alive():
                self._origin = self._start_side(
                    "origin", self.mapper.paths.origin, self.mode_origin
                )
            if self._destination is None or not self._destination.alive():
                self._destination = self._start_side(
                    "destination", self.mapper.paths.destination, self.mode_destination
                )

    def stop(self) -> None:
        with self._lock:
            for watcher in (self._origin, self._destination):
                if watcher is not None:
                    watcher.stop()
            self._origin = None
            self._destination = None

    def healthy(self) -> bool:
        with self._lock:
            return bool(
                self._origin
                and self._origin.alive()
                and self._destination
                and self._destination.alive()
            )

    def restart(self) -> None:
        self.stop()
        self.start()
