"""Consulta y descarga de releases de GitHub con verificacion de integridad."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from syncsafe import __version__

REPO = "Yeray-jim/origin-warden"
MANIFEST_URL = (
    f"https://github.com/{REPO}/releases/latest/download/release-manifest.json"
)
ASSET_BASE = f"https://github.com/{REPO}/releases/latest/download"
TRUSTED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}


class ReleaseError(RuntimeError):
    pass


def default_install_root() -> Path:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "OriginWarden"
        return Path.home() / "AppData" / "Local" / "OriginWarden"
    return Path(__file__).resolve().parent.parent


def parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(value).strip().lstrip("v").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def version_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_asset_url(url: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_HOSTS:
        raise ReleaseError(f"URL de descarga no confiable: {url}")


@dataclass(frozen=True)
class ReleaseInfo:
    manifest: dict[str, Any]
    version: str
    panel_asset: dict[str, Any] | None
    service_asset: dict[str, Any] | None
    installer_asset: dict[str, Any] | None

    @property
    def panel_newer(self) -> bool:
        return version_newer(self.version, __version__)


class ReleaseClient:
    def __init__(self, install_root: Path | None = None):
        self.install_root = install_root or default_install_root()
        self.runtime = self.install_root / "runtime"
        self.cache = self.runtime / "update-cache.json"
        self.downloads = self.runtime / "downloads"

    def _request_manifest(self) -> dict[str, Any]:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            response = client.get(
                MANIFEST_URL,
                headers={"User-Agent": f"OriginWarden/{__version__}"},
            )
            if response.status_code == 404:
                raise ReleaseError(
                    "No hay una release publicada. Publica una release con release-manifest.json."
                )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise ReleaseError("El manifest de release no es valido.")
        if data.get("app") != "origin-warden":
            raise ReleaseError("El manifest no pertenece a Origin Warden.")
        if not data.get("version"):
            raise ReleaseError("El manifest no incluye version.")
        return data

    def check(self, *, use_cache: bool = False) -> ReleaseInfo:
        manifest = None
        if use_cache:
            try:
                cached = json.loads(self.cache.read_text(encoding="utf-8"))
                if isinstance(cached, dict):
                    manifest = cached
            except (OSError, ValueError):
                pass
        if manifest is None:
            manifest = self._request_manifest()
            try:
                self.runtime.mkdir(parents=True, exist_ok=True)
                tmp = self.cache.with_name(self.cache.name + ".tmp")
                tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self.cache)
            except OSError:
                pass
        panel = manifest.get("panel") if isinstance(manifest.get("panel"), dict) else None
        service = manifest.get("service") if isinstance(manifest.get("service"), dict) else None
        installer = manifest.get("installer") if isinstance(manifest.get("installer"), dict) else None
        return ReleaseInfo(
            manifest=manifest,
            version=str(manifest.get("version")),
            panel_asset=panel,
            service_asset=service,
            installer_asset=installer,
        )

    def _asset_url(self, asset: dict[str, Any]) -> str:
        name = str(asset.get("name") or "")
        if not name or "/" in name or "\\" in name:
            raise ReleaseError("Nombre de asset invalido.")
        url = str(asset.get("url") or f"{ASSET_BASE}/{name}")
        _validate_asset_url(url)
        return url

    def download_asset(
        self,
        asset: dict[str, Any],
        target: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        expected = str(asset.get("sha256") or "").lower()
        expected_size = int(asset.get("size") or 0)
        if not expected:
            raise ReleaseError("El asset no tiene hash SHA-256.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size == expected_size and sha256_file(target) == expected:
            return target
        url = self._asset_url(asset)
        partial = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        received = 0
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            with client.stream("GET", url, headers={"User-Agent": f"OriginWarden/{__version__}"}) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if progress:
                            progress(received, expected_size)
        if expected_size and received != expected_size:
            partial.unlink(missing_ok=True)
            raise ReleaseError("El tamano descargado no coincide con el manifest.")
        if digest.hexdigest().lower() != expected:
            partial.unlink(missing_ok=True)
            raise ReleaseError("El hash SHA-256 no coincide. Descarga rechazada.")
        os.replace(partial, target)
        return target

    def prepare_service(
        self,
        release: ReleaseInfo | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[Path, Path]:
        release = release or self.check()
        if not release.service_asset or not release.installer_asset:
            raise ReleaseError("La release no contiene servicio o instalador.")
        service = self.download_asset(
            release.service_asset,
            self.downloads / str(release.service_asset.get("name")),
            progress=progress,
        )
        installer = self.download_asset(
            release.installer_asset,
            self.install_root / str(release.installer_asset.get("name")),
            progress=progress,
        )
        return service, installer

    def download_panel(self, release: ReleaseInfo | None = None) -> Path:
        release = release or self.check()
        if not release.panel_asset:
            raise ReleaseError("La release no contiene el panel.")
        target = self.downloads / "OriginWardenPanel.new.exe"
        return self.download_asset(release.panel_asset, target)

    def apply_panel_update(self, new_exe: Path) -> None:
        if not getattr(sys, "frozen", False):
            raise ReleaseError("La actualizacion automatica del panel solo aplica al ejecutable empaquetado.")
        installed = default_install_root() / "OriginWardenPanel.exe"
        old_exe = installed.resolve() if installed.exists() else Path(sys.executable).resolve()
        script = self.runtime / "update" / "apply-panel-update.ps1"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(
            "$ErrorActionPreference='Stop'\n"
            f"$old='{str(old_exe).replace(chr(39), chr(39)*2)}'\n"
            f"$new='{str(new_exe).replace(chr(39), chr(39)*2)}'\n"
            f"$pidValue={os.getpid()}\n"
            "Wait-Process -Id $pidValue -Timeout 60 -ErrorAction SilentlyContinue\n"
            "Copy-Item -LiteralPath $new -Destination $old -Force\n"
            "Start-Process -FilePath $old\n",
            encoding="utf-8",
        )
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
