param(
    [ValidateSet("Install", "Repair", "Uninstall")]
    [string]$Action = "Install",
    [string]$Config = (Join-Path $PSScriptRoot "config.json"),
    [string]$TaskName = "OriginWardenService",
    [ValidateSet("Logon", "Startup")]
    [string]$Trigger = "Logon",
    [string]$ServiceExe = "",
    [string]$Owner = "",
    [switch]$ApplyPermissions,
    [switch]$StartService,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$RuntimeDir = Join-Path $PSScriptRoot "runtime"
$LogDir = Join-Path $PSScriptRoot "logs"
$ResultPath = Join-Path $RuntimeDir "install-result.json"
$InstallLog = Join-Path $LogDir "install.log"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-InstallLog {
    param([string]$Message)
    $line = "{0} | {1}" -f (Get-Date).ToString("s"), $Message
    Add-Content -Path $InstallLog -Value $line -Encoding UTF8
}

function Write-Result {
    param([bool]$Ok, [string]$Message, [string]$Detail = "")
    $payload = [ordered]@{
        ok = $Ok
        message = $Message
        detail = $Detail
        action = $Action
        finished_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $payload | ConvertTo-Json -Depth 4 | Set-Content -Path $ResultPath -Encoding UTF8
    Write-InstallLog $Message
    if ($Detail) { Write-InstallLog $Detail }
}

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-UncPath {
    param([string]$Path)
    return $Path.StartsWith("\\") -and -not $Path.StartsWith("\\?\")
}

function Invoke-ElevatedSelf {
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Action", $Action,
        "-Config", "`"$Config`"",
        "-TaskName", $TaskName,
        "-Trigger", $Trigger
    )
    if ($ServiceExe) { $arguments += @("-ServiceExe", "`"$ServiceExe`"") }
    if ($Owner) { $arguments += @("-Owner", "`"$Owner`"") }
    if ($ApplyPermissions) { $arguments += "-ApplyPermissions" }
    if ($StartService) { $arguments += "-StartService" }
    $arguments += "-NoElevate"
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $process.ExitCode
}

if (-not (Test-IsAdmin) -and -not $NoElevate) {
    Invoke-ElevatedSelf
}

try {
    Write-InstallLog "Accion=$Action Config=$Config Trigger=$Trigger"

    if ($Action -eq "Uninstall") {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Result $true "Tarea desinstalada. Los archivos de datos no se borraron."
        } else {
            Write-Result $true "La tarea no estaba instalada."
        }
        exit 0
    }

    if (-not (Test-Path $Config)) {
        throw "No existe el archivo de configuracion: $Config"
    }
    $cfg = Get-Content -Path $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    $Origin = [string]$cfg.ORIGEN
    $Destination = [string]$cfg.DESTINO
    $AportesName = "Aportes_Usuarios"
    if ($cfg.APORTES -and $cfg.APORTES.subcarpeta) {
        $AportesName = [string]$cfg.APORTES.subcarpeta
    }
    if (-not $Origin -or -not $Destination) {
        throw "ORIGEN y DESTINO son obligatorios en config.json"
    }
    if (-not (Test-Path -PathType Container $Origin)) {
        throw "No se puede acceder a ORIGEN o no es una carpeta: $Origin"
    }
    if (-not (Test-Path -PathType Container $Destination)) {
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    }
    if (-not (Test-Path -PathType Container $Destination)) {
        throw "No se puede acceder a DESTINO o no es una carpeta: $Destination"
    }
    $Intake = Join-Path $Origin $AportesName
    New-Item -ItemType Directory -Force -Path $Intake | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir "control") | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    try {
        icacls $RuntimeDir /grant:r "${CurrentUser}:(OI)(CI)M" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
        icacls $LogDir /grant:r "${CurrentUser}:(OI)(CI)M" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
    } catch {
        Write-InstallLog "No se pudieron ajustar permisos de runtime/logs: $($_.Exception.Message)"
    }
    Write-InstallLog "Origen=$Origin Destino=$Destination Aportes=$Intake"

    if ($ApplyPermissions) {
        if (-not $Owner) {
            $Owner = $CurrentUser
        }
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $aclBackup = Join-Path $LogDir "acl_backup_$stamp"
        $canApply = $true
        if (Test-UncPath $Origin) {
            $canApply = $false
            Write-InstallLog "ORIGEN es UNC. La ACL debe aplicarse en el servidor."
        }
        if (Test-UncPath $Destination) {
            $canApply = $false
            Write-InstallLog "DESTINO es UNC. La ACL debe aplicarse en el servidor."
        }
        if ($canApply) {
            try {
                icacls $Origin /save "$aclBackup-origin.txt" /T /C /Q | Out-Null
                icacls $Destination /save "$aclBackup-destination.txt" /T /C /Q | Out-Null
            } catch {
                Write-InstallLog "No se pudo respaldar una ACL: $($_.Exception.Message)"
            }
            icacls $Origin /inheritance:r /grant:r "${Owner}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "icacls fallo en ORIGEN con codigo $LASTEXITCODE" }
            New-Item -ItemType Directory -Force -Path $Destination | Out-Null
            icacls $Destination /inheritance:r /grant:r "${Owner}:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)M" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "icacls fallo en DESTINO con codigo $LASTEXITCODE" }
            Write-InstallLog "ACL locales aplicadas. Respaldo: $aclBackup-*.txt"
        }
    }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        try {
            Export-ScheduledTask -TaskName $TaskName | Set-Content -Path (Join-Path $LogDir "task_backup.xml") -Encoding UTF8
        } catch {
            Write-InstallLog "No se pudo respaldar la tarea: $($_.Exception.Message)"
        }
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    if (-not $ServiceExe) {
        $candidates = @(
            (Join-Path $PSScriptRoot "OriginWardenService.exe"),
            (Join-Path $PSScriptRoot "OriginWardenService\OriginWardenService.exe"),
            (Join-Path $PSScriptRoot "dist\OriginWardenService\OriginWardenService.exe")
        )
        foreach ($candidate in $candidates) {
            if (Test-Path $candidate) {
                $ServiceExe = $candidate
                break
            }
        }
    }

    if ($ServiceExe) {
        if (-not (Test-Path $ServiceExe)) {
            throw "No existe el ejecutable del servicio: $ServiceExe"
        }
        $execute = $ServiceExe
        $arguments = ""
        $workingDirectory = Split-Path -Parent $ServiceExe
    } else {
        $pythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
        if (-not (Test-Path $pythonw)) {
            $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
            if (-not $command) {
                throw "No se encontro pythonw.exe. Crea el entorno .venv o usa -ServiceExe."
            }
            $pythonw = $command.Source
        }
        $execute = $pythonw
        $arguments = '"{0}"' -f (Join-Path $PSScriptRoot "sincronizador.pyw")
        $workingDirectory = $PSScriptRoot
    }

    $action = New-ScheduledTaskAction -Execute $execute -Argument $arguments -WorkingDirectory $workingDirectory
    if ($Trigger -eq "Startup") {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
        try { $trigger.Delay = "PT30S" } catch { Write-InstallLog "No se pudo configurar el retraso del disparador." }
        $principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
    }
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Origin Warden" -Force | Out-Null
    Write-InstallLog "Tarea registrada: $TaskName"

    if ($StartService) {
        Start-ScheduledTask -TaskName $TaskName
        Write-InstallLog "Tarea iniciada."
    }

    $resultMessage = if ($Action -eq "Repair") {
        "Reparacion completada. Revisa el estado y el heartbeat."
    } else {
        "Instalacion completada. Revisa el estado y arma el sistema si esta en simulacion."
    }
    Write-Result $true $resultMessage
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Result $false "La instalacion fallo: $message" $message
    Write-Error $message
    exit 1
}
