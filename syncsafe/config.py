"""Configuracion validada del servicio y del panel."""

from __future__ import annotations

import json
import ntpath
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any

from . import __version__


class ConfigError(ValueError):
    pass


DEFAULT_IGNORAR_NOMBRES = (
    "~$*",
    ".~lock.*",
    ".sync-*.tmp",
    ".syncsafe.*",
    "*.tmp",
    "*.temp",
    "*.crdownload",
    "*.part",
    "*.partial",
)

DEFAULT_IGNORAR_DIRECTORIOS = (
    "$RECYCLE.BIN",
    "System Volume Information",
    ".git",
    "__pycache__",
)


@dataclass(frozen=True)
class AportesConfig:
    subcarpeta: str = "Aportes_Usuarios"
    conflictos: str = "_Conflictos"
    papelera: str = "_Papelera"
    cuarentena: str = "_Cuarentena"
    politica_conflicto: str = "versionar"


@dataclass(frozen=True)
class ObservacionConfig:
    modo_origen: str = "auto"
    modo_destino: str = "auto"
    intervalo_polling_seg: float = 5.0


@dataclass(frozen=True)
class ReconciliacionConfig:
    inicial: bool = True
    intervalo_min: int = 20
    restauracion_max_min: int = 60
    debounce_seg: float = 1.5
    trabajadores: int = 2
    reintentos_max: int = 8
    espera_inicial_seg: float = 0.5
    espera_maxima_seg: float = 10.0
    tolerancia_mtime_seg: int = 2
    verificar_hash: bool = False
    gracia_borrado_seg: int = 60


@dataclass(frozen=True)
class SeguridadConfig:
    requerir_armado: bool = True
    modo_inicial: str = "simulacion"
    max_borrados_lote: int = 50
    max_borrados_porcentaje: int = 10
    max_ingesta_archivos_lote: int = 200
    max_ingesta_mb_lote: int = 1024
    papelera_activa: bool = True
    papelera_dias: int = 7
    papelera_max_gb: int = 10
    max_aporte_mb: int = 2048
    watchdog_sin_progreso_min: int = 15
    fallos_modo_seguro: int = 3
    ventana_fallos_min: int = 15
    heartbeat_seg: int = 5
    heartbeat_vencido_seg: int = 20
    conservar_version_destino: bool = True


@dataclass(frozen=True)
class FiltrosConfig:
    ignorar_nombres: tuple[str, ...] = DEFAULT_IGNORAR_NOMBRES
    ignorar_directorios: tuple[str, ...] = DEFAULT_IGNORAR_DIRECTORIOS


@dataclass(frozen=True)
class LogConfig:
    archivo: str = "logs/sync.log"
    nivel: str = "INFO"
    max_bytes: int = 5_242_880
    copias: int = 5


@dataclass(frozen=True)
class AppConfig:
    raiz: PureWindowsPath
    origen: PureWindowsPath
    destino: PureWindowsPath
    base_dir: Path
    aportes: AportesConfig = field(default_factory=AportesConfig)
    observacion: ObservacionConfig = field(default_factory=ObservacionConfig)
    reconciliacion: ReconciliacionConfig = field(default_factory=ReconciliacionConfig)
    seguridad: SeguridadConfig = field(default_factory=SeguridadConfig)
    filtros: FiltrosConfig = field(default_factory=FiltrosConfig)
    log: LogConfig = field(default_factory=LogConfig)

    @property
    def raiz_str(self) -> str:
        return str(self.raiz)

    @property
    def origen_str(self) -> str:
        return str(self.origen)

    @property
    def destino_str(self) -> str:
        return str(self.destino)

    @property
    def intake_root(self) -> PureWindowsPath:
        return self.origen / self.aportes.subcarpeta

    @property
    def log_path(self) -> Path:
        candidate = Path(self.log.archivo)
        if candidate.is_absolute():
            return candidate
        return self.base_dir / candidate

    @property
    def runtime_dir(self) -> Path:
        return self.base_dir / "runtime"

    def public_dict(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "raiz": self.raiz_str,
            "origen": self.origen_str,
            "destino": self.destino_str,
            "aportes": self.aportes.subcarpeta,
            "modo_observador_origen": self.observacion.modo_origen,
            "modo_observador_destino": self.observacion.modo_destino,
            "intervalo_reconciliacion_min": self.reconciliacion.intervalo_min,
            "restauracion_max_min": self.reconciliacion.restauracion_max_min,
            "requerir_armado": self.seguridad.requerir_armado,
            "papelera_activa": self.seguridad.papelera_activa,
            "log": str(self.log_path),
        }

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "RAIZ": self.raiz_str,
            "ORIGEN": self.origen_str,
            "DESTINO": self.destino_str,
            "APORTES": asdict(self.aportes),
            "OBSERVACION": asdict(self.observacion),
            "RECONCILIACION": asdict(self.reconciliacion),
            "SEGURIDAD": asdict(self.seguridad),
            "FILTROS": {
                "IGNORAR_NOMBRES": list(self.filtros.ignorar_nombres),
                "IGNORAR_DIRECTORIOS": list(self.filtros.ignorar_directorios),
            },
            "LOG": asdict(self.log),
        }


