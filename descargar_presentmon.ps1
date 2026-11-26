# Descarga PresentMon (de Intel, licencia MIT) para medir los FPS de los juegos en el modo mini de Tempia
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$version = "2.5.1"
$url = "https://github.com/GameTechDev/PresentMon/releases/download/v$version/PresentMon-$version-x64.exe"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $root "presentmon"
$exe = Join-Path $dest "PresentMon.exe"
if (Test-Path $exe) {
    Write-Host "  PresentMon ya esta descargado."
    exit 0
}
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Write-Host "  Descargando PresentMon $version ..."
Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
Unblock-File $exe
Write-Host "  Listo: .\presentmon\PresentMon.exe"
