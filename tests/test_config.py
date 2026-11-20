from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syncsafe.config import ConfigError, load_config, save_config_atomic


BASE = {
    "RAIZ": "C:\\Compartida",
    "ORIGEN": "C:\\Compartida\\Mis_Originales",
    "DESTINO": "C:\\Compartida\\Publico_Usuarios",
    "APORTES": {"subcarpeta": "Aportes_Usuarios"},
    "OBSERVACION": {"modo_origen": "auto", "modo_destino": "auto"},
    "RECONCILIACION": {"intervalo_min": 20, "restauracion_max_min": 60},
    "SEGURIDAD": {"requerir_armado": True, "papelera_activa": True},
    "LOG": {"archivo": "logs\\sync.log", "nivel": "INFO"},
}


class ConfigTests(unittest.TestCase):
    def write(self, root: Path, data: dict) -> Path:
        target = root / "config.json"
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    def test_accepts_root_origin_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), BASE)
            config = load_config(target)
            self.assertEqual(str(config.raiz), BASE["RAIZ"])
            self.assertEqual(str(config.origen), BASE["ORIGEN"])
            self.assertEqual(str(config.destino), BASE["DESTINO"])
            self.assertEqual(config.reconciliacion.intervalo_min, 20)

    def test_accepts_nested_subfolders(self):
        data = dict(BASE)
        data["ORIGEN"] = "C:\\Compartida\\A\\Originales"
        data["DESTINO"] = "C:\\Compartida\\B\\Copia"
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), data)
            config = load_config(target)
            self.assertEqual(str(config.origen), data["ORIGEN"])

    def test_rejects_same_path(self):
        data = dict(BASE)
        data["DESTINO"] = data["ORIGEN"]
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), data)
            with self.assertRaises(ConfigError):
                load_config(target)

    def test_rejects_origin_outside_root(self):
        data = dict(BASE)
        data["ORIGEN"] = "C:\\Otra\\Originales"
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), data)
            with self.assertRaises(ConfigError):
                load_config(target)

    def test_rejects_nested_destination(self):
        data = dict(BASE)
        data["DESTINO"] = data["ORIGEN"] + "\\Publico"
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), data)
            with self.assertRaises(ConfigError):
                load_config(target)

    def test_rejects_bad_observer(self):
        data = dict(BASE)
        data["OBSERVACION"] = {"modo_origen": "magia", "modo_destino": "auto"}
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), data)
            with self.assertRaises(ConfigError):
                load_config(target)

    def test_rejects_restore_below_scan(self):
        data = dict(BASE)
        data["RECONCILIACION"] = {"intervalo_min": 60, "restauracion_max_min": 20}
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), data)
            with self.assertRaises(ConfigError):
                load_config(target)

    def test_saves_last_good(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = self.write(root, BASE)
            config = load_config(target)
            save_config_atomic(config, target)
            self.assertTrue((root / "config.lastgood.json").exists())
            self.assertTrue((root / "config.json.bak").exists())

    def test_allows_missing_optional_sections(self):
        data = {
            "ORIGEN": BASE["ORIGEN"],
            "DESTINO": BASE["DESTINO"],
        }
        with tempfile.TemporaryDirectory() as temp:
            target = self.write(Path(temp), data)
            config = load_config(target)
            self.assertEqual(config.raiz, __import__("pathlib").PureWindowsPath("C:\\Compartida"))
            self.assertEqual(config.aportes.subcarpeta, "Aportes_Usuarios")
            self.assertEqual(config.seguridad.max_aporte_mb, 2048)


if __name__ == "__main__":
    unittest.main()
