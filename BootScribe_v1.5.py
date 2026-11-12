# -*- coding: utf-8 -*-
"""
BootScribe - gestor de arranque UEFI para Windows (alternativa ligera a EasyUEFI).

Lee y escribe directamente las variables UEFI (Boot####, BootOrder, BootNext) de la NVRAM
mediante la API de Windows. "Habilitar" y "Deshabilitar" modifican el atributo real
LOAD_OPTION_ACTIVE de cada entrada, que es lo que respeta el firmware.

Interfaz: tema oscuro/claro, iconos dibujados por el propio programa (sin recursos de terceros),
tipografía IBM Plex (licencia SIL OFL 1.1) cargada desde la carpeta "fonts" junto al script,
ventana centrada y adaptada a la pantalla / DPI, y recuerda tamaño, posición y tema.

Requiere Windows en modo UEFI y permisos de Administrador (se solicitan automáticamente).

"""
import base64
import ctypes
import datetime
import json
import math
import os
import re
import struct
import subprocess
import sys
import uuid
import zlib
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

APP_NAME = "BootScribe"
CREATE_NO_WINDOW = 0x08000000

EFI_GLOBAL_GUID = "{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}"
VAR_ATTRS = 0x07                 # NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS
LOAD_OPTION_ACTIVE = 0x00000001
SCAN_MAX = 0x100                 # se sondean Boot0000..Boot00FF, además de lo que liste BootOrder
VAR_NAME_RE = re.compile(r"^(BootOrder|Boot[0-9A-F]{4})$")

WIN_PATH = r"\EFI\Microsoft\Boot\bootmgfw.efi"
WIN_DESC = "Windows Boot Manager"
TXT_BLINDAR_GENERIC = "Blindar {bootmgr} con entrada seleccionada"
TXT_LIBERAR = "Restaurar / Liberar {bootmgr}"

WIN_ERRORS = {
    1: "El equipo no arrancó en modo UEFI o Windows no puede acceder al firmware.",
    5: "Acceso denegado por el firmware.",
    87: "Parámetro no válido.",
    203: "Variable no encontrada.",
    1314: "Falta el privilegio SeSystemEnvironmentPrivilege (ejecuta como Administrador).",
}

# ----------------------------- Paletas de color (propias) -----------------------------
PALETTES = {
    "dark": dict(
        bg="#11151B", surface="#181E26", stripe="#1C232C", surface2="#222A35", border="#2E3846",
        text="#E7ECF3", muted="#8D99AB", disabled="#566173",
        accent="#4C9BFF", accent_hover="#6AAEFF", accent_press="#3A86E8", accent_soft="#1B3550",
        on_accent="#0B1420", success="#2FBF7A", success_hover="#3DD68C", success_press="#27A56A",
        on_success="#04170D", warn="#F0B24A", danger="#FF7B7B", danger_soft="#3A2227",
        select="#26405E"),
    "light": dict(
        bg="#F2F5F9", surface="#FFFFFF", stripe="#F7F9FC", surface2="#E9EEF4", border="#D3DAE4",
        text="#1A2330", muted="#657386", disabled="#A7B0BD",
        accent="#1F6FE0", accent_hover="#3B84EB", accent_press="#1859B8", accent_soft="#E2EDFC",
        on_accent="#FFFFFF", success="#1E8E4E", success_hover="#26A25D", success_press="#177A42",
        on_success="#FFFFFF", warn="#B7740A", danger="#CC3434", danger_soft="#FBE9E9",
        select="#CFE1FA"),
}


def hex_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ----------------------------- Iconos propios: rasterizador vectorial mínimo -----------------------------
class Raster:
    """Dibuja formas sobre una cuadrícula de diseño de 24 x 24 con sobremuestreo (suavizado)."""
    SS = 4

    def __init__(self, n):
        self.n, self.w = n, n * self.SS
        self.k = self.w / 24.0
        self.buf = bytearray(self.w * self.w)
        self.flip = False

    def _pt(self, p):
        x, y = p
        if self.flip:
            x = 24 - x
        return x * self.k, y * self.k

    def poly(self, pts, val=255):
        P = [self._pt(p) for p in pts]
        m = len(P)
        ys = [p[1] for p in P]
        for y in range(max(0, int(min(ys))), min(self.w - 1, int(max(ys))) + 1):
            yc = y + 0.5
            xs = []
            for i in range(m):
                xa, ya = P[i]
                xb, yb = P[(i + 1) % m]
                if (ya <= yc < yb) or (yb <= yc < ya):
                    xs.append(xa + (yc - ya) * (xb - xa) / (yb - ya))
            xs.sort()
            for j in range(0, len(xs) - 1, 2):
                a = max(0, int(round(xs[j])))
                b = min(self.w, int(round(xs[j + 1])))
                if b > a:
                    self.buf[y * self.w + a:y * self.w + b] = bytes([val]) * (b - a)

    def circle(self, c, r, val=255):
        self.poly([(c[0] + r * math.cos(i * math.pi / 20), c[1] + r * math.sin(i * math.pi / 20))
                   for i in range(40)], val)

    def path(self, pts, w, closed=False, val=255):
        seq = list(pts) + ([pts[0]] if closed else [])
        h = w / 2.0
        for a, b in zip(seq, seq[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / ln * h, dx / ln * h
            self.poly([(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny),
                       (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)], val)
        for p in seq:
            self.circle(p, h, val)

    @staticmethod
    def arc_pts(c, r, a0, a1, steps=30):
        return [(c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
                 c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps))) for i in range(steps + 1)]

    def alpha(self):
        n, S, w = self.n, self.SS, self.w
        out = bytearray(n * n)
        for y in range(n):
            rows = [self.buf[(y * S + j) * w:(y * S + j + 1) * w] for j in range(S)]
            for x in range(n):
                out[y * n + x] = sum(sum(r[x * S:(x + 1) * S]) for r in rows) // (S * S)
        return bytes(out)


def _circ_arrow(m, a0, a1, r=7.5):
    c = (12, 12)
    m.path(Raster.arc_pts(c, r, a0, a1), 2.3)
    t = math.radians(a1)
    P = (c[0] + r * math.cos(t), c[1] + r * math.sin(t))
    T = (-math.sin(t), math.cos(t))
    R = (math.cos(t), math.sin(t))
    m.poly([(P[0] + T[0] * 4.6, P[1] + T[1] * 4.6),
            (P[0] + R[0] * 4.0 - T[0] * 0.8, P[1] + R[1] * 4.0 - T[1] * 0.8),
            (P[0] - R[0] * 4.0 - T[0] * 0.8, P[1] - R[1] * 4.0 - T[1] * 0.8)])


