@echo off
title Construyendo Tempia
cd /d "%~dp0"
echo.
echo  =============================================
echo     TEMPIA - Monitor de hardware  (Boldo3108)
echo  =============================================
echo.

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo  [X] No se encontro Python. Instalalo desde https://www.python.org/downloads/
  echo      y marca la casilla "Add python.exe to PATH".
  pause
  exit /b 1
)

echo  [1/4] Creando entorno virtual...
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat

echo  [2/4] Instalando dependencias...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo  [X] Fallo la instalacion de dependencias.
  pause
  exit /b 1
)

echo  [3/4] Descargando lector de sensores (LibreHardwareMonitor)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0descargar_lhm.ps1"
if errorlevel 1 (
  echo  [!] No se pudo descargar LibreHardwareMonitor. Tempia funcionara igual,
  echo      pero sin temperatura de CPU, discos ni ventiladores.
  if not exist lhm mkdir lhm
)

echo  [4/4] Creando Tempia.exe ...
pyinstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name Tempia --icon tempia.ico ^
  --add-data "lhm;lhm" --add-data "tempia.ico;." --add-data "tempia.png;." ^
  --collect-all pythonnet --collect-all clr_loader --hidden-import pynvml ^
  tempia.py
if errorlevel 1 (
  echo  [X] Error al crear el ejecutable.
  pause
  exit /b 1
)

copy /y dist\Tempia.exe "%~dp0Tempia.exe" >nul
echo.
echo  LISTO!  Tu app esta en:  %~dp0Tempia.exe
echo.
pause
