# Origin Warden

Version: **0.3.0**

Repositorio: [github.com/Yeray-jim/origin-warden](https://github.com/Yeray-jim/origin-warden)

Espejo unidireccional protegido para carpetas compartidas de Windows y SMB. Mantiene una copia pública actualizada sin exponer los originales, permite que los usuarios suban archivos nuevos a un buzón controlado y restaura automáticamente los borrados accidentales de la copia.

El servicio corre sin consola y el panel Flet es opcional. El panel no ejecuta la sincronización: solo consulta estado y controla la tarea programada.

## Razón de ser

En muchas carpetas compartidas conviven dos necesidades opuestas: el propietario necesita conservar los originales intactos y los usuarios necesitan una copia accesible donde trabajar. Las herramientas de sincronización tradicionales suelen ser bidireccionales, no distinguen entre un borrado accidental y uno intencional, o no ofrecen un lugar controlado para recibir archivos nuevos.

Origin Warden existe para resolver ese problema con una regla explícita:

- El origen normal es la fuente de verdad y el servicio nunca lo modifica ni lo borra.
- El destino es una proyección pública que se puede reconstruir y corregir.
- Las altas nuevas del destino se ingestan en `Aportes_Usuarios`, una zona administrada por el anfitrión.
- Los borrados y modificaciones no autorizados del destino se revierten o restauran.
- Las operaciones destructivas pasan por simulación, armado, papelera y guardias de lote.

El objetivo no es sustituir un backup offline. El objetivo es evitar que un error humano, un borrado accidental, un archivo bloqueado o una subida descontrolada rompan el flujo de trabajo diario sin tocar los originales.

## Casos de uso

### 1. Carpeta compartida de documentación

Una organización mantiene manuales, contratos y procedimientos en una carpeta protegida. Los usuarios consultan y descargan una copia pública. Si alguien borra un manual por error, Origin Warden lo restaura desde el original.

### 2. Publicación de diseños, planos o entregables

El equipo de diseño conserva los archivos maestros en `Mis_Originales`. Los clientes o compañeros reciben una copia pública. Los nuevos archivos que suben los usuarios se guardan en la carpeta de aportes para que el anfitrión los revise y administre.

### 3. Servidor SMB departamental

Un servidor Windows comparte una carpeta con usuarios de varias áreas. Office, antivirus y la red bloquean archivos de forma temporal. Origin Warden reintenta, conserva metadatos y evita publicar copias parciales.

### 4. Material educativo o biblioteca

El docente o bibliotecario administra el origen. Los estudiantes leen y descargan la copia pública. Si un estudiante borra un recurso, se restaura; si sube uno nuevo, queda en el buzón de aportes y no modifica el árbol original.

### 5. Sucursal con origen local y destino en red

El equipo principal trabaja sobre un disco local o una carpeta NTFS. La copia se publica en un recurso UNC. La detección automática elige observador nativo para el origen local y polling para el destino SMB.

### 6. Protección frente a borrados o cifrado accidental en la copia

Si un usuario o un proceso cifra, renombra o elimina archivos en la copia pública, el servicio revierte los cambios gestionados y restaura desde el origen o desde los aportes. Las altas masivas se pausan para aprobación.

### 7. Buzón de aportes con administración central

Los usuarios suben archivos nuevos a la copia pública. El anfitrión los revisa en `ORIGEN\Aportes_Usuarios`, los edita, los mueve o los elimina. Los cambios del anfitrión se reflejan en la copia pública.

### 8. Validación y despliegue controlado

Antes de permitir cambios reales, el servicio arranca en simulación. El anfitrión revisa el plan, arma la identidad y solo entonces habilita copias, ingestas y borrados.

## Qué hace

- Copia de forma recursiva desde `ORIGEN` hacia `DESTINO`.
- Propaga altas, modificaciones, renombrados y borrados del origen.
- Restaura en destino los archivos gestionados que un usuario borre.
- Revierte cambios sobre rutas ya gestionadas en destino.
- Ingesta las rutas nuevas creadas por usuarios en `ORIGEN\Aportes_Usuarios`.
- Mantiene una papelera interna antes de retirar copias del destino.
- Usa modo simulacion y armado inicial para evitar rutas equivocadas.
- Pausa operaciones masivas y solicita aprobacion.
- Reintenta bloqueos de Office, antivirus y SMB.
- Registra actividad en `logs/sync.log` con rotacion.

## Que no hace

- No modifica ni borra archivos del origen normal.
- No ejecuta los archivos subidos por usuarios.
- No reemplaza un backup offline ni protege frente a una cuenta de administrador comprometida.
- No copia ACL de NTFS desde el origen al destino.
- No copia flujos alternativos de NTFS, enlaces duros ni datos de EFS.

## Estructura

```text
origin-warden/
├── sincronizador.pyw
├── panel.pyw
├── config.json
├── requirements.txt
├── requirements-dev.txt
├── instalar_tarea.ps1
├── empaquetar.ps1
├── syncsafe/
├── ui/
├── tests/
├── logs/
└── runtime/
```

## Requisitos

- Windows 10 u 11.
- PowerShell 5.1 o superior.
- Python 3.10 o superior para ejecutar desde fuente.
- Acceso de lectura a `ORIGEN`.
- Escritura en `ORIGEN\Aportes_Usuarios`.
- Escritura en `DESTINO`.
- Cuenta con permisos SMB si las rutas son UNC.

El paquete compilado no necesita Python instalado.

## Instalacion desde fuente

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Edita `config.json` con tus rutas reales.

Valida la configuracion:

```powershell
python -m syncsafe --check-config
```

Registra la tarea y aplica permisos locales:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar_tarea.ps1 `
  -Action Install -ApplyPermissions -StartService
```

Abre el panel, revisa el modo simulacion y pulsa **Armar sistema**. Tambien puedes armarlo por CLI:

```powershell
python -m syncsafe --arm
```

```powershell
python panel.pyw
```

Para depurar con consola:

```powershell
python -m syncsafe --debug
```

Pruebas unitarias del dominio y la configuracion:

```powershell
python -m unittest discover -s tests -v
```

## Instalacion con paquete compilado

```powershell
.\empaquetar.ps1
```

El resultado queda en `dist\paquete`:

```text
dist\paquete\
├── AbrirPanel.bat
├── config.json
├── instalar_tarea.ps1
├── README.md
├── OriginWardenPanel\
└── OriginWardenService\
```

Edita `config.json` y ejecuta `AbrirPanel.bat`. Desde la pestana Instalacion puedes registrar la tarea, aplicar permisos locales y armar el sistema.

El panel Flet usa el runtime de Flutter y consume mas memoria mientras esta abierto. El servicio headless no incluye Flet y es el unico proceso que queda activo cuando cierras el panel.

## Primer arranque

1. La configuracion se valida y se crea la estructura de aportes.
2. Si el sistema no esta armado, arranca en **simulacion**.
3. El panel muestra el estado y el registro.
4. Pulsa **Armar sistema** o ejecuta `python -m syncsafe --arm`.
5. El servicio se reinicia y comienza a aplicar cambios reales.

En simulacion el servicio lee, calcula y registra, pero no copia, no borra y no ingesta.

## Uso del panel

El panel tiene cinco secciones:

- **Panel:** estado, interruptor del servicio, autoarranque, rutas, modo, guardias, aprobaciones y acciones rapidas.
- **Configuracion:** rutas, observadores, intervalos, papelera, limites y nivel de log.
- **Instalacion:** registro de tarea, permisos locales, armado y desinstalacion.
- **Manual:** guia offline con pasos, comandos y recuperacion.
- **Registro:** ultimas lineas de `logs/sync.log`.

El interruptor **Servicio**:

- Encender: habilita la tarea y la inicia.
- Apagar: solicita parada ordenada, detiene la tarea y deshabilita el autoarranque.
- Cerrar el panel no detiene el servicio.

## Carpetas y comportamiento

```text
ORIGEN\archivo.txt                  <-->  DESTINO\archivo.txt
ORIGEN\Aportes_Usuarios\archivo.txt <-->  DESTINO\archivo.txt
```

Prioridad de resolucion:

1. Archivo del origen normal.
2. Archivo de aportes.
3. Archivo desconocido en destino: se ingesta.
4. Sin fuente: se retira la copia del destino.

La carpeta de aportes no se muestra como `DESTINO\Aportes_Usuarios`; su contenido se mapea a la ruta publica correspondiente.

### Subidas de usuarios

Si un usuario crea `DESTINO\docs\nuevo.pdf`, el servicio copia el archivo a:

```text
ORIGEN\Aportes_Usuarios\docs\nuevo.pdf
```

El archivo publico permanece en su ruta. Si el anfitrion edita el aporte, cambia la copia publica. Si lo borra, la copia publica desaparece.

### Conflictos

Un conflicto se guarda en:

```text
ORIGEN\Aportes_Usuarios\_Conflictos\<fecha_hora>\<ruta>
```

La carpeta `_Conflictos` y `_Papelera` no se mapean al destino.

### Restauracion

Si se borra una ruta gestionada en destino, el servicio la restaura desde el origen o desde aportes. El evento se atiende de inmediato; el barrido de seguridad se ejecuta cada 20 minutos por defecto. El plazo maximo de inicio es 60 minutos.

Si el origen tambien elimino la ruta, no se restaura. La ausencia del origen gana.

### Papelera

Antes de retirar una copia del destino por un borrado en origen, el servicio intenta moverla a:

```text
ORIGEN\Aportes_Usuarios\_Papelera\<fecha_hora>\<ruta>
```

Si la papelera no esta disponible, no se borra. La retencion por defecto es de 7 dias.

## Configuracion

Las claves principales de `config.json`:

```json
{
  "ORIGEN": "C:\\CarpetaCompartida\\Mis_Originales",
  "DESTINO": "\\\\servidor\\recurso\\Publico_Usuarios",
  "APORTES": {
    "subcarpeta": "Aportes_Usuarios"
  },
  "OBSERVACION": {
    "modo_origen": "auto",
    "modo_destino": "auto",
    "intervalo_polling_seg": 5.0
  },
  "RECONCILIACION": {
    "intervalo_min": 20,
    "restauracion_max_min": 60,
    "trabajadores": 2,
    "gracia_borrado_seg": 60
  },
  "SEGURIDAD": {
    "requerir_armado": true,
    "papelera_activa": true,
    "max_aporte_mb": 2048
  }
}
```

- `modo_origen` y `modo_destino`: `auto`, `nativo` o `polling`.
- `auto` usa observador nativo para rutas locales y polling para UNC o unidades mapeadas.
- `intervalo_min` debe ser menor que `restauracion_max_min`.
- `trabajadores`: cantidad de copias concurrentes.
- `requerir_armado`: exige marcadores y confirmacion antes de operar.
- `papelera_activa`: mueve copias retiradas a la papelera interna.

## Permisos

Origen y aportes:

```powershell
$owner = "$env:USERDOMAIN\$env:USERNAME"
icacls "C:\CarpetaCompartida\Mis_Originales" /inheritance:r `
  /grant:r "$owner:(OI)(CI)F" `
  "*S-1-5-18:(OI)(CI)F" `
  "*S-1-5-32-544:(OI)(CI)F" /T /C /Q
```

Destino:

```powershell
icacls "\\servidor\recurso\Publico_Usuarios" /inheritance:r `
  /grant:r "$owner:(OI)(CI)F" `
  "*S-1-5-32-545:(OI)(CI)M" `
  "*S-1-5-18:(OI)(CI)F" `
  "*S-1-5-32-544:(OI)(CI)F" /T /C /Q
```

Recurso compartido:

```powershell
Grant-SmbShareAccess -Name "CarpetaCompartida" `
  -AccountName "Usuarios" -AccessRight Change -Force
```

Si las rutas son UNC, ejecuta los comandos de ACL en el servidor o con permisos de administracion remota. El instalador no modifica ACL remotas desde el cliente.

## Tarea programada

El instalador registra `OriginWardenService` con:

- Inicio al iniciar sesion, con retraso de 30 segundos.
- Instancia unica.
- Reinicio tras fallo.
- Sin limite de ejecucion.
- Inicio cuando este disponible.

Para arranque como `SYSTEM`:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar_tarea.ps1 `
  -Action Install -Trigger Startup -ApplyPermissions -StartService
```

Con `SYSTEM`, el acceso SMB usa la cuenta de equipo. Verifica que el servidor permita ese acceso o usa una cuenta de servicio.

## CLI

```powershell
python -m syncsafe --check-config
python -m syncsafe --arm
python -m syncsafe --status
python -m syncsafe --approve
python -m syncsafe --simulate
python -m syncsafe --debug
```

`--approve` sin token aprueba la solicitud pendiente actual. Tambien se puede aprobar desde el panel.

## Seguridad y limites

- El servicio solo escribe en `DESTINO` y en la subcarpeta de aportes del origen.
- Nunca escribe ni borra en el origen normal.
- La ingesta no sobrescribe archivos existentes del origen normal.
- Las operaciones masivas se pausan para aprobacion.
- La identidad se valida con marcadores `.syncsafe.origin.id` y `.syncsafe.destination.id`.
- Si un marcador falta o esta intercambiado, el servicio entra en modo seguro.
- Si la papelera falla, no se borra.
- Los usuarios publicos con permiso `Modify` pueden borrar su copia, pero no el original.
- Un administrador o una cuenta comprometida puede modificar el origen. Manten copias offline y EDR si el riesgo lo exige.

## Registro y diagnostico

- Log principal: `logs/sync.log`.
- Log de instalacion: `logs/install.log`.
- Estado: `runtime/status.json`.
- Configuracion valida anterior: `config.lastgood.json`.
- Resultado de instalacion: `runtime/install-result.json`.
- Papelera y conflictos: dentro de `ORIGEN\Aportes_Usuarios`.

Comandos utiles:

```powershell
Get-Content .\logs\sync.log -Tail 100
Get-ScheduledTaskInfo -TaskName OriginWardenService
Get-Content .\runtime\status.json
```

## Problemas frecuentes

**La tarea no arranca**

Revisa la cuenta de la tarea, la ruta UNC y la contrasena de la cuenta de servicio. Ejecuta el instalador en modo reparacion.

**No hay heartbeat**

El proceso figura en ejecucion pero no responde. Pulsa Reiniciar en el panel o:

```powershell
Stop-ScheduledTask -TaskName OriginWardenService
Start-ScheduledTask -TaskName OriginWardenService
```

**Acceso denegado**

Revisa la ACL del origen y del destino. Conserva SYSTEM y Administradores. No apliques `/inheritance:r` sin haber probado antes el acceso.

**El sistema esta en modo seguro**

Corrige la causa: marcador faltante, ruta incorrecta, permisos, espacio o papelera. Despues pulsa Armar o reinicia.

**Una aprobacion esta pendiente**

Revisa el lote en el panel, el registro y las rutas de ejemplo. Aprueba o rechaza desde el panel o con `python -m syncsafe --approve`.

**Se borro algo por error**

Revisa `ORIGEN\Aportes_Usuarios\_Papelera`. Si el archivo estaba en el origen normal, la copia retirada del destino puede estar alli. Restaurala manualmente y vuelve a armar.

## Desinstalacion

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar_tarea.ps1 -Action Uninstall
```

La desinstalacion no borra archivos del origen, destino, aportes ni papelera. Elimina la tarea programada.

## Estado del proyecto

Version 0.3.0: primera implementacion funcional con servicio headless, panel Flet, reconciliacion nivel-triggered, ingesta de aportes, papelera, simulacion, armado, guardias de operaciones masivas, instalador y empaquetado.

Pendiente para versiones posteriores:

- Pruebas de caos completas en Windows.
- Limite por capacidad total de papelera, ademas de retencion por dias.
- Verificacion por hash opcional.
- Integracion opcional con Microsoft Defender para aportes.
- Firma de codigo de los ejecutables.
