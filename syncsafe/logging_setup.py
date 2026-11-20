"""Configuracion de logging rotativo con fallback."""

from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import AppConfig


def configure_logging(config: AppConfig, debug: bool = False) -> logging.Logger:
    logger = logging.getLogger("syncsafe")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if logger.handlers:
        return logger

    level = logging.DEBUG if debug else getattr(logging, config.log.nivel.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s"
    )

    target = config.log_path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target,
            maxBytes=config.log.max_bytes,
            backupCount=config.log.copias,
            encoding="utf-8",
        )
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "sincronizador-sync.log"
        handler = RotatingFileHandler(
            fallback,
            maxBytes=config.log.max_bytes,
            backupCount=config.log.copias,
            encoding="utf-8",
        )
        target = fallback

    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    try:
        if os.isatty(2):
            console = logging.StreamHandler()
            console.setLevel(level)
            console.setFormatter(formatter)
            logger.addHandler(console)
    except Exception:
        pass

    logging.captureWarnings(True)
    logger.info("Log iniciado en %s", target)
    return logger