def _star(m):
    pts = []
    for i in range(10):
        r = 9.6 if i % 2 == 0 else 4.2
        a = math.radians(-90 + i * 36)
        pts.append((12 + r * math.cos(a), 12.8 + r * math.sin(a)))
    m.poly(pts)


def _draw_refresh(m): _circ_arrow(m, -30, 250)


def _draw_history(m):
    m.flip = True
    _circ_arrow(m, -30, 250)
    m.flip = False
    m.path([(12, 8), (12, 12), (15, 13.8)], 1.9)


def _draw_up(m): m.path([(6, 15), (12, 9), (18, 15)], 2.6)


def _draw_down(m): m.path([(6, 9), (12, 15), (18, 9)], 2.6)


def _draw_plus(m):
    m.path([(12, 5), (12, 19)], 2.6)
    m.path([(5, 12), (19, 12)], 2.6)


def _draw_pencil(m):
    m.path([(8.5, 15.5), (17, 7)], 4.6)
    m.poly([(4, 20), (5.6, 14.2), (9.8, 18.4)])


def _draw_trash(m):
    m.path([(5, 7), (19, 7)], 2.2)
    m.path([(9, 7), (9, 4.8), (15, 4.8), (15, 7)], 2.0)
    m.path([(6.8, 9.5), (17.2, 9.5), (16.3, 19.8), (7.7, 19.8)], 2.0, closed=True)


def _draw_check(m):
    m.path(Raster.arc_pts((12, 12), 9, 0, 360, 48), 2.1)
    m.path([(7.6, 12.6), (10.8, 15.8), (16.6, 9.2)], 2.4)


def _draw_ban(m):
    m.path(Raster.arc_pts((12, 12), 9, 0, 360, 48), 2.1)
    m.path([(6.2, 6.2), (17.8, 17.8)], 2.3)


def _draw_next(m):
    m.poly([(6, 5), (6, 19), (15.5, 12)])
    m.path([(18.5, 5.2), (18.5, 18.8)], 2.6)


def _draw_power(m):
    m.path(Raster.arc_pts((12, 13), 7.6, -50, 230), 2.3)
    m.path([(12, 3.5), (12, 12.5)], 2.5)


def _draw_download(m):
    m.path([(12, 4), (12, 14.2)], 2.5)
    m.path([(7.6, 10.2), (12, 14.6), (16.4, 10.2)], 2.5)
    m.path([(5, 15.5), (5, 19.5), (19, 19.5), (19, 15.5)], 2.2)


def _draw_shield(m):
    m.path([(12, 3), (19, 6), (19, 12), (12, 21), (5, 12), (5, 6)], 2.1, closed=True)
    m.path([(8.8, 12), (11.2, 14.4), (15.4, 9.6)], 2.1)


def _draw_undo(m):
    m.path([(9, 6.5), (4.5, 11), (9, 15.5)], 2.4)
    m.path([(5, 11), (14, 11)], 2.4)
    m.path(Raster.arc_pts((14, 15.5), 4.5, -90, 90), 2.4)


def _draw_sun(m):
    m.circle((12, 12), 4.4)
    for i in range(8):
        a = math.radians(i * 45)
        m.path([(12 + 7.3 * math.cos(a), 12 + 7.3 * math.sin(a)),
                (12 + 9.6 * math.cos(a), 12 + 9.6 * math.sin(a))], 2.0)


def _draw_moon(m):
    m.circle((12, 12), 8.4)
    m.circle((16.8, 8.6), 7.2, val=0)


def _draw_folder(m):
    m.poly([(3, 6.5), (9.5, 6.5), (11.5, 9), (21, 9), (21, 19.5), (3, 19.5)])


def _draw_copy(m):
    m.path([(9, 9), (19, 9), (19, 20), (9, 20)], 2.0, closed=True)
    m.path([(5.2, 15), (5.2, 4.2), (15, 4.2)], 2.0)


def _draw_dot(m): m.circle((12, 12), 6.2)


def _draw_ring(m): m.path(Raster.arc_pts((12, 12), 5.4, 0, 360, 36), 2.2)


ICON_DRAWERS = {
    "refresh": _draw_refresh, "history": _draw_history, "up": _draw_up, "down": _draw_down,
    "star": _star, "plus": _draw_plus, "pencil": _draw_pencil, "trash": _draw_trash,
    "check": _draw_check, "ban": _draw_ban, "next": _draw_next, "power": _draw_power,
    "download": _draw_download, "shield": _draw_shield, "undo": _draw_undo, "sun": _draw_sun,
    "moon": _draw_moon, "folder": _draw_folder, "copy": _draw_copy, "dot": _draw_dot,
    "ring": _draw_ring,
}
_ALPHA_CACHE = {}


def icon_alpha(name, n):
    key = (name, n)
    if key not in _ALPHA_CACHE:
        m = Raster(n)
        ICON_DRAWERS[name](m)
        _ALPHA_CACHE[key] = m.alpha()
    return _ALPHA_CACHE[key]


def icon_png(name, color, n):
    r, g, b = hex_rgb(color)
    rgba = bytearray()
    for a in icon_alpha(name, n):
        rgba += bytes((r, g, b, a))
    return make_png(n, n, rgba)


# ----------------------------- Icono de la aplicación (monitor) -----------------------------
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


# ----------------------------- Pantalla, tipografía, ajustes -----------------------------
def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # por monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


FONT_FILES = ("IBMPlexSans-Regular.ttf", "IBMPlexSans-Bold.ttf", "IBMPlexMono-Regular.ttf")
SANS_CANDIDATES = ("IBM Plex Sans", "Noto Sans", "Inter", "Roboto", "Open Sans", "Segoe UI",
                   "Helvetica", "Arial")
MONO_CANDIDATES = ("IBM Plex Mono", "Noto Sans Mono", "JetBrains Mono", "Roboto Mono",
                   "Consolas", "Courier New", "Courier")


def load_private_fonts():
    """Carga las fuentes IBM Plex (SIL OFL 1.1) de la carpeta 'fonts' solo para este proceso."""
    n = 0
    if os.name != "nt":
        return 0
    for f in FONT_FILES:
        p = resource_path("fonts", f)
        if os.path.exists(p):
            try:
                if ctypes.windll.gdi32.AddFontResourceExW(ctypes.c_wchar_p(p), 0x10, 0):
                    n += 1
            except Exception:
                pass
    return n


