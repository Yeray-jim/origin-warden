from __future__ import annotations

import unittest
from pathlib import PureWindowsPath

from syncsafe.domain import (
    Action,
    AppPaths,
    Entry,
    EntryKind,
    IgnoreMatcher,
    Observation,
    PathMapper,
    Source,
    entries_equal,
    resolve,
)


def entries(origin=None, intake=None, destination=None, blocked=False, managed=False):
    return Observation(origin, intake, destination, blocked, managed)


FILE = Entry(EntryKind.FILE, 10, 1_000_000_000)
FILE_OTHER = Entry(EntryKind.FILE, 20, 2_000_000_000)
DIR = Entry(EntryKind.DIRECTORY, 0, 1_000_000_000)


class MapperTests(unittest.TestCase):
    def setUp(self):
        root = PureWindowsPath(r"C:\Compartida")
        self.paths = AppPaths(
            root / "Originales",
            root / "Publico",
            root / "Originales" / "Aportes_Usuarios",
            root / "Originales" / "Aportes_Usuarios" / "_Conflictos",
            root / "Originales" / "Aportes_Usuarios" / "_Papelera",
            root / "Originales" / "Aportes_Usuarios" / "_Cuarentena",
        )
        self.mapper = PathMapper(self.paths)

    def test_normal_key(self):
        key = self.mapper.key_from_origin(PureWindowsPath(r"C:\Compartida\Originales\docs\a.txt"))
        self.assertEqual(key, PureWindowsPath(r"docs\a.txt"))
        self.assertEqual(
            self.mapper.destination_path(key),
            PureWindowsPath(r"C:\Compartida\Publico\docs\a.txt"),
        )

    def test_intake_key_maps_to_same_destination(self):
        key = self.mapper.key_from_origin(
            PureWindowsPath(r"C:\Compartida\Originales\Aportes_Usuarios\docs\a.txt")
        )
        self.assertEqual(key, PureWindowsPath(r"docs\a.txt"))
        self.assertTrue(
            self.mapper.is_intake_absolute(
                PureWindowsPath(r"C:\Compartida\Originales\Aportes_Usuarios\docs\a.txt")
            )
        )

    def test_reserved(self):
        self.assertTrue(self.mapper.is_reserved_key(PureWindowsPath(r"_Papelera\a.txt")))
        self.assertTrue(self.mapper.is_reserved_key(PureWindowsPath(r"_Conflictos\a.txt")))
        self.assertFalse(self.mapper.is_reserved_key(PureWindowsPath(r"docs\a.txt")))

    def test_outside_root(self):
        self.assertIsNone(self.mapper.key_from_origin(PureWindowsPath(r"D:\otro\a.txt")))
        self.assertIsNone(self.mapper.key_from_destination(PureWindowsPath(r"D:\otro\a.txt")))


class ResolverTests(unittest.TestCase):
    def test_origin_copy(self):
        plan = resolve(entries(origin=FILE), 2_000_000_000)
        self.assertEqual(plan.action, Action.COPY)
        self.assertEqual(plan.source, Source.ORIGIN)

    def test_origin_wins_over_intake(self):
        plan = resolve(entries(origin=FILE, intake=FILE_OTHER), 2_000_000_000)
        self.assertEqual(plan.action, Action.COPY)
        self.assertEqual(plan.source, Source.ORIGIN)

    def test_intake_restore(self):
        plan = resolve(entries(intake=FILE), 2_000_000_000)
        self.assertEqual(plan.action, Action.COPY)
        self.assertEqual(plan.source, Source.INTAKE)

    def test_destination_only_is_ingested(self):
        plan = resolve(entries(destination=FILE), 2_000_000_000)
        self.assertEqual(plan.action, Action.INGEST)

    def test_managed_destination_without_source_is_deleted(self):
        plan = resolve(entries(destination=FILE, managed=True), 2_000_000_000)
        self.assertEqual(plan.action, Action.DELETE)

    def test_blocked_destination_is_ignored(self):
        plan = resolve(entries(destination=FILE, blocked=True), 2_000_000_000)
        self.assertEqual(plan.action, Action.NOOP)

    def test_same_file_is_noop(self):
        plan = resolve(entries(origin=FILE, destination=FILE), 2_000_000_000)
        self.assertEqual(plan.action, Action.NOOP)

    def test_changed_file_is_copied(self):
        plan = resolve(entries(origin=FILE, destination=FILE_OTHER), 2_000_000_000)
        self.assertEqual(plan.action, Action.COPY)

    def test_directory_creation(self):
        plan = resolve(entries(origin=DIR), 2_000_000_000)
        self.assertEqual(plan.action, Action.MKDIR)

    def test_unknown_destination_directory_is_pruned(self):
        plan = resolve(entries(destination=DIR), 2_000_000_000)
        self.assertEqual(plan.action, Action.PRUNE_DIR)

    def test_type_conflict(self):
        plan = resolve(entries(origin=FILE, destination=DIR), 2_000_000_000)
        self.assertEqual(plan.action, Action.CONFLICT)

    def test_tolerance(self):
        close = Entry(EntryKind.FILE, 10, 1_500_000_000)
        self.assertTrue(entries_equal(FILE, close, 2_000_000_000))
        self.assertFalse(entries_equal(FILE, FILE_OTHER, 2_000_000_000))


class IgnoreTests(unittest.TestCase):
    def test_temporary_names(self):
        matcher = IgnoreMatcher(("~$*", "*.tmp", ".sync-*.tmp"), (".git",))
        self.assertTrue(matcher.ignored_name("~$documento.docx"))
        self.assertTrue(matcher.ignored_name("archivo.tmp"))
        self.assertTrue(matcher.ignored_name(".sync-abc.tmp"))
        self.assertTrue(matcher.ignored_name(".syncsafe.origin.id"))
        self.assertFalse(matcher.ignored_name("documento.docx"))
        self.assertTrue(matcher.ignored_relative(PureWindowsPath(r".git\config")))
        self.assertTrue(matcher.ignored_directory(".git"))


if __name__ == "__main__":
    unittest.main()
