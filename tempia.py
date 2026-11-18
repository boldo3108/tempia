# -*- coding: utf-8 -*-
"""
Tempia - Monitor de temperaturas, uso y ventiladores
Autor: Boldo3108

Fuentes de datos:
  - psutil ............ uso de CPU, RAM, discos y red
  - NVIDIA NVML ....... temperatura / uso / VRAM / potencia de GPUs NVIDIA (sin admin)
  - LibreHardwareMonitorLib (vía pythonnet) ... temperatura CPU, hotspot GPU,
    SSD/HDD, placa madre, ventiladores y CONTROL de ventiladores
    (requiere admin + driver PawnIO)
  - ACPI (WMI) ........ temperatura aproximada de respaldo
"""
import os
import sys
import json
import time
import queue
import random
import platform
import threading
import webbrowser
import subprocess
from collections import deque

import tkinter as tk

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

APP_NAME = "Tempia"
APP_VERSION = "1.1.0"
APP_AUTHOR = "Boldo3108"
GITHUB_REPO = "boldo3108/tempia"
GITHUB_PAGE = f"https://github.com/{GITHUB_REPO}"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
WEB_PAGE = "https://boldo3108.cl"
PAWNIO_URL = "https://pawnio.eu/"
IS_WIN = sys.platform.startswith("win")
DEMO = os.environ.get("TEMPIA_DEMO") == "1"

# --------------------------------------------------------------------------- #
#  Tema
# --------------------------------------------------------------------------- #
BG = "#0b0f17"
CARD = "#141a25"
CARD_2 = "#1a2130"
BORDER = "#242d3d"
TRACK = "#232b3a"
TEXT = "#e8edf5"
MUTED = "#8a94a8"
DIM = "#5b6578"
ACCENT = "#22d3ee"
ACTIVE_BG = "#0e7490"
CPU_COLOR = "#38bdf8"
GPU_COLOR = "#a3e635"
RAM_COLOR = "#c084fc"
DISK_COLOR = "#f59e0b"
FAN_COLOR = "#2dd4bf"
NET_DOWN = "#22d3ee"
NET_UP = "#f472b6"
OK_GREEN = "#34d399"
WARN_YELLOW = "#fbbf24"
WARN_ORANGE = "#fb923c"
HOT_RED = "#f87171"

FONT = "Segoe UI" if IS_WIN else "DejaVu Sans"
FONT_MONO = "Consolas" if IS_WIN else "DejaVu Sans Mono"

HISTORY = 120

# Ventiladores
FAN_MIN = 20            # % mínimo permitido en manual/curva
PUMP_MIN = 50           # % mínimo para bombas
EMERGENCY_CPU = 90      # °C -> todos los ventiladores controlados al 100%
EMERGENCY_GPU = 88
FAN_PRESETS = {
    "silencioso": [(40, 25), (60, 35), (75, 60), (85, 100)],
    "equilibrado": [(35, 30), (55, 45), (70, 70), (80, 100)],
    "rendimiento": [(30, 45), (50, 60), (65, 85), (75, 100)],
}
PRESET_LABEL = {"silencioso": "Silencioso", "equilibrado": "Equilibrado", "rendimiento": "Rendimiento"}
SOURCE_LABEL = {"cpu": "según CPU", "gpu": "según GPU", "max": "según la más caliente"}

# Elementos posibles del modo mini: clave -> (título, color)
MINI_ITEMS = [
    ("cpu_temp", "CPU °", CPU_COLOR),
    ("cpu_use", "CPU %", CPU_COLOR),
    ("cpu_freq", "CPU GHz", CPU_COLOR),
    ("cpu_power", "CPU W", CPU_COLOR),
    ("gpu_temp", "GPU °", GPU_COLOR),
    ("gpu_use", "GPU %", GPU_COLOR),
    ("gpu_hotspot", "Hotspot", GPU_COLOR),
    ("gpu_power", "GPU W", GPU_COLOR),
    ("vram", "VRAM", GPU_COLOR),
    ("ram", "RAM", RAM_COLOR),
    ("fan", "Ventilador", FAN_COLOR),
    ("net_down", "Bajada", NET_DOWN),
    ("net_up", "Subida", NET_UP),
    ("clock", "Hora", MUTED),
]
MINI_ITEM_NAMES = {
    "cpu_temp": "Temperatura CPU", "cpu_use": "Uso CPU", "cpu_freq": "Frecuencia CPU",
    "cpu_power": "Potencia CPU", "gpu_temp": "Temperatura GPU", "gpu_use": "Uso GPU",
    "gpu_hotspot": "Hotspot GPU", "gpu_power": "Potencia GPU", "vram": "VRAM usada",
    "ram": "RAM", "fan": "Ventilador (el más rápido)", "net_down": "Red bajada",
    "net_up": "Red subida", "clock": "Hora",
}

CARD_NAMES = [("cpu", "CPU"), ("gpu", "GPU"), ("ram", "Memoria RAM"), ("disks", "Discos"),
              ("net", "Red"), ("fans", "Ventiladores"), ("others", "Otros sensores")]


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


DEFAULT_CONFIG = {
    "interval": 1.0,
    "fahrenheit": False,
    "topmost": False,
    "geometry": "",
    "cpu_alert": 85,
    "gpu_alert": 83,
    "cards": {k: True for k, _ in CARD_NAMES},
    "show_graphs": True,
    "show_threads": True,
    "hide_zero_fans": True,
    "hidden_sensors": [],
    "check_updates": True,
    "fans": {},
    "fan_names": {},
    "mini": {
        "items": ["cpu_temp", "cpu_use", "gpu_temp", "gpu_use", "ram"],
        "orientation": "horizontal",
        "size": "normal",
        "opacity": 92,
        "labels": True,
        "topmost": True,
        "pos": "",
    },
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
        if data.get("mini_pos") and not cfg["mini"].get("pos"):
            cfg["mini"]["pos"] = data["mini_pos"]
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Formato
# --------------------------------------------------------------------------- #
def temp_color(c, alert=85):
    if c is None:
        return DIM
    if c < alert - 25:
        return OK_GREEN
    if c < alert - 10:
        return WARN_YELLOW
    if c < alert:
        return WARN_ORANGE
    return HOT_RED


def load_color(p):
    if p is None:
        return DIM
    if p < 60:
        return OK_GREEN
    if p < 85:
        return WARN_YELLOW
    return HOT_RED


def fmt_bytes(n, per_sec=False):
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    n = float(n)
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    s = f"{n:.0f} {units[i]}" if i == 0 or n >= 100 else f"{n:.1f} {units[i]}"
    return s + ("/s" if per_sec else "")


def fmt_bits(bytes_per_sec):
    if bytes_per_sec is None:
        return "—"
    bits = bytes_per_sec * 8
    for unit in ["bps", "Kbps", "Mbps", "Gbps"]:
        if bits < 1000:
            return f"{bits:.0f} {unit}" if unit == "bps" else f"{bits:.1f} {unit}"
        bits /= 1000.0
    return f"{bits:.1f} Tbps"


def cpu_model_name():
    if IS_WIN:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return name.strip()
        except Exception:
            pass
    else:
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
    return platform.processor() or "CPU"


def is_admin():
    if not IS_WIN:
        try:
            return os.geteuid() == 0
        except Exception:
            return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def pawnio_status():
    """Devuelve (instalado, estado_texto)."""
    if not IS_WIN:
        return False, "no aplica (no es Windows)"
    installed = False
    try:
        import winreg
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\PawnIO")
        installed = True
    except Exception:
        pass
    state = "no instalado"
    try:
        out = subprocess.run(["sc", "query", "PawnIO"], capture_output=True, text=True,
                             timeout=4, creationflags=0x08000000).stdout
        if "RUNNING" in out:
            state, installed = "instalado y en ejecución", True
        elif "STOPPED" in out:
            state, installed = "instalado (detenido)", True
        elif installed:
            state = "instalado"
    except Exception:
        if installed:
            state = "instalado"
    return installed, state


# --------------------------------------------------------------------------- #
#  Actualizaciones (GitHub Releases)
# --------------------------------------------------------------------------- #
def _version_tuple(v):
    out = []
    for part in str(v).lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def is_newer(remote, local=APP_VERSION):
    return bool(remote) and _version_tuple(remote) > _version_tuple(local)


def check_latest_release(timeout=10):
    """Devuelve {"version", "notas", "pagina", "exe"} o None si todavía no hay versiones publicadas."""
    import urllib.request
    import urllib.error
    req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json",
                                                        "User-Agent": f"Tempia/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub respondió {e.code}")
    exe = next((a["browser_download_url"] for a in d.get("assets", [])
                if str(a.get("name", "")).lower() == "tempia.exe"), "")
    return {"version": str(d.get("tag_name") or d.get("name") or "").lstrip("vV").strip(),
            "notas": (d.get("body") or "").strip(),
            "pagina": d.get("html_url") or RELEASES_PAGE,
            "exe": exe}


def download_file(url, dest, progress=lambda f: None):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": f"Tempia/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                progress(done / total)
    return dest


def start_self_update(new_exe):
    """Abre el Tempia.exe nuevo para que se copie encima de este. Luego hay que cerrar la app."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("La actualización automática funciona solo en Tempia.exe")
    subprocess.Popen([new_exe, "--instalar", sys.executable, "--pid", str(os.getpid())],
                     cwd=os.path.dirname(new_exe))


def install_if_requested():
    """Si este .exe se abrió con --instalar, se copia encima del viejo, lo abre y devuelve True."""
    if "--instalar" not in sys.argv:
        return False
    args = sys.argv
    target = args[args.index("--instalar") + 1]
    pid = int(args[args.index("--pid") + 1]) if "--pid" in args else 0
    error = None
    try:
        if pid and IS_WIN:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if h:
                k.WaitForSingleObject(h, 60000)
                k.CloseHandle(h)
        import shutil
        for _ in range(40):
            try:
                with open(sys.executable, "rb") as fo, open(target, "wb") as fd:
                    shutil.copyfileobj(fo, fd, 1024 * 1024)
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Windows no soltó el archivo Tempia.exe")
    except Exception as e:
        error = e
    if error and IS_WIN:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"No se pudo terminar la actualización:\n{error}\n\n"
                                                   f"Descarga la versión nueva desde {RELEASES_PAGE}",
                                             "Tempia", 0x10)
        except Exception:
            pass
    try:
        subprocess.Popen([target], cwd=os.path.dirname(target))
    except Exception:
        pass
    return True


# --------------------------------------------------------------------------- #
#  Backends
# --------------------------------------------------------------------------- #
class LHMBackend:
    """LibreHardwareMonitorLib cargado con pythonnet."""

    def __init__(self):
        self.ok = False
        self.error = None
        self.computer = None
        self.sensor_map = {}
        if not IS_WIN:
            self.error = "Solo disponible en Windows"
            return
        try:
            folder = resource_path("lhm")
            dll = os.path.join(folder, "LibreHardwareMonitorLib.dll")
            if not os.path.exists(dll):
                raise FileNotFoundError("No se encontró LibreHardwareMonitorLib.dll dentro de Tempia "
                                        "(vuelve a ejecutar CONSTRUIR_TEMPIA.bat con internet)")
            if folder not in sys.path:
                sys.path.append(folder)
            try:
                from pythonnet import load
                load("netfx")
            except Exception:
                pass
            import clr  # noqa: F401
            clr.AddReference(dll)
            from LibreHardwareMonitor.Hardware import Computer  # type: ignore

            c = Computer()
            c.IsCpuEnabled = True
            c.IsGpuEnabled = True
            c.IsMotherboardEnabled = True
            c.IsStorageEnabled = True
            c.IsControllerEnabled = True
            c.IsMemoryEnabled = False
            c.IsNetworkEnabled = False
            c.Open()
            self.computer = c
            self.ok = True
        except Exception as e:  # pragma: no cover
            self.error = f"{type(e).__name__}: {e}"

    @staticmethod
    def _val(v):
        try:
            return None if v is None else float(v)
        except Exception:
            return None

    def _walk(self, hw, root_type=None):
        try:
            hw.Update()
        except Exception:
            pass
        htype = str(hw.HardwareType)
        item = {"type": root_type or htype, "subtype": htype, "name": str(hw.Name), "sensors": []}
        for s in hw.Sensors:
            ident = str(s.Identifier)
            self.sensor_map[ident] = s
            try:
                idx = int(s.Index)
            except Exception:
                idx = 0
            item["sensors"].append((str(s.SensorType), str(s.Name), self._val(s.Value), ident, idx))
        out = [item]
        for sub in hw.SubHardware:
            out.extend(self._walk(sub, root_type or htype))
        return out

    def read(self):
        if not self.ok:
            return []
        res = []
        try:
            for hw in self.computer.Hardware:
                res.extend(self._walk(hw))
        except Exception as e:  # pragma: no cover
            self.error = str(e)
        return res

    # ---- control de ventiladores ----
    def control_of(self, ident):
        s = self.sensor_map.get(ident)
        if s is None:
            return None
        try:
            return s.Control
        except Exception:
            return None

    def set_fan(self, ident, pct):
        ctl = self.control_of(ident)
        if ctl is None:
            raise RuntimeError("sin control")
        try:
            lo, hi = float(ctl.MinSoftwareValue), float(ctl.MaxSoftwareValue)
            pct = max(lo, min(hi, pct))
        except Exception:
            pass
        ctl.SetSoftware(float(pct))

    def default_fan(self, ident):
        ctl = self.control_of(ident)
        if ctl is not None:
            ctl.SetDefault()

    def close(self):
        try:
            if self.computer is not None:
                self.computer.Close()
        except Exception:
            pass


class ACPIThermal:
    """Temperatura aproximada vía WMI (MSAcpi_ThermalZoneTemperature). Respaldo."""

    def __init__(self):
        self.ok = False
        self.searcher = None
        self.last = None
        self.last_t = 0
        if not IS_WIN:
            return
        try:
            import clr
            clr.AddReference("System.Management")
            from System.Management import ManagementObjectSearcher  # type: ignore
            self.searcher = ManagementObjectSearcher(
                "root\\WMI", "SELECT CurrentTemperature FROM MSAcpi_ThermalZoneTemperature")
            self.ok = self.read() is not None
        except Exception:
            self.ok = False

    def read(self):
        if self.searcher is None:
            return None
        if time.time() - self.last_t < 3 and self.last_t:
            return self.last
        self.last_t = time.time()
        vals = []
        try:
            for o in self.searcher.Get():
                v = float(str(o["CurrentTemperature"])) / 10.0 - 273.15
                if 15 < v < 115:
                    vals.append(v)
        except Exception:
            pass
        self.last = max(vals) if vals else None
        return self.last


class NVMLBackend:
    def __init__(self):
        self.ok = False
        self.handles = []
        self.nv = None
        self.error = None
        try:
            import pynvml as nv
            nv.nvmlInit()
            self.nv = nv
            for i in range(nv.nvmlDeviceGetCount()):
                self.handles.append(nv.nvmlDeviceGetHandleByIndex(i))
            self.ok = len(self.handles) > 0
        except Exception as e:
            self.error = str(e)

    def read(self):
        gpus = []
        if not self.ok:
            return gpus
        nv = self.nv

        def safe(fn, *a):
            try:
                return fn(*a)
            except Exception:
                return None

        for h in self.handles:
            g = {}
            name = safe(nv.nvmlDeviceGetName, h)
            if isinstance(name, bytes):
                name = name.decode(errors="ignore")
            g["name"] = name or "GPU NVIDIA"
            g["temp"] = safe(nv.nvmlDeviceGetTemperature, h, nv.NVML_TEMPERATURE_GPU)
            util = safe(nv.nvmlDeviceGetUtilizationRates, h)
            g["usage"] = float(util.gpu) if util is not None else None
            mem = safe(nv.nvmlDeviceGetMemoryInfo, h)
            if mem is not None:
                g["mem_used"] = mem.used
                g["mem_total"] = mem.total
            p = safe(nv.nvmlDeviceGetPowerUsage, h)
            g["power"] = p / 1000.0 if p is not None else None
            pl = safe(nv.nvmlDeviceGetEnforcedPowerLimit, h)
            g["power_limit"] = pl / 1000.0 if pl is not None else None
            g["fan"] = safe(nv.nvmlDeviceGetFanSpeed, h)
            g["clock"] = safe(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_GRAPHICS)
            g["mem_clock"] = safe(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_MEM)
            gpus.append(g)
        return gpus

    def close(self):
        try:
            if self.ok:
                self.nv.nvmlShutdown()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Mi PC: marca, modelo y detalles de los componentes
# --------------------------------------------------------------------------- #
SPECS_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function Sel($cls, $props, $ns) {
  if ($ns) { @(Get-CimInstance -Namespace $ns -ClassName $cls | Select-Object $props) }
  else { @(Get-CimInstance -ClassName $cls | Select-Object $props) }
}
$o = [ordered]@{}
$o.system = Sel Win32_ComputerSystem Manufacturer,Model,SystemType,TotalPhysicalMemory
$o.os = Sel Win32_OperatingSystem Caption,Version,BuildNumber,OSArchitecture,InstallDate,LastBootUpTime
$o.board = Sel Win32_BaseBoard Manufacturer,Product,Version
$o.bios = Sel Win32_BIOS Manufacturer,SMBIOSBIOSVersion,ReleaseDate
$o.cpu = Sel Win32_Processor Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,L2CacheSize,L3CacheSize,SocketDesignation
$o.ram = Sel Win32_PhysicalMemory Manufacturer,PartNumber,Capacity,Speed,ConfiguredClockSpeed,DeviceLocator,BankLabel,SMBIOSMemoryType,ConfiguredVoltage
$o.ramarray = Sel Win32_PhysicalMemoryArray MemoryDevices,MaxCapacityEx
$o.gpu = Sel Win32_VideoController Name,AdapterCompatibility,DriverVersion,DriverDate,CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate,AdapterRAM,PNPDeviceID
$o.gpumem = @(Get-ItemProperty 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' |
  Select-Object DriverDesc,@{n='Mem';e={$_.'HardwareInformation.qwMemorySize'}})
$o.disks = Sel MSFT_PhysicalDisk FriendlyName,MediaType,BusType,Size,HealthStatus root/Microsoft/Windows/Storage
$o.disks2 = Sel Win32_DiskDrive Model,Size,InterfaceType
$o.monitors = @(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorID | ForEach-Object {
  [pscustomobject]@{
    Manufacturer = (($_.ManufacturerName | Where-Object { $_ -ne 0 } | ForEach-Object { [char]$_ }) -join '')
    Name = (($_.UserFriendlyName | Where-Object { $_ -ne 0 } | ForEach-Object { [char]$_ }) -join '')
    Year = $_.YearOfManufacture } })
$o.net = @(Get-CimInstance -ClassName Win32_NetworkAdapter -Filter 'PhysicalAdapter=True' |
  Where-Object { $_.PNPDeviceID -notlike 'ROOT*' -and $_.PNPDeviceID -notlike 'SWD*' } |
  Select-Object Name,Manufacturer,Speed,NetEnabled)
$o.sound = Sel Win32_SoundDevice Name,Manufacturer
$o | ConvertTo-Json -Depth 4 -Compress
"""

