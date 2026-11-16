"""Crea version_win.txt (datos del .exe: versión, autor, nombre) leyendo APP_VERSION de tempia.py."""
import re
from pathlib import Path

raiz = Path(__file__).resolve().parent.parent
v = re.search(r'APP_VERSION = "([^"]+)"', (raiz / "tempia.py").read_text(encoding="utf-8")).group(1)
n = [int(x) for x in re.findall(r"\d+", v)][:3]
n += [0] * (4 - len(n))
t = tuple(n[:4])
(raiz / "version_win.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040A04B0', [
      StringStruct('CompanyName', 'Boldo3108'),
      StringStruct('FileDescription', 'Tempia - Monitor de temperaturas y ventiladores'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'Tempia'),
      StringStruct('LegalCopyright', 'Creado por Boldo3108'),
      StringStruct('OriginalFilename', 'Tempia.exe'),
      StringStruct('ProductName', 'Tempia'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [0x040A, 1200])])
  ]
)
""", encoding="utf-8")
print("version_win.txt ->", v)
