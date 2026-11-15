# -*- coding: utf-8 -*-
"""
BootScribe - gestor de arranque UEFI para Windows (alternativa ligera a EasyUEFI).

A diferencia de la versión anterior, NO depende de bcdedit para las entradas: lee y escribe
directamente las variables UEFI (Boot####, BootOrder, BootNext) de la NVRAM mediante la API
de Windows (GetFirmwareEnvironmentVariableExW / SetFirmwareEnvironmentVariableExW).
Así "Habilitar" y "Deshabilitar" modifican el atributo real LOAD_OPTION_ACTIVE de la entrada,
que es lo que respeta el firmware.

Requiere Windows en modo UEFI y permisos de Administrador (se solicitan automáticamente).

Uso:
    python bootscribe.py                -> abre la aplicación
    python bootscribe.py --make-icon    -> genera bootscribe.ico (icono de monitor)
"""
import base64
import ctypes
import datetime
import json
import os
import re
import struct
import subprocess
import sys
import uuid
import zlib
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

APP_NAME = "BootScribe"
CREATE_NO_WINDOW = 0x08000000

EFI_GLOBAL_GUID = "{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}"
VAR_ATTRS = 0x07                 # NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS
LOAD_OPTION_ACTIVE = 0x00000001
SCAN_MAX = 0x100                 # se sondean Boot0000..Boot00FF, además de lo que liste BootOrder
VAR_NAME_RE = re.compile(r"^(BootOrder|Boot[0-9A-F]{4})$")

WIN_PATH = r"\EFI\Microsoft\Boot\bootmgfw.efi"
WIN_DESC = "Windows Boot Manager"
TXT_BLINDAR_GENERIC = "🚀 Blindar {bootmgr} con entrada seleccionada"
TXT_LIBERAR = "↩ Restaurar / Liberar {bootmgr}"

WIN_ERRORS = {
    1: "El equipo no arrancó en modo UEFI o Windows no puede acceder al firmware.",
    5: "Acceso denegado por el firmware.",
    87: "Parámetro no válido.",
    203: "Variable no encontrada.",
    1314: "Falta el privilegio SeSystemEnvironmentPrivilege (ejecuta como Administrador).",
}


# ----------------------------- Icono de monitor (sin archivos externos) -----------------------------
def monitor_rgba(n):
    """Dibuja un monitor en una cuadrícula n x n (RGBA). Diseño base de 64 x 64."""
    buf = bytearray(n * n * 4)  # transparente

    def rect(x0, y0, x1, y1, color):
        a, b = round(x0 * n / 64), round(y0 * n / 64)
        c, d = max(round(x1 * n / 64), round(x0 * n / 64) + 1), max(round(y1 * n / 64), round(y0 * n / 64) + 1)
        px = bytes(color)
        for y in range(b, min(d, n)):
            for x in range(a, min(c, n)):
                i = (y * n + x) * 4
                buf[i:i + 4] = px

    frame = (38, 43, 52, 255)
    rect(6, 8, 58, 44, frame)                 # carcasa
    rect(9, 11, 55, 41, (30, 136, 229, 255))  # pantalla
    rect(9, 11, 55, 17, (66, 165, 245, 255))  # brillo superior
    rect(28, 44, 36, 52, frame)               # cuello
    rect(18, 52, 46, 56, frame)               # base
    return buf


def make_png(w, h, rgba):
    raw = b"".join(b"\x00" + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw, 9)) +
            chunk(b"IEND", b""))