RAM_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3",
             30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}
RAM_MAKERS = {"80CE": "Samsung", "CE00": "Samsung", "80AD": "SK hynix", "AD00": "SK hynix",
              "802C": "Micron", "2C00": "Micron", "04CD": "G.Skill", "029E": "Corsair", "9E": "Corsair",
              "0198": "Kingston", "859B": "Crucial", "04F1": "TeamGroup", "0D9B": "Crucial",
              "02FE": "Elpida", "0443": "Ramaxel", "8551": "Qimonda", "04CB": "A-DATA", "0B": "Nanya"}
RAM_PART_HINTS = [("CMK", "Corsair"), ("CMW", "Corsair"), ("CMH", "Corsair"), ("CMT", "Corsair"),
                  ("CMR", "Corsair"), ("F4-", "G.Skill"), ("F5-", "G.Skill"), ("KF", "Kingston Fury"),
                  ("KHX", "Kingston HyperX"), ("BL", "Crucial Ballistix"), ("CT", "Crucial"),
                  ("TF", "TeamGroup"), ("TD", "TeamGroup"), ("TLZ", "TeamGroup"), ("AX4", "ADATA XPG"),
                  ("AX5", "ADATA XPG"), ("M378", "Samsung"), ("M471", "Samsung"), ("HMA", "SK hynix"),
                  ("PSD", "Patriot"), ("PVS", "Patriot")]
DISK_MEDIA = {3: "HDD", 4: "SSD", 5: "SCM"}
DISK_BUS = {1: "SCSI", 3: "ATA", 6: "Fibre Channel", 7: "USB", 8: "RAID", 10: "SAS", 11: "SATA",
            12: "SD", 17: "NVMe"}


def _list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _clean(s):
    s = str(s or "").strip()
    return "" if s.lower() in ("", "to be filled by o.e.m.", "default string", "system manufacturer",
                               "system product name", "none", "not applicable", "o.e.m.", "unknown") else s


def _gb(n, dec=0):
    try:
        v = float(n) / 1024 ** 3
    except Exception:
        return ""
    return f"{v:.{dec}f} GB" if v < 1024 else f"{v / 1024:.1f} TB"


def _disk_size(n):
    try:
        v = float(n) / 1e9
    except Exception:
        return ""
    return f"{v:.0f} GB" if v < 1000 else f"{v / 1000:.1f} TB"


def _date(s):
    if isinstance(s, dict):
        s = s.get("value") or s.get("DateTime") or ""
    s = str(s or "")
    import re
    m = re.search(r"/Date\((\d+)", s)
    if m:
        return time.strftime("%d-%m-%Y", time.localtime(int(m.group(1)) / 1000))
    m = re.match(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return s[:10]


def ram_maker(m):
    maker = _clean(m.get("Manufacturer"))
    part = _clean(m.get("PartNumber"))
    key = maker.upper().replace(" ", "")
    if key in RAM_MAKERS:
        maker = RAM_MAKERS[key]
    elif key[:4] in RAM_MAKERS and all(c in "0123456789ABCDEF" for c in key):
        maker = RAM_MAKERS[key[:4]]
    if (not maker or all(c in "0123456789ABCDEF" for c in key)) and part:
        for pre, name in RAM_PART_HINTS:
            if part.upper().startswith(pre):
                return name
    return maker


def parse_specs(raw, gpu_live=None):
    """Convierte el JSON de PowerShell en secciones: [(titulo, color, [(subtitulo, [(etiqueta, valor)])])]."""
    sec = []
    summary = []
    sysi = (_list(raw.get("system")) or [{}])[0]
    osi = (_list(raw.get("os")) or [{}])[0]
    rows = []
    maker, model = _clean(sysi.get("Manufacturer")), _clean(sysi.get("Model"))
    if maker or model:
        rows.append(("Equipo", f"{maker} {model}".strip()))
    if osi.get("Caption"):
        rows.append(("Sistema", f"{osi.get('Caption')} {osi.get('OSArchitecture') or ''}".strip()))
        rows.append(("Versión", f"{osi.get('Version')} (compilación {osi.get('BuildNumber')})"))
    if osi.get("InstallDate"):
        rows.append(("Instalado el", _date(osi.get("InstallDate"))))
    if osi.get("LastBootUpTime"):
        rows.append(("Encendido desde", _date(osi.get("LastBootUpTime"))))
    if rows:
        sec.append(("Sistema", ACCENT, [(None, rows)]))

    items = []
    for b in _list(raw.get("board")):
        r = [("Marca", _clean(b.get("Manufacturer"))), ("Modelo", _clean(b.get("Product")))]
        if _clean(b.get("Version")):
            r.append(("Revisión", _clean(b.get("Version"))))
        items.append((None, r))
        summary.append(_clean(b.get("Product")))
    for b in _list(raw.get("bios")):
        items.append(("BIOS", [("Marca", _clean(b.get("Manufacturer"))),
                               ("Versión", _clean(b.get("SMBIOSBIOSVersion"))),
                               ("Fecha", _date(b.get("ReleaseDate")))]))
    if items:
        sec.append(("Placa madre", WARN_ORANGE, items))

    items = []
    for c in _list(raw.get("cpu")):
        name = " ".join(str(c.get("Name") or "").split())
        r = [("Modelo", name), ("Marca", _clean(c.get("Manufacturer")).replace("AuthenticAMD", "AMD")
                                         .replace("GenuineIntel", "Intel")),
             ("Núcleos / hilos", f"{c.get('NumberOfCores')} / {c.get('NumberOfLogicalProcessors')}")]
        if c.get("MaxClockSpeed"):
            r.append(("Frecuencia base", f"{c['MaxClockSpeed'] / 1000:.2f} GHz"))
        if c.get("L2CacheSize"):
            r.append(("Caché L2", f"{c['L2CacheSize'] / 1024:.0f} MB" if c["L2CacheSize"] >= 1024 else f"{c['L2CacheSize']} KB"))
        if c.get("L3CacheSize"):
            r.append(("Caché L3", f"{c['L3CacheSize'] / 1024:.0f} MB" if c["L3CacheSize"] >= 1024 else f"{c['L3CacheSize']} KB"))
        if _clean(c.get("SocketDesignation")):
            r.append(("Socket", _clean(c.get("SocketDesignation"))))
        items.append((None, r))
        summary.insert(0, name)
    if items:
        sec.append(("Procesador", CPU_COLOR, items))

    sticks = _list(raw.get("ram"))
    if sticks:
        total = sum(int(m.get("Capacity") or 0) for m in sticks)
        rtype = RAM_TYPES.get(int(sticks[0].get("SMBIOSMemoryType") or 0), "")
        speed = sticks[0].get("ConfiguredClockSpeed") or sticks[0].get("Speed")
        arr = (_list(raw.get("ramarray")) or [{}])[0]
        slots = arr.get("MemoryDevices")
        head = [("Total", f"{_gb(total)} {rtype}".strip()),
                ("Módulos", f"{len(sticks)}" + (f" de {slots} ranuras" if slots else ""))]
        if speed:
            head.append(("Velocidad actual", f"{speed} MT/s"))
        if arr.get("MaxCapacityEx"):
            head.append(("Máximo soportado", _gb(int(arr["MaxCapacityEx"]) * 1024)))
        items = [(None, head)]
        for m in sticks:
            r = [("Marca", ram_maker(m) or "—"), ("Modelo", _clean(m.get("PartNumber")) or "—"),
                 ("Capacidad", _gb(m.get("Capacity")))]
            sp, cs = m.get("Speed"), m.get("ConfiguredClockSpeed")
            if sp:
                r.append(("Velocidad", f"{sp} MT/s" + (f" (usando {cs})" if cs and cs != sp else "")))
            if m.get("ConfiguredVoltage"):
                r.append(("Voltaje", f"{m['ConfiguredVoltage'] / 1000:.2f} V"))
            items.append((_clean(m.get("DeviceLocator")) or _clean(m.get("BankLabel")) or "Módulo", r))
        sec.append(("Memoria RAM", RAM_COLOR, items))
        summary.append(f"{_gb(total)} {rtype}" + (f" {speed} MT/s" if speed else ""))

    mems = {str(g.get("DriverDesc")): g.get("Mem") for g in _list(raw.get("gpumem")) if g.get("Mem")}
    items = []
    for i, g in enumerate(_list(raw.get("gpu"))):
        name = str(g.get("Name") or "")
        if "basic display" in name.lower() or "virtual" in name.lower() and len(_list(raw.get("gpu"))) > 1:
            continue
        r = [("Modelo", name), ("Marca", _clean(g.get("AdapterCompatibility")))]
        vram = None
        if gpu_live and i == 0 and gpu_live.get("mem_total"):
            vram = gpu_live["mem_total"]
        elif mems.get(name):
            vram = mems[name]
        elif g.get("AdapterRAM"):
            vram = g["AdapterRAM"]
        if vram:
            r.append(("Memoria (VRAM)", _gb(vram)))
        if g.get("DriverVersion"):
            r.append(("Controlador", f"{g['DriverVersion']}  ·  {_date(g.get('DriverDate'))}"))
        if g.get("CurrentHorizontalResolution"):
            r.append(("Pantalla", f"{g['CurrentHorizontalResolution']} × {g['CurrentVerticalResolution']}"
                                  f" a {g.get('CurrentRefreshRate')} Hz"))
        items.append((None, r))
        summary.append(name)
    if items:
        sec.append(("Tarjeta de video", GPU_COLOR, items))

    items = []
    disks = _list(raw.get("disks"))
    if disks:
        for d in disks:
            items.append((None, [("Modelo", str(d.get("FriendlyName") or "")),
                                 ("Tipo", " ".join(x for x in (DISK_MEDIA.get(d.get("MediaType"), ""),
                                                               DISK_BUS.get(d.get("BusType"), "")) if x) or "—"),
                                 ("Capacidad", _disk_size(d.get("Size"))),
                                 ("Estado", {"Healthy": "Saludable", "Warning": "Advertencia",
                                             "Unhealthy": "Con problemas"}.get(str(d.get("HealthStatus")),
                                                                                str(d.get("HealthStatus") or "—")))]))
    else:
        for d in _list(raw.get("disks2")):
            items.append((None, [("Modelo", str(d.get("Model") or "")), ("Conexión", str(d.get("InterfaceType") or "")),
                                 ("Capacidad", _disk_size(d.get("Size")))]))
    if items:
        sec.append(("Almacenamiento", DISK_COLOR, items))

    items = []
    for m in _list(raw.get("monitors")):
        r = [("Modelo", m.get("Name") or "—"), ("Marca (código)", m.get("Manufacturer") or "—")]
        if m.get("Year"):
            r.append(("Fabricado", str(m["Year"])))
        items.append((None, r))
    if items:
        sec.append(("Monitores", NET_UP, items))

    items = []
    for n in _list(raw.get("net")):
        sp = n.get("Speed")
        r = [("Adaptador", str(n.get("Name") or "")), ("Marca", _clean(n.get("Manufacturer")))]
        if sp and n.get("NetEnabled"):
            sp = int(sp)
            r.append(("Velocidad", f"{sp / 1e9:g} Gbps" if sp >= 1e9 else f"{sp / 1e6:g} Mbps"))
        r.append(("Estado", "Conectado" if n.get("NetEnabled") else "Desconectado"))
        items.append((None, r))
    if items:
        sec.append(("Red", NET_DOWN, items))

    items = [(None, [("Dispositivo", str(s.get("Name") or "")), ("Marca", _clean(s.get("Manufacturer")))])
             for s in _list(raw.get("sound"))]
    if items:
        sec.append(("Audio", FAN_COLOR, items))

    # limpiar filas vacías
    out = []
    for title, color, its in sec:
        its2 = [(sub, [(k, v) for k, v in rows if str(v or "").strip()]) for sub, rows in its]
        out.append((title, color, [x for x in its2 if x[1]]))
    return out, [s for s in summary if s]


def read_specs():
    """Lee los componentes con PowerShell/CIM (unos segundos)."""
    if DEMO or not IS_WIN:
        return DEMO_SPECS
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"],
                       input=SPECS_PS, capture_output=True, text=True, encoding="utf-8",
                       timeout=60, creationflags=0x08000000)
    out = (r.stdout or "").strip()
    start = out.find("{")
    if start < 0:
        raise RuntimeError((r.stderr or "PowerShell no devolvió datos").strip()[:200])
    return json.loads(out[start:])