def pick_family(root, candidates, default):
    fams = {f.lower(): f for f in tkfont.families(root)}
    for c in candidates:
        if c.lower() in fams:
            return fams[c.lower()]
    return default


def app_data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def load_settings():
    try:
        with open(os.path.join(app_data_dir(), "settings.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(os.path.join(app_data_dir(), "settings.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except Exception:
        pass


def system_theme():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except Exception:
        return "dark"


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
    kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        p = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, **kw)
    except (OSError, ValueError) as e:
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
    d = os.path.join(app_data_dir(), "backups")
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


# ----------------------------- Componentes de interfaz -----------------------------
class Tooltip:
    """Globo de ayuda que aparece al dejar el cursor sobre un widget."""

    def __init__(self, widget, text, app):
        self.w, self.text, self.app = widget, text, app
        self.tip, self.job = None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _e):
        self._cancel()
        self.job = self.w.after(600, self._show)

    def _cancel(self):
        if self.job:
            try:
                self.w.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def _leave(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def _show(self):
        self.job = None
        if self.tip or not self.text or not self.w.winfo_exists():
            return
        c, sc = self.app.c, self.app.sc
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (self.w.winfo_rootx() + sc(6),
                                         self.w.winfo_rooty() + self.w.winfo_height() + sc(6)))
        frame = tk.Frame(self.tip, bg=c["border"], padx=1, pady=1)
        tk.Label(frame, text=self.text, bg=c["surface2"], fg=c["text"], font=self.app.f_small,
                 justify="left", padx=sc(9), pady=sc(6), wraplength=sc(340)).pack()
        frame.pack()


class Dialog(tk.Toplevel):
    """Ventana modal con el mismo tema que la ventana principal, centrada sobre ella."""

    def __init__(self, app, title):
        super().__init__(app.root)
        self.app = app
        self.withdraw()
        self.title(title)
        self.configure(bg=app.c["bg"])
        self.resizable(False, False)
        self.transient(app.root)
        set_window_icon(self)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _e: self.cancel())

    def show(self):
        self.update_idletasks()
        r = self.app.root
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = r.winfo_rootx() + (r.winfo_width() - w) // 2
        y = r.winfo_rooty() + (r.winfo_height() - h) // 3
        x = max(0, min(x, self.winfo_screenwidth() - w))
        y = max(0, min(y, self.winfo_screenheight() - h))
        self.geometry("+%d+%d" % (x, y))
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()

    def cancel(self):
        self.destroy()


class TextPrompt(Dialog):
    def __init__(self, app, title, label, initial=""):
        super().__init__(app, title)
        self.result = None
        sc = app.sc
        f = ttk.Frame(self, padding=(sc(22), sc(20), sc(22), sc(18)))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=label, style="Dlg.TLabel").pack(anchor="w")
        self.var = tk.StringVar(value=initial)
        ent = ttk.Entry(f, textvariable=self.var, width=46)
        ent.pack(fill="x", pady=(sc(8), sc(16)))
        row = ttk.Frame(f)
        row.pack(fill="x")
        app.make_button(row, "Aceptar", "check", self.ok, "Primary.TButton").pack(side="right")
        app.make_button(row, "Cancelar", None, self.cancel, "Tool.TButton").pack(side="right", padx=(0, sc(8)))
        ent.bind("<Return>", lambda _e: self.ok())
        ent.focus_set()
        ent.select_range(0, "end")

    def ok(self):
        self.result = self.var.get()
        self.destroy()


class NewEntryDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Nueva entrada de arranque UEFI")
        sc = app.sc
        self.name_var = tk.StringVar()
        self.path_var = tk.StringVar(value=r"\EFI\Boot\bootx64.efi")
        self.first_var = tk.BooleanVar(value=False)

        f = ttk.Frame(self, padding=(sc(22), sc(20), sc(22), sc(18)))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Nueva entrada de arranque", style="Dlg.Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, sc(2)))
        ttk.Label(f, text="Crea una entrada UEFI que apunta a un archivo .efi de la partición EFI (ESP).",
                  style="Dlg.Muted.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, sc(14)))
        ttk.Label(f, text="Nombre", style="Dlg.TLabel").grid(row=2, column=0, sticky="w", pady=sc(5))
        ttk.Entry(f, textvariable=self.name_var, width=46).grid(row=2, column=1, columnspan=2, sticky="ew",
                                                              padx=(sc(12), 0))
        ttk.Label(f, text="Archivo .efi", style="Dlg.TLabel").grid(row=3, column=0, sticky="w", pady=sc(5))
        ttk.Entry(f, textvariable=self.path_var, width=34).grid(row=3, column=1, sticky="ew", padx=(sc(12), 0))
        app.make_button(f, "Examinar ESP…", "folder", self.browse, "Tool.TButton").grid(
            row=3, column=2, padx=(sc(8), 0))
        ttk.Checkbutton(f, text="Colocar al inicio de la lista (hacerla predeterminada)",
                        variable=self.first_var).grid(row=4, column=1, columnspan=2, sticky="w", pady=(sc(8), 0),
                                                      padx=(sc(12), 0))
        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, columnspan=3, pady=(sc(18), 0), sticky="e")
        app.make_button(bf, "Crear entrada", "plus", self.ok, "Primary.TButton").pack(side="right")
        app.make_button(bf, "Cancelar", None, self.cancel, "Tool.TButton").pack(side="right", padx=(0, sc(8)))
        f.columnconfigure(1, weight=1)
        self.show()

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
            self.cancel()

    def cancel(self):
        self.app.release_esp()
        self.destroy()


# ----------------------------- Aplicación principal -----------------------------
STYLE_KIND = {"Tool.TButton": "text", "Primary.TButton": "on_accent", "Success.TButton": "on_success",
              "Danger.TButton": "danger", "Ghost.TButton": "text"}
ENTRY_BUTTONS = ("hab", "deshab", "up", "down", "default", "rename", "delete", "once")