def build_ico(sizes=(16, 32, 48, 64, 128, 256)):
    pngs = [make_png(s, s, monitor_rgba(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, p in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(p), offset)
        offset += len(p)
    return head + entries + b"".join(pngs)


def make_icon_file(path="bootscribe.ico"):
    with open(path, "wb") as f:
        f.write(build_ico())
    return os.path.abspath(path)


def set_window_icon(win):
    """Asigna el icono de monitor a una ventana Tk (generado en memoria)."""
    try:
        png = base64.b64encode(make_png(64, 64, monitor_rgba(64)))
        img = tk.PhotoImage(data=png)
        win.iconphoto(True, img)
        win._icon_ref = img  # evita que el recolector de basura lo elimine
    except Exception:
        pass


# ----------------------------- Elevación -----------------------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    """Relanza el programa con permisos de administrador (UAC)."""
    exe = sys.executable
    if getattr(sys, "frozen", False):
        args = sys.argv[1:]
    else:
        pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pw):
            exe = pw
        args = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    params = " ".join('"%s"' % a for a in args)
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32
    except Exception:
        return False


# ----------------------------- Utilidades de sistema (bcdedit, procesos) -----------------------------
def _oem_encoding():
    try:
        return "cp%d" % ctypes.windll.kernel32.GetOEMCP()
    except Exception:
        return "cp437"


ENC = _oem_encoding()
_sysnative = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Sysnative", "bcdedit.exe")
BCDEDIT = _sysnative if os.path.exists(_sysnative) else "bcdedit"


def run(args):
    try:
        p = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        return 127, str(e)
    out = (p.stdout + p.stderr).decode(ENC, errors="replace")
    return p.returncode, out


def norm_path(p):
    """Normaliza una ruta EFI: barras invertidas, sin letra de unidad, con '\\' inicial."""
    p = (p or "").strip().replace("/", "\\")
    if p[1:2] == ":":
        p = p[2:]
    if p and not p.startswith("\\"):
        p = "\\" + p
    return p


def is_windows_path(p):
    return norm_path(p).lower() == WIN_PATH.lower()


def parse_block(body):
    """Devuelve lista de [clave, [valores]] de un bloque de bcdedit."""
    fields = []
    for line in body:
        if not line.strip():
            continue
        if not line.startswith(" "):
            m = re.match(r"^(\S.*?)\s{2,}(.*\S)\s*$", line)
            if m:
                fields.append([m.group(1), [m.group(2)]])
        elif fields:
            fields[-1][1].append(line.strip())
    return fields


def get_field(fields, *names, prefix=False):
    for k, v in fields:
        kl = k.lower().replace(" ", "")
        for n in names:
            if (prefix and kl.startswith(n)) or (not prefix and kl == n):
                return v
    return None


def get_bootmgr():
    """Lee {bootmgr} con bcdedit. Devuelve (ok, ruta, descripcion, salida_cruda)."""
    rc, out = run([BCDEDIT, "/enum", "{bootmgr}"])
    if rc != 0:
        return False, "", "", out
    lines = out.splitlines()
    dashes = [i for i, l in enumerate(lines) if re.fullmatch(r"-{3,}", l.strip())]
    start = dashes[0] + 1 if dashes else 0
    fields = parse_block(lines[start:])
    path = (get_field(fields, "path", "ruta", "rutadeacceso") or [""])[0]
    desc = (get_field(fields, "descri", prefix=True) or [""])[0]
    return True, path, desc, out


# ----------------------------- Formato EFI_LOAD_OPTION / Device Path -----------------------------
def parse_load_option(raw):
    """EFI_LOAD_OPTION: UINT32 Attributes, UINT16 FilePathListLength, CHAR16 Description[],
    FilePathList[], OptionalData[]. Devuelve (attributes, descripcion, fpl, optional)."""
    if len(raw) < 8:
        raise ValueError("entrada demasiado corta (%d bytes)" % len(raw))
    attributes, fpl_len = struct.unpack_from("<IH", raw, 0)
    pos = end = 6
    while True:
        if end + 2 > len(raw):
            raise ValueError("descripción sin terminador")
        if raw[end:end + 2] == b"\x00\x00":
            break
        end += 2
    desc = raw[pos:end].decode("utf-16-le", errors="replace")
    pos = end + 2
    if pos + fpl_len > len(raw):
        raise ValueError("lista de rutas truncada")
    return attributes, desc, raw[pos:pos + fpl_len], raw[pos + fpl_len:]


def build_load_option(attributes, description, fpl, optional=b""):
    return (struct.pack("<IH", attributes, len(fpl)) + description.encode("utf-16-le") +
            b"\x00\x00" + bytes(fpl) + bytes(optional))


def set_active_bit(raw, active):
    attr = struct.unpack_from("<I", raw, 0)[0]
    attr = (attr | LOAD_OPTION_ACTIVE) if active else (attr & ~LOAD_OPTION_ACTIVE & 0xFFFFFFFF)
    return struct.pack("<I", attr) + raw[4:]


def parse_device_path(blob):
    """Devuelve [(tipo, subtipo, datos)] hasta el nodo final."""
    nodes, i = [], 0
    while i + 4 <= len(blob):
        t, s = blob[i], blob[i + 1]
        ln = struct.unpack_from("<H", blob, i + 2)[0]
        if t == 0x7F or ln < 4 or i + ln > len(blob):
            break
        nodes.append((t, s, bytes(blob[i + 4:i + ln])))
        i += ln
    return nodes


MESSAGING = {1: "ATAPI", 2: "SCSI", 3: "FibreChannel", 5: "USB", 11: "Red(MAC)", 12: "Red(IPv4)",
             13: "Red(IPv6)", 18: "SATA", 23: "NVMe", 24: "URI"}


def describe_nodes(nodes):
    """Devuelve (ruta_efi, descripcion_dispositivo, nodo_HD_completo_o_None)."""
    path, parts, hd = "", [], None
    for t, s, d in nodes:
        if t == 4 and s == 4:                       # File Path
            path += d.decode("utf-16-le", errors="replace").split("\x00")[0]
        elif t == 4 and s == 1 and len(d) >= 38:    # Hard Drive
            pn = struct.unpack_from("<I", d, 0)[0]
            fmt = "GPT" if d[36] == 2 else "MBR"
            if d[37] == 2:
                sig = str(uuid.UUID(bytes_le=bytes(d[20:36]))).upper()
            else:
                sig = "%08X" % struct.unpack_from("<I", d, 20)[0]
            parts.append("HD(%d,%s,%s)" % (pn, fmt, sig[:8]))
            hd = bytes([4, 1]) + struct.pack("<H", 4 + len(d)) + d
        elif t == 3:
            parts.append(MESSAGING.get(s, "Msg%d" % s))
        elif t == 1 and s == 1:
            parts.append("PCI")
        elif t == 4 and s == 2:
            parts.append("CD-ROM")
    return path, " ".join(parts), hd


def build_hd_node(part_num, start_lba, size_lba, guid):
    body = (struct.pack("<IQQ", part_num, start_lba, size_lba) +
            uuid.UUID(str(guid).strip("{}")).bytes_le + bytes([2, 2]))   # GPT, firma GUID
    return bytes([4, 1]) + struct.pack("<H", 4 + len(body)) + body


def build_file_path_list(hd_node, path):
    data = path.encode("utf-16-le") + b"\x00\x00"
    fp_node = bytes([4, 4]) + struct.pack("<H", 4 + len(data)) + data
    return bytes(hd_node) + fp_node + bytes([0x7F, 0xFF, 4, 0])


class BootEntry:
    """Una variable Boot#### ya interpretada."""

    def __init__(self, num, raw, attrs):
        self.num, self.raw, self.attrs = num, bytes(raw), attrs
        self.id = "Boot%04X" % num
        self.ok, self.error = True, ""
        self.attributes, self.name = 0, "(ilegible)"
        self.fpl, self.optional = b"", b""
        self.path, self.device, self.hd_node = "", "", None
        try:
            self.attributes, self.name, self.fpl, self.optional = parse_load_option(self.raw)
            self.path, self.device, self.hd_node = describe_nodes(parse_device_path(self.fpl))
        except Exception as ex:
            self.ok, self.error = False, str(ex)
            if len(self.raw) >= 4:
                self.attributes = struct.unpack_from("<I", self.raw, 0)[0]

    @property
    def active(self):
        return bool(self.attributes & LOAD_OPTION_ACTIVE)


# ----------------------------- Acceso a la NVRAM UEFI -----------------------------
class NvramError(Exception):
    pass


def win_error_text(code):
    base = WIN_ERRORS.get(code)
    try:
        sysmsg = ctypes.FormatError(code).strip()
    except Exception:
        sysmsg = ""
    return "Error %d: %s" % (code, base or sysmsg or "desconocido")


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", ctypes.c_uint32)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", ctypes.c_uint32), ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


