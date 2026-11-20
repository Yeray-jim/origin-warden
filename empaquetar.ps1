param(
    [switch]$SkipVenv,
    [switch]$ServiceOnly,
    [switch]$PanelOnly
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Flet = Join-Path $Venv "Scripts\flet.exe"

if (-not $SkipVenv) {
    if (-not (Test-Path $Python)) {
        py -3 -m venv $Venv
    }
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r (Join-Path $Root "requirements-dev.txt")
}

if (-not (Test-Path $Python)) {
    throw "No existe $Python. Ejecuta el script sin -SkipVenv."
}

$Dist = Join-Path $Root "dist"
$Package = Join-Path $Dist "paquete"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

if (-not $PanelOnly) {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --noconsole `
        --name OriginWardenService `
        --distpath $Dist `
        --workpath (Join-Path $Root "build\servicio") `
        --specpath (Join-Path $Root "build\servicio") `
        --exclude-module flet `
        --exclude-module ui `
        (Join-Path $Root "sincronizador.pyw")
}

if (-not $ServiceOnly) {
    if (-not (Test-Path $Flet)) {
        throw "No existe flet.exe en el entorno. Instala requirements-dev.txt."
    }
    Push-Location $Root
    try {
        & $Flet pack panel.pyw `
            --name OriginWardenPanel `
            --onedir `
            --noconsole `
            --distpath $Dist
    } finally {
        Pop-Location
    }
}

if (Test-Path $Package) {
    Remove-Item -Recurse -Force $Package
}
New-Item -ItemType Directory -Force -Path $Package | Out-Null

Copy-Item (Join-Path $Root "config.json") $Package
Copy-Item (Join-Path $Root "instalar_tarea.ps1") $Package
Copy-Item (Join-Path $Root "README.md") $Package
Copy-Item (Join-Path $Root "requirements.txt") $Package
if (Test-Path (Join-Path $Dist "OriginWardenService")) {
    Copy-Item -Recurse (Join-Path $Dist "OriginWardenService") $Package
}
if (Test-Path (Join-Path $Dist "OriginWardenPanel")) {
    Copy-Item -Recurse (Join-Path $Dist "OriginWardenPanel") $Package
}

$launcher = @"
@echo off
start "" "%~dp0OriginWardenPanel\OriginWardenPanel.exe"
"@
Set-Content -Path (Join-Path $Package "AbrirPanel.bat") -Value $launcher -Encoding ASCII

Write-Host "Paquete generado en: $Package" -ForegroundColor Green
Write-Host "Ejecuta AbrirPanel.bat o instala la tarea con instalar_tarea.ps1." -ForegroundColor Green