def _value(section: dict[str, Any], key: str, default: Any) -> Any:
    if key in section:
        return section[key]
    return default


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


def _build(cls: type, raw: dict[str, Any], upper: bool = False):
    names = {f.name for f in fields(cls)}
    data: dict[str, Any] = {}
    for name in names:
        key = name.upper() if upper else name
        if key in raw:
            data[name] = raw[key]
    return cls(**data)


def _expand(value: str) -> str:
    return os.path.expandvars(value.strip())


def _is_within(child: PureWindowsPath, parent: PureWindowsPath) -> bool:
    c = ntpath.normcase(str(child))
    p = ntpath.normcase(str(parent))
    if c == p:
        return True
    try:
        return ntpath.commonpath([c, p]) == p
    except ValueError:
        return False


def _validate_windows_path(value: str, label: str) -> PureWindowsPath:
    p = PureWindowsPath(value)
    if not value or not p.is_absolute() or not p.drive:
        raise ConfigError(f"{label} debe ser una ruta absoluta de Windows o UNC: {value!r}")
    if ".." in p.parts:
        raise ConfigError(f"{label} no puede contener '..'.")
    return p


def _infer_root(origen: PureWindowsPath, destino: PureWindowsPath) -> PureWindowsPath:
    try:
        common = ntpath.commonpath([ntpath.normcase(str(origen)), ntpath.normcase(str(destino))])
    except ValueError as exc:
        raise ConfigError("No se puede inferir RAIZ; configure la ruta principal.") from exc
    root = PureWindowsPath(common)
    if not root.parts or root == PureWindowsPath(root.anchor):
        raise ConfigError("No se puede inferir RAIZ; configure la ruta principal.")
    return root



def default_raw_config() -> dict[str, Any]:
    return {
        "RAIZ": "",
        "ORIGEN": "",
        "DESTINO": "",
        "APORTES": asdict(AportesConfig()),
        "OBSERVACION": asdict(ObservacionConfig()),
        "RECONCILIACION": asdict(ReconciliacionConfig()),
        "SEGURIDAD": asdict(SeguridadConfig()),
        "FILTROS": {
            "IGNORAR_NOMBRES": list(DEFAULT_IGNORAR_NOMBRES),
            "IGNORAR_DIRECTORIOS": list(DEFAULT_IGNORAR_DIRECTORIOS),
        },
        "LOG": asdict(LogConfig()),
    }

def load_config(path: str | Path, *, allow_last_good: bool = True) -> AppConfig:
    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    try:
        return _load_config_file(config_path)
    except Exception:
        last_good = config_path.with_name(config_path.stem + ".lastgood.json")
        if allow_last_good and last_good.exists():
            return _load_config_file(last_good)
        raise


