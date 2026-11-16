# Descarga LibreHardwareMonitorLib (lector de sensores) para Tempia
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$version = "v0.9.6"
$url = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/$version/LibreHardwareMonitor.zip"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $root "lhm"
$tmp = Join-Path $env:TEMP "tempia_lhm"
if (Test-Path (Join-Path $dest "LibreHardwareMonitorLib.dll")) {
    Write-Host "  LibreHardwareMonitorLib ya esta descargado."
    exit 0
}
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "lhm.zip"
Write-Host "  Descargando LibreHardwareMonitor $version ..."
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp "x") -Force
$dll = Get-ChildItem -Path (Join-Path $tmp "x") -Recurse -Filter "LibreHardwareMonitorLib.dll" |
       Sort-Object { $_.FullName -match "net(5|6|7|8|9|10)" } | Select-Object -First 1
if (-not $dll) { throw "No se encontro LibreHardwareMonitorLib.dll dentro del zip" }
New-Item -ItemType Directory -Path $dest -Force | Out-Null
Get-ChildItem -Path $dll.DirectoryName -Filter "*.dll" | Copy-Item -Destination $dest -Force
Get-ChildItem -Path $dest -Recurse | Unblock-File
Write-Host "  Listo: $((Get-ChildItem $dest -Filter *.dll).Count) DLLs copiadas a .\lhm"