class App:
    def __init__(self, root, nvram=None):
        self.root = root
        self.nv = nvram or Nvram()
        self.entries, self.emap, self.order = [], {}, []
        self.boot_current = self.boot_next = None
        self.dflt = None
        self.bm = (False, "", "")
        self.esp_letter, self.esp_mounted_by_us = None, False
        self._refreshing = False
        self._backed_up = False
        self.ready = False
        self._toast, self._toast_job = None, None
        self.btn, self.icon_buttons, self.borders = {}, {}, []
        self._imgs = {}

        self.settings = load_settings()
        self.theme = self.settings.get("theme") if self.settings.get("theme") in PALETTES else system_theme()
        self.c = PALETTES[self.theme]
        self.k = max(1.0, root.winfo_fpixels("1i") / 96.0)

        sans = pick_family(root, SANS_CANDIDATES, "TkDefaultFont")
        mono = pick_family(root, MONO_CANDIDATES, "TkFixedFont")
        self.f_body = tkfont.Font(root=root, family=sans, size=10)
        self.f_bold = tkfont.Font(root=root, family=sans, size=10, weight="bold")
        self.f_small = tkfont.Font(root=root, family=sans, size=9)
        self.f_cap = tkfont.Font(root=root, family=sans, size=8, weight="bold")
        self.f_title = tkfont.Font(root=root, family=sans, size=15, weight="bold")
        self.f_mono = tkfont.Font(root=root, family=mono, size=9)
        for nm in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont", "TkTooltipFont"):
            try:
                tkfont.nametofont(nm, root=root).configure(family=sans, size=10)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont("TkFixedFont", root=root).configure(family=mono, size=9)
        except tk.TclError:
            pass

        root.title(APP_NAME)
        set_window_icon(root)
        self.style = ttk.Style(root)
        self.build_styles()
        root.configure(bg=self.c["bg"])
        self.build_ui()
        self.place_window()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            self.nv.open()
        except NvramError as e:
            messagebox.showerror(APP_NAME, str(e))
            root.destroy()
            return
        self.ready = True
        self.refresh()

    # ---- escala, iconos, botones ----
    def sc(self, v):
        return int(round(v * self.k))

    def icon(self, name, color, size=None):
        n = size or self.sc(18)
        key = (name, color, n)
        if key not in self._imgs:
            self._imgs[key] = tk.PhotoImage(data=base64.b64encode(icon_png(name, color, n)))
        return self._imgs[key]

    def set_button_icon(self, btn, name, kind):
        c = self.c
        if not name:
            return
        self.icon_buttons[btn] = (name, kind)
        btn.configure(image=(self.icon(name, c[kind]), "disabled", self.icon(name, c["disabled"])),
                      compound="left")

    def make_button(self, parent, text, icon, cmd, style="Tool.TButton", tip="", width=None):
        b = ttk.Button(parent, text=(" " + text) if (text and icon) else text, command=cmd, style=style,
                       takefocus=False)
        if width:
            b.configure(width=width)
        elif not text:
            b.configure(width=0)
        if icon:
            self.set_button_icon(b, icon, STYLE_KIND.get(style, "text"))
        if tip:
            Tooltip(b, tip, self)
        return b

    # ---- estilos ----
    def build_styles(self):
        c, s, sc = self.c, self.style, self.sc
        s.theme_use("clam")
        s.configure(".", background=c["bg"], foreground=c["text"], font=self.f_body, bordercolor=c["border"],
                    lightcolor=c["bg"], darkcolor=c["bg"], troughcolor=c["surface2"], focuscolor=c["accent"],
                    fieldbackground=c["surface"], insertcolor=c["text"])
        s.configure("TFrame", background=c["bg"])
        s.configure("Header.TFrame", background=c["surface"])
        s.configure("Card.TFrame", background=c["surface"])
        s.configure("Status.TFrame", background=c["surface"])
        s.configure("TLabel", background=c["bg"], foreground=c["text"])
        s.configure("Dlg.TLabel", background=c["bg"], foreground=c["text"])
        s.configure("Dlg.Title.TLabel", background=c["bg"], foreground=c["text"], font=self.f_title)
        s.configure("Dlg.Muted.TLabel", background=c["bg"], foreground=c["muted"], font=self.f_small)
        s.configure("H.TLabel", background=c["surface"], foreground=c["text"])
        s.configure("H.Title.TLabel", background=c["surface"], foreground=c["text"], font=self.f_title)
        s.configure("H.Sub.TLabel", background=c["surface"], foreground=c["muted"], font=self.f_small)
        s.configure("H.Chip.TLabel", background=c["surface"], foreground=c["muted"], font=self.f_small)
        s.configure("Caption.TLabel", background=c["bg"], foreground=c["muted"], font=self.f_cap)
        s.configure("Card.TLabel", background=c["surface"], foreground=c["text"])
        s.configure("Card.Cap.TLabel", background=c["surface"], foreground=c["muted"], font=self.f_cap)
        s.configure("Card.Name.TLabel", background=c["surface"], foreground=c["text"], font=self.f_bold)
        s.configure("Status.TLabel", background=c["surface"], foreground=c["muted"], font=self.f_small)

        def bstyle(name, bg, fg, hover, press, border, dis_bg):
            s.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                        focuscolor=bg, relief="flat", borderwidth=1, padding=(sc(12), sc(7)), anchor="center",
                        font=self.f_body)
            s.map(name, background=[("disabled", dis_bg), ("pressed", press), ("active", hover)],
                  foreground=[("disabled", c["disabled"])],
                  bordercolor=[("disabled", c["border"] if border != bg else dis_bg)],
                  lightcolor=[("disabled", dis_bg), ("pressed", press), ("active", hover)],
                  darkcolor=[("disabled", dis_bg), ("pressed", press), ("active", hover)])

        bstyle("Tool.TButton", c["surface"], c["text"], c["surface2"], c["border"], c["border"], c["bg"])
        bstyle("Primary.TButton", c["accent"], c["on_accent"], c["accent_hover"], c["accent_press"],
               c["accent"], c["surface2"])
        bstyle("Success.TButton", c["success"], c["on_success"], c["success_hover"], c["success_press"],
               c["success"], c["surface2"])
        bstyle("Danger.TButton", c["surface"], c["danger"], c["danger_soft"], c["danger_soft"], c["border"],
               c["bg"])
        bstyle("Ghost.TButton", c["surface"], c["text"], c["surface2"], c["border"], c["surface"], c["surface"])

        s.configure("TEntry", fieldbackground=c["surface"], foreground=c["text"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"], padding=sc(7), insertcolor=c["text"])
        s.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
              darkcolor=[("focus", c["accent"])])
        s.configure("TCheckbutton", background=c["bg"], foreground=c["text"], focuscolor=c["bg"],
                    indicatorbackground=c["surface"], indicatorforeground=c["accent"])
        s.map("TCheckbutton", background=[("active", c["bg"])],
              indicatorbackground=[("selected", c["accent"]), ("!selected", c["surface"])],
              indicatorforeground=[("selected", c["on_accent"])])

        s.configure("BS.Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["text"],
                    rowheight=sc(32), borderwidth=0, relief="flat", font=self.f_body)
        s.layout("BS.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.map("BS.Treeview", background=[("selected", c["select"])], foreground=[("selected", c["text"])])
        s.configure("BS.Treeview.Heading", background=c["surface2"], foreground=c["muted"], relief="flat",
                    borderwidth=0, padding=(sc(10), sc(8)), font=self.f_cap, bordercolor=c["surface2"],
                    lightcolor=c["surface2"], darkcolor=c["surface2"])
        s.map("BS.Treeview.Heading", background=[("active", c["border"])])
        s.configure("Vertical.TScrollbar", background=c["border"], troughcolor=c["surface"],
                    bordercolor=c["surface"], arrowcolor=c["muted"], lightcolor=c["border"],
                    darkcolor=c["border"], gripcount=0, arrowsize=sc(13))
        s.map("Vertical.TScrollbar", background=[("active", c["muted"])])
        s.configure("TPanedwindow", background=c["bg"])
        try:
            s.configure("Sash", sashthickness=sc(8), gripcount=0)
        except tk.TclError:
            pass

    # ---- construcción de la interfaz ----
    def border_frame(self, parent, **pack):
        fr = tk.Frame(parent, bg=self.c["border"], padx=1, pady=1)
        self.borders.append(fr)
        return fr

    def group(self, parent, caption, specs):
        sc = self.sc
        if parent.winfo_children():
            sep = tk.Frame(parent, width=1, bg=self.c["border"])
            self.borders.append(sep)
            sep.pack(side="left", fill="y", padx=sc(14), pady=sc(2))
        g = ttk.Frame(parent)
        g.pack(side="left")
        ttk.Label(g, text=caption, style="Caption.TLabel").pack(anchor="w", pady=(0, sc(4)))
        row = ttk.Frame(g)
        row.pack(anchor="w")
        for key, text, icon, cmd, style, tip, *w in specs:
            b = self.make_button(row, text, icon, cmd, style, tip, width=(w[0] if w else None))
            b.pack(side="left", padx=(0, sc(6)))
            self.btn[key] = b

    def build_ui(self):
        c, sc, root = self.c, self.sc, self.root

        # Encabezado
        header = ttk.Frame(root, style="Header.TFrame", padding=(sc(22), sc(10), sc(22), sc(10)))
        header.pack(fill="x")
        self.logo = tk.PhotoImage(data=base64.b64encode(make_png(sc(38), sc(38), monitor_rgba(sc(38)))))
        ttk.Label(header, image=self.logo, style="H.TLabel").pack(side="left")
        tf = ttk.Frame(header, style="Header.TFrame")
        tf.pack(side="left", padx=(sc(14), 0))
        ttk.Label(tf, text="BootScribe", style="H.Title.TLabel").pack(anchor="w")
        ttk.Label(tf, text="Administrador de arranque UEFI", style="H.Sub.TLabel").pack(anchor="w")
        self.btn_theme = self.make_button(header, "", "sun" if self.theme == "dark" else "moon",
                                          self.toggle_theme, "Ghost.TButton", "Cambiar entre tema oscuro y claro")
        self.btn_theme.pack(side="right")
        self.lbl_info = ttk.Label(header, text="", style="H.Chip.TLabel")
        self.lbl_info.pack(side="right", padx=(0, sc(16)))
        line = tk.Frame(root, height=1, bg=c["border"])
        self.borders.append(line)
        line.pack(fill="x")

        # Barra de herramientas agrupada
        tb = ttk.Frame(root, padding=(sc(22), sc(10), sc(22), sc(4)))
        tb.pack(fill="x")
        rowA = ttk.Frame(tb)
        rowA.pack(fill="x")
        self.group(rowA, "ESTADO", [
            ("hab", "Habilitar entrada", "check", self.habilitar_entrada, "Success.TButton",
             "Activa la entrada (atributo ACTIVE del firmware). No cambia su prioridad."),
            ("deshab", "Deshabilitar entrada", "ban", self.deshabilitar_entrada, "Tool.TButton",
             "Desactiva la entrada sin borrarla; conserva su posición en el orden.")])
        self.group(rowA, "PRIORIDAD", [
            ("up", "Subir", "up", lambda: self.move(-1), "Tool.TButton", "Sube la entrada en el orden (Ctrl+↑)."),
            ("down", "Bajar", "down", lambda: self.move(1), "Tool.TButton", "Baja la entrada en el orden (Ctrl+↓)."),
            ("default", "Establecer predeterminada", "star", self.set_default, "Tool.TButton",
             "La coloca en primer lugar: será la que el firmware intente primero.")])
        self.group(rowA, "GESTIÓN", [
            ("new", "Nueva entrada", "plus", self.new_entry, "Tool.TButton",
             "Crea una entrada que apunta a un archivo .efi de la ESP."),
            ("rename", "Renombrar", "pencil", self.rename_entry, "Tool.TButton", "Cambia el nombre de la entrada."),
            ("delete", "Eliminar", "trash", self.delete_entry, "Danger.TButton",
             "Elimina la entrada de la NVRAM (Supr). Se guarda una copia antes.")])
        rowB = ttk.Frame(tb)
        rowB.pack(fill="x", pady=(sc(8), 0))
        self.group(rowB, "ARRANQUE", [
            ("once", "Arrancar una vez desde esta entrada", "next", self.boot_once, "Tool.TButton",
             "Usa esta entrada solo en el próximo arranque (BootNext) y reinicia."),
            ("fw", "Reiniciar a UEFI", "power", self.reboot_fw, "Tool.TButton",
             "Reinicia y abre la configuración UEFI / BIOS.")])
        self.group(rowB, "RESPALDO", [
            ("backup", "Copia de seguridad", "download", self.manual_backup, "Tool.TButton",
             "Guarda ahora un archivo con todas las entradas y el orden de arranque."),
            ("restore", "Restaurar copia…", "history", self.restore_backup, "Tool.TButton",
             "Restaura entradas y orden desde una copia de BootScribe.")])
        self.group(rowB, "AVANZADO", [
            ("bm", TXT_BLINDAR_GENERIC, "shield", self.toggle_bootmgr, "Tool.TButton",
             "Redirige el {bootmgr} de Windows (BCD) a otro gestor .efi o lo restaura. "
             "Algunos firmwares no lo respetan.", 38)])

        # Barra de estado
        sline = tk.Frame(root, height=1, bg=c["border"])
        self.borders.append(sline)
        sline.pack(fill="x", side="bottom")
        status = ttk.Frame(root, style="Status.TFrame", padding=(sc(22), sc(6)))
        status.pack(fill="x", side="bottom")
        self.lbl_status = ttk.Label(status, text="Listo.", style="Status.TLabel")
        self.lbl_status.pack(side="left")
        ttk.Label(status, text="UEFI · Administrador", style="Status.TLabel").pack(side="right")

        # Zona principal: tabla + detalle / registro (redimensionables)
        body = ttk.Frame(root, padding=(sc(22), sc(6), sc(22), 0))
        body.pack(fill="both", expand=True)
        self.paned = ttk.PanedWindow(body, orient="vertical")
        self.paned.pack(fill="both", expand=True)

        top = ttk.Frame(self.paned)
        self.top = top
        self.paned.add(top, weight=4)
        tborder = self.border_frame(top)
        tborder.pack(fill="both", expand=True)
        card = ttk.Frame(tborder, style="Card.TFrame")
        card.pack(fill="both", expand=True)
        cols = ("pos", "name", "id", "path", "device", "state")
        self.tree = ttk.Treeview(card, columns=cols, show="tree headings", selectmode="browse",
                                 style="BS.Treeview", height=5)
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=sc(48), minwidth=sc(48), stretch=False, anchor="center")
        for col, title, w, stretch in (("pos", "ORDEN", 64, False), ("name", "NOMBRE", 210, True),
                                       ("id", "ID", 92, False), ("path", "RUTA EFI", 270, True),
                                       ("device", "DISPOSITIVO", 170, True), ("state", "ESTADO", 220, True)):
            self.tree.heading(col, text=title, anchor="w")
            self.tree.column(col, width=sc(w), minwidth=sc(w // 2), anchor="w", stretch=stretch)
        sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        dborder = self.border_frame(top)
        dborder.pack(fill="x", pady=(sc(8), sc(6)))
        det = ttk.Frame(dborder, style="Card.TFrame", padding=(sc(16), sc(8)))
        det.pack(fill="x")
        self.det = {}
        for i, (key, cap, w) in enumerate((("name", "ENTRADA SELECCIONADA", 0), ("id", "ID", 0),
                                           ("path", "RUTA EFI", 0), ("device", "DISPOSITIVO", 0),
                                           ("state", "ESTADO", 0))):
            cell = ttk.Frame(det, style="Card.TFrame")
            cell.grid(row=0, column=i, sticky="w", padx=(0, sc(26)))
            ttk.Label(cell, text=cap, style="Card.Cap.TLabel").pack(anchor="w")
            lbl = ttk.Label(cell, text="–", style="Card.Name.TLabel" if key == "name" else "Card.TLabel")
            lbl.pack(anchor="w", pady=(sc(2), 0))
            self.det[key] = lbl

        bot = ttk.Frame(self.paned)
        self.paned.add(bot, weight=1)
        lh = ttk.Frame(bot)
        lh.pack(fill="x", pady=(0, sc(5)))
        ttk.Label(lh, text="REGISTRO", style="Caption.TLabel").pack(side="left")
        self.make_button(lh, "Copiar", "copy", self.copy_log, "Ghost.TButton",
                         "Copia todo el registro al portapapeles.").pack(side="right")
        self.make_button(lh, "Limpiar", "trash", self.clear_log, "Ghost.TButton",
                         "Borra el contenido del registro.").pack(side="right", padx=(0, sc(6)))
        lborder = self.border_frame(bot)
        lborder.pack(fill="both", expand=True)
        lcard = ttk.Frame(lborder, style="Card.TFrame")
        lcard.pack(fill="both", expand=True)
        self.log = tk.Text(lcard, height=5, state="disabled", font=self.f_mono, relief="flat", bd=0, wrap="word",
                           padx=sc(10), pady=sc(8), highlightthickness=0)
        lsb = ttk.Scrollbar(lcard, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        # Menú contextual y atajos
        self.menu = tk.Menu(root, tearoff=0, bd=0, relief="flat", font=self.f_body)
        for item in (("Habilitar entrada", self.habilitar_entrada),
                     ("Deshabilitar entrada", self.deshabilitar_entrada), None,
                     ("Subir", lambda: self.move(-1)), ("Bajar", lambda: self.move(1)),
                     ("Establecer predeterminada", self.set_default), None,
                     ("Arrancar una vez desde esta entrada", self.boot_once),
                     ("Renombrar", self.rename_entry), ("Eliminar", self.delete_entry)):
            if item is None:
                self.menu.add_separator()
            else:
                self.menu.add_command(label=item[0], command=item[1])
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("<Delete>", lambda _e: self.delete_entry())
        self.tree.bind("<Control-Up>", lambda _e: self.move(-1))
        self.tree.bind("<Control-Down>", lambda _e: self.move(1))
        root.bind("<F5>", lambda _e: self.refresh())
        self.apply_plain_colors()
        root.after(60, self.init_sash)

    def init_sash(self):
        try:
            h = self.paned.winfo_height()
            want = max(self.top.winfo_reqheight() + self.sc(4), int(h * 0.6))
            self.paned.sashpos(0, min(want, h - self.sc(92)))
        except tk.TclError:
            pass

    def apply_plain_colors(self):
        c = self.c
        self.root.configure(bg=c["bg"])
        for fr in self.borders:
            if fr.winfo_exists():
                fr.configure(bg=c["border"])
        self.log.configure(bg=c["surface"], fg=c["text"], insertbackground=c["text"],
                           selectbackground=c["select"], selectforeground=c["text"])
        self.log.tag_configure("ts", foreground=c["disabled"])
        self.log.tag_configure("cmd", foreground=c["muted"])
        self.log.tag_configure("ok", foreground=c["success"])
        self.log.tag_configure("err", foreground=c["danger"])
        self.log.tag_configure("warn", foreground=c["warn"])
        self.log.tag_configure("nv", foreground=c["accent"])
        self.log.tag_configure("info", foreground=c["text"])
        self.menu.configure(bg=c["surface"], fg=c["text"], activebackground=c["accent_soft"],
                            activeforeground=c["text"], disabledforeground=c["disabled"])

    # ---- ventana ----
    def place_window(self):
        r, sc = self.root, self.sc
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(sc(1260), int(sw * 0.94)), min(sc(800), int(sh * 0.92))
        x, y = (sw - w) // 2, max(0, (sh - h) // 2 - sc(16))
        g = self.settings.get("geometry")
        if isinstance(g, list) and len(g) == 4 and all(isinstance(v, int) for v in g):
            gw, gh, gx, gy = g
            if sc(900) <= gw <= sw and sc(560) <= gh <= sh and -20 <= gx <= sw - 120 and 0 <= gy <= sh - 120:
                w, h, x, y = gw, gh, gx, gy
        r.minsize(min(sc(1120), int(sw * 0.94)), min(sc(600), int(sh * 0.90)))
        r.geometry("%dx%d+%d+%d" % (w, h, x, y))
        if self.settings.get("zoomed"):
            try:
                r.state("zoomed")
            except tk.TclError:
                pass

    def on_close(self):
        try:
            zoomed = self.root.state() == "zoomed"
            if not zoomed:
                self.settings["geometry"] = [self.root.winfo_width(), self.root.winfo_height(),
                                             self.root.winfo_x(), self.root.winfo_y()]
            self.settings["zoomed"] = zoomed
            self.settings["theme"] = self.theme
            save_settings(self.settings)
        except Exception:
            pass
        self.release_esp()
        self.root.destroy()

    # ---- tema ----
    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.c = PALETTES[self.theme]
        self.build_styles()
        self.apply_plain_colors()
        for b in list(self.icon_buttons):
            if not b.winfo_exists():
                del self.icon_buttons[b]
        self.icon_buttons[self.btn_theme] = ("sun" if self.theme == "dark" else "moon", "text")
        for b, (name, kind) in list(self.icon_buttons.items()):
            self.set_button_icon(b, name, kind)
        self.render()
        self.settings["theme"] = self.theme
        save_settings(self.settings)

    # ---- avisos, registro, estado ----
    def toast(self, text, kind="success", ms=3800):
        c, sc = self.c, self.sc
        colors = {"success": (c["success"], c["on_success"]), "info": (c["accent"], c["on_accent"]),
                  "warn": (c["warn"], "#1B1200"), "error": (c["danger"], "#FFFFFF")}
        bg, fg = colors.get(kind, colors["info"])
        if self._toast is not None:
            try:
                self._toast.destroy()
                if self._toast_job:
                    self.root.after_cancel(self._toast_job)
            except Exception:
                pass
        f = tk.Frame(self.root, bg=bg)
        tk.Label(f, text=text, bg=bg, fg=fg, font=self.f_bold, padx=sc(18), pady=sc(11),
                 wraplength=sc(520), justify="left").pack()
        f.place(relx=1.0, rely=1.0, x=-sc(24), y=-sc(48), anchor="se")
        f.lift()
        self._toast = f
        self._toast_job = self.root.after(ms, self._hide_toast)
        self.set_status(text, kind)

    def _hide_toast(self):
        if self._toast is not None:
            try:
                self._toast.destroy()
            except Exception:
                pass
        self._toast = self._toast_job = None

    def set_status(self, text, kind="info"):
        c = self.c
        col = {"success": c["success"], "error": c["danger"], "warn": c["warn"]}.get(kind, c["muted"])
        self.lbl_status.configure(text=text.replace("\n", " ")[:160], foreground=col)

    def logmsg(self, text):
        text = text.rstrip()
        first = text.split("\n", 1)[0]
        tag, status = "info", None
        for pref, t, st in (("[ÉXITO]", "ok", "success"), ("[ERROR]", "err", "error"),
                            ("[AVISO]", "warn", "warn"), ("[CANCELADO]", "warn", "warn"),
                            ("[NVRAM]", "nv", None), ("[INFO]", "info", None)):
            if first.startswith(pref):
                tag, status = t, st
                break
        else:
            if first.startswith(">"):
                tag = "cmd"
        self.log.configure(state="normal")
        self.log.insert("end", datetime.datetime.now().strftime("%H:%M:%S  "), "ts")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        if status:
            self.set_status(first, status)

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end").strip())
        self.toast("Registro copiado al portapapeles.", "info", 2200)

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def fail(self, titulo, ex):
        self.logmsg("[ERROR] %s: %s" % (titulo, ex))
        messagebox.showerror(titulo, str(ex))

    def ask_text(self, title, label, initial=""):
        d = TextPrompt(self, title, label, initial)
        d.show()
        self.root.wait_window(d)
        return d.result

    # ---- selección y botones ----
    def selected_entry(self):
        sel = self.tree.selection()
        return self.emap.get(sel[0]) if sel else None

    def need_entry(self):
        e = self.selected_entry()
        if not e:
            self.toast("Selecciona una entrada de la lista.", "info", 2400)
        return e

    def on_select(self, _event=None):
        if not self._refreshing:
            self.after_select()

    def on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.root.update_idletasks()
            self.menu.tk_popup(event.x_root, event.y_root)
            self.menu.grab_release()

    def after_select(self):
        self.show_details()
        self.update_buttons()
        self.update_toggle()

    def state_text(self, e):
        if not e.active:
            s = "Deshabilitada"
        elif e.num == self.dflt:
            s = "Predeterminada"
        else:
            s = "Habilitada"
        if e.num == self.boot_current:
            s += " · en uso"
        if e.num == self.boot_next:
            s += " · próximo arranque"
        if not e.ok:
            s += " · ilegible"
        return s

    def show_details(self):
        e = self.selected_entry()
        if not e:
            vals = {"name": "Ninguna entrada seleccionada", "id": "–", "path": "–", "device": "–", "state": "–"}
        else:
            vals = {"name": e.name, "id": e.id, "path": e.path or "–", "device": e.device or "–",
                    "state": self.state_text(e)}
        for k, v in vals.items():
            self.det[k].configure(text=v)

    def update_buttons(self):
        e = self.selected_entry()

        def st(k, on):
            self.btn[k].state(["!disabled"] if on else ["disabled"])

        if not e:
            for k in ENTRY_BUTTONS:
                st(k, False)
            return
        inorder = e.num in self.order
        idx = self.order.index(e.num) if inorder else -1
        st("hab", (not e.active) or not inorder)
        st("deshab", e.active)
        st("up", inorder and idx > 0)
        st("down", inorder and 0 <= idx < len(self.order) - 1)
        st("default", e.num != self.dflt)
        st("rename", e.ok)
        st("delete", True)
        st("once", e.active)

    # ---- lectura y presentación ----
    def refresh(self, keep=None):
        try:
            self.order, self.entries = self.nv.read_all()
            self.boot_current = self.nv.get_u16("BootCurrent")
            self.boot_next = self.nv.get_u16("BootNext")
        except NvramError as e:
            self.fail("No se pudo leer la NVRAM", e)
            return
        self.emap = {e.id: e for e in self.entries}
        ok, path, desc, _ = get_bootmgr()
        self.bm = (ok, path, desc)
        self.render(keep)

    def render(self, keep=None):
        c, t = self.c, self.tree
        t.tag_configure("even", background=c["surface"], foreground=c["text"])
        t.tag_configure("odd", background=c["stripe"], foreground=c["text"])
        t.tag_configure("off_even", background=c["surface"], foreground=c["muted"])
        t.tag_configure("off_odd", background=c["stripe"], foreground=c["muted"])
        t.tag_configure("default", background=c["accent_soft"], foreground=c["text"], font=self.f_bold)
        if keep is None and t.selection():
            keep = t.selection()[0]
        by_num = {e.num: e for e in self.entries}
        self.dflt = next((n for n in self.order if n in by_num and by_num[n].active), None)
        ordered = [by_num[n] for n in self.order if n in by_num]
        rest = [e for e in self.entries if e.num not in self.order]

        px = self.sc(14)
        self._refreshing = True
        try:
            t.delete(*t.get_children())
            for i, e in enumerate(ordered + rest):
                pos = str(self.order.index(e.num) + 1) if e.num in self.order else "–"
                stripe = "even" if i % 2 == 0 else "odd"
                if not e.active:
                    tag, img = "off_" + stripe, self.icon("ring", c["disabled"], px)
                elif e.num == self.dflt:
                    tag, img = "default", self.icon("star", c["accent"], px)
                else:
                    tag, img = stripe, self.icon("dot", c["success"], px)
                t.insert("", "end", iid=e.id, image=img, tags=(tag,),
                         values=(pos, e.name, e.id, e.path, e.device, self.state_text(e)))
            if keep and t.exists(keep):
                t.selection_set(keep)
                t.see(keep)
        finally:
            self._refreshing = False
        habil = sum(1 for e in self.entries if e.active)
        cur = by_num.get(self.boot_current)
        self.lbl_info.configure(text="%d entradas  ·  %d habilitadas  ·  en uso: %s" % (
            len(self.entries), habil, cur.name if cur else "desconocido"))
        self.after_select()

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
        self.toast("Copia de seguridad guardada.", "success")

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
        self.toast("Copia restaurada correctamente.", "success")

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
        e = self.selected_entry()
        if not e or e.num not in self.order:
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
            self.toast("'%s' es ahora la predeterminada." % e.name, "success")

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
            self.toast("'%s' ya está habilitada." % e.name, "info", 2600)
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
        self.toast("'%s' habilitada." % e.name, "success")

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
            self.toast("'%s' ya está deshabilitada." % e.name, "info", 2600)
            return
        activas = {x.num for x in self.entries if x.active}
        if not [n for n in self.order if n != e.num and n in activas]:
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
            self.toast("'%s' deshabilitada." % e.name, "success")
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
            self.toast("Habilita la entrada antes de usarla para arrancar.", "warn", 3000)
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
        new = self.ask_text("Renombrar entrada", "Nuevo nombre de la entrada:", e.name)
        if not new or not new.strip() or new.strip() == e.name:
            return
        if not self.ensure_backup():
            return
        raw = build_load_option(e.attributes, new.strip(), e.fpl, e.optional)
        if self.write_var(e.id, raw, e.attrs, "renombrada a '%s'" % new.strip()):
            self.refresh(e.id)
            self.logmsg("[ÉXITO] Entrada renombrada.")
            self.toast("Entrada renombrada.", "success")

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
        self.toast("'%s' eliminada." % e.name, "success")

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
            self.toast("Entrada '%s' creada (%s)." % (name, var), "success")
        else:
            self.logmsg("[AVISO] El firmware no devolvió la entrada tal como se escribió.")
            messagebox.showwarning("Verifica el resultado",
                                   "La entrada se escribió, pero el firmware no la devolvió idéntica. "
                                   "Revisa la tabla y la BIOS.")
        return True

    # ---- Toggle de {bootmgr} (vía bcdedit) ----
    def update_toggle(self):
        """Actualiza texto, icono y rol del botón según el {bootmgr} leído (en caché, sin lanzar bcdedit)."""
        ok, path, desc = self.bm
        b = self.btn["bm"]
        if not ok:
            b.configure(text=" " + TXT_BLINDAR_GENERIC)
            self.set_button_icon(b, "shield", "text")
            return
        if is_windows_path(path) or not path:
            e = self.selected_entry()
            if e and e.path.strip() and not is_windows_path(e.path):
                nm = e.name if len(e.name) <= 18 else e.name[:17] + "…"
                b.configure(text=" Blindar {bootmgr} con: %s" % nm)
            else:
                b.configure(text=" " + TXT_BLINDAR_GENERIC)
            self.set_button_icon(b, "shield", "text")
        else:
            b.configure(text=" " + TXT_LIBERAR)
            self.set_button_icon(b, "undo", "text")

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
            target_name = self.ask_text("Descripción", "Descripción para {bootmgr}:", base)
            if not target_name or not target_name.strip():
                return
            target_name = target_name.strip()

        if is_windows_path(target_path):
            self.toast("Esa es la ruta estándar de Windows. Selecciona otra entrada (rEFInd, GRUB, etc.).",
                       "info", 4200)
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
            self.toast("%s completado." % titulo, "success")
        else:
            self.logmsg("[AVISO] %s: la ruta se cambió, pero la descripción falló: %s"
                        % (titulo, out2.strip() or "sin detalle"))
            messagebox.showwarning(titulo, "La ruta se cambió, pero no se pudo actualizar la descripción.")
        self.refresh()


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
    enable_dpi_awareness()
    load_private_fonts()
    root = tk.Tk()
    app = App(root)
    if app.ready:
        root.mainloop()


if __name__ == "__main__":
    main()