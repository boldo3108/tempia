# Tempia — contexto para Claude Code

Tempia es una app de escritorio para **Windows** que muestra temperaturas, uso y ventiladores del PC.
Creada por **Boldo3108** (Patricio, Chile). Todo en **español**: interfaz, mensajes, commits y respuestas.

- Repo: https://github.com/boldo3108/tempia (público)
- Descarga: https://github.com/boldo3108/tempia/releases/latest/download/Tempia.exe
- Se publica en su página **boldo3108.cl** (repo `boldo3108/portafolio`, ver más abajo)
- Versión actual: **1.1.1** (`APP_VERSION` en `tempia.py`)

## El dueño
- No es programador: aprende con IA. Explicar en simple, sin jerga, y entregar cosas listas para usar.
- Prefiere apps **.exe de un solo archivo** (nada de webapps).
- Su PC: Ryzen 9 5950X, 32 GB DDR4 3200, RTX 4070 SUPER 12 GB, SSD NVMe 1 TB + HDD 1 TB.

## Estructura
| Archivo | Qué es |
|---|---|
| `tempia.py` | Toda la app (~3000 líneas, tkinter). |
| `requirements.txt` | psutil, nvidia-ml-py, pythonnet, pyinstaller |
| `descargar_lhm.ps1` | Baja LibreHardwareMonitor v0.9.6 (`LibreHardwareMonitor.zip`, net472) y copia sus DLL a `lhm/` |
| `CONSTRUIR_TEMPIA.bat` | Compila el .exe a mano en Windows (venv + LHM + PyInstaller) |
| `.github/workflows/compilar.yml` | Compila `Tempia.exe` en windows-latest y lo sube a la Release |
| `herramientas/version_win.py` | Genera `version_win.txt` (datos del .exe: versión, "Creado por Boldo3108") |
| `notas/vX.Y.Z.md` | Notas de cada versión (texto de la Release) |
| `tempia.ico` / `tempia.png` | Ícono |
| `LEEME.txt` | Instrucciones para usuarios |

`lhm/`, `build/`, `dist/`, `.venv/`, `version_win.txt` y `*.spec` están en `.gitignore`.

## Cómo está armado `tempia.py`
1. **Config**: `load_config` / `save_config` → `%APPDATA%\Tempia\config.json`. `DEFAULT_CONFIG` define todas las claves (tarjetas visibles, modo mini, ventiladores, sensores ocultos, `check_updates`…).
2. **Actualizaciones**: `check_latest_release` (API de GitHub Releases), `download_file`, `start_self_update` (abre el .exe nuevo con `--instalar <ruta_vieja> --pid <pid>`), `install_if_requested` (se ejecuta al inicio de `main()`: espera que cierre el viejo, se copia encima y lo abre). Mismo sistema que Voxia.
3. **Sensores** (backends):
   - `LHMBackend`: LibreHardwareMonitorLib vía **pythonnet** (`clr.AddReference`). Da temp. CPU, hotspot GPU, discos, placa madre, ventiladores y **control de ventiladores** (`sensor.Control.SetSoftware/SetDefault`). Necesita **admin + driver PawnIO**.
   - `NVMLBackend`: GPU NVIDIA con `pynvml` (sin admin).
   - `ACPIThermal`: respaldo aproximado vía WMI `MSAcpi_ThermalZoneTemperature`.
   - psutil: uso CPU/RAM/discos/red.
4. **Mi PC** (`SPECS_PS`, `read_specs`, `parse_specs`): script PowerShell/CIM ejecutado con `-EncodedCommand` (¡no con `-Command -`, eso falló!) que devuelve JSON; `parse_specs` lo convierte en secciones. `ram_maker` traduce códigos JEDEC/part numbers a marcas.
5. **`Monitor`**: hilo de lectura. `collect()` arma un "snapshot" (dict) con cpu, gpus, ram, parts, drives, net, disk_io, fans, others, warnings. `_apply_fans()` aplica Manual/Curva con seguridad (mín. 20 %, bombas 50 %, emergencia CPU≥90 °C / GPU≥88 °C → 100 %, vuelve a automático al cerrar). `diagnostics()` = texto de Configuración → Diagnóstico. `_demo()` = datos falsos.
6. **Widgets** propios dibujados en Canvas: `Gauge`, `HBar`, `CoreBars`, `Spark`, `FlatButton`, `Segmented`, `InfoRow`. Tema oscuro (constantes `BG`, `CARD`, `ACCENT`…). `px()` escala por DPI.
7. **`TempiaApp`**: ventana principal (tarjetas CPU, GPU, RAM, Discos, Red, Ventiladores, Otros sensores), ventanas de **Ventiladores**, **Configuración** (pestañas General, Ventana principal, Sensores, Modo mini, Diagnóstico, Acerca de), **Mi PC** y **modo mini** flotante. La UI se actualiza en `_render(snapshot)` vía `_poll()` (cola desde el hilo).