class NvramBase:
    """Operaciones de alto nivel; las subclases implementan _raw_get/_raw_set."""

    def open(self):
        pass

    def _raw_get(self, name):          # -> (bytes, attrs) | None
        raise NotImplementedError

    def _raw_set(self, name, data, attrs):
        raise NotImplementedError

    def get(self, name):
        return self._raw_get(name)

    def set(self, name, data, attrs=VAR_ATTRS):
        self._raw_set(name, bytes(data), attrs)

    def delete(self, name):
        self._raw_set(name, b"", VAR_ATTRS)

    def get_u16(self, name):
        v = self.get(name)
        return struct.unpack_from("<H", v[0], 0)[0] if v and len(v[0]) >= 2 else None

    def set_u16(self, name, value):
        self.set(name, struct.pack("<H", value))

    def boot_order(self):
        v = self.get("BootOrder")
        if not v:
            return []
        n = len(v[0]) // 2
        return list(struct.unpack("<%dH" % n, v[0][:n * 2]))

    def set_boot_order(self, nums):
        if not nums:
            raise NvramError("BootOrder no puede quedar vacío.")
        self.set("BootOrder", struct.pack("<%dH" % len(nums), *nums))

    def read_all(self):
        """Devuelve (orden, [BootEntry])."""
        order = self.boot_order()
        entries = []
        for n in sorted(set(range(SCAN_MAX)) | set(order)):
            v = self.get("Boot%04X" % n)
            if v:
                entries.append(BootEntry(n, v[0], v[1]))
        return order, entries


class Nvram(NvramBase):
    """Implementación para Windows (kernel32 + advapi32)."""

    def __init__(self):
        self.k32 = None

    def open(self):
        if os.name != "nt":
            raise NvramError("Esta herramienta solo funciona en Windows.")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetFirmwareEnvironmentVariableExW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32)]
        k32.GetFirmwareEnvironmentVariableExW.restype = ctypes.c_uint32
        k32.SetFirmwareEnvironmentVariableExW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32]
        k32.SetFirmwareEnvironmentVariableExW.restype = ctypes.c_int
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.k32 = k32

        try:  # Windows 8+: 1 = BIOS heredado, 2 = UEFI
            ftype = ctypes.c_uint32(0)
            k32.GetFirmwareType.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
            if k32.GetFirmwareType(ctypes.byref(ftype)) and ftype.value == 1:
                raise NvramError("Este equipo arrancó en modo BIOS heredado (Legacy/CSM), "
                                 "no en UEFI. No hay variables UEFI que administrar.")
        except (AttributeError, OSError):
            pass
        self._enable_privilege("SeSystemEnvironmentPrivilege")

    def _enable_privilege(self, name):
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        adv.OpenProcessToken.restype = ctypes.c_int
        adv.LookupPrivilegeValueW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(_LUID)]
        adv.LookupPrivilegeValueW.restype = ctypes.c_int
        adv.AdjustTokenPrivileges.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(_TOKEN_PRIVILEGES),
                                              ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        adv.AdjustTokenPrivileges.restype = ctypes.c_int
        token = ctypes.c_void_p()
        if not adv.OpenProcessToken(self.k32.GetCurrentProcess(), 0x28, ctypes.byref(token)):
            raise NvramError("No se pudo abrir el token del proceso. " + win_error_text(ctypes.get_last_error()))
        try:
            tp = _TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            if not adv.LookupPrivilegeValueW(None, name, ctypes.byref(tp.Privileges[0].Luid)):
                raise NvramError("No se pudo buscar el privilegio. " + win_error_text(ctypes.get_last_error()))
            tp.Privileges[0].Attributes = 2  # SE_PRIVILEGE_ENABLED
            ctypes.set_last_error(0)
            ok = adv.AdjustTokenPrivileges(token, 0, ctypes.byref(tp), 0, None, None)
            err = ctypes.get_last_error()
            if not ok or err == 1300:  # ERROR_NOT_ALL_ASSIGNED
                raise NvramError("No se pudo activar el privilegio SeSystemEnvironmentPrivilege. "
                                 "Ejecuta BootScribe como Administrador.")
        finally:
            self.k32.CloseHandle(token)

    def _raw_get(self, name):
        size = 4096
        while True:
            buf = ctypes.create_string_buffer(size)
            attrs = ctypes.c_uint32(0)
            ctypes.set_last_error(0)
            n = self.k32.GetFirmwareEnvironmentVariableExW(
                name, EFI_GLOBAL_GUID, ctypes.addressof(buf), size, ctypes.byref(attrs))
            if n:
                return buf.raw[:n], attrs.value
            err = ctypes.get_last_error()
            if err == 122 and size < (1 << 20):    # ERROR_INSUFFICIENT_BUFFER
                size *= 4
                continue
            if err in (1, 1314):
                raise NvramError("No se pueden leer las variables UEFI. " + win_error_text(err))
            return None                             # no existe

    def _raw_set(self, name, data, attrs):
        buf = ctypes.create_string_buffer(data, len(data)) if data else None
        ctypes.set_last_error(0)
        ok = self.k32.SetFirmwareEnvironmentVariableExW(
            name, EFI_GLOBAL_GUID, ctypes.addressof(buf) if buf is not None else None,
            len(data), attrs)
        if not ok:
            err = ctypes.get_last_error()
            raise NvramError("No se pudo %s '%s'. %s" % (
                "escribir" if data else "eliminar", name, win_error_text(err)))


# ----------------------------- Copias de seguridad -----------------------------
BACKUP_FORMAT = 1


def backup_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME, "backups")
    os.makedirs(d, exist_ok=True)
    return d


def snapshot_json(order, entries):
    vars_ = {"BootOrder": {"attrs": VAR_ATTRS, "hex": struct.pack("<%dH" % len(order), *order).hex()}}
    for e in entries:
        vars_[e.id] = {"attrs": e.attrs, "hex": e.raw.hex()}
    return {"app": APP_NAME, "format": BACKUP_FORMAT,
            "created": datetime.datetime.now().isoformat(timespec="seconds"), "vars": vars_}


def parse_backup(data):
    """Valida un diccionario de copia. Devuelve {nombre: (bytes, attrs)}; lanza ValueError si es inválido."""
    if not isinstance(data, dict) or data.get("app") != APP_NAME or not isinstance(data.get("vars"), dict):
        raise ValueError("El archivo no es una copia de BootScribe.")
    out = {}
    for name, v in data["vars"].items():
        if not VAR_NAME_RE.match(name):
            raise ValueError("Variable no permitida en la copia: %r" % name)
        raw = bytes.fromhex(v["hex"])
        attrs = int(v.get("attrs", VAR_ATTRS))
        if not raw:
            raise ValueError("Variable vacía en la copia: %s" % name)
        if name == "BootOrder" and len(raw) % 2:
            raise ValueError("BootOrder dañado en la copia.")
        out[name] = (raw, attrs)
    if "BootOrder" not in out:
        raise ValueError("La copia no contiene BootOrder.")
    return out


