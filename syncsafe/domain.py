"""Modelos, mapeo de rutas, filtros y resolucion pura de estado."""

from __future__ import annotations

import fnmatch
import ntpath
from dataclasses import dataclass
from enum import Enum
from pathlib import PureWindowsPath

MARKER_ORIGIN = ".syncsafe.origin.id"
MARKER_DESTINATION = ".syncsafe.destination.id"
MARKER_NAMES = frozenset({MARKER_ORIGIN, MARKER_DESTINATION})


class EntryKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    OTHER = "other"


class Action(str, Enum):
    NOOP = "noop"
    COPY = "copy"
    INGEST = "ingest"
    DELETE = "delete"
    MKDIR = "mkdir"
    PRUNE_DIR = "prune_dir"
    CONFLICT = "conflict"


class Source(str, Enum):
    ORIGIN = "origin"
    INTAKE = "intake"


@dataclass(frozen=True)
class Entry:
    kind: EntryKind
    size: int = 0
    mtime_ns: int = 0

    @property
    def is_file(self) -> bool:
        return self.kind == EntryKind.FILE

    @property
    def is_dir(self) -> bool:
        return self.kind == EntryKind.DIRECTORY


@dataclass(frozen=True)
class Observation:
    origin: Entry | None
    intake: Entry | None
    destination: Entry | None
    blocked: bool = False
    managed: bool = False


@dataclass(frozen=True)
class Plan:
    action: Action
    source: Source | None = None
    reason: str = ""


@dataclass(frozen=True)
class AppPaths:
    origin: PureWindowsPath
    destination: PureWindowsPath
    intake: PureWindowsPath
    conflictos: PureWindowsPath
    papelera: PureWindowsPath
    cuarentena: PureWindowsPath


def normalize(value: str) -> str:
    return ntpath.normcase(value)


def path_key(value: PureWindowsPath) -> str:
    return normalize(str(value))


class PathMapper:
    def __init__(self, paths: AppPaths):
        self.paths = paths
        self.origin_key = path_key(paths.origin)
        self.destination_key = path_key(paths.destination)
        self.intake_key = path_key(paths.intake)

    def _relative(self, path: PureWindowsPath, root: PureWindowsPath) -> PureWindowsPath | None:
        try:
            return path.relative_to(root)
        except ValueError:
            return None

    def _under(self, path: PureWindowsPath, root: PureWindowsPath) -> bool:
        key = path_key(path)
        root_key = path_key(root)
        return key == root_key or key.startswith(root_key.rstrip("\\/") + "\\")

    def key_from_origin(self, path: PureWindowsPath) -> PureWindowsPath | None:
        if not self._under(path, self.paths.origin):
            return None
        if self._under(path, self.paths.intake):
            rel = self._relative(path, self.paths.intake)
            if rel is None or rel == PureWindowsPath("."):
                return None
            return rel
        rel = self._relative(path, self.paths.origin)
        if rel is None or rel == PureWindowsPath("."):
            return None
        return rel

    def key_from_destination(self, path: PureWindowsPath) -> PureWindowsPath | None:
        if not self._under(path, self.paths.destination):
            return None
        rel = self._relative(path, self.paths.destination)
        if rel is None or rel == PureWindowsPath("."):
            return None
        return rel

    def origin_path(self, key: PureWindowsPath) -> PureWindowsPath:
        return self.paths.origin / key

    def intake_path(self, key: PureWindowsPath) -> PureWindowsPath:
        return self.paths.intake / key

    def destination_path(self, key: PureWindowsPath) -> PureWindowsPath:
        return self.paths.destination / key

    def is_intake_absolute(self, path: PureWindowsPath) -> bool:
        return self._under(path, self.paths.intake)

    def is_reserved_key(self, key: PureWindowsPath) -> bool:
        parts = key.parts
        if not parts:
            return False
        first = parts[0]
        reserved = {
            self.paths.conflictos.name,
            self.paths.papelera.name,
            self.paths.cuarentena.name,
        }
        return first in reserved

    def is_reserved_absolute(self, path: PureWindowsPath) -> bool:
        if self._under(path, self.paths.conflictos):
            return True
        if self._under(path, self.paths.papelera):
            return True
        return self._under(path, self.paths.cuarentena)

    def relative_to_intake(self, path: PureWindowsPath) -> PureWindowsPath | None:
        if not self._under(path, self.paths.intake):
            return None
        return self._relative(path, self.paths.intake)


class IgnoreMatcher:
    def __init__(self, names: tuple[str, ...], directories: tuple[str, ...]):
        self.names = tuple(pattern.lower() for pattern in names)
        self.directories = tuple(pattern.lower() for pattern in directories)

    def ignored_name(self, name: str) -> bool:
        lower = name.lower()
        if lower in MARKER_NAMES:
            return True
        if lower.startswith(".sync-") and lower.endswith(".tmp"):
            return True
        return any(fnmatch.fnmatch(lower, pattern) for pattern in self.names)

    def ignored_directory(self, name: str) -> bool:
        lower = name.lower()
        return any(fnmatch.fnmatch(lower, pattern) for pattern in self.directories)

    def ignored_relative(self, key: PureWindowsPath) -> bool:
        for part in key.parts:
            if self.ignored_name(part) or self.ignored_directory(part):
                return True
        return False


def entries_equal(a: Entry, b: Entry, tolerance_ns: int = 2_000_000_000) -> bool:
    if a.kind != b.kind:
        return False
    if a.is_dir:
        return True
    if a.size != b.size:
        return False
    return abs(a.mtime_ns - b.mtime_ns) <= tolerance_ns


def resolve(observation: Observation, tolerance_ns: int) -> Plan:
    source = observation.origin or observation.intake
    source_side = Source.ORIGIN if observation.origin is not None else Source.INTAKE

    if source is not None:
        if source.is_dir:
            if observation.destination is None:
                return Plan(Action.MKDIR, source_side, "crear directorio")
            if not observation.destination.is_dir:
                return Plan(Action.CONFLICT, source_side, "archivo contra directorio")
            return Plan(Action.NOOP, source_side, "directorio ya existe")
        if observation.destination is None:
            return Plan(Action.COPY, source_side, "destino ausente")
        if observation.destination.is_dir:
            return Plan(Action.CONFLICT, source_side, "destino es directorio")
        if not entries_equal(source, observation.destination, tolerance_ns):
            return Plan(Action.COPY, source_side, "contenido distinto")
        return Plan(Action.NOOP, source_side, "sincronizado")

    if observation.destination is not None:
        if observation.destination.is_dir:
            return Plan(Action.PRUNE_DIR, None, "directorio sin fuente")
        if observation.blocked:
            return Plan(Action.NOOP, None, "renombrado reciente en destino")
        if observation.managed:
            return Plan(Action.DELETE, None, "fuente eliminada")
        return Plan(Action.INGEST, None, "alta desconocida en destino")

    return Plan(Action.NOOP, None, "sin cambios")
