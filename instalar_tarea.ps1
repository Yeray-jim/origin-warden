param(
    [ValidateSet("Install", "Repair", "Update", "Uninstall")]
    [string]$Action = "Install",
    [string]$Config = (Join-Path $PSScriptRoot "config.json"),
    [string]$TaskName = "OriginWardenService",
    [ValidateSet("Logon", "Startup")]
    [string]$Trigger = "Logon",
    [string]$ServiceExe = "",
    [string]$Owner = "",
    [string]$Version = "0.4.0",
    [switch]$ApplyPermissions,
    [switch]$StartService,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"
$CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
$RuntimeDir = Join-Path $PSScriptRoot "runtime"
$LogDir = Join-Path $PSScriptRoot "logs"
$ResultPath = Join-Path $RuntimeDir "install-result.json"
$ReceiptPath = Join-Path $RuntimeDir "installation.json"
$AuditPath = Join-Path $RuntimeDir "install-audit.jsonl"
$InstallLog = Join-Path $LogDir "install.log"
$InstallRoot = $PSScriptRoot
$TargetService = Join-Path $InstallRoot "OriginWardenService.exe"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-InstallLog {
    param([string]$Message)
    $line = "{0} | {1}" -f (Get-Date).ToString("s"), $Message
    Add-Content -Path $InstallLog -Value $line -Encoding UTF8
}

function Write-Audit {
    param([string]$EventName, [hashtable]$Data = @{})
    $entry = [ordered]@{
        event = $EventName
        action = $Action
        at = (Get-Date).ToUniversalTime().ToString("o")
        data = $Data
    }
    Add-Content -Path $AuditPath -Value ($entry | ConvertTo-Json -Depth 5 -Compress) -Encoding UTF8
}

function Write-Result {
    param([bool]$Ok, [string]$Message, [string]$Code = "", [string]$Detail = "")
    $payload = [ordered]@{
        ok = $Ok
        code = $Code
        message = $Message
        detail = $Detail
        action = $Action
        task_name = $TaskName
        finished_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $payload | ConvertTo-Json -Depth 4 | Set-Content -Path $ResultPath -Encoding UTF8
    Write-InstallLog $Message
    if ($Detail) { Write-InstallLog $Detail }
}

function Read-Receipt {
    if (Test-Path $ReceiptPath) {
        try { return Get-Content $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
    }
    return $null
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
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-Action", $Action, "-Config", "`"$Config`"", "-TaskName", $TaskName,
        "-Trigger", $Trigger, "-Version", $Version
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

$mutex = New-Object System.Threading.Mutex($false, "Global\OriginWardenInstaller")
if (-not $mutex.WaitOne(0)) {
    Write-Result $false "Otra instalacion o actualizacion de Origin Warden esta en curso." "installer_busy"
    exit 20
}

try {
    Write-InstallLog "Accion=$Action Config=$Config Trigger=$Trigger Version=$Version"
    Write-Audit "action_started" @{ version = $Version; config = $Config }

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $receipt = Read-Receipt

    if ($Action -eq "Uninstall") {
        if ($existing) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        if ($receipt) {
            $receipt.installed = $false
            $receipt.status = "uninstalled"
            $receipt.last_verified_at = (Get-Date).ToUniversalTime().ToString("o")
            $receipt | ConvertTo-Json -Depth 5 | Set-Content -Path $ReceiptPath -Encoding UTF8
        }
        Write-Audit "uninstall_succeeded"
        Write-Result $true "Servicio desinstalado. Los archivos de datos no se borraron." "uninstalled"
        exit 0
    }

    if (($Action -eq "Install") -and ($existing -or ($receipt -and $receipt.installed))) {
        Write-Audit "already_installed" @{ task = $TaskName }
        Write-Result $false "Origin Warden ya esta instalado. Usa Reparar o Actualizar; no se permite una segunda instalacion." "already_installed"
        exit 10
    }
    if (($Action -eq "Repair" -or $Action -eq "Update") -and -not $existing) {
        $receiptInstalled = $receipt -and $receipt.installed
        if (-not $receiptInstalled) {
            Write-Audit "not_installed"
            Write-Result $false "No hay una instalacion existente que reparar o actualizar." "not_installed"
            exit 11
        }
    }
    if ($Action -eq "Update" -and -not $receipt) {
        Write-Result $false "No existe recibo de instalacion; usa Reparar para adoptar la instalacion." "receipt_missing"
        exit 12
    }

    if (-not (Test-Path $Config)) {
        throw "No existe el archivo de configuracion: $Config"
    }
    $cfg = Get-Content -Path $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    $Root = [string]$cfg.RAIZ
    $Origin = [string]$cfg.ORIGEN
    $Destination = [string]$cfg.DESTINO
    $AportesName = "Aportes_Usuarios"
    if ($cfg.APORTES -and $cfg.APORTES.subcarpeta) { $AportesName = [string]$cfg.APORTES.subcarpeta }
    if (-not $Origin -or -not $Destination) { throw "ORIGEN y DESTINO son obligatorios en config.json" }
    if (-not (Test-Path -PathType Container $Origin)) { throw "No se puede acceder a ORIGEN o no es una carpeta: $Origin" }
    if (-not (Test-Path -PathType Container $Destination)) { New-Item -ItemType Directory -Force -Path $Destination | Out-Null }
    if (-not (Test-Path -PathType Container $Destination)) { throw "No se puede acceder a DESTINO o no es una carpeta: $Destination" }
    $Intake = Join-Path $Origin $AportesName
    New-Item -ItemType Directory -Force -Path $Intake | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir "control") | Out-Null
    try {
        icacls $RuntimeDir /grant:r "${CurrentUser}:(OI)(CI)M" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
        icacls $LogDir /grant:r "${CurrentUser}:(OI)(CI)M" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
    } catch {
        Write-InstallLog "No se pudieron ajustar permisos de runtime/logs: $($_.Exception.Message)"
    }

    if ($ApplyPermissions) {
        if (-not $Owner) { $Owner = $CurrentUser }
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $aclBackup = Join-Path $LogDir "acl_backup_$stamp"
        $canApply = $true
        if (Test-UncPath $Origin) { $canApply = $false; Write-InstallLog "ORIGEN es UNC. La ACL debe aplicarse en el servidor." }
        if (Test-UncPath $Destination) { $canApply = $false; Write-InstallLog "DESTINO es UNC. La ACL debe aplicarse en el servidor." }
        if ($canApply) {
            try {
                icacls $Origin /save "$aclBackup-origin.txt" /T /C /Q | Out-Null
                icacls $Destination /save "$aclBackup-destination.txt" /T /C /Q | Out-Null
            } catch { Write-InstallLog "No se pudo respaldar una ACL: $($_.Exception.Message)" }
            icacls $Origin /inheritance:r /grant:r "${Owner}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "icacls fallo en ORIGEN con codigo $LASTEXITCODE" }
            icacls $Destination /inheritance:r /grant:r "${Owner}:(OI)(CI)F" "*S-1-5-32-545:(OI)(CI)M" "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /T /C /Q | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "icacls fallo en DESTINO con codigo $LASTEXITCODE" }
            Write-InstallLog "ACL locales aplicadas. Respaldo: $aclBackup-*.txt"
        }
    }

    if ($existing) {
        try { Export-ScheduledTask -TaskName $TaskName | Set-Content -Path (Join-Path $LogDir "task_backup.xml") -Encoding UTF8 } catch { Write-InstallLog "No se pudo respaldar la tarea." }
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    $backupService = $null
    if ($Action -eq "Update" -and (Test-Path $TargetService)) {
        $backupDir = Join-Path $InstallRoot "backup"
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $backupService = Join-Path $backupDir ("OriginWardenService." + (Get-Date -Format "yyyyMMdd_HHmmss") + ".exe")
        Copy-Item -LiteralPath $TargetService -Destination $backupService -Force
    }

    if (-not $ServiceExe) {
        if (Test-Path $TargetService) { $ServiceExe = $TargetService }
    }
    if ($ServiceExe -and ((Resolve-Path $ServiceExe).Path -ne $TargetService)) {
        Copy-Item -LiteralPath $ServiceExe -Destination $TargetService -Force
    }

    if (Test-Path $TargetService) {
        $execute = $TargetService
        $arguments = ""
        $workingDirectory = $InstallRoot
    } else {
        $pythonw = Join-Path $InstallRoot ".venv\Scripts\pythonw.exe"
        if (-not (Test-Path $pythonw)) {
            $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
            if (-not $command) { throw "No se encontro el servicio ni pythonw.exe." }
            $pythonw = $command.Source
        }
        $execute = $pythonw
        $arguments = '"{0}"' -f (Join-Path $InstallRoot "sincronizador.pyw")
        $workingDirectory = $InstallRoot
    }

    $taskAction = New-ScheduledTaskAction -Execute $execute -Argument $arguments -WorkingDirectory $workingDirectory
    if ($Trigger -eq "Startup") {
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
        try { $trigger.Delay = "PT30S" } catch { Write-InstallLog "No se pudo configurar el retraso." }
        $principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
    }
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Description "Origin Warden" -Force | Out-Null

    $configHash = (Get-FileHash -Algorithm SHA256 -Path $Config).Hash
    $now = (Get-Date).ToUniversalTime().ToString("o")
    $installId = if ($receipt -and $receipt.install_id) { [string]$receipt.install_id } else { [guid]::NewGuid().ToString() }
    $installedAt = if ($receipt -and $receipt.installed_at) { [string]$receipt.installed_at } else { $now }
    $newReceipt = [ordered]@{
        schema = 1
        install_id = $installId
        version = $Version
        installed = $true
        status = "ok"
        task_name = $TaskName
        package_dir = $InstallRoot
        service_executable = $execute
        raiz = $Root
        origen = $Origin
        destino = $Destination
        config_path = (Resolve-Path $Config).Path
        config_hash = $configHash
        trigger = $Trigger
        account = if ($Trigger -eq "Startup") { "SYSTEM" } else { $CurrentUser }
        installed_at = $installedAt
        last_verified_at = $now
        updated_at = if ($Action -eq "Update") { $now } else { $null }
    }
    $newReceipt | ConvertTo-Json -Depth 5 | Set-Content -Path $ReceiptPath -Encoding UTF8

    if ($StartService) {
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 3
        $state = (Get-ScheduledTask -TaskName $TaskName).State.ToString()
        if ($state -notin @("Running", "Ready")) {
            if ($backupService -and (Test-Path $backupService)) {
                Copy-Item -LiteralPath $backupService -Destination $TargetService -Force
                Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
                Start-ScheduledTask -TaskName $TaskName
                Write-Result $false "La actualizacion no pudo arrancar; se restauro el servicio anterior." "update_rolled_back"
                exit 30
            }
        }
    }

    Write-Audit "action_succeeded" @{ version = $Version; action = $Action }
    $message = switch ($Action) {
        "Repair" { "Reparacion completada. Revisa el estado y arma el sistema si esta en simulacion." }
        "Update" { "Actualizacion completada. El servicio fue reiniciado." }
        default { "Instalacion completada. Revisa el estado y arma el sistema si esta en simulacion." }
    }
    Write-Result $true $message "ok"
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Audit "action_failed" @{ error = $message }
    Write-Result $false "La operacion fallo: $message" "failed" $message
    Write-Error $message
    exit 1
} finally {
    try { $mutex.ReleaseMutex() } catch {}
}
