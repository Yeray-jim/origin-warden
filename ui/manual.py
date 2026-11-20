"""Contenido offline del manual del panel."""

from __future__ import annotations

MANUAL = [
    {
        "title": "1. Que hace el sistema",
        "body": (
            "El origen normal es la fuente de verdad y el servicio nunca lo modifica. "
            "Los cambios del origen se copian al destino. Los borrados y renombrados del origen "
            "tambien se reflejan en el destino. Si un usuario modifica o borra una ruta ya gestionada "
            "en el destino, el servicio la revierte o la restaura desde el origen. "
            "Si un usuario sube una ruta nueva al destino, se guarda en la carpeta de aportes del origen. "
            "La subcarpeta de aportes es administrada por el anfitrion."
        ),
        "bullets": [
            "El servicio no escribe ni borra en el origen normal.",
            "El destino es publico, pero solo persisten las altas nuevas.",
            "La restauracion de un borrado en destino comienza de inmediato o en el siguiente barrido.",
            "El panel no es el servicio: cerrar el panel no detiene la sincronizacion.",
        ],
        "commands": [],
    },
    {
        "title": "2. Requisitos",
        "body": (
            "Windows 10 u 11 con PowerShell 5.1 o superior. Para el modo fuente se necesita Python 3.10 o "
            "superior; para el paquete compilado no se necesita Python. La cuenta que ejecuta el servicio "
            "debe tener lectura sobre el origen, escritura sobre los aportes y escritura sobre el destino. "
            "Para SMB se recomienda usar rutas UNC y una cuenta con permisos en el servidor."
        ),
        "bullets": [
            "Origen protegido: propietario, SYSTEM y administradores.",
            "Destino: usuarios con permiso Modify si deben subir archivos.",
            "El recurso compartido debe permitir Change a los usuarios del destino.",
            "No uses unidades mapeadas en una tarea de arranque; usa \\\\servidor\\recurso.",
        ],
        "commands": [
            "Test-Path 'C:\\CarpetaCompartida\\Mis_Originales'",
            "Test-Path '\\\\servidor\\recurso\\Publico_Usuarios'",
        ],
    },
    {
        "title": "3. Instalacion automatica",
        "body": (
            "Abre la pestana Instalacion. Verifica las rutas, guarda la configuracion y pulsa "
            "Instalar o reparar. El asistente solicitara elevacion para aplicar permisos locales y "
            "registrar la tarea. Si la ruta es UNC, la ACL remota debe aplicarse en el servidor; el "
            "instalador mostrara los comandos y no intentara modificarla desde el cliente."
        ),
        "bullets": [
            "La primera ejecucion se hace en modo simulacion.",
            "Revisa el resumen y pulsa Armar para permitir cambios reales.",
            "El instalador guarda respaldos de configuracion, ACL y tarea.",
            "Si UAC se deniega, no se aplican cambios parciales.",
        ],
        "commands": [
            "powershell -ExecutionPolicy Bypass -File .\\instalar_tarea.ps1 -Action Install -ApplyPermissions -StartService",
        ],
    },
    {
        "title": "4. Permisos NTFS y recurso compartido",
        "body": (
            "icacls solo cambia permisos NTFS. Los permisos del recurso compartido se ajustan aparte. "
            "Conserva SYSTEM y Administradores para no bloquear la recuperacion. Ejecuta los comandos "
            "en el servidor si las carpetas son remotas."
        ),
        "bullets": [
            "Origen: propietario, SYSTEM y Administradores con control total.",
            "Aportes: hereda la ACL del origen; no agregues usuarios publicos.",
            "Destino: Usuarios con Modify, SYSTEM y Administradores con control total.",
        ],
        "commands": [
            "icacls \"C:\\CarpetaCompartida\\Mis_Originales\" /inheritance:r /grant:r \"$env:USERDOMAIN\\$env:USERNAME:(OI)(CI)F\" \"*S-1-5-18:(OI)(CI)F\" \"*S-1-5-32-544:(OI)(CI)F\" /T /C /Q",
            "icacls \"\\\\servidor\\recurso\\Publico_Usuarios\" /inheritance:r /grant:r \"*S-1-5-32-545:(OI)(CI)M\" \"*S-1-5-18:(OI)(CI)F\" \"*S-1-5-32-544:(OI)(CI)F\" /T /C /Q",
            "Grant-SmbShareAccess -Name \"CarpetaCompartida\" -AccountName \"Usuarios\" -AccessRight Change -Force",
        ],
    },
    {
        "title": "5. Subidas de usuarios y aportes",
        "body": (
            "Cuando un usuario crea una ruta nueva en el destino, el servicio la copia a "
            "ORIGEN\\Aportes_Usuarios. El mapeo hace que el archivo publico siga correspondiendo a esa "
            "ruta. Si el anfitrion edita un aporte, el cambio se refleja en el destino. Si lo borra, "
            "el archivo publico desaparece. Si el destino ya tenia una version de esa ruta, no se "
            "ingesta: se considera una modificacion y se revierte."
        ),
        "bullets": [
            "Los conflictos se guardan en Aportes_Usuarios\\_Conflictos con fecha y hora.",
            "Los aportes demasiado grandes se omiten y se registran.",
            "Si hay muchas altas seguidas, la ingesta se pausa para aprobacion.",
            "El anfitrion puede revisar la carpeta de aportes directamente en el origen.",
        ],
        "commands": [],
    },
    {
        "title": "6. Restauracion y borrados",
        "body": (
            "Si un usuario borra una ruta gestionada en el destino, el servicio la restaura desde el "
            "origen o desde aportes. El evento se atiende de inmediato; el barrido de seguridad "
            "garantiza el inicio de la restauracion dentro del plazo configurado, por defecto 60 minutos. "
            "Si el origen tambien borro la ruta, no se restaura: la ausencia se propaga."
        ),
        "bullets": [
            "El plazo es de inicio de restauracion; la copia completa depende del tamano y la red.",
            "Si el origen no esta disponible, la restauracion queda pendiente.",
            "Los borrados del origen pasan por una ventana de gracia y una papelera interna.",
            "Si la papelera no esta disponible, no se borra y se alerta.",
        ],
        "commands": [],
    },
    {
        "title": "7. Modo seguro, aprobaciones y papelera",
        "body": (
            "El servicio entra en modo seguro si la identidad no coincide, si falla repetidamente o si "
            "no puede verificar permisos. En modo seguro sigue vivo y escribe estado, pero no modifica "
            "archivos. Las operaciones masivas se pausan y esperan aprobacion en el panel. "
            "La papelera interna guarda temporalmente las copias retiradas del destino."
        ),
        "bullets": [
            "Simulacion: no escribe ni borra; solo muestra el plan en el registro.",
            "Degradado: no borra ni ingesta; mantiene salud y reintentos.",
            "Aprobacion pendiente: las operaciones masivas esperan confirmacion.",
            "Papelera: revisar antes de vaciarla; el anfitrion decide.",
        ],
        "commands": [
            "python -m syncsafe --status",
            "python -m syncsafe --approve",
            "python -m syncsafe --arm",
        ],
    },
    {
        "title": "8. Problemas frecuentes",
        "body": (
            "Revisa primero el estado y el registro. Si la tarea no arranca, comprueba la cuenta, "
            "la ruta UNC y la contrasena de la cuenta de servicio. Si aparece acceso denegado, revisa "
            "la ACL y usa la reparacion. Si no hay heartbeat, reinicia el servicio. Si el servicio esta "
            "en modo seguro, corrige la causa y vuelve a armar."
        ),
        "bullets": [
            "La tarea figura en ejecucion pero el panel no responde: posible cuelgue; reinicia.",
            "Un archivo queda bloqueado: el servicio reintenta y puede enviarlo a dead-letter.",
            "El destino esta lleno: el servicio pausa operaciones que necesitan espacio.",
            "Un usuario borro el marcador de destino: el servicio entra en modo seguro.",
            "Si hay una aprobacion pendiente, revisa el lote y aprueba o rechaza.",
        ],
        "commands": [
            "Get-ScheduledTaskInfo -TaskName OriginWardenService",
            "icacls \"C:\\CarpetaCompartida\\Mis_Originales\"",
            "Get-Content .\\logs\\sync.log -Tail 80",
        ],
    },
    {
        "title": "9. Apagar, reiniciar y desinstalar",
        "body": (
            "Apagar el servicio detiene la sincronizacion y las restauraciones. El panel intenta una "
            "parada ordenada y, si no responde, usa Task Scheduler. Cerrar el panel no apaga el servicio. "
            "Desinstalar la tarea no borra archivos del origen, destino ni aportes."
        ),
        "bullets": [
            "Apagar no borra archivos.",
            "El autoarranque se controla aparte del estado actual.",
            "Antes de desinstalar, deten el servicio y guarda una copia de la configuracion.",
            "La papelera y los aportes son datos del anfitrion: no se eliminan automaticamente.",
        ],
        "commands": [
            "powershell -ExecutionPolicy Bypass -File .\\instalar_tarea.ps1 -Action Uninstall",
        ],
    },
]
