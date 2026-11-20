# Origin Warden

Version: **0.4.2**

Repositorio: [github.com/Yeray-jim/origin-warden](https://github.com/Yeray-jim/origin-warden)

Descarga del panel: [ultima release](https://github.com/Yeray-jim/origin-warden/releases/latest)

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
├── publicar_release.ps1
├── .github/
│   └── workflows/
│       └── release.yml
├── syncsafe/
│   ├── config.py
│   ├── engine.py
│   ├── path_setup.py
│   ├── security.py
│   └── ...
├── ui/
│   ├── app.py
│   ├── release_client.py
│   ├── services.py
│   └── ...
├── tests/
├── logs/
└── runtime/
```

Los ejecutables no se guardan en el arbol Git. Se publican como assets de GitHub Releases.

## Requisitos

- Windows 10 u 11.
- PowerShell 5.1 o superior.
- Permisos de administrador para aplicar ACL locales y registrar la tarea.
- Acceso de lectura a `ORIGEN`.
- Escritura en `ORIGEN\Aportes_Usuarios`.
- Escritura en `DESTINO`.
- Cuenta con permisos SMB si las rutas son UNC.
- Python 3.10 o superior solo para desarrollo o ejecucion desde fuente.

La version publicada no necesita Python instalado y no requiere abrir una terminal.

## Instalacion recomendada, sin terminal

1. Descarga `OriginWardenPanel.exe` desde la [ultima release](https://github.com/Yeray-jim/origin-warden/releases/latest).
2. Ejecuta el panel.
3. En **Rutas**, selecciona el nodo principal.
4. Analiza la raiz:
   - Si esta vacia, crea las carpetas de origen y copia.
   - Si tiene contenido, elige **Englobar contenido** o **Usar carpetas existentes**.
5. En **Instalacion**, pulsa **Instalar servicio**.
6. El panel consulta GitHub, descarga el servicio, verifica su SHA-256 y lo instala.
7. Acepta UAC cuando Windows lo solicite.
8. Revisa el modo simulacion y pulsa **Armar sistema**.

Durante el proceso no se abre PowerShell ni se escribe ningun comando. UAC es una ventana de Windows, no una terminal. El panel se copia a `%LOCALAPPDATA%\OriginWarden` y guarda alli la configuracion, los logs, el runtime y el ejecutable del servicio.

La instalacion se audita en `runtime/installation.json` y `runtime/install-audit.jsonl`. Una segunda instalacion queda bloqueada por el panel y por el instalador.

## Instalacion desde fuente, solo desarrollo

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Configura `config.json` o usa la pestana **Rutas** del panel.

Valida la configuracion:

```powershell
python -m syncsafe --check-config
```

Abre el panel:

```powershell
python panel.pyw
```

Para depurar con consola:

```powershell
python -m syncsafe --debug
```

Pruebas unitarias:

```powershell
python -m unittest discover -s tests -v
```

## Publicacion de una release

El flujo de mantenedor es:

```powershell
.\empaquetar.ps1 -Version 0.4.2
.\publicar_release.ps1 -Version 0.4.2
```

`empaquetar.ps1` genera:

```text
release/
├── OriginWardenPanel.exe
├── OriginWardenService.exe
├── instalar_tarea.ps1
├── release-manifest.json
└── SHA256SUMS.txt
```

Tambien puedes publicar automaticamente desde GitHub Actions. El workflow `.github/workflows/release.yml` se ejecuta al empujar un tag `v*` o manualmente desde la pestana Actions. Compila en `windows-latest`, ejecuta las pruebas, verifica el manifest y publica la release.

```powershell
git tag v0.4.2
git push origin v0.4.2
```

`publicar_release.ps1` sigue disponible para publicar manualmente desde Windows.

El panel consulta:

```text
https://github.com/Yeray-jim/origin-warden/releases/latest/download/release-manifest.json
```

No se usa la API de GitHub, por lo que no hay tokens ni limite de peticiones de API.

## Actualizaciones

- El panel comprueba si hay una release nueva al iniciar y cada 6 horas.
- El boton **Buscar actualizaciones** permite comprobarlo manualmente.
- Si hay una version nueva, muestra version actual, version publicada y pide confirmacion.
- Descarga el asset, verifica tamano y SHA-256.
- Actualiza el servicio con backup y rollback si no arranca.
- Actualiza el panel con un proceso auxiliar oculto y lo reinicia.
- Conserva `config.json`, `runtime`, `logs`, marcadores, papelera y aportes.

No hay certificado de firma en esta version. Por eso:

- La actualizacion nunca es silenciosa.
- Se exige confirmacion del usuario.
- Se verifica SHA-256 contra el manifest.
- Se recomienda firmar los ejecutables en el futuro.
- Una cuenta de GitHub comprometida podria alterar el manifest; la firma Authenticode eliminaria ese riesgo.

## Primer arranque

1. El panel valida la configuracion y la jerarquia de rutas.
2. Si el sistema no esta armado, arranca en **simulacion**.
3. El panel muestra el estado y el registro.
4. Revisa la simulacion y pulsa **Armar sistema**.
5. El servicio se reinicia y comienza a aplicar cambios reales.

En simulacion el servicio lee, calcula y registra, pero no copia, no borra y no ingesta.

## Uso del panel

El panel tiene seis secciones:

- **Panel:** estado, interruptor del servicio, autoarranque, instalacion pendiente, actualizaciones, guardias y aprobaciones.
- **Rutas:** nodo principal, origen, copia, analisis de contenido, creacion, englobado y adopcion de carpetas.
- **Configuracion:** observadores, intervalos, papelera, limites y nivel de log.
- **Instalacion:** instalar, actualizar, reparar, desinstalar y ver auditoria.
- **Manual:** guia offline sin terminal y anexo avanzado.
- **Registro:** ultimas lineas de `logs/sync.log`.

El interruptor **Servicio**:

- Encender: habilita la tarea y la inicia.
- Apagar: solicita parada ordenada, detiene la tarea y deshabilita el autoarranque.
- Cerrar el panel no detiene el servicio.

## Configuracion de rutas

El panel trabaja con tres niveles:

```text
RAIZ
├── ORIGEN
└── DESTINO
```

`RAIZ` es la carpeta compartida principal. `ORIGEN` y `DESTINO` deben estar dentro de `RAIZ` y no pueden estar anidados entre si. Se permiten rutas con subcarpetas, por ejemplo `RAIZ\Trabajo\Originales` y `RAIZ\Publico\Copia`.

### Raiz vacia

El panel permite crear las carpetas de origen y copia, incluyendo rutas anidadas. No se mueve ni se borra nada.

### Raiz con contenido

Hay dos modos:

**Englobar contenido**

Crea la carpeta de originales y mueve a ella las carpetas y archivos sueltos de la raiz. En este modo:

- Origen y destino deben ser hijos directos de la raiz.
- La carpeta de origen debe estar vacia.
- Se muestra un resumen y se pide confirmacion.
- Se usa un journal y se intenta rollback si falla un movimiento.
- No se sobrescribe nada.

**Usar carpetas existentes**

El usuario selecciona un origen y una copia que ya existen. Pueden tener contenido y estar en subcarpetas. Si el destino contiene archivos que no estan en el origen, el servicio los tratara como aportes de usuarios; la primera ejecucion en simulacion permite revisar el plan.

La configuracion de rutas solo se puede cambiar con el servicio desinstalado. Esto evita tareas apuntando a una raiz distinta.

## Auditoria de instalacion

- La tarea programada es la fuente principal de verdad.
- `runtime/installation.json` guarda version, tarea, ejecutable, raiz, origen, destino y hashes.
- `runtime/install-audit.jsonl` registra intentos, bloqueos, reparaciones, actualizaciones y desinstalaciones.
- El boton **Instalar servicio** solo existe si no hay instalacion.
- Si ya esta instalado, las acciones son Iniciar, Reparar, Actualizar o Desinstalar.
- Una segunda instalacion se bloquea en el panel y en `instalar_tarea.ps1`.
- Una tarea existente sin recibo se marca como instalacion heredada y requiere reparacion o adopcion explicita.

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
  "RAIZ": "\\\\servidor\\recurso",
  "ORIGEN": "\\\\servidor\\recurso\\Mis_Originales",
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

- `RAIZ`: carpeta compartida principal.
- `ORIGEN` y `DESTINO`: subcarpetas dentro de `RAIZ`, no anidadas entre si.
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
- Las actualizaciones usan HTTPS y SHA-256, pero no hay certificado de firma. Se exige confirmacion del usuario.
- Si se compromete la cuenta de GitHub, el manifest podria ser alterado; la firma de codigo es una mejora pendiente.

## Registro y diagnostico

- Log principal: `logs/sync.log`.
- Log de instalacion: `logs/install.log`.
- Estado: `runtime/status.json`.
- Configuracion valida anterior: `config.lastgood.json`.
- Resultado de instalacion: `runtime/install-result.json`.
- Recibo de instalacion: `runtime/installation.json`.
- Auditoria de instalacion: `runtime/install-audit.jsonl`.
- Journal de rutas: `runtime/path-setup.json`.
- Log de preparacion de rutas: `logs/path-setup.log`.
- Papelera y conflictos: dentro de `ORIGEN\Aportes_Usuarios`.

Comandos utiles:

```powershell
Get-Content .\logs\sync.log -Tail 100
Get-ScheduledTaskInfo -TaskName OriginWardenService
Get-Content .\runtime\status.json
```

## Problemas frecuentes

**El panel dice que no hay release publicada**

Publica una release con `.\empaquetar.ps1` y `.\publicar_release.ps1`. El panel consulta siempre la ultima release estable.

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

Desde el panel, en **Instalacion**, pulsa **Desinstalar tarea**. No hace falta usar la terminal.

La desinstalacion no borra archivos del origen, destino, aportes ni papelera. Elimina la tarea programada. El panel permite volver a instalar despues.

Comando avanzado equivalente:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar_tarea.ps1 -Action Uninstall
```

## Estado del proyecto

Version 0.4.2: corrige la validacion del argumento vacio al registrar la tarea programada. Version 0.4.0: instalacion y actualizacion sin terminal, descarga del servicio desde GitHub Releases, verificacion SHA-256, configuracion de raiz/origen/copia con creacion, englobado y adopcion, auditoria anti-duplicados, panel Flet, servicio headless, reconciliacion nivel-triggered, ingesta de aportes, papelera, simulacion y armado.

Pendiente para versiones posteriores:

- Pruebas de caos completas en Windows.
- Firma Authenticode de los ejecutables.
- Firma criptografica del manifest de release.
- Verificacion por hash opcional de archivos sincronizados.
- Integracion opcional con Microsoft Defender para aportes.
- Actualizacion delta para reducir descargas.