## Probar
- En Windows: `python tempia.py` (para sensores reales: consola como administrador y `lhm/` descargado con `descargar_lhm.ps1`).
- **Modo demo** (datos falsos, sirve en cualquier sistema): `set TEMPIA_DEMO=1` y luego `python tempia.py` (PowerShell: `$env:TEMPIA_DEMO=1`).
- El workflow de GitHub tiene pasos de prueba en Windows ("Probar que los sensores cargan" y "Probar Mi PC"); revisar su log antes de publicar. Se puede lanzar sin publicar desde Actions → "Compilar Tempia.exe" → **Run workflow** (deja el .exe como artifact).

## Publicar una versión nueva
1. Cambiar `APP_VERSION` en `tempia.py` (ej. `"1.2.0"`).
2. Escribir `notas/v1.2.0.md`.
3. Commit + push a `main`.
4. `git tag v1.2.0 && git push origin v1.2.0` → GitHub Actions compila `Tempia.exe` y crea la Release (≈2 min). El workflow también pone la versión del tag en `APP_VERSION`.
5. Los usuarios ven "⬆ Nueva v1.2.0" en Tempia y se actualiza sola.
6. Actualizar la tarjeta en la página (abajo).

Autor de los commits: `Boldo3108 <135436320+boldo3108@users.noreply.github.com>`.

## Página boldo3108.cl (repo `boldo3108/portafolio`, Vercel, se publica solo al hacer push a `main`)
- `index.html` → arreglo `APPS` (entrada `id:"tempia"`: `ver`, `size`, `d`, `url`), escena animada `SCENES.tempia` (CSS `.s-tempia`), arreglo `LOG` (bitácora; la primera entrada con `tag:"apps"` y `nuevo:"Tempia X.Y"` sale en Inicio como "Nuevo").
- `api/descargas.js` → contador de descargas desde GitHub (`tempia: 'tempia'` ya está).
- Al sacar versión: cambiar `ver:"vX.Y.Z"` (y `size` si cambia; el .exe pesa ~17 MB) y, si es algo importante, agregar entrada en `LOG`.

## Cosas importantes / trampas conocidas
- **Temperatura del CPU**: sin PawnIO (https://pawnio.eu/) no hay temp. CPU, placa madre ni ventiladores. LHM 0.9.6 ya no usa WinRing0. La app muestra el botón "Descargar PawnIO" en el aviso, en la tarjeta del CPU y en Diagnóstico.
- El .exe se compila con `--uac-admin` (pide administrador siempre) y `--onefile`.
- PyInstaller necesita `--collect-all pythonnet --collect-all clr_loader --hidden-import pynvml` y `--add-data "lhm;lhm"`.
- Las DLL de LHM se cargan desde `sys._MEIPASS/lhm` (`resource_path`).
- **Nada se ha probado aún en el PC real del dueño** con el .exe (solo modo demo + pruebas en el Windows de GitHub). Lo primero es probarlo y arreglar lo que falle.
- La release v1.1.0 tiene "Mi PC" roto (se puede borrar); la buena es v1.1.1.

## Pendientes / ideas (ordenadas por prioridad)
1. Probar el .exe en el PC del dueño (temp. CPU con PawnIO, control de ventiladores, actualización automática 1.1.1 → siguiente).
2. Si Tempia se cae o se mata desde el Administrador de tareas, los ventiladores quedan en la última velocidad manual: agregar un respaldo (ej. proceso vigía o tarea que los devuelva a automático).
3. Ícono en la bandeja del sistema + iniciar con Windows (necesario para que la curva de ventiladores funcione siempre).
4. Overlay para OBS (fuente de navegador con fondo transparente) — el dueño hace stream en Twitch.
5. Alertas (notificación de Windows / sonido) al pasar la temperatura límite.
6. FPS en el modo mini (PresentMon o similar).
7. Historial de temperaturas por sesión + exportar.
8. Curva de ventiladores editable (arrastrar puntos).
9. Mostrar más de una GPU.
