"""Panel de control Flet del Origin Warden."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import flet as ft

from syncsafe import APP_NAME, __version__
from ui.manual import MANUAL
from ui.services import (
    ConfigService,
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
        self.status_reader = StatusReader(self.base)
        self.tasks = TaskController(runtime_dir=self.base / "runtime")
        self.config_service = ConfigService(self.base)
        self.installer = InstallerService(self.base)
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
        self._build_shell()
        self._select("panel")
        self.page.run_task(self._poll_loop)

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
                "origen": config.origen_str,
                "destino": config.destino_str,
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
                "origen", "destino", "aportes", "modo_origen", "modo_destino",
                "intervalo_min", "restauracion_max_min", "trabajadores",
                "debounce_seg", "gracia_borrado_seg", "max_aporte_mb", "nivel_log"
            )}
        specs = [
            ("origen", "Origen protegido", "C:\\CarpetaCompartida\\Mis_Originales"),
            ("destino", "Destino publico", "\\\\servidor\\recurso\\Publico_Usuarios"),
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
                    fields["origen"],
                    fields["destino"],
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
        steps = [
            ("1", "Requisitos", "Rutas, PowerShell, permisos de escritura y runtime."),
            ("2", "Identidad", "Marcadores de origen y destino para evitar rutas equivocadas."),
            ("3", "Permisos", "ACL locales automatizadas; ACL remotas se ejecutan en el servidor."),
            ("4", "Tarea", "Registro en Task Scheduler y arranque."),
            ("5", "Prueba", "Heartbeat, modo del servicio y acceso a rutas."),
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
                    ft.Column([ft.Text(title, weight=ft.FontWeight.BOLD), ft.Text(text, size=12, color=ft.Colors.ON_SURFACE_VARIANT)], spacing=2),
                ],
                spacing=12,
            )
            for number, title, text in steps
        ]
        self.install_status = ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT, selectable=True)
        body = card(
            ft.Column(
                [
                    ft.Text("Instalacion y reparacion", size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "El asistente valida la configuracion, crea la carpeta de aportes, aplica permisos locales y registra la tarea. "
                        "Si UAC se deniega, no se aplican cambios parciales.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    ft.Divider(height=12, color=ft.Colors.OUTLINE_VARIANT),
                    *step_rows,
                    ft.Divider(height=12, color=ft.Colors.OUTLINE_VARIANT),
                    ft.Row(
                        [
                            ft.FilledButton(
                                content="Instalar o reparar",
                                icon=ft.Icons.BUILD,
                                on_click=self._install_clicked,
                            ),
                            ft.OutlinedButton(
                                content="Armar sistema",
                                icon=ft.Icons.LOCK_OPEN,
                                on_click=self._arm_clicked,
                            ),
                            ft.OutlinedButton(
                                content="Desinstalar tarea",
                                icon=ft.Icons.DELETE_OUTLINE,
                                on_click=self._uninstall_clicked,
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                    self.install_status,
                ],
                spacing=12,
            )
        )
        remote = card(
            ft.Column(
                [
                    ft.Text("Permisos en SMB remoto", weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Si el origen o el destino estan en otro servidor, ejecuta los comandos del manual en ese servidor. "
                        "El instalador no modifica ACL remotas desde el cliente.",
                        size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ],
                spacing=6,
            )
        )
        return ft.Column(
            [ft.Text("Instalacion", size=26, weight=ft.FontWeight.BOLD), body, remote],
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
        self.busy = True
        self.install_status.value = "Ejecutando instalador. Si aparece UAC, confirma los permisos."
        self.page.update()
        try:
            ok, message = await asyncio.to_thread(
                self.installer.install,
                self.config_service.path,
                True,
                True,
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
            if kind == "origen":
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
