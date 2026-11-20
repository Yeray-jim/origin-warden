"""Panel de control Flet del Origin Warden."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import flet as ft

from syncsafe import APP_NAME, __version__
from ui.manual import MANUAL
from ui.release_client import default_install_root, ReleaseClient, ReleaseError, version_newer
from ui.services import (
    ConfigService,
    ensure_panel_copy,
    InstallerService,
    LogReader,
    StatusReader,
    app_dir,
)
from ui.task_control import TaskController

ACCENT = "#0F766E"
GREEN = ft.Colors.GREEN_600
AMBER = ft.Colors.AMBER_700
RED = ft.Colors.RED_600
GREY = ft.Colors.BLUE_GREY_400


def card(content: ft.Control, padding: int = 18) -> ft.Container:
    return ft.Container(
        content=content,
        padding=padding,
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=ft.BorderRadius.all(12),
    )


def label(value: str, size: int = 12, color: str | None = None) -> ft.Text:
    return ft.Text(
        value,
        size=size,
        color=color or ft.Colors.ON_SURFACE_VARIANT,
        weight=ft.FontWeight.W_500,
    )


class PanelApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.base = app_dir()
        ensure_panel_copy()
        self.status_reader = StatusReader(self.base)
        self.tasks = TaskController(runtime_dir=self.base / "runtime")
        self.config_service = ConfigService(self.base)
        self.installer = InstallerService(self.base, task_controller=self.tasks)
        self.release = ReleaseClient(self.base)
        self._release_info = None
        self._update_available = False
        self.logs = LogReader(self.base)
        self.busy = False
        self._task_cache = None
        self._task_cache_at = 0.0
        self._current = "panel"
        self._views: dict[str, ft.Control] = {}
        self._settings_fields: dict[str, ft.TextField] = {}
        self._manual_view: ft.Control | None = None
        self._logs_view: ft.Control | None = None
        self._panel_view: ft.Control | None = None
        self._log_field: ft.TextField | None = None
        self._log_filter: ft.TextField | None = None
        self._build_status_controls()
        self._configure_page()
        self.dir_picker = ft.FilePicker()
        self.page.services = [*list(self.page.services), self.dir_picker]
        self._build_shell()
        self._select("panel")
        self.page.run_task(self._poll_loop)
        self.page.run_task(self._auto_update_check)

    def _configure_page(self) -> None:
        self.page.title = f"{APP_NAME} {__version__}"
        self.page.theme_mode = ft.ThemeMode.SYSTEM
        self.page.padding = 0
        self.page.bgcolor = ft.Colors.SURFACE
        try:
            self.page.window.width = 1120
            self.page.window.height = 760
            self.page.window.min_width = 900
            self.page.window.min_height = 620
        except Exception:
            pass

    def _build_status_controls(self) -> None:
        self.status_dot = ft.Container(
            width=10, height=10, border_radius=5, bgcolor=GREY
        )
        self.status_text = ft.Text("Comprobando...", size=16, weight=ft.FontWeight.BOLD)
        self.status_sub = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.status_chip = ft.Container(
            content=ft.Row([self.status_dot, self.status_text], spacing=8),
            padding=ft.Padding.symmetric(horizontal=12, vertical=7),
            border_radius=20,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGH,
        )
        self.service_switch = ft.Switch(
            label="Servicio",
            value=False,
            active_color=ACCENT,
            on_change=self._switch_changed,
        )
        self.paths_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT, selectable=True)
        self.mode_text = ft.Text("", size=12)
        self.heartbeat_text = ft.Text("", size=12)
        self.counters_text = ft.Text("", size=12)
        self.alert = ft.Container(visible=False)
        self.pending_card = ft.Container(visible=False)
        self.pending_text = ft.Text("", size=12)
        self.armed_badge = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.task_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.auto_text = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
        self.status_last_error = ft.Text("", size=12, color=RED, selectable=True)
        self.stale_banner = ft.Container(visible=False)

    def _build_shell(self) -> None:
        nav_items = [
            ("panel", "Panel", ft.Icons.DASHBOARD_OUTLINED),
            ("paths", "Rutas", ft.Icons.FOLDER_OPEN),
            ("settings", "Configuracion", ft.Icons.TUNE),
            ("install", "Instalacion", ft.Icons.BUILD_OUTLINED),
            ("manual", "Manual", ft.Icons.MENU_BOOK_OUTLINED),
            ("logs", "Registro", ft.Icons.DESCRIPTION_OUTLINED),
        ]
        self.nav_buttons: dict[str, ft.TextButton] = {}
        nav_column = ft.Column(spacing=4)
        for key, text, icon in nav_items:
            button = ft.TextButton(
                content=text,
                icon=icon,
                width=210,
                on_click=lambda _e, k=key: self._select(k),
            )
            self.nav_buttons[key] = button
            nav_column.controls.append(button)
        sidebar = ft.Container(
            width=230,
            padding=ft.Padding.symmetric(horizontal=14, vertical=18),
            bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.SYNC, color=ACCENT, size=26),
                            ft.Text(APP_NAME, size=14, weight=ft.FontWeight.BOLD),
                        ],
                        spacing=10,
                    ),
                    ft.Divider(height=18, color=ft.Colors.OUTLINE_VARIANT),
                    nav_column,
                    ft.Container(expand=True),
                    ft.Text(f"v{__version__}", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                expand=True,
            ),
        )
        self.content_host = ft.Container(expand=True, padding=24)
        self.page.add(ft.Row([sidebar, self.content_host], expand=True))

    def _select(self, key: str) -> None:
        self._current = key
        for name, button in self.nav_buttons.items():
            button.style = ft.ButtonStyle(
                color=ACCENT if name == key else ft.Colors.ON_SURFACE_VARIANT
            )
        if key == "panel":
            if self._panel_view is None:
                self._panel_view = self._build_panel()
            view = self._panel_view
        elif key == "paths":
            view = self._build_paths()
        elif key == "settings":
            view = self._build_settings()
        elif key == "install":
            view = self._build_install()
        elif key == "manual":
            if self._manual_view is None:
                self._manual_view = self._build_manual()
            view = self._manual_view
        else:
            if self._logs_view is None:
                self._logs_view = self._build_logs()
            view = self._logs_view
            self._refresh_logs()
        self.content_host.content = view
        self.page.update()
        if key == "panel":
            self._refresh_status()

    def _build_panel(self) -> ft.Control:
        overview = card(
            ft.Column(
                [
                    ft.Row(
                        [
                            self.status_chip,
                            ft.Row(
                                [self.service_switch, ft.Text("Autoarranque", size=12)],
                                spacing=18,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    self.status_sub,
                    self.stale_banner,
                    ft.Divider(height=14, color=ft.Colors.OUTLINE_VARIANT),
                    self.paths_text,
                    self.armed_badge,
                    self.status_last_error,
                ],
                spacing=8,
            )
        )
        self.alert_button = ft.TextButton(
            content="Abrir manual de recuperacion",
            icon=ft.Icons.MENU_BOOK_OUTLINED,
            on_click=lambda _e: self._select("manual"),
        )
        self.alert.content = ft.Row(
            [
                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=AMBER),
                ft.Text("", size=13, expand=True),
                self.alert_button,
            ],
            spacing=10,
        )
        self.alert.padding = 12
        self.alert.border_radius = 10
        self.alert.bgcolor = ft.Colors.AMBER_50
        self.pending_card.content = ft.Row(
            [
                ft.Icon(ft.Icons.LOCK_CLOCK, color=AMBER),
                ft.Column([ft.Text("Aprobacion pendiente", weight=ft.FontWeight.BOLD), self.pending_text], spacing=2, expand=True),
                ft.FilledButton(
                    content="Aprobar lote",
                    icon=ft.Icons.CHECK,
                    on_click=self._approve_clicked,
                ),
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.pending_card.padding = 14
        self.pending_card.border_radius = 10
        self.pending_card.bgcolor = ft.Colors.SURFACE_CONTAINER_HIGH
        metrics = ft.Row(
            [
                card(
                    ft.Column(
                        [
                            label("Modo"),
                            self.mode_text,
                            label("Ultimo heartbeat"),
                            self.heartbeat_text,
                        ],
                        spacing=4,
                    ),
                    padding=16,
                ),
                card(
                    ft.Column(
                        [
                            label("Trabajo"),
                            self.counters_text,
                            label("Dead-letter / papelera"),
                            self.task_text,
                        ],
                        spacing=4,
                    ),
                    padding=16,
                ),
            ],
            spacing=14,
            expand=True,
        )
        quick = card(
            ft.Column(
                [
                    ft.Text("Acciones rapidas", weight=ft.FontWeight.BOLD),
                    ft.Row(
                        [
                            ft.OutlinedButton(
                                content="Abrir origen",
                                icon=ft.Icons.FOLDER_OPEN,
                                on_click=lambda _e: self._open_path("origen"),
                            ),
                            ft.OutlinedButton(
                                content="Abrir destino",
                                icon=ft.Icons.FOLDER_OPEN,
                                on_click=lambda _e: self._open_path("destino"),
                            ),
                            ft.OutlinedButton(
                                content="Abrir aportes",
                                icon=ft.Icons.INBOX,
                                on_click=lambda _e: self._open_path("aportes"),
                            ),
                            ft.OutlinedButton(
                                content="Abrir registro",
                                icon=ft.Icons.DESCRIPTION,
                                on_click=lambda _e: self._select("logs"),
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                ],
                spacing=10,
            )
        )
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text("Panel de control", size=26, weight=ft.FontWeight.BOLD),
                                ft.Text(
                                    "Estado del servicio, guardias y restauraciones.",
                                    size=13,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                            spacing=2,
                        ),
                        self.auto_text,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                overview,
                self.alert,
                self.pending_card,
                metrics,
                quick,
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )


    def _format_bytes(self, value: int) -> str:
        size = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{value} B"

    def _build_paths(self) -> ft.Control:
        try:
            config = self.config_service.load()
            error = ""
        except Exception as exc:
            config = None
            error = str(exc)
        try:
            audit = self.installer.audit()
            installed = audit.status != "not_installed" and config is not None
        except Exception:
            installed = False
        self._path_root = ft.TextField(
            label="Nodo principal / carpeta compartida",
            value=config.raiz_str if config else "",
            hint_text=r"\\servidor\recurso o C:\CarpetaCompartida",
            dense=True,
            disabled=installed,
            expand=True,
        )
        self._path_origin = ft.TextField(
            label="Carpeta de originales",
            value=config.origen_str if config else "",
            hint_text=r"\\servidor\recurso\Mis_Originales",
            dense=True,
            disabled=installed,
            expand=True,
        )
        self._path_destination = ft.TextField(
            label="Carpeta publica para usuarios",
            value=config.destino_str if config else "",
            hint_text=r"\\servidor\recurso\Publico_Usuarios",
            dense=True,
            disabled=installed,
            expand=True,
        )
        self._path_analysis_data = None
        self._path_analysis = ft.Text(
            "Selecciona la raiz y pulsa Analizar para ver el contenido.",
            size=12,
            color=ft.Colors.ON_SURFACE_VARIANT,
            selectable=True,
        )
        self._path_status = ft.Text(error, size=12, color=RED if error else ft.Colors.ON_SURFACE_VARIANT, selectable=True)
        self._path_warning = ft.Container(visible=False)
        if installed:
            self._path_warning.content = ft.Row(
                [
                    ft.Icon(ft.Icons.LOCK_OUTLINE, color=AMBER),
                    ft.Text(
                        "El servicio esta instalado. Desinstalalo antes de cambiar la raiz, el origen o la copia.",
                        size=12,
                        expand=True,
                    ),
                    ft.TextButton(content="Ir a Instalacion", on_click=lambda _e: self._select("install")),
                ],
                spacing=10,
            )
            self._path_warning.visible = True
            self._path_warning.padding = 12
            self._path_warning.border_radius = 10
            self._path_warning.bgcolor = ft.Colors.AMBER_50
        browse_root = ft.OutlinedButton(
            content="Examinar",
            icon=ft.Icons.FOLDER_OPEN,
            disabled=installed,
            on_click=self._pick_root_clicked,
        )
        analyze = ft.FilledButton(
            content="Analizar raiz",
            icon=ft.Icons.SEARCH,
            disabled=installed,
            on_click=self._analyze_root_clicked,
        )
        create = ft.OutlinedButton(
            content="Crear estructura",
            icon=ft.Icons.CREATE_NEW_FOLDER,
            disabled=installed,
            on_click=self._create_paths_clicked,
        )
        engulf = ft.OutlinedButton(
            content="Englobar contenido",
            icon=ft.Icons.DRIVE_FILE_MOVE_OUTLINED,
            disabled=installed,
            on_click=self._engulf_paths_clicked,
        )
        adopt = ft.OutlinedButton(
            content="Usar carpetas existentes",
            icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
            disabled=installed,
            on_click=self._adopt_paths_clicked,
        )
        open_root = ft.OutlinedButton(
            content="Abrir raiz",
            icon=ft.Icons.OPEN_IN_NEW,
            on_click=lambda _e: self._open_path("raiz"),
        )
        body = card(
            ft.Column(
                [
                    ft.Text("Nodo principal y carpetas", size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Define primero la carpeta compartida. Despues crea el origen y la copia, o adopta carpetas existentes. "
                        "El origen y la copia pueden estar en subcarpetas de la raiz, pero no dentro del otro.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    self._path_warning,
                    ft.Row([self._path_root, browse_root], spacing=10),
                    ft.Row([self._path_origin, self._path_destination], spacing=12),
                    ft.Row([analyze, open_root], spacing=10, wrap=True),
                    self._path_analysis,
                    ft.Divider(height=12, color=ft.Colors.OUTLINE_VARIANT),
                    ft.Text("Preparar rutas", weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Si la raiz esta vacia, crea la estructura. Si ya tiene contenido, puedes moverlo al origen o usar carpetas existentes.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Row([create, engulf, adopt], spacing=10, wrap=True),
                    self._path_status,
                ],
                spacing=12,
            )
        )
        guide = card(
            ft.Column(
                [
                    ft.Text("Como decidir", weight=ft.FontWeight.BOLD),
                    ft.Text("Raiz vacia: crea las carpetas y continua con la instalacion.", size=12),
                    ft.Text("Raiz con contenido y quieres conservarlo como original: Englobar contenido.", size=12),
                    ft.Text("Raiz con carpetas de origen y copia ya existentes: Usar carpetas existentes.", size=12),
                    ft.Text("El servicio debe estar desinstalado para mover contenido o cambiar rutas.", size=12, color=AMBER),
                ],
                spacing=6,
            )
        )
        return ft.Column(
            [ft.Text("Rutas de trabajo", size=26, weight=ft.FontWeight.BOLD), body, guide],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _pick_root_clicked(self, _e) -> None:
        self.page.run_task(self._pick_root_async)

    async def _pick_root_async(self) -> None:
        try:
            path = await self.dir_picker.get_directory_path(dialog_title="Seleccionar nodo principal")
            if path:
                self._path_root.value = path
                self.page.update()
        except Exception as exc:
            self._snack(f"No se pudo abrir el selector: {exc}", error=True)

    def _analyze_root_clicked(self, _e) -> None:
        self.page.run_task(self._analyze_root_async)

    async def _analyze_root_async(self) -> None:
        root = (self._path_root.value or "").strip()
        if not root:
            self._path_status.value = "Escribe o selecciona la raiz."
            self._path_status.color = RED
            self.page.update()
            return
        self._path_status.value = "Analizando raiz..."
        self._path_status.color = ft.Colors.ON_SURFACE_VARIANT
        self.page.update()
        try:
            analysis = await asyncio.to_thread(self.config_service.analyze_root, root)
            self._path_analysis_data = analysis
            state = "vacia" if analysis.empty else "con contenido"
            lines = [
                f"Raiz: {analysis.root}",
                f"Estado: {state}",
                f"Entradas: {analysis.entries} | Archivos: {analysis.files} | Carpetas: {analysis.directories}",
                f"Tamano aproximado: {self._format_bytes(analysis.total_bytes)}",
            ]
            if analysis.top_level:
                lines.append("Elementos: " + ", ".join(analysis.top_level[:20]))
            if analysis.warnings:
                lines.append("Avisos: " + " | ".join(analysis.warnings[:10]))
            if analysis.error:
                lines.append("Error: " + analysis.error)
            self._path_analysis.value = "\n".join(lines)
            base = root.rstrip("\\/")
            if not (self._path_origin.value or "").strip():
                self._path_origin.value = f"{base}\\Mis_Originales"
            if not (self._path_destination.value or "").strip():
                self._path_destination.value = f"{base}\\Publico_Usuarios"
            self._path_status.value = "Analisis completado."
            self._path_status.color = GREEN if not analysis.error else RED
        except Exception as exc:
            self._path_status.value = str(exc)
            self._path_status.color = RED
        self.page.update()

    def _path_values(self) -> tuple[str, str, str]:
        return (
            (self._path_root.value or "").strip(),
            (self._path_origin.value or "").strip(),
            (self._path_destination.value or "").strip(),
        )

    def _create_paths_clicked(self, _e) -> None:
        if self._path_analysis_data is None:
            self._snack("Analiza la raiz antes de crear la estructura.", error=True)
            return
        if not self._path_analysis_data.empty:
            self._snack("La raiz tiene contenido. Usa Englobar contenido o Usar carpetas existentes.", error=True)
            return
        self.page.run_task(self._apply_paths_async, "create")

    def _engulf_paths_clicked(self, _e) -> None:
        if self._path_analysis_data is None:
            self._snack("Analiza la raiz antes de mover contenido.", error=True)
            return
        if self._path_analysis_data.empty:
            self._snack("La raiz esta vacia. Usa Crear estructura.", error=True)
            return
        count = self._path_analysis_data.entries
        dialog = ft.AlertDialog(
            title=ft.Text("Mover contenido al origen"),
            content=ft.Text(
                f"Se moveran {count} elementos de la raiz a la carpeta de originales. "
                "No se sobrescribe nada. El servicio debe estar desinstalado. "
                "Esta operacion se registra y puede intentar revertirse si falla."
            ),
            actions=[
                ft.TextButton(content="Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                ft.FilledButton(content="Mover contenido", on_click=self._confirm_engulf),
            ],
        )
        self.page.show_dialog(dialog)

    def _confirm_engulf(self, _e) -> None:
        self.page.pop_dialog()
        self.page.run_task(self._apply_paths_async, "engulf")

    def _adopt_paths_clicked(self, _e) -> None:
        self.page.run_task(self._apply_paths_async, "adopt")

    async def _apply_paths_async(self, mode: str) -> None:
        if self.busy:
            return
        root, origin, destination = self._path_values()
        if not root or not origin or not destination:
            self._path_status.value = "Completa la raiz, el origen y la copia."
            self._path_status.color = RED
            self.page.update()
            return
        self.busy = True
        self._path_status.value = "Aplicando configuracion de rutas..."
        self._path_status.color = ft.Colors.ON_SURFACE_VARIANT
        self.page.update()
        try:
            audit = await asyncio.to_thread(self.installer.audit)
            installed = audit.status != "not_installed"
            await asyncio.to_thread(
                self.config_service.apply_paths,
                root,
                origin,
                destination,
                mode,
                service_installed=installed,
            )
            self._path_status.value = "Rutas guardadas. Continua con la instalacion del servicio."
            self._path_status.color = GREEN
            self._snack("Rutas preparadas correctamente.")
            self._refresh_status(force_task=True)
        except Exception as exc:
            self._path_status.value = str(exc)
            self._path_status.color = RED
            self._snack(str(exc), error=True)
        finally:
            self.busy = False
            self.page.update()

    def _build_settings(self) -> ft.Control:
        try:
            config = self.config_service.load()
            error = ""
        except Exception as exc:
            config = None
            error = str(exc)
        fields: dict[str, ft.TextField] = {}
        if config is not None:
            values = {
                "aportes": config.aportes.subcarpeta,
                "modo_origen": config.observacion.modo_origen,
                "modo_destino": config.observacion.modo_destino,
                "intervalo_min": str(config.reconciliacion.intervalo_min),
                "restauracion_max_min": str(config.reconciliacion.restauracion_max_min),
                "trabajadores": str(config.reconciliacion.trabajadores),
                "debounce_seg": str(config.reconciliacion.debounce_seg),
                "gracia_borrado_seg": str(config.reconciliacion.gracia_borrado_seg),
                "max_aporte_mb": str(config.seguridad.max_aporte_mb),
                "nivel_log": config.log.nivel,
            }
        else:
            values = {key: "" for key in (
                "aportes", "modo_origen", "modo_destino",
                "intervalo_min", "restauracion_max_min", "trabajadores",
                "debounce_seg", "gracia_borrado_seg", "max_aporte_mb", "nivel_log"
            )}
        specs = [
            ("aportes", "Subcarpeta de aportes", "Aportes_Usuarios"),
            ("modo_origen", "Observador del origen (auto/nativo/polling)", "auto"),
            ("modo_destino", "Observador del destino (auto/nativo/polling)", "auto"),
            ("intervalo_min", "Reconciliacion (minutos)", "20"),
            ("restauracion_max_min", "Plazo de restauracion (minutos)", "60"),
            ("trabajadores", "Trabajadores concurrentes", "2"),
            ("debounce_seg", "Debounce (segundos)", "1.5"),
            ("gracia_borrado_seg", "Ventana de gracia para borrar (segundos)", "60"),
            ("max_aporte_mb", "Tamano maximo de aporte (MB)", "2048"),
            ("nivel_log", "Nivel de log (DEBUG/INFO/WARNING/ERROR)", "INFO"),
        ]
        for key, text, hint in specs:
            fields[key] = ft.TextField(
                label=text,
                value=values.get(key, ""),
                hint_text=hint,
                dense=True,
                expand=True,
            )
        self._settings_fields = fields
        self._papelera_switch = ft.Switch(
            label="Papelera interna activa",
            value=bool(config.seguridad.papelera_activa) if config else True,
        )
        self._armado_switch = ft.Switch(
            label="Exigir armado inicial",
            value=bool(config.seguridad.requerir_armado) if config else True,
        )
        self._settings_error = ft.Text(error, color=RED, size=12, selectable=True)
        form = card(
            ft.Column(
                [
                    ft.Text("Configuracion", size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "La configuracion se valida antes de guardar. El servicio debe reiniciarse para aplicarla.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=12, color=ft.Colors.OUTLINE_VARIANT),
                    ft.Text(
                        "La raiz, el origen y la copia se configuran en la pestana Rutas.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Row([fields["aportes"], fields["nivel_log"]], spacing=12),
                    ft.Row([fields["modo_origen"], fields["modo_destino"]], spacing=12),
                    ft.Row(
                        [
                            fields["intervalo_min"],
                            fields["restauracion_max_min"],
                            fields["trabajadores"],
                        ],
                        spacing=12,
                    ),
                    ft.Row(
                        [
                            fields["debounce_seg"],
                            fields["gracia_borrado_seg"],
                            fields["max_aporte_mb"],
                        ],
                        spacing=12,
                    ),
                    ft.Row([self._papelera_switch, self._armado_switch], spacing=24, wrap=True),
                    self._settings_error,
                    ft.Row(
                        [
                            ft.OutlinedButton(
                                content="Probar rutas",
                                icon=ft.Icons.NETWORK_CHECK,
                                on_click=self._test_paths_clicked,
                            ),
                            ft.OutlinedButton(
                                content="Guardar",
                                icon=ft.Icons.SAVE_OUTLINED,
                                on_click=self._save_settings_clicked,
                            ),
                            ft.FilledButton(
                                content="Guardar y reiniciar",
                                icon=ft.Icons.RESTART_ALT,
                                on_click=self._save_restart_clicked,
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                ],
                spacing=12,
            )
        )
        return ft.Column(
            [
                ft.Text("Configuracion del servicio", size=26, weight=ft.FontWeight.BOLD),
                form,
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _build_install(self) -> ft.Control:
        audit = self.installer.audit()
        installed = audit.installed
        try:
            config = self.config_service.load()
            config_ok = True
            config_error = ""
        except Exception as exc:
            config = None
            config_ok = False
            config_error = str(exc)
        steps = [
            ("1", "Rutas", "Configura la raiz, el origen y la copia."),
            ("2", "Descarga", "El panel obtiene el servicio verificado desde GitHub."),
            ("3", "Instalacion", "Registra la tarea y aplica permisos."),
            ("4", "Prueba", "Inicia, comprueba heartbeat y arma el sistema."),
        ]
        step_rows = [
            ft.Row(
                [
                    ft.Container(
                        content=ft.Text(number, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                        width=28,
                        height=28,
                        alignment=ft.Alignment.CENTER,
                        bgcolor=ACCENT,
                        border_radius=14,
                    ),
                    ft.Column(
                        [
                            ft.Text(title, weight=ft.FontWeight.BOLD),
                            ft.Text(text, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                        ],
                        spacing=2,
                    ),
                ],
                spacing=12,
            )
            for number, title, text in steps
        ]
        self.install_status = ft.Text(
            audit.message,
            size=12,
            color=GREEN if installed else ft.Colors.ON_SURFACE_VARIANT,
            selectable=True,
        )
        update_message = (
            f"Ultima version publicada: {self._release_info.version}"
            if self._release_info is not None
            else ""
        )
        self.update_status = ft.Text(
            update_message,
            size=12,
            color=AMBER if self._update_available else ft.Colors.ON_SURFACE_VARIANT,
            selectable=True,
        )
        if not config_ok:
            config_hint = ft.Text(
                f"Configuracion pendiente: {config_error}. Ve a Rutas y prepara la estructura.",
                size=12,
                color=AMBER,
            )
        else:
            config_hint = ft.Text(
                f"Raiz: {config.raiz_str}\nOrigen: {config.origen_str}\nCopia: {config.destino_str}",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
                selectable=True,
            )
        if installed:
            primary = ft.FilledButton(
                content="Abrir panel",
                icon=ft.Icons.DASHBOARD_OUTLINED,
                on_click=lambda _e: self._select("panel"),
            )
            repair = ft.OutlinedButton(
                content="Reparar instalacion",
                icon=ft.Icons.BUILD,
                on_click=self._repair_clicked,
            )
            uninstall = ft.OutlinedButton(
                content="Desinstalar tarea",
                icon=ft.Icons.DELETE_OUTLINE,
                on_click=self._uninstall_clicked,
            )
        else:
            primary = ft.FilledButton(
                content="Instalar servicio",
                icon=ft.Icons.DOWNLOAD,
                on_click=self._install_clicked,
            )
            repair = ft.OutlinedButton(
                content="Preparar rutas",
                icon=ft.Icons.FOLDER_OPEN,
                on_click=lambda _e: self._select("paths"),
            )
            uninstall = ft.OutlinedButton(
                content="Desinstalar tarea",
                icon=ft.Icons.DELETE_OUTLINE,
                disabled=True,
            )
        check_updates = ft.OutlinedButton(
            content="Buscar actualizaciones",
            icon=ft.Icons.SYSTEM_UPDATE_ALT,
            on_click=self._check_update_clicked,
        )
        update_button = ft.OutlinedButton(
            content="Actualizar ahora" if self._update_available else "Actualizar",
            icon=ft.Icons.SYSTEM_UPDATE,
            on_click=self._update_clicked,
            visible=self._update_available,
        )
        self._update_button = update_button
        body = card(
            ft.Column(
                [
                    ft.Text("Instalacion del servicio", size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "El panel descarga el servicio, verifica su SHA-256 y registra la tarea. "
                        "No necesitas abrir PowerShell ni escribir comandos. UAC solo aparece para permisos.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=12, color=ft.Colors.OUTLINE_VARIANT),
                    *step_rows,
                    ft.Divider(height=12, color=ft.Colors.OUTLINE_VARIANT),
                    config_hint,
                    ft.Row([primary, repair, uninstall], spacing=10, wrap=True),
                    ft.Row([check_updates, update_button], spacing=10, wrap=True),
                    self.install_status,
                    self.update_status,
                ],
                spacing=12,
            )
        )
        remote = card(
            ft.Column(
                [
                    ft.Text("Permisos en SMB remoto", weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Si el origen o el destino estan en otro servidor, la ACL remota debe aplicarse en ese servidor. "
                        "El instalador no modifica ACL remotas desde el cliente.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=6,
            )
        )
        running = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve()
        canonical = default_install_root() / "OriginWardenPanel.exe"
        installer_script = self.base / "instalar_tarea.ps1"
        running_canonical = running == canonical.resolve()
        diag_lines = [
            f"Version del panel: {__version__}",
            f"Ejecutable: {running}",
            f"Carpeta de datos: {self.base}",
            f"Instalador: {installer_script} ({'presente' if installer_script.exists() else 'faltante'})",
            f"Copia canonica: {canonical}",
        ]
        diag_column: list[ft.Control] = [
            ft.Text("Diagnostico del panel", weight=ft.FontWeight.BOLD),
            ft.Text(
                "\n".join(diag_lines),
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
                selectable=True,
            ),
        ]
        if not running_canonical:
            diag_column.append(
                ft.Text(
                    "Aviso: estas ejecutando una copia fuera de la carpeta canonica. "
                    "Usa el acceso directo a %LOCALAPPDATA%\\OriginWarden\\OriginWardenPanel.exe.",
                    size=12,
                    color=AMBER,
                )
            )
        diag = card(ft.Column(diag_column, spacing=6))
        return ft.Column(
            [ft.Text("Instalacion y actualizacion", size=26, weight=ft.FontWeight.BOLD), body, remote, diag],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _build_manual(self) -> ft.Control:
        tiles = []
        for section in MANUAL:
            content: list[ft.Control] = [
                ft.Text(section["body"], size=13, selectable=True),
            ]
            for bullet in section.get("bullets", []):
                content.append(ft.Row([ft.Icon(ft.Icons.CIRCLE, size=6, color=ACCENT), ft.Text(bullet, size=12, expand=True)], spacing=8))
            for command in section.get("commands", []):
                content.append(
                    ft.TextField(
                        value=command,
                        read_only=True,
                        multiline=True,
                        min_lines=1,
                        max_lines=4,
                        text_style=ft.TextStyle(size=12),
                    )
                )
            tiles.append(
                ft.ExpansionTile(
                    title=ft.Text(section["title"], weight=ft.FontWeight.BOLD),
                    controls=[ft.Container(content=ft.Column(content, spacing=8), padding=ft.Padding.only(left=8, right=8, bottom=12))],
                )
            )
        return ft.Column(
            [
                ft.Text("Manual de uso y recuperacion", size=26, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "Contenido offline. Selecciona y copia los comandos que necesites.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Column(tiles, spacing=4, scroll=ft.ScrollMode.AUTO, expand=True),
            ],
            spacing=12,
            expand=True,
        )

    def _build_logs(self) -> ft.Control:
        self._log_field = ft.TextField(
            value="",
            multiline=True,
            min_lines=20,
            max_lines=30,
            read_only=True,
            text_style=ft.TextStyle(size=11),
            expand=True,
        )
        self._log_filter = ft.TextField(
            label="Filtrar por nivel (INFO, WARNING, ERROR)",
            dense=True,
            width=320,
        )
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("Registro del servicio", size=26, weight=ft.FontWeight.BOLD),
                        ft.Row(
                            [
                                self._log_filter,
                                ft.IconButton(
                                    icon=ft.Icons.REFRESH,
                                    tooltip="Actualizar",
                                    on_click=lambda _e: self._refresh_logs(),
                                ),
                                ft.TextButton(
                                    content="Abrir log",
                                    icon=ft.Icons.OPEN_IN_NEW,
                                    on_click=lambda _e: self._open_path("log"),
                                ),
                            ],
                            spacing=8,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                self._log_field,
            ],
            spacing=12,
            expand=True,
        )

    def _refresh_logs(self) -> None:
        if self._log_field is None:
            return
        level = (self._log_filter.value or "").strip() if self._log_filter else ""
        self._log_field.value = self.logs.tail(level=level or None)
        self.page.update()

    def _switch_changed(self, _e) -> None:
        target = bool(self.service_switch.value)
        self.page.run_task(self._toggle_service, target)

    async def _toggle_service(self, target: bool) -> None:
        if self.busy:
            return
        self.busy = True
        self.service_switch.disabled = True
        self.page.update()
        try:
            if target:
                ok, message = await asyncio.to_thread(self.tasks.turn_on)
            else:
                ok, message = await asyncio.to_thread(self.tasks.turn_off, True)
            self._snack(message, error=not ok)
        except Exception as exc:
            self._snack(f"Error controlando el servicio: {exc}", error=True)
        finally:
            self.busy = False
            self.service_switch.disabled = False
            self._refresh_status(force_task=True)

    async def _install_async(self) -> None:
        if self.busy:
            return
        try:
            config = self.config_service.load()
        except Exception as exc:
            self.install_status.value = f"Configura las rutas antes de instalar: {exc}"
            self._snack("Ve a Rutas y prepara la estructura.", error=True)
            self.page.update()
            return
        if not config.raiz_str or not config.origen_str or not config.destino_str:
            self.install_status.value = "Faltan rutas. Ve a la pestana Rutas."
            self.page.update()
            return
        self.busy = True
        self.install_status.value = "Verificando instalacion y preparando servicio..."
        self.page.update()
        try:
            audit = await asyncio.to_thread(self.installer.audit)
            if audit.installed:
                self.install_status.value = audit.message
                self._snack("Ya esta instalado. Usa Reparar o Actualizar.", error=True)
                return
            service_path = self.base / "OriginWardenService.exe"
            installer_path = self.base / "instalar_tarea.ps1"
            release = self._release_info
            self.install_status.value = "Descargando y verificando servicio desde GitHub..."
            self.page.update()
            try:
                release = release or await asyncio.to_thread(self.release.check)
                self._release_info = release
                service_path, installer_path = await asyncio.to_thread(
                    self.release.prepare_service, release
                )
            except Exception as exc:
                if not installer_path.exists() or not service_path.exists():
                    raise ReleaseError(
                        f"No se pudo obtener el servicio desde GitHub: {exc}"
                    ) from exc
                self.install_status.value = (
                    "No se pudo consultar GitHub; se usaran los archivos locales."
                )
                self.page.update()
            service_target = self.base / "OriginWardenService.exe"
            if service_path.exists():
                if service_path.resolve() != service_target.resolve():
                    shutil.copy2(service_path, service_target)
            else:
                raise ReleaseError(f"No se encontro el ejecutable del servicio: {service_path}")
            service_arg = service_target
            self.install_status.value = "Instalando servicio. Confirma UAC si aparece."
            self.page.update()
            ok, message = await asyncio.to_thread(
                self.installer.install,
                self.config_service.path,
                True,
                True,
                service_arg,
                release.version if release else None,
            )
            self.install_status.value = message
            self._snack("Instalacion completada." if ok else message, error=not ok)
        except Exception as exc:
            self.install_status.value = str(exc)
            self._snack(str(exc), error=True)
        finally:
            self.busy = False
            self.page.update()
            self._refresh_status(force_task=True)

    def _repair_clicked(self, _e) -> None:
        self.page.run_task(self._repair_async)

    async def _repair_async(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.install_status.value = "Reparando tarea y ejecutable del servicio..."
        self.page.update()
        try:
            release = self._release_info
            service_path = self.base / "OriginWardenService.exe"
            installer_path = self.base / "instalar_tarea.ps1"
            try:
                release = release or await asyncio.to_thread(self.release.check)
                self._release_info = release
                service_path, installer_path = await asyncio.to_thread(
                    self.release.prepare_service, release
                )
            except Exception:
                if not installer_path.exists() or not service_path.exists():
                    raise ReleaseError(
                        "No se pudo obtener el servicio desde GitHub y no hay archivos locales."
                    )
            service_target = self.base / "OriginWardenService.exe"
            if service_path.exists():
                if service_path.resolve() != service_target.resolve():
                    shutil.copy2(service_path, service_target)
            receipt = self.installer.receipt() or {}
            version = release.version if release else str(receipt.get("version") or __version__)
            ok, message = await asyncio.to_thread(
                self.installer.repair,
                self.config_service.path,
                service_target if service_target.exists() else None,
                version,
            )
            self.install_status.value = message
            self._snack(message, error=not ok)
        except Exception as exc:
            self.install_status.value = str(exc)
            self._snack(str(exc), error=True)
        finally:
            self.busy = False
            self.page.update()
            self._refresh_status(force_task=True)

    def _check_update_clicked(self, _e) -> None:
        self.page.run_task(self._check_update_async, True)

    async def _auto_update_check(self) -> None:
        await asyncio.sleep(3)
        try:
            await self._check_update_async(False)
        except Exception:
            pass

    async def _check_update_async(self, show_snack: bool = True) -> None:
        try:
            release = await asyncio.to_thread(self.release.check)
            self._release_info = release
            audit = await asyncio.to_thread(self.installer.audit)
            receipt = self.installer.receipt() or {}
            installed_version = str(receipt.get("version") or "0.0.0")
            service_newer = (
                audit.status == "installed_ok"
                and bool(receipt)
                and version_newer(release.version, installed_version)
            )
            panel_newer = release.panel_newer
            self._update_available = service_newer or panel_newer
            parts = [f"Ultima version publicada: {release.version}"]
            if panel_newer:
                parts.append("hay una actualizacion del panel")
            if service_newer:
                parts.append("hay una actualizacion del servicio")
            if not panel_newer and not service_newer:
                parts.append("estas al dia")
            message = " | ".join(parts)
            if hasattr(self, "update_status"):
                self.update_status.value = message
                self.update_status.color = AMBER if self._update_available else GREEN
            if hasattr(self, "_update_button"):
                self._update_button.visible = self._update_available
                self._update_button.content = "Actualizar ahora"
            if show_snack:
                self._snack(message, error=False)
            self.page.update()
        except Exception as exc:
            self._update_available = False
            if hasattr(self, "update_status"):
                self.update_status.value = f"No se pudo consultar GitHub: {exc}"
                self.update_status.color = AMBER
            if show_snack:
                self._snack(str(exc), error=True)
            self.page.update()

    def _update_clicked(self, _e) -> None:
        if not self._release_info:
            self.page.run_task(self._check_update_async, True)
            return
        release = self._release_info
        dialog = ft.AlertDialog(
            title=ft.Text(f"Actualizar a {release.version}"),
            content=ft.Text(
                "Se descargara la version publicada, se verificara su SHA-256 y se aplicara. "
                "El servicio puede detenerse unos segundos. El panel puede reiniciarse al finalizar."
            ),
            actions=[
                ft.TextButton(content="Cancelar", on_click=lambda _e: self.page.pop_dialog()),
                ft.FilledButton(content="Actualizar ahora", on_click=self._confirm_update),
            ],
        )
        self.page.show_dialog(dialog)

    def _confirm_update(self, _e) -> None:
        self.page.pop_dialog()
        self.page.run_task(self._update_async)

    async def _update_async(self) -> None:
        if self.busy:
            return
        release = self._release_info
        if not release:
            self._snack("Primero busca actualizaciones.", error=True)
            return
        self.busy = True
        if hasattr(self, "update_status"):
            self.update_status.value = "Descargando y verificando actualizacion..."
        self.page.update()
        try:
            audit = await asyncio.to_thread(self.installer.audit)
            receipt = self.installer.receipt() or {}
            installed_version = str(receipt.get("version") or "0.0.0")
            service_updated = False
            if (
                audit.status == "installed_ok"
                and bool(receipt)
                and version_newer(release.version, installed_version)
            ):
                service_path, _installer_path = await asyncio.to_thread(
                    self.release.prepare_service, release
                )
                ok, message = await asyncio.to_thread(
                    self.installer.update,
                    self.config_service.path,
                    service_path,
                    release.version,
                )
                if not ok:
                    raise ReleaseError(message)
                service_updated = True
            if release.panel_newer:
                new_panel = await asyncio.to_thread(self.release.download_panel, release)
                await asyncio.to_thread(self.release.apply_panel_update, new_panel)
                self._snack("Panel actualizado. Reiniciando...")
                await self.page.window.close()
                os._exit(0)
            if hasattr(self, "update_status"):
                self.update_status.value = (
                    "Servicio actualizado correctamente." if service_updated else "No habia actualizacion aplicable."
                )
            self._update_available = False
            if hasattr(self, "_update_button"):
                self._update_button.visible = False
            self._snack("Actualizacion completada.")
        except Exception as exc:
            if hasattr(self, "update_status"):
                self.update_status.value = str(exc)
            self._snack(str(exc), error=True)
        finally:
            self.busy = False
            self.page.update()
            self._refresh_status(force_task=True)

    def _install_clicked(self, _e) -> None:
        self.page.run_task(self._install_async)

    def _arm_clicked(self, _e) -> None:
        self.page.run_task(self._arm_async)

    async def _arm_async(self) -> None:
        try:
            await asyncio.to_thread(self.config_service.arm, False)
            self._snack("Sistema armado. Reiniciando servicio.")
            await asyncio.to_thread(self.tasks.restart)
        except Exception as exc:
            self._snack(f"No se pudo armar: {exc}", error=True)
        self._refresh_status(force_task=True)

    def _approve_clicked(self, _e) -> None:
        try:
            ok = self.config_service.approve()
            self._snack("Lote aprobado." if ok else "No hay aprobacion pendiente.", error=not ok)
        except Exception as exc:
            self._snack(str(exc), error=True)
        self._refresh_status(force_task=True)

    def _uninstall_clicked(self, _e) -> None:
        self.page.run_task(self._uninstall_async)

    async def _uninstall_async(self) -> None:
        self.install_status.value = "Desinstalando tarea. Los archivos no se borran."
        self.page.update()
        ok, message = await asyncio.to_thread(self.installer.uninstall)
        self.install_status.value = message
        self._snack(message, error=not ok)
        self._refresh_status(force_task=True)

    def _test_paths_clicked(self, _e) -> None:
        try:
            ok, message = self.config_service.test_paths()
            self._settings_error.value = message
            self._settings_error.color = GREEN if ok else RED
        except Exception as exc:
            self._settings_error.value = str(exc)
            self._settings_error.color = RED
        self.page.update()

    def _collect_settings(self) -> dict[str, Any]:
        values = {key: field.value for key, field in self._settings_fields.items()}
        values["papelera_activa"] = bool(self._papelera_switch.value)
        values["requerir_armado"] = bool(self._armado_switch.value)
        return values

    def _save_common(self) -> bool:
        try:
            self.config_service.save_fields(self._collect_settings())
            self._settings_error.value = "Configuracion guardada. Reinicia el servicio para aplicarla."
            self._settings_error.color = GREEN
            return True
        except Exception as exc:
            self._settings_error.value = str(exc)
            self._settings_error.color = RED
            return False
        finally:
            self.page.update()

    def _save_settings_clicked(self, _e) -> None:
        self._save_common()
        self._snack("Configuracion guardada.", error=False)

    def _save_restart_clicked(self, _e) -> None:
        if not self._save_common():
            return
        self.page.run_task(self._restart_async)

    async def _restart_async(self) -> None:
        ok, message = await asyncio.to_thread(self.tasks.restart)
        self._snack(message, error=not ok)
        self._refresh_status(force_task=True)

    def _open_path(self, kind: str) -> None:
        try:
            config = self.config_service.load()
            if kind == "raiz":
                target = Path(str(config.raiz))
            elif kind == "origen":
                target = Path(str(config.origen))
            elif kind == "destino":
                target = Path(str(config.destino))
            elif kind == "aportes":
                target = Path(str(config.intake_root))
            else:
                target = config.log_path
            if target.exists():
                import os
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                self._snack(f"No existe: {target}", error=True)
        except Exception as exc:
            self._snack(str(exc), error=True)

    def _snack(self, message: str, error: bool = False) -> None:
        try:
            self.page.show_dialog(
                ft.SnackBar(
                    content=ft.Text(message),
                    bgcolor=RED if error else ft.Colors.INVERSE_SURFACE,
                )
            )
        except Exception:
            pass

    def _refresh_status(self, force_task: bool = False) -> None:
        if self._current != "panel":
            return
        status = self.status_reader.read()
        now = time.monotonic()
        if force_task or self._task_cache is None or now - self._task_cache_at > 10:
            self._task_cache = self.tasks.get_state()
            self._task_cache_at = now
        task = self._task_cache
        if not task.exists:
            state_text, color = "No instalado", GREY
            self.service_switch.value = False
            self.auto_text.value = "Autoarranque: no disponible"
        elif task.running and status and not status.get("stale"):
            mode = str(status.get("mode") or "normal")
            if mode == "simulation":
                state_text, color = "Simulacion", AMBER
            elif mode in {"safe", "degraded"}:
                state_text, color = "Modo seguro", AMBER
            elif mode == "normal":
                state_text, color = "Activo", GREEN
            else:
                state_text, color = mode.title(), AMBER
            self.service_switch.value = True
            self.auto_text.value = "Autoarranque: habilitado" if not task.disabled else "Autoarranque: deshabilitado"
        elif task.running and (not status or status.get("stale")):
            state_text, color = "Sin respuesta", RED
            self.service_switch.value = True
            self.auto_text.value = "Heartbeat vencido"
        else:
            state_text, color = "Detenido", GREY
            self.service_switch.value = False
            self.auto_text.value = "Autoarranque: deshabilitado" if task.disabled else "Autoarranque: listo"
        self.status_dot.bgcolor = color
        self.status_text.value = state_text
        self.status_text.color = color
        self.status_chip.bgcolor = ft.Colors.SURFACE_CONTAINER_HIGH

        if status:
            mode = str(status.get("mode") or "desconocido")
            self.status_sub.value = f"Modo: {mode} | PID {status.get('pid', '-')}"
            self.paths_text.value = (
                f"Origen: {status.get('origen', '-')}\n"
                f"Destino: {status.get('destino', '-')}"
            )
            hb = status.get("heartbeat", "-")
            self.heartbeat_text.value = str(hb)
            self.mode_text.value = f"Modo: {mode}"
            counters = status.get("counters") or {}
            self.counters_text.value = (
                f"Copias: {counters.get('copies', 0)} | "
                f"Ingestas: {counters.get('ingests', 0)} | "
                f"Borrados: {counters.get('deletes', 0)} | "
                f"Errores: {counters.get('errors', 0)}"
            )
            trash = status.get("trash") or {}
            self.task_text.value = (
                f"Dead-letter: {status.get('dead_letter', 0)} | "
                f"Papelera: {trash.get('files', 0)} archivos"
            )
            armed = bool(status.get("armed"))
            self.armed_badge.value = "Armado" if armed else "NO armado"
            self.armed_badge.color = GREEN if armed else AMBER
            error = status.get("last_error")
            self.status_last_error.value = f"Ultimo error: {error}" if error else ""
            stale = bool(status.get("stale"))
            self.stale_banner.visible = stale
            self.stale_banner.content = ft.Row(
                [
                    ft.Icon(ft.Icons.CLOUD_OFF, color=RED),
                    ft.Text("Sin heartbeat reciente. Revisa el registro o reinicia el servicio.", color=RED, size=12),
                ]
            )
            request = status.get("pending_approval")
        else:
            request = None
            self.status_sub.value = "No hay estado del servicio."
            self.paths_text.value = ""
            self.heartbeat_text.value = "-"
            self.mode_text.value = "Modo: -"
            self.counters_text.value = "Sin datos"
            self.task_text.value = ""
            self.armed_badge.value = ""
            self.status_last_error.value = ""
            self.stale_banner.visible = False

        alerts: list[str] = []
        if not task.exists:
            alerts.append("La tarea no esta instalada. Usa Instalacion para registrarla.")
        elif not task.running:
            alerts.append("El servicio esta detenido: no se replican ni restauran archivos.")
        if status and status.get("mode") == "simulation":
            alerts.append("Modo simulacion: revisa el plan y pulsa Armar para activar cambios reales.")
        if status and status.get("mode") in {"safe", "degraded"}:
            alerts.append("Modo seguro o degradado: se pausaron operaciones destructivas. Revisa el manual.")
        if status and not status.get("armed"):
            alerts.append("El sistema no esta armado. La sincronizacion real esta bloqueada.")
        if status and status.get("stale") and task.running:
            alerts.append("El proceso figura en ejecucion pero no responde. Reinicia el servicio.")
        if self._update_available:
            alerts.append("Hay una actualizacion disponible. Abre Instalacion y pulsa Actualizar.")
        if request:
            kind = request.get("kind", "operacion")
            count = request.get("count", "?")
            self.pending_card.visible = True
            self.pending_text.value = f"Lote de {kind}: {count} elementos. Revisa el registro antes de aprobar."
        else:
            self.pending_card.visible = False
        if alerts:
            self.alert.visible = True
            alert_text = self.alert.content.controls[1]
            alert_text.value = " ".join(alerts)
            if not task.exists:
                self.alert_button.content = "Instalar servicio"
                self.alert_button.icon = ft.Icons.DOWNLOAD
                self.alert_button.on_click = lambda _e: self._select("install")
            elif self._update_available:
                self.alert_button.content = "Ver actualizacion"
                self.alert_button.icon = ft.Icons.SYSTEM_UPDATE
                self.alert_button.on_click = lambda _e: self._select("install")
            else:
                self.alert_button.content = "Abrir manual de recuperacion"
                self.alert_button.icon = ft.Icons.MENU_BOOK_OUTLINED
                self.alert_button.on_click = lambda _e: self._select("manual")
        else:
            self.alert.visible = False
        self.page.update()

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(2.0)
                self._refresh_status()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5.0)


def main(page: ft.Page) -> None:
    PanelApp(page)


if __name__ == "__main__":
    ft.run(main)
