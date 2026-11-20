param(
    [string]$Version = "0.4.0",
    [string]$Notes = "Instalacion guiada sin terminal, rutas raiz/origen/copia, actualizaciones y auditoria."
)
$ErrorActionPreference = "Stop"
$Release = Join-Path $PSScriptRoot "release"
if (-not (Test-Path (Join-Path $Release "release-manifest.json"))) {
    throw "No existe release/release-manifest.json. Ejecuta empaquetar.ps1 primero."
}
$tag = "v$Version"
gh release create $tag `
    (Join-Path $Release "OriginWardenPanel.exe") `
    (Join-Path $Release "OriginWardenService.exe") `
    (Join-Path $Release "instalar_tarea.ps1") `
    (Join-Path $Release "release-manifest.json") `
    (Join-Path $Release "SHA256SUMS.txt") `
    --title "Origin Warden $Version" `
    --notes $Notes
