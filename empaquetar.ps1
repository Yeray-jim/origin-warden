param(
    [string]$Version = "0.4.2",
    [switch]$SkipVenv,
    [switch]$ServiceOnly,
    [switch]$PanelOnly
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Flet = Join-Path $Venv "Scripts\flet.exe"
$Dist = Join-Path $Root "dist"
$PanelDist = Join-Path $Dist "panel"
$ServiceDist = Join-Path $Dist "service"
$Release = Join-Path $Root "release"
$Package = Join-Path $Dist "paquete"

if (-not $SkipVenv) {
    $BasePython = Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $BasePython) { $BasePython = Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1 }
    if (-not $BasePython) { throw "No se encontro python ni py en PATH." }
    if (-not (Test-Path $Python)) { & $BasePython.Source -m venv $Venv }
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r (Join-Path $Root "requirements-dev.txt")
}
if (-not (Test-Path $Python)) { throw "No existe $Python. Ejecuta el script sin -SkipVenv." }

$FletWinUtils = Join-Path $Venv "Lib\site-packages\flet_cli\__pyinstaller\win_utils.py"
if (Test-Path $FletWinUtils) {
    $winUtilsText = Get-Content $FletWinUtils -Raw
    $patchedText = $winUtilsText.Replace('open(exe_path, "ab", encoding="utf-8")', 'open(exe_path, "ab")')
    if ($patchedText -ne $winUtilsText) {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($FletWinUtils, $patchedText, $utf8NoBom)
        Write-Host "Aplicado workaround de empaquetado Flet 1.1.2." -ForegroundColor Yellow
    }
}

New-Item -ItemType Directory -Force -Path $Dist | Out-Null
if (Test-Path $Release) { Remove-Item -Recurse -Force $Release }
New-Item -ItemType Directory -Force -Path $Release | Out-Null

if (-not $PanelOnly) {
    & $Python -m PyInstaller `
        --noconfirm --clean --onefile --noconsole `
        --name OriginWardenService `
        --distpath $ServiceDist `
        --workpath (Join-Path $Root "build\servicio") `
        --specpath (Join-Path $Root "build\servicio") `
        --exclude-module flet --exclude-module ui `
        (Join-Path $Root "sincronizador.pyw")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallo con codigo $LASTEXITCODE" }
}

if (-not $ServiceOnly) {
    if (-not (Test-Path $Flet)) { throw "No existe flet.exe. Instala requirements-dev.txt." }
    Push-Location $Root
    try {
        & $Flet pack panel.pyw `
            --name OriginWardenPanel `
            --yes `
            --product-name "Origin Warden" `
            --file-description "Panel de control de Origin Warden" `
            --product-version $Version `
            --file-version ($Version + ".0") `
            --company-name "Origin Warden" `
            --distpath $PanelDist
        if ($LASTEXITCODE -ne 0) { throw "flet pack fallo con codigo $LASTEXITCODE" }
    } finally { Pop-Location }
}

Copy-Item (Join-Path $ServiceDist "OriginWardenService.exe") $Release -ErrorAction SilentlyContinue
Copy-Item (Join-Path $PanelDist "OriginWardenPanel.exe") $Release -ErrorAction SilentlyContinue
foreach ($asset in @("OriginWardenPanel.exe", "OriginWardenService.exe")) {
    if (-not (Test-Path (Join-Path $Release $asset))) { throw "No se genero el asset $asset" }
}
Copy-Item (Join-Path $Root "instalar_tarea.ps1") $Release
Copy-Item (Join-Path $Root "README.md") $Release
Copy-Item (Join-Path $Root "config.json") $Release

$assets = @("OriginWardenPanel.exe", "OriginWardenService.exe", "instalar_tarea.ps1")
$manifest = [ordered]@{
    schema = 1
    app = "origin-warden"
    channel = "stable"
    version = $Version
    min_panel_version = $Version
    released_at = (Get-Date).ToUniversalTime().ToString("o")
    notes = "Instalacion guiada sin terminal, configuracion de raiz/origen/copia, actualizaciones y auditoria."
    panel = $null
    service = $null
    installer = $null
    config_schema = 1
}
foreach ($asset in $assets) {
    $path = Join-Path $Release $asset
    if (-not (Test-Path $path)) { continue }
    $entry = [ordered]@{
        name = $asset
        sha256 = (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLower()
        size = (Get-Item $path).Length
    }
    if ($asset -eq "OriginWardenPanel.exe") { $manifest.panel = $entry }
    elseif ($asset -eq "OriginWardenService.exe") { $manifest.service = $entry }
    else { $manifest.installer = $entry }
}
$manifestJson = $manifest | ConvertTo-Json -Depth 6
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $Release "release-manifest.json"), $manifestJson, $utf8NoBom)

$sums = foreach ($asset in $assets) {
    $path = Join-Path $Release $asset
    if (Test-Path $path) {
        "{0}  {1}" -f (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLower(), $asset
    }
}
$sums | Set-Content -Path (Join-Path $Release "SHA256SUMS.txt") -Encoding ASCII

Write-Host "Release preparada en: $Release" -ForegroundColor Green
Write-Host "Sube los archivos a GitHub Releases con: .\publicar_release.ps1 -Version $Version" -ForegroundColor Green