DEMO_SPECS = {
    "system": {"Manufacturer": "To Be Filled By O.E.M.", "Model": "To Be Filled By O.E.M."},
    "os": {"Caption": "Microsoft Windows 11 Pro", "Version": "10.0.26200", "BuildNumber": "26200",
           "OSArchitecture": "64 bits", "InstallDate": "/Date(1717000000000)/", "LastBootUpTime": "/Date(1763460000000)/"},
    "board": {"Manufacturer": "ASUSTeK COMPUTER INC.", "Product": "ROG STRIX B550-F GAMING", "Version": "Rev X.0x"},
    "bios": {"Manufacturer": "American Megatrends Inc.", "SMBIOSBIOSVersion": "3621", "ReleaseDate": "/Date(1700000000000)/"},
    "cpu": {"Name": "AMD Ryzen 9 5950X 16-Core Processor            ", "Manufacturer": "AuthenticAMD",
            "NumberOfCores": 16, "NumberOfLogicalProcessors": 32, "MaxClockSpeed": 3401, "L2CacheSize": 8192,
            "L3CacheSize": 65536, "SocketDesignation": "AM4"},
    "ram": [{"Manufacturer": "029E", "PartNumber": "CMK16GX4M2E3200C16  ", "Capacity": 8589934592 * 2, "Speed": 3200,
             "ConfiguredClockSpeed": 3200, "DeviceLocator": "DIMM_A2", "SMBIOSMemoryType": 26, "ConfiguredVoltage": 1350},
            {"Manufacturer": "029E", "PartNumber": "CMK16GX4M2E3200C16  ", "Capacity": 8589934592 * 2, "Speed": 3200,
             "ConfiguredClockSpeed": 3200, "DeviceLocator": "DIMM_B2", "SMBIOSMemoryType": 26, "ConfiguredVoltage": 1350}],
    "ramarray": {"MemoryDevices": 4, "MaxCapacityEx": 134217728},
    "gpu": {"Name": "NVIDIA GeForce RTX 4070 SUPER", "AdapterCompatibility": "NVIDIA", "DriverVersion": "32.0.15.8157",
            "DriverDate": "/Date(1760000000000)/", "CurrentHorizontalResolution": 2560, "CurrentVerticalResolution": 1440,
            "CurrentRefreshRate": 165, "AdapterRAM": 4293918720},
    "gpumem": {"DriverDesc": "NVIDIA GeForce RTX 4070 SUPER", "Mem": 12878610432},
    "disks": [{"FriendlyName": "Samsung SSD 980 1TB", "MediaType": 4, "BusType": 17, "Size": 1000204886016, "HealthStatus": "Healthy"},
              {"FriendlyName": "ST1000DM010-2EP102", "MediaType": 3, "BusType": 11, "Size": 1000204886016, "HealthStatus": "Healthy"}],
    "monitors": [{"Manufacturer": "AUS", "Name": "VG27AQ1A", "Year": 2022}],
    "net": [{"Name": "Realtek Gaming 2.5GbE Family Controller", "Manufacturer": "Realtek", "Speed": 1000000000, "NetEnabled": True},
            {"Name": "Intel(R) Wi-Fi 6 AX200 160MHz", "Manufacturer": "Intel Corporation", "Speed": None, "NetEnabled": False}],
    "sound": [{"Name": "NVIDIA High Definition Audio", "Manufacturer": "NVIDIA"},
              {"Name": "Realtek USB Audio", "Manufacturer": "Realtek"}],
}


def _find(sensors, stype, *names, default=None):
    for n in names:
        for t, sname, v, *_ in sensors:
            if t == stype and sname.lower() == n.lower() and v is not None:
                return v
    for n in names:
        for t, sname, v, *_ in sensors:
            if t == stype and n.lower() in sname.lower() and v is not None:
                return v
    return default


def curve_value(points, temp):
    if temp is None:
        return points[-1][1]
    if temp <= points[0][0]:
        return points[0][1]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        if temp <= t1:
            return p0 + (p1 - p0) * (temp - t0) / (t1 - t0)
    return 100


GROUP_NAMES = {"Cpu": "CPU", "Motherboard": "Placa madre", "Storage": "Almacenamiento",
               "Cooler": "Refrigeración", "EmbeddedController": "Placa madre",
               "GpuNvidia": "GPU", "GpuAmd": "GPU", "GpuIntel": "GPU", "SuperIO": "Placa madre"}


