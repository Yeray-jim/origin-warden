"""Arranque, CLI y ciclo de vida del servicio."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from . import APP_NAME, __version__
from .config import ConfigError, load_config
from .engine import SyncEngine
from .logging_setup import configure_logging
from .mutex import SingleInstance
from .security import IdentityManager, SafetyController, SafetyError
from .status import StatusStore, utc_now


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    if (base / "config.json").exists():
        return base
    if (base.parent / "config.json").exists():
        return base.parent
    return base


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sincronizador")
    parser.add_argument("--config", default=str(application_dir() / "config.json"))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--arm", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--approve", nargs="?", const="")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--version", action="store_true")
    return parser.parse_args(argv)


def _load(args):
    return load_config(args.config)


def _print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _crash_guard(config) -> bool:
    path = config.runtime_dir / "starts.json"
    now = time.time()
    window = max(1, config.seguridad.ventana_fallos_min * 60)
    values: list[float] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            values = [float(value) for value in raw if isinstance(value, (int, float))]
    except (OSError, ValueError):
        values = []
    values = [value for value in values if now - value <= window]
    values.append(now)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(values), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return len(values) > config.seguridad.fallos_modo_seguro


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0

    try:
        config = _load(args)
    except (ConfigError, OSError, ValueError) as exc:
        print(f"Configuracion invalida: {exc}", file=sys.stderr)
        return 2

    if args.check_config:
        _print_json(config.public_dict())
        return 0

    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    status = StatusStore(config.runtime_dir)

    if args.status:
        _print_json(status.read() or {})
        return 0

    if args.arm:
        try:
            sync_id = IdentityManager(config).arm()
        except (SafetyError, OSError) as exc:
            print(f"No se pudo armar: {exc}", file=sys.stderr)
            return 3
        print(f"Sistema armado con identidad {sync_id}")
        return 0

    if args.approve is not None:
        safety = SafetyController(config)
        if safety.approve(args.approve or None):
            print("Lote aprobado")
            return 0
        print("No hay una solicitud de aprobacion pendiente o el token no coincide.", file=sys.stderr)
        return 4

    logger = configure_logging(config, args.debug)
    logger.info("%s %s", APP_NAME, __version__)
    mutex = SingleInstance(f"{config.origen_str}|{config.destino_str}")
    if not mutex.acquire():
        logger.warning("Ya existe otra instancia para estas raices.")
        return 0

    crash_loop = _crash_guard(config)
    if crash_loop:
        logger.critical("Bucle de reinicios detectado; se arranca en simulacion segura.")
    engine = SyncEngine(config, logger, simulate=args.simulate or crash_loop)
    stop_event = threading.Event()
    control_dir = config.runtime_dir / "control"
    stop_request = control_dir / "stop.request"

    def request_stop(*_args):
        stop_event.set()

    try:
        control_dir.mkdir(parents=True, exist_ok=True)
        try:
            stop_request.unlink()
        except OSError:
            pass
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
    except Exception:
        pass

    try:
        status.set(
            state="starting",
            app=APP_NAME,
            version=__version__,
            started_at=utc_now(),
            config_path=str(Path(args.config).resolve()),
        )
        status.write()
        engine.start()
        while not stop_event.wait(1.0):
            if stop_request.exists():
                logger.info("Solicitud de parada ordenada recibida.")
                break
    except Exception:
        logger.exception("Fallo fatal del servicio")
        status.set(state="error", last_error="Fallo fatal del servicio")
        status.write()
        return 1
    finally:
        try:
            engine.stop()
        except Exception:
            logger.exception("Fallo al detener el servicio")
        try:
            mutex.release()
        except Exception:
            pass
        try:
            if stop_request.exists():
                stop_request.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