# ----------------------------- Diálogo nueva entrada -----------------------------
class NewEntryDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Nueva entrada de arranque UEFI")
        self.resizable(False, False)
        self.transient(app.root)
        self.name_var = tk.StringVar()
        self.path_var = tk.StringVar(value=r"\EFI\Boot\bootx64.efi")
        self.first_var = tk.BooleanVar(value=False)

        f = ttk.Frame(self, padding=12)
        f.grid(sticky="nsew")
        ttk.Label(f, text="Nombre:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.name_var, width=48).grid(row=0, column=1, columnspan=2, pady=4)
        ttk.Label(f, text="Archivo .efi (en la ESP):").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.path_var, width=40).grid(row=1, column=1, pady=4)
        ttk.Button(f, text="Examinar ESP…", command=self.browse).grid(row=1, column=2, padx=(6, 0))
        ttk.Checkbutton(f, text="Colocar al inicio de la lista (hacerla predeterminada)",
                        variable=self.first_var).grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
        bf = ttk.Frame(f)
        bf.grid(row=3, column=0, columnspan=3, pady=(10, 0), sticky="e")
        ttk.Button(bf, text="Crear", command=self.ok).pack(side="left", padx=4)
        ttk.Button(bf, text="Cancelar", command=self.close).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.grab_set()

    def browse(self):
        try:
            letter = self.app.ensure_esp()
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)
            return
        p = filedialog.askopenfilename(parent=self, initialdir=letter + ":\\EFI",
                                       title="Selecciona un archivo .efi",
                                       filetypes=[("Aplicación EFI", "*.efi"), ("Todos", "*.*")])
        if p:
            p = norm_path(p)
            self.path_var.set(p)
            if not self.name_var.get().strip():
                self.name_var.set(os.path.splitext(os.path.basename(p))[0])

    def ok(self):
        name = self.name_var.get().strip()
        path = norm_path(self.path_var.get())
        if not name or not path:
            messagebox.showwarning("Datos incompletos", "Indica nombre y ruta .efi.", parent=self)
            return
        if self.app.create_entry(name, path, self.first_var.get()):
            self.close()

    def close(self):
        self.app.release_esp()
        self.destroy()


