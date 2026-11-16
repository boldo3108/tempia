# Tempia

Monitor de temperaturas, uso y ventiladores para Windows. Creado por **[Boldo3108](https://boldo3108.cl)**.

**[⬇ Descargar Tempia.exe](https://github.com/boldo3108/tempia/releases/latest/download/Tempia.exe)**

## Qué muestra
- CPU: temperatura, uso total y por hilo, frecuencia, potencia
- GPU: temperatura, hotspot, memoria, uso, VRAM, potencia, ventilador, relojes
- RAM, discos (espacio, temperatura, lectura/escritura) y red
- Ventiladores: RPM y control (Automático / Manual / Curva según temperatura)
- Modo mini flotante y configurable
- Se actualiza sola desde las Releases de este repositorio

## Temperatura del CPU
Windows no permite leer la temperatura del CPU, la placa madre ni los ventiladores sin un driver.
Instala **[PawnIO](https://pawnio.eu/)** (gratis), reinicia el PC y abre Tempia (pide permisos de administrador).

## Compilar
- Automático: subir un tag `vX.Y.Z` → GitHub Actions compila `Tempia.exe` y crea la Release.
- Manual en tu PC: doble clic en `CONSTRUIR_TEMPIA.bat`.

Sensores: [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (MPL-2.0), NVIDIA NVML, psutil.