class Monitor:
    """Recolecta datos y aplica el control de ventiladores (en un hilo aparte)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lhm = None
        self.nvml = None
        self.acpi = None
        self.cpu_name = cpu_model_name()
        self.cores = psutil.cpu_count(logical=False) if psutil else None
        self.threads = psutil.cpu_count(logical=True) if psutil else None
        self._last_net = None
        self._last_disk = None
        self._last_t = None
        self.admin = is_admin()
        self.pawnio = (False, "desconocido")
        self.last_hw = []
        self.cpu_temp_source = None
        self.fan_state = {}       # ctrl_id -> último % aplicado
        self.fan_errors = {}
        self.reinit_requested = False
        self._demo_fans = {}

    def start(self):
        if not DEMO:
            self.pawnio = pawnio_status()
            self.nvml = NVMLBackend()
            self.lhm = LHMBackend()
        if psutil:
            psutil.cpu_percent(percpu=True)

    def reinit(self):
        self.restore_fans()
        if self.lhm:
            self.lhm.close()
        self.pawnio = pawnio_status()
        self.admin = is_admin()
        self.lhm = LHMBackend()
        self.acpi = None

    def restore_fans(self):
        for ident in list(self.fan_state):
            try:
                if self.lhm and self.lhm.ok:
                    self.lhm.default_fan(ident)
            except Exception:
                pass
        self.fan_state.clear()

    def close(self):
        self.restore_fans()
        if self.lhm:
            self.lhm.close()
        if self.nvml:
            self.nvml.close()

    # -------------------------------------------------------------- #
    def collect(self):
        if self.reinit_requested:
            self.reinit_requested = False
            if not DEMO:
                self.reinit()
        if DEMO:
            snap = self._demo()
            self._apply_fans(snap)
            return snap
        now = time.time()
        snap = {"warnings": []}
        lhm_hw = self.lhm.read() if (self.lhm and self.lhm.ok) else []
        self.last_hw = lhm_hw

        # ---------------- CPU ----------------
        cpu = {"name": self.cpu_name, "cores": self.cores, "threads": self.threads,
               "temp": None, "usage": None, "per_core": [], "freq": None, "power": None,
               "temp_src": None}
        if psutil:
            per = psutil.cpu_percent(percpu=True)
            cpu["per_core"] = per
            cpu["usage"] = sum(per) / len(per) if per else None
            try:
                f = psutil.cpu_freq()
                cpu["freq"] = f.current if f else None
            except Exception:
                pass
        lhm_cpu = [h for h in lhm_hw if h["type"] == "Cpu"]
        if lhm_cpu:
            s = [x for h in lhm_cpu for x in h["sensors"]]
            cpu["name"] = lhm_cpu[0]["name"] or cpu["name"]
            cpu["temp"] = _find(s, "Temperature", "Core (Tctl/Tdie)", "CPU Package",
                                "Tctl/Tdie", "Package", "Tdie", "Tctl", "Core Average", "Core Max")
            if cpu["temp"] is None:
                temps = [v for t, n, v, *_ in s if t == "Temperature" and v]
                cpu["temp"] = max(temps) if temps else None
            if cpu["temp"] is not None:
                cpu["temp_src"] = "lhm"
            cpu["power"] = _find(s, "Power", "Package", "CPU Package")
            clocks = [v for t, n, v, *_ in s if t == "Clock" and n.lower().startswith("core #") and v]
            if clocks:
                cpu["freq"] = sum(clocks) / len(clocks)
        if cpu["temp"] is None and psutil and hasattr(psutil, "sensors_temperatures"):
            try:
                st = psutil.sensors_temperatures()
                for key in ("k10temp", "coretemp", "zenpower", "cpu_thermal"):
                    if key in st and st[key]:
                        cpu["temp"] = max(x.current for x in st[key])
                        cpu["temp_src"] = "os"
                        break
            except Exception:
                pass
        if cpu["temp"] is None and IS_WIN:
            if self.acpi is None:
                self.acpi = ACPIThermal()
            if self.acpi.ok:
                cpu["temp"] = self.acpi.read()
                if cpu["temp"] is not None:
                    cpu["temp_src"] = "acpi"
        self.cpu_temp_source = cpu["temp_src"]
        snap["cpu"] = cpu

        # ---------------- GPU ----------------
        gpus = self.nvml.read() if (self.nvml and self.nvml.ok) else []
        lhm_gpus = [h for h in lhm_hw if h["subtype"].startswith("Gpu")]
        for i, lg in enumerate(lhm_gpus):
            s = lg["sensors"]
            extra = {
                "hotspot": _find(s, "Temperature", "GPU Hot Spot", "Hot Spot"),
                "mem_temp": _find(s, "Temperature", "GPU Memory Junction", "Memory Junction", "GPU Memory"),
            }
            if i < len(gpus):
                gpus[i].update({k: v for k, v in extra.items() if v is not None})
            else:
                used = _find(s, "SmallData", "GPU Memory Used", "D3D Dedicated Memory Used")
                total = _find(s, "SmallData", "GPU Memory Total")
                g = {
                    "name": lg["name"],
                    "temp": _find(s, "Temperature", "GPU Core", "GPU"),
                    "usage": _find(s, "Load", "GPU Core", "D3D 3D"),
                    "mem_used": used * 1024 * 1024 if used is not None else None,
                    "mem_total": total * 1024 * 1024 if total is not None else None,
                    "power": _find(s, "Power", "GPU Package", "GPU Power", "GPU Core"),
                    "fan": _find(s, "Control", "GPU Fan"),
                    "clock": _find(s, "Clock", "GPU Core"),
                    "mem_clock": _find(s, "Clock", "GPU Memory"),
                }
                g.update(extra)
                gpus.append(g)
        snap["gpus"] = gpus

        # ---------------- RAM ----------------
        if psutil:
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()
            snap["ram"] = {"used": vm.total - vm.available, "total": vm.total, "pct": vm.percent,
                           "swap_used": sw.used, "swap_total": sw.total, "swap_pct": sw.percent}
        else:
            snap["ram"] = None

        # ---------------- Discos ----------------
        parts = []
        if psutil:
            for p in psutil.disk_partitions(all=False):
                if "cdrom" in p.opts or not p.fstype:
                    continue
                if not IS_WIN and not (p.mountpoint == "/" or p.mountpoint.startswith(("/home", "/mnt", "/media"))):
                    continue
                try:
                    u = psutil.disk_usage(p.mountpoint)
                except Exception:
                    continue
                label = p.device.rstrip("\\") if IS_WIN else p.mountpoint
                parts.append({"name": label, "pct": u.percent, "used": u.used, "total": u.total})
        snap["parts"] = parts[:6]
        drives = []
        for h in lhm_hw:
            if h["type"] == "Storage" and h["subtype"] == "Storage":
                s = h["sensors"]
                drives.append({"name": h["name"],
                               "temp": _find(s, "Temperature", "Temperature", "Composite"),
                               "activity": _find(s, "Load", "Total Activity")})
        snap["drives"] = drives

        # ---------------- IO / Red ----------------
        dt = (now - self._last_t) if self._last_t else None
        snap["net"] = {"down": None, "up": None}
        snap["disk_io"] = {"read": None, "write": None}
        if psutil:
            try:
                n = psutil.net_io_counters()
                if self._last_net and dt:
                    snap["net"] = {"down": max(0, (n.bytes_recv - self._last_net.bytes_recv) / dt),
                                   "up": max(0, (n.bytes_sent - self._last_net.bytes_sent) / dt)}
                self._last_net = n
            except Exception:
                pass
            try:
                d = psutil.disk_io_counters()
                if d and self._last_disk and dt:
                    snap["disk_io"] = {"read": max(0, (d.read_bytes - self._last_disk.read_bytes) / dt),
                                       "write": max(0, (d.write_bytes - self._last_disk.write_bytes) / dt)}
                self._last_disk = d
            except Exception:
                pass
        self._last_t = now

        # ---------------- Ventiladores ----------------
        fans = []
        for h in lhm_hw:
            grp = GROUP_NAMES.get(h["type"], h["type"])
            fan_s = {idx: (n, v, ident) for t, n, v, ident, idx in h["sensors"] if t == "Fan"}
            ctl_s = {idx: (n, v, ident) for t, n, v, ident, idx in h["sensors"] if t == "Control"}
            for idx in sorted(set(fan_s) | set(ctl_s)):
                f = fan_s.get(idx)
                c = ctl_s.get(idx)
                if c and not f and "fan" not in c[0].lower() and "pump" not in c[0].lower():
                    continue
                ctrl_id = c[2] if c else None
                controllable = bool(ctrl_id and self.lhm.control_of(ctrl_id) is not None)
                fans.append({
                    "id": ctrl_id or f[2],
                    "ctrl_id": ctrl_id,
                    "controllable": controllable,
                    "group": grp,
                    "name": (f or c)[0],
                    "rpm": f[1] if f else None,
                    "pct": c[1] if c else None,
                })
        snap["fans"] = fans
        self._apply_fans(snap)

        # ---------------- Otros sensores (temperaturas) ----------------
        others = []
        for h in lhm_hw:
            grp = GROUP_NAMES.get(h["type"], h["type"])
            for t, n, v, ident, idx in h["sensors"]:
                if v is None or t != "Temperature":
                    continue
                if h["type"] == "Cpu" and n.lower().startswith("core #"):
                    continue
                if v <= 0 or v > 150:
                    continue
                name = n if h["type"] != "Storage" else h["name"]
                others.append({"id": ident, "group": grp, "name": name, "value": v})
        snap["others"] = others

        # ---------------- Avisos ----------------
        snap["warnings"] = self._warnings(cpu)
        return snap

    def _warnings(self, cpu):
        w = []
        if not IS_WIN or DEMO:
            return w
        if cpu["temp"] is None or cpu["temp_src"] == "acpi":
            extra = " (ahora se muestra una temperatura aproximada de ACPI)" if cpu["temp_src"] == "acpi" else ""
            if not self.admin:
                w.append(("admin", "Tempia no tiene permisos de administrador y sin ellos no puede leer la "
                                   "temperatura del CPU" + extra + ". Ciérrala y ábrela con clic derecho → "
                                   "Ejecutar como administrador."))
            elif self.lhm and not self.lhm.ok:
                w.append(("lhm", "No se pudo cargar el lector de sensores: " + str(self.lhm.error) + extra))
            elif not self.pawnio[0]:
                w.append(("pawnio", "Falta el driver PawnIO, necesario para leer la temperatura del CPU, la placa "
                                    "madre y los ventiladores" + extra + ". Instálalo (es gratis), reinicia el PC "
                                    "y vuelve a abrir Tempia."))
            else:
                w.append(("nodata", "PawnIO está " + self.pawnio[1] + ", pero la CPU no entregó temperatura"
                          + extra + ". Reinicia el PC; si sigue igual, abre Configuración → Diagnóstico y "
                                    "mándale el texto a Claude."))
        return w

    # -------------------------------------------------------------- #
    def _apply_fans(self, snap):
        settings = self.cfg.get("fans", {})
        cpu_t = snap["cpu"].get("temp")
        gpu_t = (snap.get("gpus") or [{}])[0].get("temp")
        emergency = (cpu_t is not None and cpu_t >= EMERGENCY_CPU) or \
                    (gpu_t is not None and gpu_t >= EMERGENCY_GPU)
        snap["fan_emergency"] = emergency
        for f in snap.get("fans", []):
            cid = f["ctrl_id"]
            st = settings.get(cid or "", {})
            mode = st.get("mode", "auto") if f["controllable"] else "auto"
            f["mode"] = mode
            f["error"] = self.fan_errors.get(cid)
            if not f["controllable"]:
                continue
            if mode == "auto":
                if cid in self.fan_state:
                    try:
                        if DEMO:
                            self._demo_fans.pop(cid, None)
                        else:
                            self.lhm.default_fan(cid)
                    except Exception:
                        pass
                    self.fan_state.pop(cid, None)
                f["target"] = None
                continue
            if mode == "manual":
                target = float(st.get("value", 50))
            else:
                src = st.get("source", "cpu")
                temps = {"cpu": cpu_t, "gpu": gpu_t,
                         "max": max([x for x in (cpu_t, gpu_t) if x is not None], default=None)}
                target = curve_value(FAN_PRESETS.get(st.get("preset", "equilibrado"),
                                                     FAN_PRESETS["equilibrado"]), temps.get(src))
                prev = self.fan_state.get(cid)
                if prev is not None and target < prev:
                    target = max(target, prev - 4)  # baja suave
            minimum = PUMP_MIN if "pump" in f["name"].lower() or "bomba" in f["name"].lower() else FAN_MIN
            target = max(minimum, min(100.0, target))
            if emergency:
                target = 100.0
            f["target"] = target
            prev = self.fan_state.get(cid)
            if prev is None or abs(prev - target) >= 1:
                try:
                    if DEMO:
                        self._demo_fans[cid] = target
                    else:
                        self.lhm.set_fan(cid, target)
                    self.fan_state[cid] = target
                    self.fan_errors.pop(cid, None)
                except Exception as e:
                    self.fan_errors[cid] = str(e)
                    f["error"] = str(e)

    # -------------------------------------------------------------- #
    def diagnostics(self):
        lines = [f"Tempia {APP_VERSION} — diagnóstico",
                 f"Windows: {platform.platform()}",
                 f"Python: {sys.version.split()[0]}  ·  .exe: {'sí' if getattr(sys, 'frozen', False) else 'no'}",
                 f"Administrador: {'sí' if self.admin else 'NO'}",
                 f"PawnIO: {self.pawnio[1]}",
                 f"LibreHardwareMonitor: {'cargado' if (self.lhm and self.lhm.ok) else 'NO cargado'}"
                 + (f" — {self.lhm.error}" if self.lhm and self.lhm.error else ""),
                 f"NVIDIA NVML: {'ok' if (self.nvml and self.nvml.ok) else 'no'}"
                 + (f" — {self.nvml.error}" if self.nvml and self.nvml.error else ""),
                 f"ACPI: {'ok' if (self.acpi and self.acpi.ok) else 'no'}",
                 f"Fuente temp. CPU: {self.cpu_temp_source or 'ninguna'}",
                 f"CPU: {self.cpu_name}", ""]
        if not self.last_hw:
            lines.append("(LibreHardwareMonitor no entregó hardware)")
        for h in self.last_hw:
            lines.append(f"[{h['subtype']}] {h['name']}")
            for t, n, v, ident, idx in h["sensors"]:
                vv = "—" if v is None else f"{v:.1f}"
                ctl = ""
                if t == "Control" and self.lhm and self.lhm.control_of(ident) is not None:
                    ctl = "  (controlable)"
                lines.append(f"    {t:<12} {n:<30} {vv:>8}   {ident}{ctl}")
        return "\n".join(lines)

    # -------------------------------------------------------------- #
    _demo_state = {"t": 0}

    def _demo(self):
        import math
        st = self._demo_state
        st["t"] += 1
        t = st["t"]
        load = max(1, min(100, 35 + 30 * math.sin(t / 9) + random.uniform(-6, 6)))
        gload = max(0, min(100, 55 + 40 * math.sin(t / 13) + random.uniform(-5, 5)))
        per = [max(0, min(100, load + random.uniform(-30, 30))) for _ in range(32)]

        def fan(cid, name, base_rpm, max_rpm, default_pct, grp="Placa madre"):
            pct = self._demo_fans.get(cid, default_pct)
            return {"id": cid, "ctrl_id": cid, "controllable": True, "group": grp, "name": name,
                    "rpm": base_rpm + (max_rpm - base_rpm) * pct / 100 + random.uniform(-15, 15), "pct": pct}
        fans = [
            fan("/lpc/nct6798d/control/1", "CPU Fan", 300, 2000, 45),
            fan("/lpc/nct6798d/control/0", "Chassis Fan #1", 250, 1500, 40),
            fan("/lpc/nct6798d/control/2", "Chassis Fan #2", 250, 1500, 40),
            fan("/lpc/nct6798d/control/3", "Pump Fan", 1200, 3200, 70),
            fan("/gpu-nvidia/0/control/1", "GPU Fan 1", 0, 3000, gload * 0.6, "GPU"),
            {"id": "/lpc/nct6798d/fan/5", "ctrl_id": None, "controllable": False, "group": "Placa madre",
             "name": "Fan #6", "rpm": 0, "pct": None},
        ]
        return {
            "warnings": [],
            "cpu": {"name": "AMD Ryzen 9 5950X 16-Core Processor", "cores": 16, "threads": 32,
                    "temp": 48 + load * 0.35, "usage": load, "per_core": per, "temp_src": "lhm",
                    "freq": 3900 + random.uniform(-300, 600), "power": 45 + load * 0.9},
            "gpus": [{"name": "NVIDIA GeForce RTX 4070 SUPER", "temp": 40 + gload * 0.32,
                      "hotspot": 50 + gload * 0.36, "mem_temp": 48 + gload * 0.2, "usage": gload,
                      "mem_used": (3 + gload / 18) * 1024 ** 3, "mem_total": 12 * 1024 ** 3,
                      "power": 30 + gload * 1.9, "power_limit": 220, "fan": int(gload * 0.6),
                      "clock": 2475, "mem_clock": 10501}],
            "ram": {"used": 14.2 * 1024 ** 3, "total": 32 * 1024 ** 3, "pct": 44.4,
                    "swap_used": 1.1 * 1024 ** 3, "swap_total": 5 * 1024 ** 3, "swap_pct": 22},
            "parts": [{"name": "C:", "pct": 71.3, "used": 680e9, "total": 953e9},
                      {"name": "D:", "pct": 43.0, "used": 400e9, "total": 931e9}],
            "drives": [{"name": "Samsung SSD 980 1TB", "temp": 41 + random.uniform(-1, 1), "activity": 3},
                       {"name": "ST1000DM010", "temp": 34, "activity": 0}],
            "disk_io": {"read": random.uniform(0, 40e6), "write": random.uniform(0, 8e6)},
            "net": {"down": abs(random.gauss(2e6, 1.5e6)), "up": abs(random.gauss(4e5, 3e5))},
            "fans": fans,
            "others": [{"id": f"o{i}", "group": g, "name": n, "value": v} for i, (g, n, v) in enumerate([
                ("CPU", "CCD1 (Tdie)", 52.3), ("CPU", "CCD2 (Tdie)", 49.8),
                ("Placa madre", "System", 36.0), ("Placa madre", "VRM MOS", 44.5),
                ("Placa madre", "Chipset", 55.0), ("Placa madre", "PCH", 47.0),
                ("Almacenamiento", "Samsung SSD 980 1TB", 41.0), ("Almacenamiento", "ST1000DM010", 34.0)])],
        }


# --------------------------------------------------------------------------- #
#  Widgets
# --------------------------------------------------------------------------- #
class Scale:
    f = 1.0


def px(n):
    return int(round(n * Scale.f))


class Gauge(tk.Canvas):
    def __init__(self, master, size, caption, thickness=12, bg=CARD):
        size = px(size)
        thickness = px(thickness)
        super().__init__(master, width=size, height=size, bg=bg, highlightthickness=0)
        pad = thickness / 2 + px(4)
        box = (pad, pad, size - pad, size - pad)
        self.create_arc(*box, start=-45, extent=270, style="arc", outline=TRACK, width=thickness)
        self.arc = self.create_arc(*box, start=225, extent=-1, style="arc", outline=ACCENT, width=thickness)
        c = size / 2
        self.value = self.create_text(c, c - px(6), text="—", fill=TEXT,
                                      font=(FONT, max(14, int(size / 6.2 / Scale.f)), "bold"))
        self.caption = self.create_text(c, c + px(20), text=caption, fill=MUTED, font=(FONT, 9))

    def set(self, text, frac, color, caption=None):
        frac = 0 if frac is None else max(0.0, min(1.0, frac))
        if frac <= 0.002:
            self.itemconfigure(self.arc, state="hidden")
        else:
            self.itemconfigure(self.arc, state="normal", extent=-270 * frac, outline=color)
        self.itemconfigure(self.value, text=text, fill=color if text != "—" else DIM)
        if caption is not None:
            self.itemconfigure(self.caption, text=caption)


class HBar(tk.Canvas):
    def __init__(self, master, height=8, bg=CARD, color=ACCENT):
        super().__init__(master, height=px(height), bg=bg, highlightthickness=0)
        self.color = color
        self.frac = 0
        self.track = self.create_rectangle(0, 0, 0, 0, fill=TRACK, outline="")
        self.fill = self.create_rectangle(0, 0, 0, 0, fill=color, outline="")
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, frac, color=None):
        self.frac = 0 if frac is None else max(0.0, min(1.0, frac))
        if color:
            self.color = color
        self._draw()

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        self.coords(self.track, 0, 0, w, h)
        self.coords(self.fill, 0, 0, w * self.frac, h)
        self.itemconfigure(self.fill, fill=self.color)


class CoreBars(tk.Canvas):
    def __init__(self, master, height=46, bg=CARD):
        super().__init__(master, height=px(height), bg=bg, highlightthickness=0)
        self.values = []
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, values):
        self.values = list(values)
        self._draw()

    def _draw(self):
        self.delete("all")
        n = len(self.values)
        if not n:
            return
        w, h = self.winfo_width(), self.winfo_height()
        gap = px(2)
        bw = max(2, (w - gap * (n - 1)) / n)
        for i, v in enumerate(self.values):
            x0 = i * (bw + gap)
            self.create_rectangle(x0, 0, x0 + bw, h, fill=TRACK, outline="")
            vh = h * max(0, min(100, v)) / 100
            if vh > 0:
                self.create_rectangle(x0, h - vh, x0 + bw, h, fill=load_color(v), outline="")


class Spark(tk.Canvas):
    def __init__(self, master, series, height=70, bg=CARD_2, vmax=100.0, auto=False):
        super().__init__(master, height=px(height), bg=bg, highlightthickness=0)
        self.series = series
        self.data = [deque(maxlen=HISTORY) for _ in series]
        self.vmax = vmax
        self.auto = auto
        self.bind("<Configure>", lambda e: self._draw())

    def push(self, *values):
        for d, v in zip(self.data, values):
            d.append(v if v is not None else 0)
        if self.winfo_ismapped():
            self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        for k in (0.25, 0.5, 0.75):
            self.create_line(0, h * k, w, h * k, fill=BORDER, dash=(2, 4))
        vmax = self.vmax
        if self.auto:
            vmax = max([max(d) for d in self.data if d] + [1]) * 1.15
        step = w / (HISTORY - 1)
        for (label, color), d in zip(self.series, self.data):
            if len(d) < 2:
                continue
            pts = []
            off = HISTORY - len(d)
            for i, v in enumerate(d):
                pts.extend(((off + i) * step, h - 3 - (h - 6) * max(0, min(1, v / vmax))))
            self.create_line(*pts, fill=color, width=max(1, px(2)), smooth=True)
        x = px(8)
        for label, color in self.series:
            self.create_oval(x, px(7), x + px(7), px(14), fill=color, outline="")
            t = self.create_text(x + px(11), px(10), text=label, anchor="w", fill=MUTED, font=(FONT, 8))
            bbox = self.bbox(t)
            x = (bbox[2] if bbox else x + px(60)) + px(12)


class FlatButton(tk.Label):
    def __init__(self, master, text, command, active=False, bg=CARD_2, font_size=9, padx=12, pady=5):
        super().__init__(master, text=text, bg=bg, fg=TEXT, font=(FONT, font_size),
                         padx=px(padx), pady=px(pady), cursor="hand2")
        self.command = command
        self.base_bg = bg
        self.active = active
        self._paint()
        self.bind("<Button-1>", lambda e: self.command())
        self.bind("<Enter>", lambda e: self.configure(bg="#26304a" if not self.active else ACTIVE_BG))
        self.bind("<Leave>", lambda e: self._paint())

    def set_active(self, v):
        self.active = v
        self._paint()

    def _paint(self):
        self.configure(bg=ACTIVE_BG if self.active else self.base_bg)


class Segmented(tk.Frame):
    """Grupo de botones donde solo uno está activo."""

    def __init__(self, master, options, value, command, bg=CARD, font_size=9):
        super().__init__(master, bg=bg)
        self.buttons = {}
        self.command = command
        for key, label in options:
            b = FlatButton(self, label, lambda k=key: self.select(k, True), bg=CARD_2,
                           font_size=font_size, padx=10, pady=3)
            b.pack(side="left", padx=(0, px(3)))
            self.buttons[key] = b
        self.select(value, False)

    def select(self, key, notify=True):
        self.value = key
        for k, b in self.buttons.items():
            b.set_active(k == key)
        if notify:
            self.command(key)


class InfoRow(tk.Frame):
    def __init__(self, master, label, bg=CARD):
        super().__init__(master, bg=bg)
        self.lab = tk.Label(self, text=label, bg=bg, fg=MUTED, font=(FONT, 9))
        self.lab.pack(side="left")
        self.val = tk.Label(self, text="—", bg=bg, fg=TEXT, font=(FONT, 9, "bold"))
        self.val.pack(side="right")

    def set(self, text, color=TEXT):
        if self.val.cget("text") != text or self.val.cget("fg") != color:
            self.val.configure(text=text, fg=color)


def make_check(parent, text, value, command, bg=CARD):
    var = tk.BooleanVar(value=value)
    cb = tk.Checkbutton(parent, text=text, variable=var, command=lambda: command(var.get()),
                        bg=bg, fg=TEXT, selectcolor=CARD_2, activebackground=bg,
                        activeforeground=TEXT, font=(FONT, 10), anchor="w",
                        highlightthickness=0, bd=0, padx=0)
    cb.var = var
    return cb


def make_card(master, title, color):
    outer = tk.Frame(master, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
    head = tk.Frame(outer, bg=CARD)
    head.pack(fill="x", padx=px(14), pady=(px(12), px(6)))
    dot = tk.Canvas(head, width=px(10), height=px(10), bg=CARD, highlightthickness=0)
    dot.create_oval(1, 1, px(9), px(9), fill=color, outline="")
    dot.pack(side="left", padx=(0, px(8)))
    tk.Label(head, text=title, bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
    sub = tk.Label(head, text="", bg=CARD, fg=MUTED, font=(FONT, 9))
    sub.pack(side="left", padx=(px(10), 0))
    body = tk.Frame(outer, bg=CARD)
    body.pack(fill="both", expand=True, padx=px(14), pady=(0, px(12)))
    outer.head = head
    return outer, body, sub


def dark_scale(parent, command, value=50, frm=0, to=100, length=200):
    s = tk.Scale(parent, from_=frm, to=to, orient="horizontal", showvalue=0, length=px(length),
                 bg=ACCENT, troughcolor=TRACK, highlightthickness=0, bd=0, sliderrelief="flat",
                 activebackground="#67e8f9", fg=TEXT, sliderlength=px(18), width=px(10),
                 command=command)
    s.set(value)
    return s


# --------------------------------------------------------------------------- #
#  App
# --------------------------------------------------------------------------- #
class TempiaApp:
    def __init__(self):
        if IS_WIN:
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Boldo3108.Tempia")
            except Exception:
                pass
        self.cfg = load_config()
        self.root = tk.Tk()
        Scale.f = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self.root.title(f"{APP_NAME} — Monitor de hardware")
        self.root.configure(bg=BG)
        self.root.minsize(px(640), px(420))
        try:
            ico = resource_path("tempia.ico")
            if IS_WIN and os.path.exists(ico):
                self.root.iconbitmap(default=ico)
            else:
                png = resource_path("tempia.png")
                if os.path.exists(png):
                    self._icon_img = tk.PhotoImage(file=png)
                    self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass
        try:
            self.root.geometry(self.cfg.get("geometry") or f"{px(1180)}x{px(800)}")
        except Exception:
            self.root.geometry(f"{px(1180)}x{px(800)}")
        self.root.attributes("-topmost", bool(self.cfg.get("topmost")))

        self.monitor = Monitor(self.cfg)
        self.q = queue.Queue(maxsize=2)
        self.running = True
        self.mini = None
        self.fan_win = None
        self.settings_win = None
        self.last = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()
        self.root.after(200, self._poll)
        if self.cfg.get("check_updates", True) and not DEMO:
            self.root.after(3000, self.check_updates)

    # ================================================================== UI
    def _build_ui(self):
        r = self.root
        header = tk.Frame(r, bg=BG)
        header.pack(fill="x", padx=px(18), pady=(px(14), px(8)))
        logo = tk.Canvas(header, width=px(34), height=px(34), bg=BG, highlightthickness=0)
        self._draw_logo(logo, px(34))
        logo.pack(side="left")
        tbox = tk.Frame(header, bg=BG)
        tbox.pack(side="left", padx=(px(10), 0))
        tk.Label(tbox, text="TEMPIA", bg=BG, fg=TEXT, font=(FONT, 17, "bold")).pack(anchor="w")
        self.status = tk.Label(tbox, text="Iniciando sensores…", bg=BG, fg=MUTED, font=(FONT, 9))
        self.status.pack(anchor="w")

        btns = tk.Frame(header, bg=BG)
        btns.pack(side="right")
        FlatButton(btns, "⚙  Configuración", self.open_settings, bg=CARD).pack(side="right", padx=(px(6), 0))
        FlatButton(btns, "🖥  Mi PC", self.open_specs, bg=CARD).pack(side="right", padx=(px(6), 0))
        self.b_update = FlatButton(btns, "", lambda: self.open_settings("about"), bg="#14532d")
        self.update_info = None
        FlatButton(btns, "▣  Modo mini", self.open_mini, bg=CARD).pack(side="right", padx=(px(6), 0))
        self.b_top = FlatButton(btns, "📌  Siempre visible", self.toggle_top,
                                active=bool(self.cfg.get("topmost")), bg=CARD)
        self.b_top.pack(side="right", padx=(px(6), 0))
        self.b_unit = FlatButton(btns, "", self.toggle_unit, bg=CARD)
        self.b_unit.pack(side="right", padx=(px(6), 0))
        self.b_int = FlatButton(btns, "", self.cycle_interval, bg=CARD)
        self.b_int.pack(side="right", padx=(px(6), 0))
        self._refresh_buttons()

        self.banner = tk.Frame(r, bg="#3b2a12", highlightthickness=1, highlightbackground="#7c5216")
        self.banner_text = tk.Label(self.banner, text="", bg="#3b2a12", fg="#fcd34d",
                                    font=(FONT, 9), wraplength=px(760), justify="left")
        self.banner_text.pack(side="left", padx=px(12), pady=px(8))
        bb = tk.Frame(self.banner, bg="#3b2a12")
        bb.pack(side="right", padx=px(10))
        self.banner_btn = FlatButton(bb, "⬇  Descargar PawnIO", lambda: webbrowser.open(PAWNIO_URL),
                                     bg="#7c5216")
        self.banner_btn.pack(side="left", padx=(0, px(6)))
        FlatButton(bb, "Diagnóstico", lambda: self.open_settings("diag"), bg="#7c5216").pack(side="left")
        self.banner_shown = False
        self.banner_kind = None

        self.main = tk.Frame(r, bg=BG)
        self.main.pack(fill="both", expand=True, padx=px(18), pady=(px(4), px(16)))
        self.top = tk.Frame(self.main, bg=BG)
        self.bottom = tk.Frame(self.main, bg=BG)

        self.cards = {
            "cpu": self._build_cpu(self.top),
            "gpu": self._build_gpu(self.top),
            "ram": self._build_ram(self.bottom),
            "disks": self._build_disks(self.bottom),
            "net": self._build_net(self.bottom),
            "fans": self._build_fans(self.bottom),
            "others": self._build_others(self.bottom),
        }
        self.apply_layout()

    def apply_layout(self):
        vis = self.cfg["cards"]
        for c in self.cards.values():
            c.grid_forget()
        top_keys = [k for k in ("cpu", "gpu") if vis.get(k, True)]
        bot_keys = [k for k in ("ram", "disks", "net", "fans", "others") if vis.get(k, True)]
        for i in range(5):
            self.top.columnconfigure(i, weight=0, uniform="")
            self.bottom.columnconfigure(i, weight=0, uniform="")
        self.top.pack_forget()
        self.bottom.pack_forget()
        if top_keys:
            self.top.pack(fill="both", expand=True)
            self.top.rowconfigure(0, weight=1)
            for i, k in enumerate(top_keys):
                self.top.columnconfigure(i, weight=1, uniform="a")
                pad = (0 if i == 0 else px(6), 0 if i == len(top_keys) - 1 else px(6))
                self.cards[k].grid(row=0, column=i, sticky="nsew", padx=pad)
        if bot_keys:
            self.bottom.pack(fill="both", expand=True, pady=(px(12) if top_keys else 0, 0))
            self.bottom.rowconfigure(0, weight=1)
            for i, k in enumerate(bot_keys):
                self.bottom.columnconfigure(i, weight=1, uniform="b")
                pad = (0 if i == 0 else px(6), 0 if i == len(bot_keys) - 1 else px(6))
                self.cards[k].grid(row=0, column=i, sticky="nsew", padx=pad)
        # gráficos / barras por hilo
        for sp in (self.cpu_spark, self.gpu_spark, self.ram_spark, self.net_spark):
            if self.cfg["show_graphs"]:
                sp.pack(fill="both", expand=True, pady=(px(10), 0))
            else:
                sp.pack_forget()
        if self.cfg["show_threads"]:
            self.cpu_bars_lbl.pack(anchor="w", pady=(px(10), px(3)), before=self.cpu_spark if self.cfg["show_graphs"] else None)
            self.cpu_bars.pack(fill="x", after=self.cpu_bars_lbl)
        else:
            self.cpu_bars_lbl.pack_forget()
            self.cpu_bars.pack_forget()

    def _draw_logo(self, c, s):
        c.create_oval(0, 0, s, s, fill="#0e2a35", outline="")
        cx = s / 2
        c.create_line(cx, s * 0.22, cx, s * 0.62, fill=ACCENT, width=max(3, s * 0.16), capstyle="round")
        r_ = s * 0.15
        c.create_oval(cx - r_, s * 0.62 - r_, cx + r_, s * 0.62 + r_, fill=HOT_RED, outline="")
        c.create_line(cx, s * 0.45, cx, s * 0.62, fill=HOT_RED, width=max(2, s * 0.08))

    def _gauges_and_info(self, body):
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x")
        g1 = Gauge(row, 132, "Temperatura")
        g1.pack(side="left")
        g2 = Gauge(row, 132, "Uso")
        g2.pack(side="left", padx=(px(4), px(14)))
        info = tk.Frame(row, bg=CARD)
        info.pack(side="left", fill="both", expand=True, pady=px(6))
        return g1, g2, info

    def _build_cpu(self, parent):
        card, body, self.cpu_sub = make_card(parent, "CPU", CPU_COLOR)
        self.cpu_gt, self.cpu_gu, info = self._gauges_and_info(body)
        self.cpu_rows = {}
        for key, lab in (("freq", "Frecuencia"), ("power", "Potencia"), ("cores", "Núcleos / Hilos"),
                         ("max", "Máx. sesión"), ("usage", "Uso promedio")):
            row = InfoRow(info, lab)
            row.pack(fill="x", pady=px(3))
            self.cpu_rows[key] = row
        self.cpu_pawn = tk.Label(body, text="¿No ves la temperatura del CPU?  Descarga PawnIO (gratis) →",
                                 bg=CARD, fg=ACCENT, font=(FONT, 9, "underline"), cursor="hand2")
        self.cpu_pawn.bind("<Button-1>", lambda e: webbrowser.open(PAWNIO_URL))
        self.cpu_pawn_shown = False
        self.cpu_bars_lbl = tk.Label(body, text="Uso por hilo", bg=CARD, fg=MUTED, font=(FONT, 8))
        self.cpu_bars = CoreBars(body, 44)
        self.cpu_spark = Spark(body, [("Temperatura °C", HOT_RED), ("Uso %", CPU_COLOR)], 78)
        self.cpu_max = None
        return card

    def _build_gpu(self, parent):
        card, body, self.gpu_sub = make_card(parent, "GPU", GPU_COLOR)
        self.gpu_gt, self.gpu_gu, info = self._gauges_and_info(body)
        self.gpu_rows = {}
        for key, lab in (("hotspot", "Hotspot"), ("mem_temp", "Temp. memoria"), ("power", "Potencia"),
                         ("fan", "Ventilador"), ("clock", "Reloj núcleo / mem")):
            row = InfoRow(info, lab)
            row.pack(fill="x", pady=px(3))
            self.gpu_rows[key] = row
        vr = tk.Frame(body, bg=CARD)
        vr.pack(fill="x", pady=(px(10), px(3)))
        tk.Label(vr, text="VRAM", bg=CARD, fg=MUTED, font=(FONT, 8)).pack(side="left")
        self.vram_lbl = tk.Label(vr, text="—", bg=CARD, fg=TEXT, font=(FONT, 8, "bold"))
        self.vram_lbl.pack(side="right")
        self.vram_bar = HBar(body, 10, color=GPU_COLOR)
        self.vram_bar.pack(fill="x")
        self.gpu_spark = Spark(body, [("Temperatura °C", HOT_RED), ("Uso %", GPU_COLOR)], 78)
        self.gpu_max = None
        return card

    def _build_ram(self, parent):
        card, body, self.ram_sub = make_card(parent, "Memoria RAM", RAM_COLOR)
        self.ram_big = tk.Label(body, text="—", bg=CARD, fg=TEXT, font=(FONT, 24, "bold"))
        self.ram_big.pack(anchor="w")
        self.ram_lbl = tk.Label(body, text="", bg=CARD, fg=MUTED, font=(FONT, 9))
        self.ram_lbl.pack(anchor="w", pady=(0, px(6)))
        self.ram_bar = HBar(body, 10, color=RAM_COLOR)
        self.ram_bar.pack(fill="x")
        sw = tk.Frame(body, bg=CARD)
        sw.pack(fill="x", pady=(px(10), px(3)))
        tk.Label(sw, text="Paginación", bg=CARD, fg=MUTED, font=(FONT, 8)).pack(side="left")
        self.swap_lbl = tk.Label(sw, text="—", bg=CARD, fg=TEXT, font=(FONT, 8, "bold"))
        self.swap_lbl.pack(side="right")
        self.swap_bar = HBar(body, 6, color="#7c3aed")
        self.swap_bar.pack(fill="x")
        self.ram_spark = Spark(body, [("Uso %", RAM_COLOR)], 60)
        return card

    def _build_disks(self, parent):
        card, body, self.disk_sub = make_card(parent, "Discos", DISK_COLOR)
        self.disk_body = tk.Frame(body, bg=CARD)
        self.disk_body.pack(fill="x")
        self.disk_widgets = {}
        self.drive_body = tk.Frame(body, bg=CARD)
        self.drive_body.pack(fill="x", pady=(px(8), 0))
        self.drive_rows = {}
        io = tk.Frame(body, bg=CARD)
        io.pack(fill="x", side="bottom", pady=(px(8), 0))
        self.io_r = InfoRow(io, "Lectura")
        self.io_r.pack(fill="x")
        self.io_w = InfoRow(io, "Escritura")
        self.io_w.pack(fill="x")
        return card

    def _build_net(self, parent):
        card, body, self.net_sub = make_card(parent, "Red", NET_DOWN)
        for arrow, color, attr in (("↓", NET_DOWN, "net_down"), ("↑", NET_UP, "net_up")):
            r1 = tk.Frame(body, bg=CARD)
            r1.pack(fill="x")
            tk.Label(r1, text=arrow, bg=CARD, fg=color, font=(FONT, 18, "bold")).pack(side="left")
            lbl = tk.Label(r1, text="—", bg=CARD, fg=TEXT, font=(FONT, 15, "bold"))
            lbl.pack(side="left", padx=px(6))
            setattr(self, attr, lbl)
        self.net_spark = Spark(body, [("Bajada", NET_DOWN), ("Subida", NET_UP)], 80, auto=True)
        return card

    def _build_fans(self, parent):
        card, body, self.fan_sub = make_card(parent, "Ventiladores", FAN_COLOR)
        FlatButton(body, "⚙  Controlar ventiladores", self.open_fans, bg=CARD_2, font_size=9,
                   pady=4).pack(side="bottom", fill="x", pady=(px(8), 0))
        self.fan_text = self._make_list(body)
        self._fan_cache = None
        return card

    def _build_others(self, parent):
        card, body, self.oth_sub = make_card(parent, "Otros sensores", WARN_ORANGE)
        self.oth_text = self._make_list(body)
        self._others_cache = None
        return card

    def _make_list(self, body):
        tx = tk.Text(body, bg=CARD, fg=TEXT, font=(FONT, 9), relief="flat", highlightthickness=0,
                     wrap="none", height=8, cursor="arrow", padx=0, pady=0, spacing1=px(2), spacing3=px(2))
        tx.pack(fill="both", expand=True)
        tx.tag_configure("grp", foreground=MUTED, font=(FONT, 8, "bold"), spacing1=px(6))
        tx.tag_configure("name", foreground=TEXT)
        tx.tag_configure("small", foreground=DIM, font=(FONT, 8))
        for c in (OK_GREEN, WARN_YELLOW, WARN_ORANGE, HOT_RED, ACCENT, DIM, FAN_COLOR, MUTED):
            tx.tag_configure(c, foreground=c, font=(FONT, 9, "bold"))

        def tabs(e=None):
            w = tx.winfo_width()
            tx.configure(tabs=(max(10, w - px(4)), "right"))
        tx.bind("<Configure>", tabs)
        tx.configure(state="disabled")
        return tx

    # ================================================================ acciones
    def _refresh_buttons(self):
        self.b_unit.configure(text="°F" if self.cfg["fahrenheit"] else "°C")
        self.b_int.configure(text=f"⟳  {self.cfg['interval']:g} s")
        self.b_top.set_active(bool(self.cfg.get("topmost")))

    def toggle_top(self):
        self.cfg["topmost"] = not self.cfg.get("topmost")
        self.root.attributes("-topmost", self.cfg["topmost"])
        self._refresh_buttons()
        save_config(self.cfg)

    def toggle_unit(self):
        self.cfg["fahrenheit"] = not self.cfg["fahrenheit"]
        self._refresh_buttons()
        save_config(self.cfg)
        self._rerender()

    def cycle_interval(self):
        opts = [0.5, 1.0, 2.0, 5.0]
        cur = self.cfg["interval"]
        self.cfg["interval"] = opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else 1.0
        self._refresh_buttons()
        save_config(self.cfg)

    def _rerender(self):
        self._others_cache = None
        self._fan_cache = None
        if self.last:
            self._render(self.last, push=False)

    def t(self, c, unit=True):
        if c is None:
            return "—"
        if self.cfg["fahrenheit"]:
            v = c * 9 / 5 + 32
            return f"{v:.0f}°F" if unit else f"{v:.0f}°"
        return f"{c:.0f}°C" if unit else f"{c:.0f}°"

    def fan_name(self, f):
        return self.cfg["fan_names"].get(f["id"]) or f["name"]

    # ================================================================== datos
    def _worker(self):
        try:
            self.monitor.start()
        except Exception as e:  # pragma: no cover
            print("Error iniciando sensores:", e)
        while self.running:
            t0 = time.time()
            try:
                snap = self.monitor.collect()
                try:
                    self.q.put_nowait(snap)
                except queue.Full:
                    try:
                        self.q.get_nowait()
                    except queue.Empty:
                        pass
                    self.q.put_nowait(snap)
            except Exception as e:  # pragma: no cover
                print("Error leyendo sensores:", e)
            end = time.time() + max(0.05, self.cfg["interval"] - (time.time() - t0))
            while self.running and time.time() < end and not self.monitor.reinit_requested:
                time.sleep(0.05)
        try:
            self.monitor.close()
        except Exception:
            pass

    def _poll(self):
        if not self.running:
            return
        snap = None
        try:
            while True:
                snap = self.q.get_nowait()
        except queue.Empty:
            pass
        if snap is not None:
            self.last = snap
            try:
                self._render(snap)
            except Exception as e:  # pragma: no cover
                import traceback
                traceback.print_exc()
        self.root.after(150, self._poll)

    def _render(self, s, push=True):
        alert_c = self.cfg["cpu_alert"]
        alert_g = self.cfg["gpu_alert"]
        src = []
        if DEMO:
            src.append("modo demo")
        else:
            if self.monitor.lhm and self.monitor.lhm.ok:
                src.append("LibreHardwareMonitor")
            if self.monitor.nvml and self.monitor.nvml.ok:
                src.append("NVIDIA")
            src.append("psutil")
        self.status.configure(text=f"v{APP_VERSION} · " + " + ".join(src) + f" · {time.strftime('%H:%M:%S')}")

        warns = s.get("warnings") or []
        if warns and not self.banner_shown:
            self.banner.pack(fill="x", padx=px(18), pady=(0, px(6)), before=self.main)
            self.banner_shown = True
        elif not warns and self.banner_shown:
            self.banner.pack_forget()
            self.banner_shown = False
        if warns:
            self.banner_text.configure(text="⚠  " + "  ".join(w[1] for w in warns))
            kind = warns[0][0]
            if kind != self.banner_kind:
                self.banner_kind = kind
                if kind == "admin":
                    self.banner_btn.pack_forget()
                else:
                    self.banner_btn.pack(side="left", padx=(0, px(6)))

        # ---------- CPU
        c = s["cpu"]
        self.cpu_sub.configure(text=c.get("name") or "")
        ct = c.get("temp")
        if ct is not None:
            self.cpu_max = ct if self.cpu_max is None else max(self.cpu_max, ct)
        cap = "Temp. aprox. (ACPI)" if c.get("temp_src") == "acpi" else "Temperatura"
        self.cpu_gt.set(self.t(ct, unit=False), (ct or 0) / 100, temp_color(ct, alert_c), cap)
        need_pawn = IS_WIN and not DEMO and (ct is None or c.get("temp_src") == "acpi")
        if need_pawn != self.cpu_pawn_shown:
            self.cpu_pawn_shown = need_pawn
            if need_pawn:
                self.cpu_pawn.pack(anchor="w", pady=(px(8), 0), after=self.cpu_gt.master)
            else:
                self.cpu_pawn.pack_forget()
        cu = c.get("usage")
        self.cpu_gu.set(f"{cu:.0f}%" if cu is not None else "—", (cu or 0) / 100, CPU_COLOR)
        f = c.get("freq")
        self.cpu_rows["freq"].set(f"{f / 1000:.2f} GHz" if f else "—")
        p = c.get("power")
        self.cpu_rows["power"].set(f"{p:.0f} W" if p else "—")
        self.cpu_rows["cores"].set(f"{c.get('cores') or '—'} / {c.get('threads') or '—'}")
        self.cpu_rows["max"].set(self.t(self.cpu_max), temp_color(self.cpu_max, alert_c))
        self.cpu_rows["usage"].set(f"{cu:.1f}%" if cu is not None else "—")
        if self.cfg["show_threads"]:
            self.cpu_bars.set(c.get("per_core") or [])
        if push:
            self.cpu_spark.push(ct, cu)

        # ---------- GPU
        gpus = s.get("gpus") or []
        if gpus:
            g = gpus[0]
            extra = f"  (+{len(gpus) - 1} más)" if len(gpus) > 1 else ""
            self.gpu_sub.configure(text=(g.get("name") or "") + extra)
            gt = g.get("temp")
            if gt is not None:
                self.gpu_max = gt if self.gpu_max is None else max(self.gpu_max, gt)
            self.gpu_gt.set(self.t(gt, unit=False), (gt or 0) / 100, temp_color(gt, alert_g))
            gu = g.get("usage")
            self.gpu_gu.set(f"{gu:.0f}%" if gu is not None else "—", (gu or 0) / 100, GPU_COLOR)
            hs = g.get("hotspot")
            self.gpu_rows["hotspot"].set(self.t(hs), temp_color(hs, alert_g + 12) if hs else MUTED)
            mt = g.get("mem_temp")
            self.gpu_rows["mem_temp"].set(self.t(mt), temp_color(mt, alert_g + 12) if mt else MUTED)
            pw, pl = g.get("power"), g.get("power_limit")
            self.gpu_rows["power"].set((f"{pw:.0f} W" + (f" / {pl:.0f} W" if pl else "")) if pw is not None else "—")
            fan = g.get("fan")
            self.gpu_rows["fan"].set(f"{fan:.0f}%" if fan is not None else "—")
            ck, mk = g.get("clock"), g.get("mem_clock")
            self.gpu_rows["clock"].set(f"{ck:.0f} / {mk:.0f} MHz" if ck and mk else (f"{ck:.0f} MHz" if ck else "—"))
            mu, mtot = g.get("mem_used"), g.get("mem_total")
            if mu is not None and mtot:
                self.vram_lbl.configure(text=f"{fmt_bytes(mu)} / {fmt_bytes(mtot)}  ({mu / mtot * 100:.0f}%)")
                self.vram_bar.set(mu / mtot, GPU_COLOR)
            else:
                self.vram_lbl.configure(text="—")
                self.vram_bar.set(0)
            if push:
                self.gpu_spark.push(gt, gu)
        else:
            self.gpu_sub.configure(text="No se detectó una GPU compatible")
            self.gpu_gt.set("—", 0, DIM)
            self.gpu_gu.set("—", 0, DIM)

        # ---------- RAM
        ram = s.get("ram")
        if ram:
            self.ram_big.configure(text=f"{ram['pct']:.0f}%", fg=load_color(ram["pct"]))
            self.ram_lbl.configure(text=f"{fmt_bytes(ram['used'])} de {fmt_bytes(ram['total'])} en uso")
            self.ram_bar.set(ram["pct"] / 100, RAM_COLOR)
            if ram.get("swap_total"):
                self.swap_lbl.configure(text=f"{fmt_bytes(ram['swap_used'])} / {fmt_bytes(ram['swap_total'])}")
                self.swap_bar.set(ram["swap_pct"] / 100)
            self.ram_sub.configure(text=fmt_bytes(ram["total"]))
            if push:
                self.ram_spark.push(ram["pct"])

        # ---------- Discos
        self._render_disks(s)

        # ---------- Red
        net = s.get("net") or {}
        self.net_down.configure(text=fmt_bits(net.get("down")))
        self.net_up.configure(text=fmt_bits(net.get("up")))
        self.net_sub.configure(text=f"{fmt_bytes(net.get('down'), True)} ↓" if net.get("down") is not None else "")
        if push:
            self.net_spark.push(net.get("down"), net.get("up"))

        # ---------- Ventiladores
        self._render_fans(s)

        # ---------- Otros
        self._render_others(s)

        if self.mini is not None:
            self._render_mini(s)
        if self.fan_win is not None:
            self._update_fan_window(s)

    def _render_disks(self, s):
        parts = s.get("parts") or []
        names = [p["name"] for p in parts]
        for n in list(self.disk_widgets):
            if n not in names:
                self.disk_widgets.pop(n)[0].destroy()
        for p in parts:
            if p["name"] not in self.disk_widgets:
                fr = tk.Frame(self.disk_body, bg=CARD)
                fr.pack(fill="x", pady=(0, px(6)))
                top = tk.Frame(fr, bg=CARD)
                top.pack(fill="x")
                tk.Label(top, text=p["name"], bg=CARD, fg=TEXT, font=(FONT, 9, "bold")).pack(side="left")
                l2 = tk.Label(top, text="", bg=CARD, fg=MUTED, font=(FONT, 8))
                l2.pack(side="right")
                bar = HBar(fr, 7, color=DISK_COLOR)
                bar.pack(fill="x", pady=(px(2), 0))
                self.disk_widgets[p["name"]] = (fr, l2, bar)
            fr, l2, bar = self.disk_widgets[p["name"]]
            l2.configure(text=f"{fmt_bytes(p['used'])} / {fmt_bytes(p['total'])} · {p['pct']:.0f}%")
            bar.set(p["pct"] / 100, HOT_RED if p["pct"] >= 90 else DISK_COLOR)
        drives = s.get("drives") or []
        dnames = [d["name"] for d in drives]
        for n in list(self.drive_rows):
            if n not in dnames:
                self.drive_rows.pop(n).destroy()
        for d in drives:
            if d["name"] not in self.drive_rows:
                label = d["name"] if len(d["name"]) <= 15 else d["name"][:14] + "…"
                row = InfoRow(self.drive_body, "🌡 " + label)
                row.pack(fill="x", pady=px(1))
                self.drive_rows[d["name"]] = row
            txt = self.t(d.get("temp"))
            self.drive_rows[d["name"]].set(txt, temp_color(d.get("temp"), 60))
        self.disk_sub.configure(text=f"{len(parts)} unidades")
        io = s.get("disk_io") or {}
        self.io_r.set(fmt_bytes(io.get("read"), True))
        self.io_w.set(fmt_bytes(io.get("write"), True))

    def visible_fans(self, s):
        hidden = set(self.cfg["hidden_sensors"])
        out = []
        for f in s.get("fans") or []:
            if f["id"] in hidden:
                continue
            if self.cfg["hide_zero_fans"] and not f["rpm"] and f.get("mode", "auto") == "auto" \
                    and not f["controllable"]:
                continue
            out.append(f)
        return out

    def _render_fans(self, s):
        fans = self.visible_fans(s)
        key = [(f["id"], round(f["rpm"] or -1, -1), round(f["pct"] or -1), f.get("mode"),
                self.fan_name(f)) for f in fans] + [s.get("fan_emergency")]
        if key == self._fan_cache:
            return
        self._fan_cache = key
        tx = self.fan_text
        tx.configure(state="normal")
        tx.delete("1.0", "end")
        if not fans:
            tx.insert("end", "No hay ventiladores disponibles.\n", DIM)
            tx.insert("end", "Se leen con LibreHardwareMonitor\n+ PawnIO (ejecutando como admin).", "small")
        if s.get("fan_emergency"):
            tx.insert("end", "⚠ Temp. alta: ventiladores al 100%\n", HOT_RED)
        if any(f.get("mode") in ("manual", "curve") for f in fans):
            tx.insert("end", "M = manual · C = curva (Tempia)\n", "small")
        last = None
        for f in fans:
            if f["group"] != last:
                tx.insert("end", f["group"].upper() + "\n", "grp")
                last = f["group"]
            name = self.fan_name(f)
            name = name if len(name) <= 14 else name[:13] + "…"
            badge = {"manual": "  ·M", "curve": "  ·C"}.get(f.get("mode"), "")
            tx.insert("end", name, "name")
            if badge:
                tx.insert("end", badge, ACCENT)
            if f["rpm"] is not None:
                val = f"{f['rpm']:.0f} rpm"
            elif f["pct"] is not None:
                val = f"{f['pct']:.0f}%"
            else:
                val = "—"
            tx.insert("end", "\t" + val + "\n", FAN_COLOR if (f["rpm"] or f["pct"]) else DIM)
        tx.configure(state="disabled")
        self.fan_sub.configure(text=str(len(fans)) if fans else "")

    def _render_others(self, s):
        hidden = set(self.cfg["hidden_sensors"])
        others = [o for o in (s.get("others") or []) if o["id"] not in hidden]
        key = [(o["id"], round(o["value"])) for o in others] + [self.cfg["fahrenheit"]]
        if key == self._others_cache:
            return
        self._others_cache = key
        tx = self.oth_text
        tx.configure(state="normal")
        tx.delete("1.0", "end")
        if not others:
            tx.insert("end", "Sin sensores extra disponibles.\n", DIM)
            tx.insert("end", "Placa madre y SSD se leen con\nLibreHardwareMonitor + PawnIO.", "small")
        last = None
        for o in others:
            if o["group"] != last:
                tx.insert("end", o["group"].upper() + "\n", "grp")
                last = o["group"]
            col = temp_color(o["value"], 60 if o["group"] == "Almacenamiento" else 85)
            name = o["name"] if len(o["name"]) <= 22 else o["name"][:21] + "…"
            tx.insert("end", name, "name")
            tx.insert("end", "\t" + self.t(o["value"]) + "\n", col)
        tx.configure(state="disabled")
        self.oth_sub.configure(text=str(len(others)) if others else "")

    # ============================================================ ventiladores
    def open_fans(self):
        if self.fan_win is not None:
            self.fan_win.lift()
            return
        w = tk.Toplevel(self.root)
        w.title("Tempia — Ventiladores")
        w.configure(bg=BG)
        w.geometry(f"{px(760)}x{px(560)}")
        w.minsize(px(640), px(360))
        w.attributes("-topmost", bool(self.cfg.get("topmost")))
        w.protocol("WM_DELETE_WINDOW", self.close_fans)
        self.fan_win = w
        head = tk.Frame(w, bg=BG)
        head.pack(fill="x", padx=px(18), pady=(px(14), px(6)))
        tk.Label(head, text="Control de ventiladores", bg=BG, fg=TEXT, font=(FONT, 14, "bold")).pack(anchor="w")
        tk.Label(head, bg=BG, fg=MUTED, font=(FONT, 9), justify="left", wraplength=px(700),
                 text=("Automático = lo controla la placa madre / GPU como siempre.  Manual = velocidad fija.  "
                       "Curva = sube y baja según la temperatura.\n"
                       f"Seguridad: mínimo {FAN_MIN}% ({PUMP_MIN}% en bombas); si la CPU llega a {EMERGENCY_CPU}°C o "
                       f"la GPU a {EMERGENCY_GPU}°C, todos los ventiladores controlados suben al 100%. "
                       "Al cerrar Tempia vuelven a modo automático.")).pack(anchor="w", pady=(px(4), 0))
        self.fan_emerg_lbl = tk.Label(head, text="", bg=BG, fg=HOT_RED, font=(FONT, 9, "bold"))
        self.fan_emerg_lbl.pack(anchor="w")

        outer = tk.Frame(w, bg=BG)
        outer.pack(fill="both", expand=True, padx=px(18), pady=(px(4), px(14)))
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.fan_list = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window(0, 0, window=self.fan_list, anchor="nw")
        self.fan_list.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win_id, width=e.width))

        def wheel(e):
            canvas.yview_scroll(int(-e.delta / 120) if e.delta else (1 if e.num == 5 else -1), "units")
        w.bind("<MouseWheel>", wheel)
        w.bind("<Button-4>", wheel)
        w.bind("<Button-5>", wheel)
        self.fan_rows = {}
        self._fan_ids = None
        if self.last:
            self._update_fan_window(self.last)

    def close_fans(self):
        if self.fan_win is not None:
            self.fan_win.destroy()
            self.fan_win = None

    def _fan_setting(self, fid):
        st = self.cfg["fans"].setdefault(fid, {})
        st.setdefault("mode", "auto")
        st.setdefault("value", 50)
        st.setdefault("preset", "equilibrado")
        st.setdefault("source", "cpu")
        return st

    def _build_fan_row(self, f):
        row = tk.Frame(self.fan_list, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        row.pack(fill="x", pady=(0, px(8)))
        top = tk.Frame(row, bg=CARD)
        top.pack(fill="x", padx=px(12), pady=(px(10), px(4)))
        name_var = tk.StringVar(value=self.fan_name(f))
        ent = tk.Entry(top, textvariable=name_var, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                       font=(FONT, 11, "bold"), width=22, highlightthickness=1,
                       highlightbackground=CARD, highlightcolor=ACCENT)
        ent.pack(side="left")

        def save_name(e=None, fid=f["id"], orig=f["name"]):
            v = name_var.get().strip()
            if v and v != orig:
                self.cfg["fan_names"][fid] = v
            else:
                self.cfg["fan_names"].pop(fid, None)
            save_config(self.cfg)
            self._fan_cache = None
        ent.bind("<Return>", save_name)
        ent.bind("<FocusOut>", save_name)
        tk.Label(top, text=f["group"] + (" · " + f["name"] if self.cfg["fan_names"].get(f["id"]) else ""),
                 bg=CARD, fg=DIM, font=(FONT, 8)).pack(side="left", padx=px(8))
        live = tk.Label(top, text="", bg=CARD, fg=FAN_COLOR, font=(FONT, 11, "bold"))
        live.pack(side="right")
        widgets = {"row": row, "live": live}
        if not f["controllable"]:
            tk.Label(row, text="Solo lectura: este ventilador no permite control desde Windows.",
                     bg=CARD, fg=DIM, font=(FONT, 9)).pack(anchor="w", padx=px(12), pady=(0, px(10)))
            return widgets
        st = self._fan_setting(f["id"])
        ctl = tk.Frame(row, bg=CARD)
        ctl.pack(fill="x", padx=px(12), pady=(px(2), px(10)))
        manual = tk.Frame(row, bg=CARD)
        curve = tk.Frame(row, bg=CARD)

        def show_mode(m):
            manual.pack_forget()
            curve.pack_forget()
            if m == "manual":
                manual.pack(fill="x", padx=px(12), pady=(0, px(10)))
            elif m == "curve":
                curve.pack(fill="x", padx=px(12), pady=(0, px(10)))

        def set_mode(m, fid=f["id"]):
            self._fan_setting(fid)["mode"] = m
            save_config(self.cfg)
            show_mode(m)
            self._fan_cache = None
        Segmented(ctl, [("auto", "Automático"), ("manual", "Manual"), ("curve", "Curva")],
                  st["mode"], set_mode).pack(side="left")
        widgets["err"] = tk.Label(ctl, text="", bg=CARD, fg=HOT_RED, font=(FONT, 8))
        widgets["err"].pack(side="right")

        val_lbl = tk.Label(manual, text=f"{st['value']:.0f}%", bg=CARD, fg=TEXT, font=(FONT, 10, "bold"), width=5)

        def on_slide(v, fid=f["id"]):
            v = float(v)
            self._fan_setting(fid)["value"] = v
            val_lbl.configure(text=f"{v:.0f}%")
            save_config(self.cfg)
        tk.Label(manual, text="Velocidad", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")
        lo = PUMP_MIN if "pump" in f["name"].lower() else FAN_MIN
        sc = dark_scale(manual, on_slide, value=max(lo, st["value"]), frm=lo, to=100, length=360)
        sc.pack(side="left", fill="x", expand=True, padx=px(10))
        val_lbl.pack(side="left")

        tk.Label(curve, text="Perfil", bg=CARD, fg=MUTED, font=(FONT, 9)).pack(side="left")

        def set_preset(p, fid=f["id"]):
            self._fan_setting(fid)["preset"] = p
            save_config(self.cfg)
        Segmented(curve, [(k, PRESET_LABEL[k]) for k in FAN_PRESETS], st["preset"], set_preset,
                  font_size=8).pack(side="left", padx=(px(8), px(14)))

        def set_src(v, fid=f["id"]):
            self._fan_setting(fid)["source"] = v
            save_config(self.cfg)
        Segmented(curve, [("cpu", "CPU"), ("gpu", "GPU"), ("max", "La más caliente")], st["source"],
                  set_src, font_size=8).pack(side="left")
        show_mode(st["mode"])
        return widgets

    def _update_fan_window(self, s):
        fans = [f for f in (s.get("fans") or [])
                if not (self.cfg["hide_zero_fans"] and not f["rpm"] and not f["controllable"])]
        ids = [f["id"] for f in fans]
        if ids != self._fan_ids:
            self._fan_ids = ids
            for ch in self.fan_list.winfo_children():
                ch.destroy()
            self.fan_rows = {}
            if not fans:
                tk.Label(self.fan_list, bg=BG, fg=MUTED, font=(FONT, 10), justify="left", wraplength=px(680),
                         text=("No se encontraron ventiladores.\n\nPara verlos y controlarlos, Tempia necesita el "
                               "driver PawnIO instalado y abrirse como administrador. Algunas placas madre no "
                               "permiten el control desde Windows.")).pack(anchor="w", pady=px(10))
            for f in fans:
                self.fan_rows[f["id"]] = self._build_fan_row(f)
        for f in fans:
            w = self.fan_rows.get(f["id"])
            if not w:
                continue
            txt = f"{f['rpm']:.0f} RPM" if f["rpm"] is not None else ""
            if f["pct"] is not None:
                txt += f"   {f['pct']:.0f}%"
            w["live"].configure(text=txt or "—")
            if "err" in w:
                w["err"].configure(text=("Error: " + f["error"][:60]) if f.get("error") else "")
        self.fan_emerg_lbl.configure(
            text="⚠ Temperatura alta: ventiladores controlados al 100%" if s.get("fan_emergency") else "")

    # ============================================================ Mi PC
    def open_specs(self):
        if getattr(self, "specs_win", None) is not None:
            self.specs_win.lift()
            return
        w = tk.Toplevel(self.root)
        w.title("Tempia — Mi PC")
        w.configure(bg=BG)
        w.geometry(f"{px(820)}x{px(680)}")
        w.minsize(px(560), px(400))
        w.attributes("-topmost", bool(self.cfg.get("topmost")))
        w.protocol("WM_DELETE_WINDOW", self.close_specs)
        self.specs_win = w
        head = tk.Frame(w, bg=BG)
        head.pack(fill="x", padx=px(18), pady=(px(14), px(4)))
        tk.Label(head, text="Mi PC", bg=BG, fg=TEXT, font=(FONT, 15, "bold")).pack(side="left")
        bar = tk.Frame(head, bg=BG)
        bar.pack(side="right")
        self.specs_copy = FlatButton(bar, "📋  Copiar todo", self._copy_specs, bg=CARD)
        self.specs_copy.pack(side="left", padx=(0, px(6)))
        FlatButton(bar, "⟳  Actualizar", lambda: self._load_specs(force=True), bg=CARD).pack(side="left")
        self.specs_sum = tk.Label(w, text="", bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                                  wraplength=px(760))
        self.specs_sum.pack(fill="x", padx=px(18), pady=(0, px(8)))
        outer = tk.Frame(w, bg=BG)
        outer.pack(fill="both", expand=True, padx=(px(18), px(4)), pady=(0, px(14)))
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.specs_list = tk.Frame(canvas, bg=BG)
        wid = canvas.create_window(0, 0, window=self.specs_list, anchor="nw")
        self.specs_list.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(wid, width=e.width - px(10)))

        def wheel(e):
            try:
                canvas.yview_scroll(int(-e.delta / 120) if e.delta else (1 if e.num == 5 else -1), "units")
            except tk.TclError:
                pass
        w.bind("<MouseWheel>", wheel)
        w.bind("<Button-4>", wheel)
        w.bind("<Button-5>", wheel)
        self._load_specs()

    def close_specs(self):
        if getattr(self, "specs_win", None) is not None:
            self.specs_win.destroy()
            self.specs_win = None

    def _load_specs(self, force=False):
        if getattr(self, "specs_data", None) and not force:
            self._show_specs()
            return
        for ch in self.specs_list.winfo_children():
            ch.destroy()
        self.specs_sum.configure(text="")
        tk.Label(self.specs_list, text="Leyendo los componentes de tu PC…", bg=BG, fg=MUTED,
                 font=(FONT, 11)).pack(anchor="w", pady=px(20))

        def work():
            try:
                raw = read_specs()
                err = None
            except Exception as e:
                raw, err = None, str(e)
            self.root.after(0, lambda: self._specs_done(raw, err))
        threading.Thread(target=work, daemon=True).start()

    def _specs_done(self, raw, err):
        if getattr(self, "specs_win", None) is None:
            return
        if err:
            for ch in self.specs_list.winfo_children():
                ch.destroy()
            tk.Label(self.specs_list, text=f"No se pudieron leer los componentes:\n{err}", bg=BG, fg=HOT_RED,
                     font=(FONT, 10), justify="left").pack(anchor="w", pady=px(20))
            return
        gpu_live = ((self.last or {}).get("gpus") or [None])[0]
        self.specs_data = parse_specs(raw, gpu_live)
        self._show_specs()

    def _show_specs(self):
        sections, summary = self.specs_data
        for ch in self.specs_list.winfo_children():
            ch.destroy()
        self.specs_sum.configure(text="  ·  ".join(summary))
        for title, color, items in sections:
            card = tk.Frame(self.specs_list, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
            card.pack(fill="x", pady=(0, px(10)))
            hd = tk.Frame(card, bg=CARD)
            hd.pack(fill="x", padx=px(14), pady=(px(10), px(4)))
            dot = tk.Canvas(hd, width=px(10), height=px(10), bg=CARD, highlightthickness=0)
            dot.create_oval(1, 1, px(9), px(9), fill=color, outline="")
            dot.pack(side="left", padx=(0, px(8)))
            tk.Label(hd, text=title, bg=CARD, fg=TEXT, font=(FONT, 11, "bold")).pack(side="left")
            body = tk.Frame(card, bg=CARD)
            body.pack(fill="x", padx=px(14), pady=(0, px(10)))
            for i, (sub, rows) in enumerate(items):
                if sub:
                    tk.Label(body, text=sub.upper(), bg=CARD, fg=MUTED, font=(FONT, 8, "bold")).pack(
                        anchor="w", pady=(px(8), px(2)))
                elif i > 0:
                    tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=px(6))
                for k, v in rows:
                    r = tk.Frame(body, bg=CARD)
                    r.pack(fill="x", pady=px(1))
                    tk.Label(r, text=k, bg=CARD, fg=MUTED, font=(FONT, 9), width=18, anchor="w").pack(side="left")
                    tk.Label(r, text=str(v), bg=CARD, fg=TEXT, font=(FONT, 9, "bold"), anchor="w",
                             justify="left", wraplength=px(520)).pack(side="left", fill="x")

    def _copy_specs(self):
        if not getattr(self, "specs_data", None):
            return
        sections, summary = self.specs_data
        lines = ["Mi PC (Tempia)", "  ·  ".join(summary), ""]
        for title, _c, items in sections:
            lines.append(f"== {title} ==")
            for sub, rows in items:
                if sub:
                    lines.append(f"[{sub}]")
                lines.extend(f"  {k}: {v}" for k, v in rows)
            lines.append("")
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.specs_copy.configure(text="✓ Copiado")
        self.root.after(1500, lambda: self.specs_copy.configure(text="📋  Copiar todo"))

    # ============================================================ configuración
    def open_settings(self, tab="general"):
        if self.settings_win is not None:
            self.settings_win.lift()
            self._settings_tab(tab)
            return
        w = tk.Toplevel(self.root)
        w.title("Tempia — Configuración")
        w.configure(bg=BG)
        w.geometry(f"{px(780)}x{px(580)}")
        w.minsize(px(640), px(440))
        w.attributes("-topmost", bool(self.cfg.get("topmost")))
        w.protocol("WM_DELETE_WINDOW", self.close_settings)
        self.settings_win = w
        nav = tk.Frame(w, bg=CARD, width=px(180))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Label(nav, text="Configuración", bg=CARD, fg=TEXT, font=(FONT, 12, "bold")).pack(
            anchor="w", padx=px(14), pady=(px(16), px(10)))
        self.nav_btns = {}
        for key, label in (("general", "General"), ("main", "Ventana principal"), ("sensors", "Sensores"),
                           ("mini", "Modo mini"), ("diag", "Diagnóstico"),
                           ("about", "Acerca de")):
            b = FlatButton(nav, label, lambda k=key: self._settings_tab(k), bg=CARD, font_size=10)
            b.configure(anchor="w")
            b.pack(fill="x", padx=px(8), pady=px(1))
            self.nav_btns[key] = b
        tk.Label(nav, text=f"Tempia v{APP_VERSION}\nCreado por {APP_AUTHOR}", bg=CARD, fg=DIM,
                 font=(FONT, 8), justify="left").pack(side="bottom", anchor="w", padx=px(14), pady=px(12))
        self.settings_body = tk.Frame(w, bg=BG)
        self.settings_body.pack(side="left", fill="both", expand=True, padx=px(20), pady=px(16))
        self._settings_tab(tab)

    def close_settings(self):
        if self.settings_win is not None:
            self.settings_win.destroy()
            self.settings_win = None

    def _section(self, parent, title, subtitle=None):
        tk.Label(parent, text=title, bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w", pady=(px(10), 0))
        if subtitle:
            tk.Label(parent, text=subtitle, bg=BG, fg=MUTED, font=(FONT, 9), justify="left",
                     wraplength=px(540)).pack(anchor="w")
        box = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="x", pady=(px(6), px(4)))
        inner = tk.Frame(box, bg=CARD)
        inner.pack(fill="x", padx=px(12), pady=px(10))
        return inner

    def _settings_tab(self, tab):
        for k, b in self.nav_btns.items():
            b.set_active(k == tab)
        for ch in self.settings_body.winfo_children():
            ch.destroy()
        getattr(self, f"_tab_{tab}")(self.settings_body)

    def _set(self, key, value, layout=False):
        self.cfg[key] = value
        save_config(self.cfg)
        if layout:
            self.apply_layout()
        self._rerender()

    def _tab_general(self, p):
        box = self._section(p, "Unidades y actualización")
        r = tk.Frame(box, bg=CARD)
        r.pack(fill="x", pady=px(3))
        tk.Label(r, text="Temperatura en", bg=CARD, fg=TEXT, font=(FONT, 10), width=18, anchor="w").pack(side="left")
        Segmented(r, [(False, "°C Celsius"), (True, "°F Fahrenheit")], self.cfg["fahrenheit"],
                  lambda v: (self._set("fahrenheit", v), self._refresh_buttons())).pack(side="left")
        r = tk.Frame(box, bg=CARD)
        r.pack(fill="x", pady=px(3))
        tk.Label(r, text="Actualizar cada", bg=CARD, fg=TEXT, font=(FONT, 10), width=18, anchor="w").pack(side="left")
        Segmented(r, [(0.5, "0,5 s"), (1.0, "1 s"), (2.0, "2 s"), (5.0, "5 s")], self.cfg["interval"],
                  lambda v: (self._set("interval", v), self._refresh_buttons())).pack(side="left")

        box = self._section(p, "Alertas de temperatura",
                            "Desde este valor el número se pone rojo (y naranja/amarillo un poco antes).")
        for key, label in (("cpu_alert", "CPU"), ("gpu_alert", "GPU")):
            r = tk.Frame(box, bg=CARD)
            r.pack(fill="x", pady=px(3))
            tk.Label(r, text=f"Alerta {label}", bg=CARD, fg=TEXT, font=(FONT, 10), width=18, anchor="w").pack(side="left")
            lbl = tk.Label(r, text=f"{self.cfg[key]}°C", bg=CARD, fg=HOT_RED, font=(FONT, 10, "bold"), width=6)

            def cb(v, k=key, l=lbl):
                self.cfg[k] = int(float(v))
                l.configure(text=f"{self.cfg[k]}°C")
                save_config(self.cfg)
            dark_scale(r, cb, self.cfg[key], 60, 100, 260).pack(side="left", padx=(0, px(8)))
            lbl.pack(side="left")

        box = self._section(p, "Ventana")
        make_check(box, "Siempre visible (encima de otras ventanas)", bool(self.cfg["topmost"]),
                   lambda v: (self.cfg.update(topmost=v), self.root.attributes("-topmost", v),
                              save_config(self.cfg), self._refresh_buttons())).pack(anchor="w")

    def _tab_main(self, p):
        box = self._section(p, "Secciones visibles", "Marca lo que quieres ver en la ventana principal.")
        grid = tk.Frame(box, bg=CARD)
        grid.pack(fill="x")
        for i, (key, label) in enumerate(CARD_NAMES):
            def cb(v, k=key):
                self.cfg["cards"][k] = v
                save_config(self.cfg)
                self.apply_layout()
            make_check(grid, label, self.cfg["cards"].get(key, True), cb).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, px(40)), pady=px(2))
        box = self._section(p, "Detalles")
        make_check(box, "Gráficos de historial", self.cfg["show_graphs"],
                   lambda v: self._set("show_graphs", v, True)).pack(anchor="w", pady=px(2))
        make_check(box, "Barras de uso por hilo del CPU", self.cfg["show_threads"],
                   lambda v: self._set("show_threads", v, True)).pack(anchor="w", pady=px(2))
        make_check(box, "Ocultar ventiladores detenidos o desconectados (0 RPM)", self.cfg["hide_zero_fans"],
                   lambda v: self._set("hide_zero_fans", v)).pack(anchor="w", pady=px(2))

    def _tab_sensors(self, p):
        tk.Label(p, text="Sensores", bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(anchor="w", pady=(px(10), 0))
        tk.Label(p, text="Desmarca los que no quieras ver en «Ventiladores» y «Otros sensores».",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        outer = tk.Frame(p, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        outer.pack(fill="both", expand=True, pady=(px(6), 0))
        canvas = tk.Canvas(outer, bg=CARD, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=CARD)
        wid = canvas.create_window(px(12), px(8), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(wid, width=e.width - px(24)))
        s = self.last or {}
        items = [("Ventiladores", f["id"], f"{self.fan_name(f)}  ({f['group']})") for f in s.get("fans", [])]
        items += [("Temperaturas", o["id"], f"{o['name']}  ({o['group']})") for o in s.get("others", [])]
        if not items:
            tk.Label(inner, text="Todavía no hay sensores extra detectados.", bg=CARD, fg=DIM,
                     font=(FONT, 10)).pack(anchor="w")
        last = None
        for grp, sid, label in items:
            if grp != last:
                tk.Label(inner, text=grp.upper(), bg=CARD, fg=MUTED, font=(FONT, 8, "bold")).pack(
                    anchor="w", pady=(px(8), px(2)))
                last = grp

            def cb(v, sid=sid):
                h = set(self.cfg["hidden_sensors"])
                if v:
                    h.discard(sid)
                else:
                    h.add(sid)
                self.cfg["hidden_sensors"] = sorted(h)
                save_config(self.cfg)
                self._rerender()
            make_check(inner, label, sid not in self.cfg["hidden_sensors"], cb).pack(anchor="w")

        def wheel(e):
            try:
                canvas.yview_scroll(int(-e.delta / 120) if e.delta else (1 if e.num == 5 else -1), "units")
            except tk.TclError:
                pass
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def _tab_mini(self, p):
        m = self.cfg["mini"]

        def upd(key, value):
            m[key] = value
            save_config(self.cfg)
            self.rebuild_mini()

        box = self._section(p, "Qué mostrar en el modo mini")
        grid = tk.Frame(box, bg=CARD)
        grid.pack(fill="x")
        for i, (key, _t, _c) in enumerate(MINI_ITEMS):
            def cb(v, k=key):
                items = [x for x in m["items"] if x != k]
                if v:
                    order = [x for x, _a, _b in MINI_ITEMS]
                    items.append(k)
                    items.sort(key=order.index)
                upd("items", items)
            make_check(grid, MINI_ITEM_NAMES[key], key in m["items"], cb).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, px(30)), pady=px(1))

        box = self._section(p, "Apariencia")
        rows = [("Orientación", "orientation", [("horizontal", "Horizontal"), ("vertical", "Vertical")]),
                ("Tamaño", "size", [("small", "Pequeño"), ("normal", "Normal"), ("large", "Grande")])]
        for label, key, opts in rows:
            r = tk.Frame(box, bg=CARD)
            r.pack(fill="x", pady=px(3))
            tk.Label(r, text=label, bg=CARD, fg=TEXT, font=(FONT, 10), width=14, anchor="w").pack(side="left")
            Segmented(r, opts, m[key], lambda v, k=key: upd(k, v)).pack(side="left")
        r = tk.Frame(box, bg=CARD)
        r.pack(fill="x", pady=px(3))
        tk.Label(r, text="Opacidad", bg=CARD, fg=TEXT, font=(FONT, 10), width=14, anchor="w").pack(side="left")
        ol = tk.Label(r, text=f"{m['opacity']}%", bg=CARD, fg=TEXT, font=(FONT, 10, "bold"), width=5)

        def op(v):
            m["opacity"] = int(float(v))
            ol.configure(text=f"{m['opacity']}%")
            save_config(self.cfg)
            if self.mini is not None:
                try:
                    self.mini.attributes("-alpha", m["opacity"] / 100)
                except Exception:
                    pass
        dark_scale(r, op, m["opacity"], 30, 100, 240).pack(side="left", padx=(0, px(8)))
        ol.pack(side="left")
        make_check(box, "Mostrar títulos (CPU °, GPU %, …)", m["labels"],
                   lambda v: upd("labels", v)).pack(anchor="w", pady=(px(4), 0))
        make_check(box, "Siempre encima de todo (juegos, OBS…)", m["topmost"],
                   lambda v: upd("topmost", v)).pack(anchor="w")
        r = tk.Frame(p, bg=BG)
        r.pack(fill="x", pady=(px(10), 0))
        FlatButton(r, "▣  Abrir modo mini", self.open_mini, bg=CARD).pack(side="left")
        tk.Label(r, text="Arrástralo para moverlo · doble clic o ✕ para volver",
                 bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left", padx=px(10))

    def _tab_diag(self, p):
        tk.Label(p, text="Diagnóstico de sensores", bg=BG, fg=TEXT, font=(FONT, 11, "bold")).pack(
            anchor="w", pady=(px(10), 0))
        tk.Label(p, text="Si algo no se detecta (como la temperatura del CPU), copia este texto y mándaselo a Claude.",
                 bg=BG, fg=MUTED, font=(FONT, 9), wraplength=px(540), justify="left").pack(anchor="w")
        bar = tk.Frame(p, bg=BG)
        bar.pack(fill="x", pady=px(6))
        tx = tk.Text(p, bg=CARD, fg=TEXT, font=(FONT_MONO, 9), relief="flat", highlightthickness=1,
                     highlightbackground=BORDER, wrap="none")

        def fill():
            tx.configure(state="normal")
            tx.delete("1.0", "end")
            tx.insert("end", self.monitor.diagnostics())
            tx.configure(state="disabled")

        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.monitor.diagnostics())
            b_copy.configure(text="✓ Copiado")
            self.root.after(1500, lambda: b_copy.configure(text="📋  Copiar"))

        def retry():
            self.monitor.reinit_requested = True
            b_retry.configure(text="Reiniciando…")
            self.root.after(2500, lambda: (fill(), b_retry.configure(text="⟳  Reintentar sensores")))
        b_copy = FlatButton(bar, "📋  Copiar", copy, bg=CARD)
        b_copy.pack(side="left")
        b_retry = FlatButton(bar, "⟳  Reintentar sensores", retry, bg=CARD)
        b_retry.pack(side="left", padx=px(6))
        FlatButton(bar, "⬇  Descargar PawnIO", lambda: webbrowser.open(PAWNIO_URL), bg=CARD).pack(side="left")
        FlatButton(bar, "↻", fill, bg=CARD).pack(side="right")
        tx.pack(fill="both", expand=True)
        fill()

    def _tab_about(self, p):
        top = tk.Frame(p, bg=BG)
        top.pack(fill="x", pady=(px(10), px(6)))
        logo = tk.Canvas(top, width=px(64), height=px(64), bg=BG, highlightthickness=0)
        self._draw_logo(logo, px(64))
        logo.pack(side="left")
        tb = tk.Frame(top, bg=BG)
        tb.pack(side="left", padx=px(14))
        tk.Label(tb, text="TEMPIA", bg=BG, fg=TEXT, font=(FONT, 20, "bold")).pack(anchor="w")
        tk.Label(tb, text=f"Versión {APP_VERSION}  ·  Monitor de temperaturas, uso y ventiladores",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")

        box = self._section(p, "Creado por")
        r = tk.Frame(box, bg=CARD)
        r.pack(fill="x")
        tk.Label(r, text=APP_AUTHOR, bg=CARD, fg=TEXT, font=(FONT, 13, "bold")).pack(side="left")
        lk = tk.Frame(box, bg=CARD)
        lk.pack(fill="x", pady=(px(6), 0))
        for text, url in (("🌐  boldo3108.cl", WEB_PAGE), ("GitHub", GITHUB_PAGE)):
            l = tk.Label(lk, text=text, bg=CARD, fg=ACCENT, font=(FONT, 10, "underline"), cursor="hand2")
            l.pack(side="left", padx=(0, px(18)))
            l.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
        tk.Label(box, text="Sensores: LibreHardwareMonitor (MPL-2.0), NVIDIA NVML, psutil. Driver: PawnIO.",
                 bg=CARD, fg=DIM, font=(FONT, 8)).pack(anchor="w", pady=(px(8), 0))

        box = self._section(p, "Actualizaciones", "Las versiones nuevas se publican en GitHub.")
        self.upd_lbl = tk.Label(box, text="", bg=CARD, fg=TEXT, font=(FONT, 10), justify="left",
                                wraplength=px(500))
        self.upd_lbl.pack(anchor="w")
        self.upd_bar = HBar(box, 6, bg=CARD, color=ACCENT)
        br = tk.Frame(box, bg=CARD)
        br.pack(fill="x", pady=(px(8), 0))
        FlatButton(br, "⟳  Buscar actualizaciones", lambda: self.check_updates(manual=True)).pack(side="left")
        self.upd_install = FlatButton(br, "⬇  Descargar e instalar", self.install_update, bg="#14532d")
        FlatButton(br, "Ver en GitHub", lambda: webbrowser.open(RELEASES_PAGE)).pack(side="right")
        make_check(box, "Buscar actualizaciones al abrir Tempia", self.cfg.get("check_updates", True),
                   lambda v: (self.cfg.update(check_updates=v), save_config(self.cfg))).pack(anchor="w", pady=(px(8), 0))
        self._show_update_state()

    def _show_update_state(self, text=None, color=TEXT):
        if not hasattr(self, "upd_lbl") or not self.upd_lbl.winfo_exists():
            return
        info = self.update_info
        if text is None:
            if info and is_newer(info["version"]):
                text, color = f"¡Hay una versión nueva! Tienes la {APP_VERSION} y ya salió la {info['version']}.", OK_GREEN
            elif info is not None:
                text, color = f"Tienes la última versión ({APP_VERSION}).", MUTED
            else:
                text, color = f"Versión instalada: {APP_VERSION}.", MUTED
        self.upd_lbl.configure(text=text, fg=color)
        if info and is_newer(info["version"]):
            self.upd_install.configure(text=f"⬇  Descargar e instalar v{info['version']}")
            self.upd_install.pack(side="left", padx=px(6))
        else:
            self.upd_install.pack_forget()

    def check_updates(self, manual=False):
        if manual:
            self._show_update_state("Buscando en GitHub…", MUTED)

        def work():
            try:
                info = check_latest_release()
                err = None
            except Exception as e:
                info, err = None, str(e)
            self.root.after(0, lambda: self._updates_done(info, err, manual))
        threading.Thread(target=work, daemon=True).start()

    def _updates_done(self, info, err, manual):
        if err:
            if manual:
                self._show_update_state(f"No se pudo revisar: {err}", HOT_RED)
            return
        self.update_info = info or {"version": APP_VERSION, "exe": "", "pagina": RELEASES_PAGE}
        if info and is_newer(info["version"]):
            self.b_update.configure(text=f"⬆  Nueva v{info['version']}")
            self.b_update.pack(side="right", padx=(px(6), 0))
        else:
            self.b_update.pack_forget()
        self._show_update_state()

    def install_update(self):
        info = self.update_info
        if not info or not is_newer(info["version"]):
            return
        if not getattr(sys, "frozen", False) or not info.get("exe"):
            webbrowser.open(info.get("pagina") or RELEASES_PAGE)
            return
        import tempfile
        folder = tempfile.mkdtemp(prefix="tempia_nueva_")
        dest = os.path.join(folder, "Tempia.exe")
        self.upd_bar.pack(fill="x", pady=(px(6), 0))
        self._show_update_state(f"Descargando Tempia v{info['version']}…", ACCENT)

        def prog(f):
            self.root.after(0, lambda: self.upd_bar.set(f))

        def work():
            try:
                download_file(info["exe"], dest, prog)
                self.root.after(0, lambda: self._finish_update(dest))
            except Exception as e:
                self.root.after(0, lambda: self._show_update_state(f"Falló la descarga: {e}", HOT_RED))
        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self, new_exe):
        self._show_update_state("Instalando… Tempia se va a reiniciar.", OK_GREEN)
        try:
            start_self_update(new_exe)
        except Exception as e:
            self._show_update_state(f"No se pudo instalar: {e}", HOT_RED)
            return
        self.root.after(300, self.on_close)

    # ================================================================ modo mini
    def rebuild_mini(self):
        if self.mini is not None:
            pos = f"+{self.mini.winfo_x()}+{self.mini.winfo_y()}"
            self.cfg["mini"]["pos"] = pos
            self.mini.destroy()
            self.mini = None
            self.open_mini(keep_main=True)

    def open_mini(self, keep_main=False):
        if self.mini is not None:
            return
        mc = self.cfg["mini"]
        sizes = {"small": (7, 10), "normal": (8, 13), "large": (10, 18)}
        tsz, vsz = sizes.get(mc["size"], sizes["normal"])
        m = tk.Toplevel(self.root)
        m.overrideredirect(True)
        m.attributes("-topmost", bool(mc["topmost"]))
        try:
            m.attributes("-alpha", mc["opacity"] / 100)
        except Exception:
            pass
        m.configure(bg=BORDER)
        inner = tk.Frame(m, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.mini_labels = {}
        items = mc["items"] or ["cpu_temp"]
        colors = {k: c for k, _t, c in MINI_ITEMS}
        titles = {k: t for k, t, _c in MINI_ITEMS}
        vertical = mc["orientation"] == "vertical"
        body = tk.Frame(inner, bg=CARD)
        body.pack(side="left", fill="both", expand=True, padx=px(10), pady=px(6))
        for i, key in enumerate(items):
            if vertical:
                fr = tk.Frame(body, bg=CARD)
                fr.pack(fill="x", pady=px(1))
                if mc["labels"]:
                    tk.Label(fr, text=titles[key], bg=CARD, fg=colors[key],
                             font=(FONT, tsz, "bold")).pack(side="left", padx=(0, px(14)))
                lv = tk.Label(fr, text="—", bg=CARD, fg=TEXT, font=(FONT, vsz, "bold"))
                lv.pack(side="right")
            else:
                fr = tk.Frame(body, bg=CARD)
                fr.pack(side="left", padx=(0 if i == 0 else px(14), 0))
                if mc["labels"]:
                    tk.Label(fr, text=titles[key], bg=CARD, fg=colors[key],
                             font=(FONT, tsz, "bold")).pack(anchor="w")
                lv = tk.Label(fr, text="—", bg=CARD, fg=TEXT, font=(FONT, vsz, "bold"))
                lv.pack(anchor="w")
            self.mini_labels[key] = lv
        side = tk.Frame(inner, bg=CARD)
        side.pack(side="right", fill="y", padx=(0, px(4)), pady=px(2))
        close = tk.Label(side, text="✕", bg=CARD, fg=DIM, font=(FONT, 9), cursor="hand2")
        close.pack(side="top")
        gear = tk.Label(side, text="⚙", bg=CARD, fg=DIM, font=(FONT, 9), cursor="hand2")
        gear.pack(side="top")

        def start(e):
            m._dx, m._dy = e.x_root - m.winfo_x(), e.y_root - m.winfo_y()

        def drag(e):
            m.geometry(f"+{e.x_root - m._dx}+{e.y_root - m._dy}")

        def all_children(w):
            out = [w]
            for ch in w.winfo_children():
                out.extend(all_children(ch))
            return out
        for w in all_children(m):
            if w in (close, gear):
                continue
            w.bind("<ButtonPress-1>", start)
            w.bind("<B1-Motion>", drag)
            w.bind("<Double-Button-1>", lambda e: self.close_mini())
        close.bind("<Button-1>", lambda e: self.close_mini())
        gear.bind("<Button-1>", lambda e: self.open_settings("mini"))
        pos = mc.get("pos")
        m.geometry(pos if pos else f"+{self.root.winfo_screenwidth() - px(420)}+{px(40)}")
        self.mini = m
        if not keep_main:
            self.root.withdraw()
        if self.last:
            self._render_mini(self.last)

    def close_mini(self):
        if self.mini is None:
            return
        self.cfg["mini"]["pos"] = f"+{self.mini.winfo_x()}+{self.mini.winfo_y()}"
        save_config(self.cfg)
        self.mini.destroy()
        self.mini = None
        self.root.deiconify()

    def _mini_value(self, key, s):
        c = s["cpu"]
        g = (s.get("gpus") or [{}])[0]
        ram = s.get("ram") or {}
        net = s.get("net") or {}
        if key == "cpu_temp":
            return self.t(c.get("temp")), temp_color(c.get("temp"), self.cfg["cpu_alert"])
        if key == "cpu_use":
            u = c.get("usage")
            return (f"{u:.0f}%" if u is not None else "—"), load_color(u)
        if key == "cpu_freq":
            f = c.get("freq")
            return (f"{f / 1000:.2f}" if f else "—"), TEXT
        if key == "cpu_power":
            p = c.get("power")
            return (f"{p:.0f} W" if p else "—"), TEXT
        if key == "gpu_temp":
            return self.t(g.get("temp")), temp_color(g.get("temp"), self.cfg["gpu_alert"])
        if key == "gpu_use":
            u = g.get("usage")
            return (f"{u:.0f}%" if u is not None else "—"), load_color(u)
        if key == "gpu_hotspot":
            return self.t(g.get("hotspot")), temp_color(g.get("hotspot"), self.cfg["gpu_alert"] + 12)
        if key == "gpu_power":
            p = g.get("power")
            return (f"{p:.0f} W" if p is not None else "—"), TEXT
        if key == "vram":
            mu = g.get("mem_used")
            return (fmt_bytes(mu) if mu is not None else "—"), TEXT
        if key == "ram":
            return (f"{ram['pct']:.0f}%" if ram else "—"), load_color(ram.get("pct"))
        if key == "fan":
            rpms = [f["rpm"] for f in self.visible_fans(s) if f.get("rpm")]
            return (f"{max(rpms):.0f} rpm" if rpms else "—"), FAN_COLOR
        if key == "net_down":
            return fmt_bits(net.get("down")), TEXT
        if key == "net_up":
            return fmt_bits(net.get("up")), TEXT
        if key == "clock":
            return time.strftime("%H:%M"), TEXT
        return "—", DIM

    def _render_mini(self, s):
        for key, lv in self.mini_labels.items():
            txt, col = self._mini_value(key, s)
            if lv.cget("text") != txt or lv.cget("fg") != col:
                lv.configure(text=txt, fg=col)

    # ================================================================== cierre
    def on_close(self):
        try:
            if self.root.state() == "normal":
                self.cfg["geometry"] = self.root.geometry()
        except Exception:
            pass
        if self.mini is not None:
            self.cfg["mini"]["pos"] = f"+{self.mini.winfo_x()}+{self.mini.winfo_y()}"
        save_config(self.cfg)
        self.running = False  # el hilo devuelve los ventiladores a automático y cierra sensores
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        self.running = False
        self.worker.join(timeout=4)


def main():
    if install_if_requested():
        return
    TempiaApp().run()


if __name__ == "__main__":
    main()