# ----------------------------- Aplicación principal -----------------------------
class App:
    def __init__(self, root, nvram=None):
        self.root = root
        self.nv = nvram or Nvram()
        self.entries = []
        self.emap = {}
        self.order = []
        self.boot_current = None
        self.boot_next = None
        self.esp_letter = None
        self.esp_mounted_by_us = False
        self._refreshing = False
        self._backed_up = False
        self.ready = False

        root.title(APP_NAME)
        set_window_icon(root)
        root.geometry("1160x680")
        root.minsize(940, 560)
        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        bar = ttk.Frame(root, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        for text, cmd in [("⟳ Actualizar", self.refresh),
                          ("▲ Subir", lambda: self.move(-1)),
                          ("▼ Bajar", lambda: self.move(1)),
                          ("★ Establecer predeterminada", self.set_default),
                          ("＋ Nueva entrada", self.new_entry),
                          ("✎ Renombrar", self.rename_entry),
                          ("✖ Eliminar", self.delete_entry)]:
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)

        bar2 = ttk.Frame(root, padding=(8, 4, 8, 0))
        bar2.pack(fill="x")
        ttk.Button(bar2, text="Habilitar entrada", command=self.habilitar_entrada).pack(side="left", padx=2)
        ttk.Button(bar2, text="Deshabilitar entrada", command=self.deshabilitar_entrada).pack(side="left", padx=2)
        ttk.Button(bar2, text="⏭ Arrancar una vez desde esta entrada",
                   command=self.boot_once).pack(side="left", padx=(14, 2))
        ttk.Button(bar2, text="⏻ Reiniciar a UEFI", command=self.reboot_fw).pack(side="right", padx=2)

        bar3 = ttk.Frame(root, padding=(8, 4, 8, 4))
        bar3.pack(fill="x")
        ttk.Button(bar3, text="💾 Copia de seguridad", command=self.manual_backup).pack(side="left", padx=2)
        ttk.Button(bar3, text="♻ Restaurar copia…", command=self.restore_backup).pack(side="left", padx=2)
        self.btn_toggle = ttk.Button(bar3, text=TXT_BLINDAR_GENERIC, command=self.toggle_bootmgr)
        self.btn_toggle.pack(side="left", padx=(14, 2))

        self.lbl_info = ttk.Label(root, text="", padding=(10, 0, 8, 0))
        self.lbl_info.pack(fill="x")
        self.lbl_state = ttk.Label(root, text="", padding=(10, 0, 8, 2))
        self.lbl_state.pack(fill="x")

        mid = ttk.Frame(root, padding=(8, 4))
        mid.pack(fill="both", expand=True)
        cols = ("pos", "name", "id", "path", "device", "state")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("pos", "#", 40), ("name", "Nombre", 200), ("id", "ID", 80),
                        ("path", "Ruta EFI", 250), ("device", "Dispositivo", 160),
                        ("state", "Estado", 270)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c not in ("pos", "id")))
        self.tree.tag_configure("disabled", foreground="#888888")
        self.tree.tag_configure("default", font=("Segoe UI", 9, "bold"))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        root.bind("<F5>", lambda _e: self.refresh())

        lf = ttk.LabelFrame(root, text="Registro", padding=4)
        lf.pack(fill="x", padx=8, pady=(4, 8))
        self.log = tk.Text(lf, height=8, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            self.nv.open()
        except NvramError as e:
            messagebox.showerror(APP_NAME, str(e))
            root.destroy()
            return
        self.ready = True
        self.refresh()

    # ---- helpers ----
    def logmsg(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def fail(self, titulo, ex):
        self.logmsg("[ERROR] %s: %s" % (titulo, ex))
        messagebox.showerror(titulo, str(ex))

    def selected_entry(self):
        sel = self.tree.selection()
        return self.emap.get(sel[0]) if sel else None

    def need_entry(self):
        e = self.selected_entry()
        if not e:
            messagebox.showinfo("Selecciona una entrada", "Primero selecciona una entrada de la lista.")
        return e

    def on_select(self, _event=None):
        if not self._refreshing:
            self.update_toggle()

    def refresh(self, keep=None):
        self._refreshing = True
        try:
            try:
                self.order, self.entries = self.nv.read_all()
                self.boot_current = self.nv.get_u16("BootCurrent")
                self.boot_next = self.nv.get_u16("BootNext")
            except NvramError as e:
                self.fail("No se pudo leer la NVRAM", e)
                return
            self.emap = {e.id: e for e in self.entries}
            by_num = {e.num: e for e in self.entries}
            ordered = [by_num[n] for n in self.order if n in by_num]
            rest = [e for e in self.entries if e.num not in self.order]
            dflt = next((n for n in self.order if n in by_num and by_num[n].active), None)

            self.tree.delete(*self.tree.get_children())
            for e in ordered + rest:
                pos = str(self.order.index(e.num) + 1) if e.num in self.order else "–"
                if not e.active:
                    state, tags = "Deshabilitada", ("disabled",)
                elif e.num == dflt:
                    state, tags = "Predeterminada", ("default",)
                else:
                    state, tags = "Habilitada", ()
                if e.num == self.boot_current:
                    state += " · en uso"
                if e.num == self.boot_next:
                    state += " · próximo arranque"
                if not e.ok:
                    state += " · ilegible"
                self.tree.insert("", "end", iid=e.id, tags=tags,
                                 values=(pos, e.name, e.id, e.path, e.device, state))
            if keep and self.tree.exists(keep):
                self.tree.selection_set(keep)
                self.tree.see(keep)
            habil = sum(1 for e in self.entries if e.active)
            cur = by_num.get(self.boot_current)
            self.lbl_info.configure(text="Entradas: %d   Habilitadas: %d   Arranque actual: %s" % (
                len(self.entries), habil, ("%s (%s)" % (cur.name, cur.id)) if cur else "desconocido"))
        finally:
            self._refreshing = False
            self.update_toggle()

    # ---- copias de seguridad ----
    def make_backup(self, tag):
        order, entries = self.nv.read_all()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(backup_dir(), "boot_%s_%s.json" % (stamp, tag))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot_json(order, entries), f, indent=1)
        return path

    def ensure_backup(self):
        """Antes del primer cambio de la sesión se guarda una copia automática."""
        if self._backed_up:
            return True
        try:
            p = self.make_backup("auto")
        except Exception as ex:
            self.logmsg("[ERROR] No se pudo crear la copia de seguridad: %s" % ex)
            return messagebox.askyesno("Sin copia de seguridad",
                                       "No se pudo crear la copia de seguridad:\n%s\n\n"
                                       "¿Continuar de todos modos?" % ex, icon="warning")
        self._backed_up = True
        self.logmsg("[INFO] Copia de seguridad creada: %s" % p)
        return True

    def manual_backup(self):
        try:
            p = self.make_backup("manual")
        except Exception as ex:
            self.fail("Copia de seguridad", ex)
            return
        self.logmsg("[ÉXITO] Copia de seguridad creada: %s" % p)
        messagebox.showinfo("Copia de seguridad", "Copia guardada en:\n%s" % p)

    def restore_backup(self):
        p = filedialog.askopenfilename(initialdir=backup_dir(), title="Selecciona una copia de BootScribe",
                                       filetypes=[("Copia BootScribe", "*.json"), ("Todos", "*.*")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                saved = parse_backup(json.load(f))
        except Exception as ex:
            self.fail("Copia no válida", ex)
            return
        try:
            _, current = self.nv.read_all()
        except NvramError as ex:
            self.fail("No se pudo leer la NVRAM", ex)
            return
        extra = [e for e in current if e.id not in saved]
        if not messagebox.askyesno(
                "Restaurar copia",
                "Se restaurarán %d entradas y el orden de arranque desde:\n%s\n\n"
                "Se eliminarán %d entradas que no estaban en la copia%s.\n\n"
                "Antes se guarda una copia del estado actual. ¿Continuar?" % (
                    len(saved) - 1, os.path.basename(p), len(extra),
                    (": " + ", ".join(e.name for e in extra)) if extra else ""),
                icon="warning"):
            return
        try:
            safety = self.make_backup("antes_de_restaurar")
            self.logmsg("[INFO] Copia del estado actual: %s" % safety)
            for name in sorted(saved, key=lambda n: n == "BootOrder"):   # BootOrder al final
                raw, attrs = saved[name]
                self.nv.set(name, raw, attrs)
                self.logmsg("[NVRAM] %s restaurada (%d bytes)" % (name, len(raw)))
            for e in extra:
                self.nv.delete(e.id)
                self.logmsg("[NVRAM] %s eliminada (no estaba en la copia)" % e.id)
        except NvramError as ex:
            self.fail("Error al restaurar", ex)
            self.refresh()
            return
        self.logmsg("[ÉXITO] Copia restaurada.")
        self.refresh()
        messagebox.showinfo("Restaurar copia", "Copia restaurada correctamente.")

    # ---- escritura segura ----
    def write_var(self, name, data, attrs=VAR_ATTRS, what=""):
        try:
            self.nv.set(name, data, attrs)
        except NvramError as ex:
            self.fail("Error de escritura", ex)
            return False
        self.logmsg("[NVRAM] %s%s" % (name, (" — " + what) if what else ""))
        return True

    def verify(self, name, data):
        v = self.nv.get(name)
        return bool(v) and v[0] == bytes(data)

    # ---- acciones ----
    def move(self, delta):
        e = self.need_entry()
        if not e:
            return
        if e.num not in self.order:
            messagebox.showinfo("Fuera del orden", "Esta entrada no está en el orden de arranque.\n"
                                                   "Habilítala primero.")
            return
        order = list(self.order)
        i = order.index(e.num)
        j = i + delta
        if not 0 <= j < len(order):
            return
        if not self.ensure_backup():
            return
        order[i], order[j] = order[j], order[i]
        if self.write_var("BootOrder", struct.pack("<%dH" % len(order), *order),
                          what="orden: %s" % " ".join("%04X" % n for n in order)):
            self.refresh(e.id)

    def set_default(self):
        e = self.need_entry()
        if not e:
            return
        if not e.active:
            if not messagebox.askyesno("Entrada deshabilitada",
                                       "'%s' está deshabilitada.\n¿Habilitarla y ponerla en primer lugar?" % e.name):
                return
        if not self.ensure_backup():
            return
        if not e.active and not self.write_var(e.id, set_active_bit(e.raw, True), e.attrs, "habilitada"):
            return
        order = [e.num] + [n for n in self.order if n != e.num]
        if self.write_var("BootOrder", struct.pack("<%dH" % len(order), *order),
                          what="'%s' ahora es la primera del orden" % e.name):
            self.refresh(e.id)
            self.logmsg("[ÉXITO] '%s' establecida como predeterminada." % e.name)

    def habilitar_entrada(self):
        """Habilita la entrada (bit LOAD_OPTION_ACTIVE = 1). NO cambia su prioridad: si ya figuraba
        en BootOrder conserva su posición; si no figuraba, se agrega al final."""
        e = self.need_entry()
        if not e:
            return
        if len(e.raw) < 4:
            messagebox.showerror("Entrada dañada", "La variable %s no se puede interpretar." % e.id)
            return
        if e.active and e.num in self.order:
            self.logmsg("[INFO] '%s' ya está habilitada." % e.name)
            messagebox.showinfo("Ya habilitada", "La entrada '%s' ya está habilitada." % e.name)
            return
        if not self.ensure_backup():
            return
        self.logmsg("Habilitando '%s' (%s)…" % (e.name, e.id))
        if not e.active:
            new = set_active_bit(e.raw, True)
            if not self.write_var(e.id, new, e.attrs, "atributo ACTIVE: 0 → 1"):
                return
            if not self.verify(e.id, new):
                self.logmsg("[ERROR] El firmware no conservó el cambio en %s." % e.id)
                messagebox.showwarning("Verifica el resultado",
                                       "Se escribió la variable, pero el firmware no conservó el cambio.")
                self.refresh(e.id)
                return
        if e.num not in self.order:
            order = list(self.order) + [e.num]
            if not self.write_var("BootOrder", struct.pack("<%dH" % len(order), *order),
                                  what="agregada al final del orden (no cambia la predeterminada)"):
                return
        self.refresh(e.id)
        self.logmsg("[ÉXITO] Entrada '%s' habilitada." % e.name)
        messagebox.showinfo("Entrada habilitada", "'%s' está habilitada." % e.name)

    def deshabilitar_entrada(self):
        """Deshabilita la entrada (bit LOAD_OPTION_ACTIVE = 0). Conserva su posición en BootOrder
        para poder volver a habilitarla igual que estaba."""
        e = self.need_entry()
        if not e:
            return
        if len(e.raw) < 4:
            messagebox.showerror("Entrada dañada", "La variable %s no se puede interpretar." % e.id)
            return
        if not e.active:
            self.logmsg("[INFO] '%s' ya está deshabilitada." % e.name)
            messagebox.showinfo("Ya deshabilitada", "La entrada '%s' ya está deshabilitada." % e.name)
            return
        otras = [n for n in self.order if n != e.num and n in {x.num for x in self.entries if x.active}]
        if not otras:
            messagebox.showwarning("No permitido",
                                   "No puedes deshabilitar la única entrada habilitada del orden de arranque: "
                                   "el equipo quedaría sin nada que iniciar.")
            return
        if not self.ensure_backup():
            return
        self.logmsg("Deshabilitando '%s' (%s)…" % (e.name, e.id))
        new = set_active_bit(e.raw, False)
        if not self.write_var(e.id, new, e.attrs, "atributo ACTIVE: 1 → 0"):
            return
        ok = self.verify(e.id, new)
        self.refresh(e.id)
        if ok:
            self.logmsg("[ÉXITO] Entrada '%s' deshabilitada." % e.name)
            messagebox.showinfo("Entrada deshabilitada", "'%s' está deshabilitada." % e.name)
        else:
            self.logmsg("[ERROR] El firmware no conservó el cambio en %s." % e.id)
            messagebox.showwarning("Verifica el resultado",
                                   "Se escribió la variable, pero el firmware no conservó el cambio.")

    def boot_once(self):
        """Fija BootNext: el próximo arranque (solo uno) usará esta entrada."""
        e = self.need_entry()
        if not e:
            return
        if not e.active:
            messagebox.showinfo("Entrada deshabilitada", "Habilita la entrada antes de usarla para arrancar.")
            return
        if not messagebox.askyesno("Arrancar una vez",
                                   "El próximo arranque (solo ese) usará:\n\n%s\n\n"
                                   "¿Establecerlo y reiniciar ahora?" % e.name):
            return
        try:
            self.nv.set_u16("BootNext", e.num)
        except NvramError as ex:
            self.fail("No se pudo fijar BootNext", ex)
            return
        self.logmsg("[NVRAM] BootNext = %s (%s)" % (e.id, e.name))
        rc, out = run(["shutdown", "/r", "/t", "0"])
        if rc != 0:
            self.fail("No se pudo reiniciar", out.strip() or "error desconocido")
            self.refresh(e.id)

    def rename_entry(self):
        e = self.need_entry()
        if not e:
            return
        if not e.ok:
            messagebox.showerror("Entrada ilegible", "No se puede renombrar: %s" % e.error)
            return
        new = simpledialog.askstring("Renombrar", "Nuevo nombre:", initialvalue=e.name, parent=self.root)
        if not new or not new.strip() or new.strip() == e.name:
            return
        if not self.ensure_backup():
            return
        raw = build_load_option(e.attributes, new.strip(), e.fpl, e.optional)
        if self.write_var(e.id, raw, e.attrs, "renombrada a '%s'" % new.strip()):
            self.refresh(e.id)
            self.logmsg("[ÉXITO] Entrada renombrada.")

    def delete_entry(self):
        e = self.need_entry()
        if not e:
            return
        restantes = [n for n in self.order if n != e.num and n in {x.num for x in self.entries}]
        if not restantes:
            messagebox.showwarning("No permitido", "No puedes eliminar la única entrada del orden de arranque.")
            return
        aviso = ""
        if e.path.lower() == WIN_PATH.lower():
            aviso = "\n\nATENCIÓN: es el Administrador de arranque de Windows."
        if not messagebox.askyesno("Confirmar eliminación",
                                   "¿Eliminar definitivamente la entrada?\n\n%s (%s)\n%s%s" % (
                                       e.name, e.id, e.path, aviso), icon="warning"):
            return
        if not self.ensure_backup():
            return
        try:
            if e.num in self.order:
                self.nv.set_boot_order([n for n in self.order if n != e.num])
                self.logmsg("[NVRAM] BootOrder sin %s" % e.id)
            self.nv.delete(e.id)
            self.logmsg("[NVRAM] %s eliminada" % e.id)
            if self.boot_next == e.num:
                self.nv.delete("BootNext")
        except NvramError as ex:
            self.fail("Error al eliminar", ex)
            self.refresh()
            return
        self.logmsg("[ÉXITO] Entrada '%s' eliminada." % e.name)
        self.refresh()

    def reboot_fw(self):
        if messagebox.askyesno("Reiniciar", "El equipo se reiniciará de inmediato en la configuración UEFI.\n"
                                            "Guarda tu trabajo. ¿Continuar?", icon="warning"):
            rc, out = run(["shutdown", "/r", "/fw", "/t", "0"])
            if rc != 0:
                self.fail("No se pudo reiniciar al firmware", out.strip() or "error desconocido")

    # ---- ESP y nueva entrada ----
    def ensure_esp(self):
        if self.esp_letter and os.path.exists(self.esp_letter + ":\\"):
            return self.esp_letter
        for l in "STUVWXYZRQPONMLKJIHGFED":
            if not os.path.exists(l + ":\\"):
                rc, out = run(["mountvol", l + ":", "/S"])
                self.logmsg("> mountvol %s: /S\n%s" % (l, out.strip()))
                if rc != 0:
                    raise RuntimeError("No se pudo montar la partición del sistema EFI:\n" + out.strip())
                self.esp_letter, self.esp_mounted_by_us = l, True
                return l
        raise RuntimeError("No hay letras de unidad libres para montar la ESP.")

    def release_esp(self):
        if self.esp_letter and self.esp_mounted_by_us:
            rc, out = run(["mountvol", self.esp_letter + ":", "/D"])
            self.logmsg("> mountvol %s: /D\n%s" % (self.esp_letter, out.strip()))
        self.esp_letter, self.esp_mounted_by_us = None, False

    def esp_file_exists(self, path):
        """True/False si el archivo existe en la ESP; None si no se pudo verificar."""
        try:
            letter = self.ensure_esp()
            return os.path.exists(letter + ":" + path)
        except Exception as e:
            self.logmsg("[AVISO] No se pudo verificar la ESP: %s" % e)
            return None
        finally:
            self.release_esp()

    def find_esp_hd_node(self):
        """Nodo 'disco duro' de la partición ESP. Primero se copia el de la entrada de Windows
        (lo creó el propio firmware); si no existe, se consulta con PowerShell."""
        for e in self.entries:
            if e.hd_node and e.path.lower() == WIN_PATH.lower():
                return e.hd_node
        ps = ("Get-Partition | Where-Object { $_.GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' } | "
              "ForEach-Object { $d = Get-Disk -Number $_.DiskNumber; [pscustomobject]@{ n=$_.PartitionNumber; "
              "off=[int64]$_.Offset; size=[int64]$_.Size; guid=[string]$_.Guid; ls=[int]$d.LogicalSectorSize; "
              "sys=[bool]$_.IsSystem } } | ConvertTo-Json -Compress")
        rc, out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
        if rc != 0 or not out.strip():
            return None
        try:
            data = json.loads(out)
            parts = data if isinstance(data, list) else [data]
            parts.sort(key=lambda p: not p.get("sys"))
            p = parts[0]
            ls = int(p["ls"]) or 512
            return build_hd_node(int(p["n"]), int(p["off"]) // ls, int(p["size"]) // ls, p["guid"])
        except Exception as ex:
            self.logmsg("[AVISO] No se pudo interpretar la partición ESP: %s" % ex)
            return None

    def new_entry(self):
        NewEntryDialog(self)

    def create_entry(self, name, path, first):
        hd = self.find_esp_hd_node()
        if not hd:
            messagebox.showerror("Error", "No se pudo determinar la partición del sistema EFI (ESP).")
            return False
        existe = self.esp_file_exists(path)
        if existe is False and not messagebox.askyesno(
                "Archivo no encontrado",
                "No se encontró %s en la ESP.\n¿Crear la entrada de todos modos?" % path):
            return False
        if not self.ensure_backup():
            return False
        used = {e.num for e in self.entries} | set(self.order)
        num = 0
        while num in used:
            num += 1
        if num > 0xFFFF:
            messagebox.showerror("Error", "No hay números de entrada libres.")
            return False
        var = "Boot%04X" % num
        raw = build_load_option(LOAD_OPTION_ACTIVE, name, build_file_path_list(hd, path))
        try:
            self.nv.set(var, raw)
            self.logmsg("[NVRAM] %s creada: '%s' → %s" % (var, name, path))
            order = ([num] + list(self.order)) if first else (list(self.order) + [num])
            self.nv.set_boot_order(order)
            self.logmsg("[NVRAM] BootOrder: %s" % " ".join("%04X" % n for n in order))
        except NvramError as ex:
            self.fail("Error al crear la entrada", ex)
            try:
                self.nv.delete(var)
            except NvramError:
                pass
            self.refresh()
            return False
        ok = self.verify(var, raw)
        self.refresh(var)
        if ok:
            self.logmsg("[ÉXITO] Entrada '%s' creada (%s)." % (name, var))
            messagebox.showinfo("Listo", "Entrada creada: %s\n%s" % (name, var))
        else:
            self.logmsg("[AVISO] El firmware no devolvió la entrada tal como se escribió.")
            messagebox.showwarning("Verifica el resultado",
                                   "La entrada se escribió, pero el firmware no la devolvió idéntica. "
                                   "Revisa la tabla y la BIOS.")
        return True

    # ---- Toggle de {bootmgr} (vía bcdedit) ----
    def update_toggle(self):
        """Lee {bootmgr} (bcdedit /enum {bootmgr}) y ajusta texto y rol del botón."""
        ok, path, desc, _ = get_bootmgr()
        if not ok:
            self.btn_toggle.configure(text=TXT_BLINDAR_GENERIC)
            self.lbl_state.configure(text="{bootmgr}: no se pudo leer su estado (bcdedit).")
            return
        if is_windows_path(path) or not path:
            e = self.selected_entry()
            if e and e.path.strip() and not is_windows_path(e.path):
                nm = e.name if len(e.name) <= 30 else e.name[:29] + "…"
                self.btn_toggle.configure(text="🚀 Blindar {bootmgr} con: %s" % nm)
            else:
                self.btn_toggle.configure(text=TXT_BLINDAR_GENERIC)
            self.lbl_state.configure(
                text="{bootmgr} → %s   [%s]   Estado A: ruta predeterminada de Windows (sin blindar)"
                     % (path or "(sin ruta)", desc or "sin descripción"))
        else:
            self.btn_toggle.configure(text=TXT_LIBERAR)
            self.lbl_state.configure(
                text="{bootmgr} → %s   [%s]   Estado B: BLINDADO con gestor alternativo"
                     % (path, desc or "sin descripción"))

    def bcd(self, args):
        rc, out = run([BCDEDIT] + args)
        self.logmsg("> bcdedit " + " ".join(args) + "\n" + out.strip())
        return rc == 0, out

    def toggle_bootmgr(self):
        """Estado A (ruta de Windows): blinda {bootmgr} con la entrada/archivo .efi elegido.
        Estado B (ruta alternativa): restaura {bootmgr} a la ruta estándar de Windows.
        Nota: esto modifica el BCD de Windows; algunos firmwares no lo respetan."""
        ok, path, _, raw = get_bootmgr()
        self.logmsg("> bcdedit /enum {bootmgr}\n" + raw.strip())
        if not ok:
            self.fail("No se pudo leer {bootmgr}", raw.strip() or "error desconocido")
            self.update_toggle()
            return
        if is_windows_path(path) or not path:
            self.blindar_con_seleccion()
        else:
            self.liberar_bootmgr(path)

    def blindar_con_seleccion(self):
        e = self.selected_entry()
        if e and e.path.strip():
            target_path, target_name = norm_path(e.path), e.name
        else:
            if not messagebox.askokcancel("Elegir archivo .efi",
                                          "No hay una entrada con ruta .efi seleccionada.\n"
                                          "¿Quieres elegir un archivo .efi de la partición EFI?"):
                return
            try:
                letter = self.ensure_esp()
            except Exception as ex:
                messagebox.showerror("Error", str(ex))
                return
            p = filedialog.askopenfilename(initialdir=letter + ":\\EFI",
                                           title="Selecciona el gestor .efi (rEFInd, GRUB, etc.)",
                                           filetypes=[("Aplicación EFI", "*.efi"), ("Todos", "*.*")])
            self.release_esp()
            if not p:
                return
            target_path = norm_path(p)
            base = os.path.splitext(os.path.basename(target_path))[0]
            target_name = simpledialog.askstring("Descripción", "Descripción para {bootmgr}:",
                                                 initialvalue=base, parent=self.root)
            if not target_name or not target_name.strip():
                return
            target_name = target_name.strip()

        if is_windows_path(target_path):
            messagebox.showinfo("Ya es la ruta estándar",
                                "La ruta seleccionada es la de Windows, que ya es la actual.\n"
                                "Selecciona otra entrada (rEFInd, GRUB, etc.).")
            return
        existe = self.esp_file_exists(target_path)
        if existe is False:
            if not messagebox.askyesno(
                    "Archivo no encontrado",
                    "No se encontró %s en la partición EFI.\n\n"
                    "Si continúas, el equipo podría no arrancar correctamente.\n"
                    "¿Aplicar el cambio de todos modos?" % target_path, icon="warning"):
                self.logmsg("[CANCELADO] Blindaje: archivo no encontrado en la ESP (%s)." % target_path)
                return
        elif not messagebox.askyesno(
                "Blindar {bootmgr}",
                "Se ejecutará:\n\nbcdedit /set {bootmgr} path %s\n"
                "bcdedit /set {bootmgr} description \"%s\"\n\n¿Continuar?" % (target_path, target_name)):
            return
        self.aplicar_bootmgr(target_path, target_name, "Blindaje de {bootmgr}")

    def liberar_bootmgr(self, current_path):
        existe = self.esp_file_exists(WIN_PATH)
        extra = ""
        if existe is False:
            extra = "\n\nATENCIÓN: no se encontró %s en la ESP; el equipo podría no arrancar." % WIN_PATH
        if not messagebox.askyesno(
                "Restaurar / Liberar {bootmgr}",
                "{bootmgr} apunta actualmente a:\n%s\n\nSe ejecutará:\n\n"
                "bcdedit /set {bootmgr} path %s\n"
                "bcdedit /set {bootmgr} description \"%s\"\n\n¿Continuar?%s"
                % (current_path, WIN_PATH, WIN_DESC, extra),
                icon="warning" if existe is False else "question"):
            return
        self.aplicar_bootmgr(WIN_PATH, WIN_DESC, "Restauración de {bootmgr}")

    def aplicar_bootmgr(self, path, desc, titulo):
        self.logmsg("%s…" % titulo)
        ok1, out1 = self.bcd(["/set", "{bootmgr}", "path", path])
        if not ok1:
            self.fail(titulo, out1.strip() or "no se pudo cambiar la ruta")
            self.refresh()
            return
        ok2, out2 = self.bcd(["/set", "{bootmgr}", "description", desc])
        if ok2:
            self.logmsg("[ÉXITO] %s: {bootmgr} → %s (%s)." % (titulo, path, desc))
            messagebox.showinfo(titulo, "Operación completada.\n{bootmgr} → %s" % path)
        else:
            self.logmsg("[AVISO] %s: la ruta se cambió, pero la descripción falló: %s"
                        % (titulo, out2.strip() or "sin detalle"))
            messagebox.showwarning(titulo, "La ruta se cambió, pero no se pudo actualizar la descripción.")
        self.refresh()

    def on_close(self):
        self.release_esp()
        self.root.destroy()


def main():
    if "--make-icon" in sys.argv:
        print("Icono generado:", make_icon_file())
        return
    if os.name != "nt":
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("Sistema no compatible", "Esta herramienta solo funciona en Windows.")
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BootScribe.App")
    except Exception:
        pass
    if not is_admin():
        if elevate():
            sys.exit(0)
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("Permisos", "Se requieren permisos de Administrador para continuar.")
        sys.exit(1)
    root = tk.Tk()
    app = App(root)
    if app.ready:
        root.mainloop()


if __name__ == "__main__":
    main()