def _load_config_file(config_path: Path) -> AppConfig:
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ConfigError("config.json debe contener un objeto JSON.")

    origen = _validate_windows_path(_expand(str(raw.get("ORIGEN", ""))), "ORIGEN")
    destino = _validate_windows_path(_expand(str(raw.get("DESTINO", ""))), "DESTINO")
    raiz_raw = str(raw.get("RAIZ", "")).strip()
    if raiz_raw:
        raiz = _validate_windows_path(_expand(raiz_raw), "RAIZ")
    else:
        raiz = _infer_root(origen, destino)
    if _is_within(raiz, origen) or _is_within(raiz, destino):
        raise ConfigError("ORIGEN y DESTINO deben estar dentro de RAIZ.")
    if ntpath.normcase(str(raiz)) == ntpath.normcase(str(origen)):
        raise ConfigError("ORIGEN no puede ser igual a RAIZ.")
    if ntpath.normcase(str(raiz)) == ntpath.normcase(str(destino)):
        raise ConfigError("DESTINO no puede ser igual a RAIZ.")
    if not _is_within(origen, raiz):
        raise ConfigError("ORIGEN debe estar dentro de RAIZ.")
    if not _is_within(destino, raiz):
        raise ConfigError("DESTINO debe estar dentro de RAIZ.")
    if ntpath.normcase(str(origen)) == ntpath.normcase(str(destino)):
        raise ConfigError("ORIGEN y DESTINO no pueden ser la misma ruta.")
    if _is_within(destino, origen):
        raise ConfigError("DESTINO no puede estar dentro de ORIGEN.")
    if _is_within(origen, destino):
        raise ConfigError("ORIGEN no puede estar dentro de DESTINO.")

    aportes = _build(AportesConfig, _section(raw, "APORTES"))
    observacion = _build(ObservacionConfig, _section(raw, "OBSERVACION"))
    reconciliacion = _build(ReconciliacionConfig, _section(raw, "RECONCILIACION"))
    seguridad = _build(SeguridadConfig, _section(raw, "SEGURIDAD"))
    filtros_raw = _section(raw, "FILTROS")
    filtros = FiltrosConfig(
        ignorar_nombres=tuple(
            filtros_raw.get("IGNORAR_NOMBRES", DEFAULT_IGNORAR_NOMBRES)
        ),
        ignorar_directorios=tuple(
            filtros_raw.get("IGNORAR_DIRECTORIOS", DEFAULT_IGNORAR_DIRECTORIOS)
        ),
    )
    log = _build(LogConfig, _section(raw, "LOG"))

    if not aportes.subcarpeta or any(c in aportes.subcarpeta for c in ("/", "\\")):
        raise ConfigError("APORTES.SUBCARPETA debe ser un nombre de carpeta simple.")
    if observacion.modo_origen not in {"auto", "nativo", "polling"}:
        raise ConfigError("OBSERVACION.MODO_ORIGEN invalido.")
    if observacion.modo_destino not in {"auto", "nativo", "polling"}:
        raise ConfigError("OBSERVACION.MODO_DESTINO invalido.")
    if reconciliacion.trabajadores < 1 or reconciliacion.trabajadores > 16:
        raise ConfigError("RECONCILIACION.TRABAJADORES debe estar entre 1 y 16.")
    if reconciliacion.intervalo_min < 1:
        raise ConfigError("RECONCILIACION.INTERVALO_MIN debe ser mayor que cero.")
    if reconciliacion.restauracion_max_min < reconciliacion.intervalo_min:
        raise ConfigError(
            "RECONCILIACION.RESTAURACION_MAX_MIN debe ser mayor o igual al intervalo."
        )
    if reconciliacion.gracia_borrado_seg < 0:
        raise ConfigError("RECONCILIACION.GRACIA_BORRADO_SEG no puede ser negativo.")
    if seguridad.max_aporte_mb < 0:
        raise ConfigError("SEGURIDAD.MAX_APORTE_MB no puede ser negativo.")
    if log.nivel.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("LOG.NIVEL invalido.")

    return AppConfig(
        raiz=raiz,
        origen=origen,
        destino=destino,
        base_dir=config_path.parent,
        aportes=aportes,
        observacion=observacion,
        reconciliacion=reconciliacion,
        seguridad=seguridad,
        filtros=filtros,
        log=log,
    )


def save_config_atomic(config: AppConfig, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + ".bak")
        backup.write_bytes(target.read_bytes())
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(
        json.dumps(config.to_json_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, target)
    last_good = target.with_name(target.stem + ".lastgood.json")
    last_good.write_text(
        json.dumps(config.to_json_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def write_raw_config_atomic(raw: dict[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.with_name(target.name + ".bak").write_bytes(target.read_bytes())
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, target)
