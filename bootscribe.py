# -*- coding: utf-8 -*-
"""
BootScribe - gestor de arranque UEFI para Windows.

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
import glob
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

LANG = ["es"]
EN = {}


def _(s):
    """Traduce un texto de la interfaz (los originales están en español)."""
    return EN.get(s, s) if LANG[0] == "en" else s


DEFAULT_LANG = "en"      # idioma inicial la primera vez que se abre
DEFAULT_THEME = "dark"   # tema inicial la primera vez que se abre

VERSION = "3.0.0"
APP_NAME = f"BootScribe v{VERSION}"
APP_ID = "BootScribe"    # identificador interno: no cambia con la versión (carpeta de datos y copias)
CREATE_NO_WINDOW = 0x08000000

EFI_GLOBAL_GUID = "{8BE4DF61-93CA-11D2-AA0D-00E098032B8C}"
VAR_ATTRS = 0x07                 # NON_VOLATILE | BOOTSERVICE_ACCESS | RUNTIME_ACCESS
LOAD_OPTION_ACTIVE = 0x00000001
SCAN_MAX = 0x100                 # se sondean Boot0000..Boot00FF, además de lo que liste BootOrder
VAR_NAME_RE = re.compile(r"^(BootOrder|Boot[0-9A-F]{4})$")

WIN_PATH = r"\EFI\Microsoft\Boot\bootmgfw.efi"
WIN_DESC = "Windows Boot Manager"
TXT_BLINDAR_GENERIC = "Blindar {bootmgr} con selección"
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
        select="#26405E", dirty="#2B2819"),
    "light": dict(
        bg="#F2F5F9", surface="#FFFFFF", stripe="#F7F9FC", surface2="#E9EEF4", border="#D3DAE4",
        text="#1A2330", muted="#657386", disabled="#A7B0BD",
        accent="#1F6FE0", accent_hover="#3B84EB", accent_press="#1859B8", accent_soft="#E2EDFC",
        on_accent="#FFFFFF", success="#1E8E4E", success_hover="#26A25D", success_press="#177A42",
        on_success="#FFFFFF", warn="#B7740A", danger="#CC3434", danger_soft="#FBE9E9",
        select="#CFE1FA", dirty="#FFF4D6"),
}


def hex_rgb(h):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ----------------------------- Iconos propios: rasterizador vectorial mínimo -----------------------------
class Raster:
    """Dibuja formas sobre una cuadrícula de diseño de 24 x 24 con sobremuestreo (suavizado)."""
    SS = 4

    def __init__(self, n, ss=None):
        self.SS = ss or Raster.SS
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
        seg = max(24, min(360, int(6.2832 * r * self.k / 4)))      # más segmentos cuanto mayor es el círculo
        self.poly([(c[0] + r * math.cos(2 * math.pi * i / seg), c[1] + r * math.sin(2 * math.pi * i / seg))
                   for i in range(seg)], val)

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


def _draw_alert(m):
    m.path([(12, 4), (21, 19.5), (3, 19.5)], 2.1, closed=True)
    m.path([(12, 10), (12, 14.2)], 2.3)
    m.circle((12, 17.1), 1.25)


def _draw_file(m):
    m.path([(6, 3.5), (14, 3.5), (19, 8.5), (19, 20.5), (6, 20.5)], 2.0, closed=True)
    m.path([(14, 3.5), (14, 8.5), (19, 8.5)], 2.0)


def _draw_save(m):
    m.path([(4, 4), (17, 4), (20, 7), (20, 20), (4, 20)], 2.0, closed=True)
    m.path([(8, 4), (8, 9), (15, 9), (15, 4)], 1.8)
    m.path([(7.5, 20), (7.5, 14), (16.5, 14), (16.5, 20)], 1.8)


def _draw_dot(m): m.circle((12, 12), 6.2)


def _draw_ring(m): m.path(Raster.arc_pts((12, 12), 5.4, 0, 360, 36), 2.2)


ICON_DRAWERS = {
    "refresh": _draw_refresh, "history": _draw_history, "up": _draw_up, "down": _draw_down,
    "star": _star, "plus": _draw_plus, "pencil": _draw_pencil, "trash": _draw_trash,
    "check": _draw_check, "ban": _draw_ban, "next": _draw_next, "power": _draw_power,
    "download": _draw_download, "shield": _draw_shield, "undo": _draw_undo, "sun": _draw_sun,
    "moon": _draw_moon, "folder": _draw_folder, "copy": _draw_copy, "dot": _draw_dot,
    "ring": _draw_ring, "alert": _draw_alert, "file": _draw_file, "save": _draw_save,
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


_FLAG_RECT = [(2, 5.5), (22, 5.5), (22, 18.5), (2, 18.5)]


def _flag_layers(code):
    """Banderas simplificadas dibujadas por el propio programa: (color, opacidad, función de dibujo)."""
    def band(x0, y0, x1, y1):
        return lambda m: m.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    rim = ((0, 0, 0), 0.35, lambda m: m.path(_FLAG_RECT, 0.9, closed=True))
    if code == "es":
        return [((198, 11, 30), 1.0, band(2, 5.5, 22, 18.5)), ((255, 196, 0), 1.0, band(2, 8.75, 22, 15.25)), rim]
    white, red = (255, 255, 255), (200, 16, 46)

    def diag(w):
        return lambda m: (m.path([(2, 5.5), (22, 18.5)], w), m.path([(22, 5.5), (2, 18.5)], w))

    def cross(w):
        return lambda m: (band(12 - w / 2, 5.5, 12 + w / 2, 18.5)(m), band(2, 12 - w / 2, 22, 12 + w / 2)(m))

    return [((1, 33, 105), 1.0, band(2, 5.5, 22, 18.5)), (white, 1.0, diag(3.0)), (red, 1.0, diag(1.2)),
            (white, 1.0, cross(4.6)), (red, 1.0, cross(2.6)), rim]


def flag_png(code, n):
    clip = Raster(n)
    clip.poly(_FLAG_RECT)
    ca = clip.alpha()
    out = [[0.0, 0.0, 0.0, 0.0] for i in range(n * n)]
    for color, opacity, draw in _flag_layers(code):
        m = Raster(n)
        draw(m)
        al_mask = m.alpha()
        for i in range(n * n):
            al = al_mask[i] / 255.0 * ca[i] / 255.0 * opacity
            if al <= 0:
                continue
            r, g, b, a = out[i]
            na = al + a * (1 - al)
            out[i] = [(color[0] * al + r * a * (1 - al)) / na, (color[1] * al + g * a * (1 - al)) / na,
                      (color[2] * al + b * a * (1 - al)) / na, na]
    rgba = bytearray()
    for r, g, b, a in out:
        rgba += bytes((int(r + 0.5), int(g + 0.5), int(b + 0.5), int(a * 255 + 0.5)))
    return make_png(n, n, rgba)


# ----------------------------- Icono de la aplicación (monitor) -----------------------------
def _box_blur(alpha, n, rad):
    """Desenfoque de caja separable sobre una máscara n x n (para la sombra suave)."""
    k = 2 * rad + 1
    tmp = bytearray(n * n)
    for y in range(n):
        row = alpha[y * n:(y + 1) * n]
        for x in range(n):
            tmp[y * n + x] = sum(row[max(0, x - rad):x + rad + 1]) // k
    out = bytearray(n * n)
    for x in range(n):
        col = bytes(tmp[y * n + x] for y in range(n))
        for y in range(n):
            out[y * n + x] = sum(col[max(0, y - rad):y + rad + 1]) // k
    return out


def _icon_arrow_mask(n, ss):
    """Flecha circular en sentido antihorario (restaurar / volver a arrancar), cuadrícula de 24 unidades."""
    m = Raster(n, ss)
    c, r = (12.0, 12.2), 6.1
    w = 2.1 if n >= 48 else 2.7                                # trazo algo más grueso en tamaños pequeños
    a0, a1 = 172.0, -128.0                                      # de la cola (izquierda) hacia arriba-izquierda
    m.path(Raster.arc_pts(c, r, a0, a1, 72), w)
    t = math.radians(a1)
    p = (c[0] + r * math.cos(t), c[1] + r * math.sin(t))
    tg = (math.sin(t), -math.cos(t))                            # tangente en sentido antihorario
    rd = (math.cos(t), math.sin(t))
    hw, hl = 3.1, 3.7                                           # semiancho y largo de la punta
    tip = (p[0] + tg[0] * hl, p[1] + tg[1] * hl)
    b1 = (p[0] + rd[0] * hw - tg[0] * 0.2, p[1] + rd[1] * hw - tg[1] * 0.2)
    b2 = (p[0] - rd[0] * hw - tg[0] * 0.2, p[1] - rd[1] * hw - tg[1] * 0.2)
    m.path([tip, b1, b2], 0.9, closed=True)                     # esquinas redondeadas
    m.poly([tip, b1, b2])
    return m.alpha()


def app_icon_rgba(n):
    """Icono de BootScribe: disco con degradado magenta → cian y flecha circular blanca con sombra suave."""
    ss = 4 if n <= 128 else 3
    disc = Raster(n, ss)
    disc.circle((12, 12), 11.7)
    dm = disc.alpha()
    arrow = _icon_arrow_mask(n, ss)
    top, bot = (240, 10, 255), (32, 190, 228)
    shadow = None
    if n >= 48:
        dy = max(1, round(n * 0.022))
        blurred = _box_blur(arrow, n, max(1, n // 48))
        shadow = bytearray(n * n)
        for y in range(dy, n):
            shadow[y * n:(y + 1) * n] = blurred[(y - dy) * n:(y - dy + 1) * n]
    rgba = bytearray(n * n * 4)
    for y in range(n):
        ty = (y + 0.5) / n
        for x in range(n):
            i = y * n + x
            d = dm[i]
            if not d:
                continue
            tt = min(1.0, max(0.0, ty * 0.94 + (x / n) * 0.06))
            col = [top[k] + (bot[k] - top[k]) * tt for k in range(3)]
            if n >= 48 and ty < 0.55:                           # brillo superior muy suave
                g = 0.09 * (1 - ty / 0.55) ** 2
                col = [c0 + (255 - c0) * g for c0 in col]
            if shadow is not None and shadow[i]:
                sa = shadow[i] / 255.0 * 0.30
                col = [c0 * (1 - sa) + sh * sa for c0, sh in zip(col, (70, 20, 140))]
            aa = arrow[i] / 255.0
            if aa:
                col = [c0 * (1 - aa) + 255 * aa for c0 in col]
            rgba[i * 4:i * 4 + 4] = bytes((int(col[0] + 0.5), int(col[1] + 0.5), int(col[2] + 0.5), d))
    return rgba


_ICON_PNG = {}


def app_icon_png(n):
    if n not in _ICON_PNG:
        _ICON_PNG[n] = make_png(n, n, app_icon_rgba(n))
    return _ICON_PNG[n]


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
    pngs = [app_icon_png(s) for s in sizes]
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
        png = base64.b64encode(app_icon_png(64))
        img = tk.PhotoImage(master=win, data=png)
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


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]


def work_area(root):
    """Área útil de la pantalla principal (sin la barra de tareas): (izq, arriba, der, abajo)."""
    try:
        r = _RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


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
    d = os.path.join(base, APP_ID)
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


def run(args, timeout=90):
    """Ejecuta un comando capturando stdout+stderr. Nunca lanza excepciones:
    124 = tiempo agotado, 127 = no se pudo ejecutar, 1 = fallo inesperado."""
    kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        p = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return 124, _("Tiempo de espera agotado (%d s) ejecutando: %s") % (timeout, " ".join(map(str, args)))
    except (OSError, ValueError) as e:
        return 127, _("No se pudo ejecutar %s: %s") % (args[0], e)
    except Exception as e:
        return 1, _("Error inesperado ejecutando %s: %s") % (args[0], e)
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


def ps_json(script, timeout=60):
    """Ejecuta PowerShell y devuelve (objeto_json | None, error)."""
    rc, out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)
    if rc != 0:
        return None, out.strip() or _("PowerShell devolvió el código %d") % rc
    out = out.strip()
    if not out:
        return None, _("PowerShell no devolvió datos")
    try:
        return json.loads(out), ""
    except ValueError as e:
        return None, _("respuesta de PowerShell no válida: %s") % e


def validate_efi_file(full):
    """Comprueba que exista físicamente un archivo .efi legible y con cabecera PE ('MZ')."""
    try:
        if not os.path.exists(full):
            return False, _("el archivo no existe (¿fue borrado o la unidad está desconectada?)")
        if not os.path.isfile(full):
            return False, _("la ruta no es un archivo")
        size = os.path.getsize(full)
        if size < 64:
            return False, _("el archivo está vacío o dañado (%d bytes)") % size
        with open(full, "rb") as f:
            head = f.read(2)
        if head != b"MZ":
            return False, _("no es un ejecutable EFI válido (cabecera incorrecta)")
        return True, "%d KB" % max(1, size // 1024)
    except OSError as e:
        return False, _("no se pudo leer el archivo: %s") % e


# ----------------------------- Formato EFI_LOAD_OPTION / Device Path -----------------------------
def parse_load_option(raw):
    """EFI_LOAD_OPTION: UINT32 Attributes, UINT16 FilePathListLength, CHAR16 Description[],
    FilePathList[], OptionalData[]. Devuelve (attributes, descripcion, fpl, optional)."""
    if len(raw) < 8:
        raise ValueError(_("entrada demasiado corta (%d bytes)") % len(raw))
    attributes, fpl_len = struct.unpack_from("<IH", raw, 0)
    pos = end = 6
    while True:
        if end + 2 > len(raw):
            raise ValueError(_("descripción sin terminador"))
        if raw[end:end + 2] == b"\x00\x00":
            break
        end += 2
    desc = raw[pos:end].decode("utf-16-le", errors="replace")
    pos = end + 2
    if pos + fpl_len > len(raw):
        raise ValueError(_("lista de rutas truncada"))
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


def hd_guid(hd_node):
    """GUID de la partición GPT de un nodo 'disco duro' (o None si es MBR)."""
    if hd_node and len(hd_node) >= 42 and hd_node[40] == 2 and hd_node[41] == 2:
        return str(uuid.UUID(bytes_le=bytes(hd_node[24:40])))
    return None


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
    return _("Error %d: %s") % (code, _(base) if base else (sysmsg or _("desconocido")))


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
            raise NvramError(_("BootOrder no puede quedar vacío."))
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
            raise NvramError(_("Esta herramienta solo funciona en Windows."))
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
                raise NvramError(_("Este equipo arrancó en modo BIOS heredado (Legacy/CSM), "
                                   "no en UEFI. No hay variables UEFI que administrar."))
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
            raise NvramError(_("No se pudo abrir el token del proceso. ") + win_error_text(ctypes.get_last_error()))
        try:
            tp = _TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            if not adv.LookupPrivilegeValueW(None, name, ctypes.byref(tp.Privileges[0].Luid)):
                raise NvramError(_("No se pudo buscar el privilegio. ") + win_error_text(ctypes.get_last_error()))
            tp.Privileges[0].Attributes = 2  # SE_PRIVILEGE_ENABLED
            ctypes.set_last_error(0)
            ok = adv.AdjustTokenPrivileges(token, 0, ctypes.byref(tp), 0, None, None)
            err = ctypes.get_last_error()
            if not ok or err == 1300:  # ERROR_NOT_ALL_ASSIGNED
                raise NvramError(_("No se pudo activar el privilegio SeSystemEnvironmentPrivilege. "
                                   "Ejecuta BootScribe como Administrador."))
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
                raise NvramError(_("No se pueden leer las variables UEFI. ") + win_error_text(err))
            return None                             # no existe

    def _raw_set(self, name, data, attrs):
        buf = ctypes.create_string_buffer(data, len(data)) if data else None
        ctypes.set_last_error(0)
        ok = self.k32.SetFirmwareEnvironmentVariableExW(
            name, EFI_GLOBAL_GUID, ctypes.addressof(buf) if buf is not None else None,
            len(data), attrs)
        if not ok:
            err = ctypes.get_last_error()
            raise NvramError((_("No se pudo escribir '%s'. %s") if data else _("No se pudo eliminar '%s'. %s")) % (
                name, win_error_text(err)))


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
    return {"app": APP_ID, "format": BACKUP_FORMAT,
            "created": datetime.datetime.now().isoformat(timespec="seconds"), "vars": vars_}


def parse_backup(data):
    """Valida un diccionario de copia. Devuelve {nombre: (bytes, attrs)}; lanza ValueError si es inválido."""
    if not isinstance(data, dict) or data.get("app") != APP_ID or not isinstance(data.get("vars"), dict):
        raise ValueError(_("El archivo no es una copia de BootScribe."))
    out = {}
    for name, v in data["vars"].items():
        if not VAR_NAME_RE.match(name):
            raise ValueError(_("Variable no permitida en la copia: %r") % name)
        raw = bytes.fromhex(v["hex"])
        attrs = int(v.get("attrs", VAR_ATTRS))
        if not raw:
            raise ValueError(_("Variable vacía en la copia: %s") % name)
        if name == "BootOrder" and len(raw) % 2:
            raise ValueError(_("BootOrder dañado en la copia."))
        out[name] = (raw, attrs)
    if "BootOrder" not in out:
        raise ValueError(_("La copia no contiene BootOrder."))
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
        tk.Label(frame, text=_(self.text), bg=c["surface2"], fg=c["text"], font=self.app.f_small,
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
        (getattr(self, "_focus", None) or self).focus_set()

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
        app.make_button(row, "Aceptar", "check", self.ok, "Primary.TButton", reg=False).pack(side="right")
        app.make_button(row, "Cancelar", None, self.cancel, "Tool.TButton", reg=False).pack(
            side="right", padx=(0, sc(8)))
        ent.bind("<Return>", lambda _e: self.ok())
        ent.focus_set()
        ent.select_range(0, "end")

    def ok(self):
        self.result = self.var.get()
        self.destroy()


class NewEntryDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, _("Nueva entrada de arranque UEFI"))
        app._dlg = self
        sc = app.sc
        self.name_var = tk.StringVar()
        self.path_var = tk.StringVar(value=r"\EFI\Boot\bootx64.efi")
        self.first_var = tk.BooleanVar(value=False)

        f = ttk.Frame(self, padding=(sc(22), sc(20), sc(22), sc(18)))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=_("Nueva entrada de arranque"), style="Dlg.Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, sc(2)))
        ttk.Label(f, text=_("Prepara una entrada UEFI que apunta a un archivo .efi de la partición EFI (ESP). "
                            "Se creará al pulsar Guardar cambios."),
                  style="Dlg.Muted.TLabel", wraplength=sc(520), justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, sc(14)))
        ttk.Label(f, text=_("Nombre"), style="Dlg.TLabel").grid(row=2, column=0, sticky="w", pady=sc(5))
        ttk.Entry(f, textvariable=self.name_var, width=46).grid(row=2, column=1, columnspan=2, sticky="ew",
                                                              padx=(sc(12), 0))
        ttk.Label(f, text=_("Archivo .efi"), style="Dlg.TLabel").grid(row=3, column=0, sticky="w", pady=sc(5))
        ttk.Entry(f, textvariable=self.path_var, width=34).grid(row=3, column=1, sticky="ew", padx=(sc(12), 0))
        app.make_button(f, "Examinar ESP…", "folder", self.browse, "Tool.TButton", reg=False).grid(
            row=3, column=2, padx=(sc(8), 0))
        ttk.Checkbutton(f, text=_("Colocar al inicio de la lista (hacerla predeterminada)"),
                        variable=self.first_var).grid(row=4, column=1, columnspan=2, sticky="w", pady=(sc(8), 0),
                                                      padx=(sc(12), 0))
        bf = ttk.Frame(f)
        bf.grid(row=5, column=0, columnspan=3, pady=(sc(18), 0), sticky="e")
        app.make_button(bf, "Añadir a la lista", "plus", self.ok, "Primary.TButton", reg=False).pack(side="right")
        app.make_button(bf, "Cancelar", None, self.cancel, "Tool.TButton", reg=False).pack(
            side="right", padx=(0, sc(8)))
        f.columnconfigure(1, weight=1)
        self.show()

    def browse(self):
        try:
            letter = self.app.ensure_esp()
        except Exception as e:
            messagebox.showerror(_("Error"), str(e), parent=self)
            return
        p = filedialog.askopenfilename(parent=self, initialdir=letter + ":\\EFI",
                                       title=_("Selecciona un archivo .efi"),
                                       filetypes=[(_("Aplicación EFI"), "*.efi"), (_("Todos"), "*.*")])
        if p:
            p = norm_path(p)
            self.path_var.set(p)
            if not self.name_var.get().strip():
                self.name_var.set(os.path.splitext(os.path.basename(p))[0])

    def ok(self):
        name = self.name_var.get().strip()
        path = norm_path(self.path_var.get())
        if not name or not path:
            messagebox.showwarning(_("Datos incompletos"), _("Indica nombre y ruta .efi."), parent=self)
            return
        if self.app.stage_new_entry(name, path, self.first_var.get()):
            self.cancel()

    def cancel(self):
        self.app.release_esp()
        self.app._dlg = None
        self.destroy()


class ChangeDialog(Dialog):
    """Resumen antes/después: compara el estado ACTUAL con el PROPUESTO y pide aprobación explícita."""

    def __init__(self, app, title, headline, rows, notes=(), danger=False, checkbox=None,
                 ok_text="Aplicar cambio"):
        super().__init__(app, title)
        c, sc = app.c, app.sc
        self.result, self.checked = False, False
        f = ttk.Frame(self, padding=(sc(26), sc(22), sc(26), sc(20)))
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=headline, style="Dlg.Title.TLabel", wraplength=sc(720), justify="left").pack(anchor="w")
        ttk.Label(f, text=_("Revisa el antes y el después. No se modificará nada hasta que lo confirmes."),
                  style="Dlg.Muted.TLabel").pack(anchor="w", pady=(sc(3), sc(14)))

        border = tk.Frame(f, bg=c["border"], padx=1, pady=1)
        border.pack(fill="x")
        g = tk.Frame(border, bg=c["surface"])
        g.pack(fill="x")
        for col, text in ((0, ""), (1, _("ACTUAL")), (2, ""), (3, _("PROPUESTO"))):
            tk.Label(g, text=text, bg=c["surface2"], fg=c["muted"], font=app.f_cap, anchor="w",
                     padx=sc(12), pady=sc(7)).grid(row=0, column=col, sticky="nsew")
        wrap = sc(310)
        for i, (label, before, after) in enumerate(rows):
            r = 1 + 2 * i
            changed = str(before) != str(after)
            new_fg = c["danger"] if (danger and changed) else (c["success"] if changed else c["text"])
            tk.Label(g, text=label, bg=c["surface"], fg=c["muted"], font=app.f_small, anchor="nw",
                     justify="left", wraplength=sc(170), padx=sc(12), pady=sc(8)).grid(row=r, column=0, sticky="nsew")
            tk.Label(g, text=str(before), bg=c["surface"], fg=c["muted"] if changed else c["text"],
                     font=app.f_body, anchor="nw", justify="left", wraplength=wrap, padx=sc(12),
                     pady=sc(8)).grid(row=r, column=1, sticky="nsew")
            tk.Label(g, text="→" if changed else "", bg=c["surface"], fg=c["muted"], font=app.f_bold,
                     padx=sc(4), pady=sc(8)).grid(row=r, column=2, sticky="n")
            tk.Label(g, text=str(after), bg=c["surface"], fg=new_fg, font=app.f_bold if changed else app.f_body,
                     anchor="nw", justify="left", wraplength=wrap, padx=sc(12),
                     pady=sc(8)).grid(row=r, column=3, sticky="nsew")
            tk.Frame(g, height=1, bg=c["border"]).grid(row=r + 1, column=0, columnspan=4, sticky="ew")
        g.grid_columnconfigure(1, weight=1, uniform="v")
        g.grid_columnconfigure(3, weight=1, uniform="v")

        if notes:
            nf = ttk.Frame(f)
            nf.pack(fill="x", pady=(sc(14), 0))
            for kind, text in notes:
                row = ttk.Frame(nf)
                row.pack(fill="x", pady=sc(2))
                icon, col = ("check", c["success"]) if kind == "ok" else ("alert", c["warn"])
                ttk.Label(row, image=app.icon(icon, col, sc(16)), style="Dlg.TLabel").pack(
                    side="left", anchor="n", padx=(0, sc(8)))
                ttk.Label(row, text=text, style="Dlg.TLabel", wraplength=sc(700), justify="left").pack(
                    side="left", anchor="w")

        self.var = tk.BooleanVar(value=True)
        self.has_check = bool(checkbox)
        if checkbox:
            ttk.Checkbutton(f, text=checkbox, variable=self.var).pack(anchor="w", pady=(sc(14), 0))

        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(sc(18), 0))
        ok_btn = app.make_button(bf, ok_text, "check", self.accept,
                                 "Danger.TButton" if danger else "Primary.TButton", reg=False)
        ok_btn.pack(side="right")
        cancel = app.make_button(bf, "Cancelar", None, self.cancel, "Tool.TButton", reg=False)
        cancel.pack(side="right", padx=(0, sc(8)))
        self._focus = cancel if danger else ok_btn          # en acciones destructivas, el foco va a Cancelar
        if not danger:
            self.bind("<Return>", lambda _e: self.accept())

    def accept(self):
        self.result = True
        self.checked = bool(self.var.get()) if self.has_check else False
        self.destroy()


class UnsavedDialog(Dialog):
    """Se muestra al cerrar con cambios sin guardar. choice: 'save', 'discard' o 'cancel'."""

    def __init__(self, app, n):
        super().__init__(app, _("Cambios sin guardar"))
        self.choice = None
        sc = app.sc
        f = ttk.Frame(self, padding=(sc(26), sc(22), sc(26), sc(20)))
        f.pack(fill="both", expand=True)
        head = ttk.Frame(f)
        head.pack(fill="x")
        ttk.Label(head, image=app.icon("alert", app.c["warn"], sc(26)), style="Dlg.TLabel").pack(
            side="left", padx=(0, sc(12)))
        ttk.Label(head, text=_("No se guardaron los cambios"), style="Dlg.Title.TLabel").pack(side="left")
        txt = (_("Tienes 1 cambio sin guardar.") if n == 1 else _("Tienes %d cambios sin guardar.") % n)
        ttk.Label(f, text=txt + " " + _("Si cierras BootScribe ahora, NO se aplicarán a la NVRAM."),
                  style="Dlg.TLabel", wraplength=sc(460), justify="left").pack(anchor="w", pady=(sc(14), sc(18)))
        bf = ttk.Frame(f)
        bf.pack(fill="x")
        save = app.make_button(bf, "Guardar y salir", "save", lambda: self.pick("save"), "Primary.TButton",
                               reg=False)
        save.pack(side="right")
        app.make_button(bf, "Salir sin guardar", "trash", lambda: self.pick("discard"), "Danger.TButton",
                        reg=False).pack(side="right", padx=(0, sc(8)))
        app.make_button(bf, "Cancelar", None, self.cancel, "Tool.TButton", reg=False).pack(
            side="right", padx=(0, sc(8)))
        self._focus = save

    def pick(self, choice):
        self.choice = choice
        self.destroy()

    def cancel(self):
        self.choice = "cancel"
        self.destroy()


# ----------------------------- Aplicación principal -----------------------------
STYLE_KIND = {"Tool.TButton": "text", "Primary.TButton": "on_accent", "Success.TButton": "on_success",
              "Danger.TButton": "danger", "Ghost.TButton": "text"}
ENTRY_BUTTONS = ("hab", "deshab", "up", "down", "default", "rename", "delete", "once")
LOGTAGS = {"ok": ("[ÉXITO]", "[SUCCESS]"), "err": ("[ERROR]", "[ERROR]"), "warn": ("[AVISO]", "[WARNING]"),
           "info": ("[INFO]", "[INFO]"), "nv": ("[NVRAM]", "[NVRAM]"), "cancel": ("[CANCELADO]", "[CANCELLED]")}


class DraftEntry:
    """Copia de trabajo de una entrada. Los cambios se ven en la tabla pero no se escriben en la
    NVRAM hasta pulsar 'Guardar cambios'."""

    def __init__(self, key, base=None):
        self.key, self.base = key, base
        self.deleted = False
        if base is not None:
            self.num, self.ok, self.error = base.num, base.ok, base.error
            self.name, self.path, self.device = base.name, base.path, base.device
            self.hd_node, self.active = base.hd_node, base.active
        else:
            self.num, self.ok, self.error = None, True, ""
            self.name = self.path = self.device = ""
            self.hd_node, self.active = None, True

    @property
    def id(self):
        return self.key

    @property
    def is_new(self):
        return self.base is None

    @property
    def raw(self):
        return self.base.raw if self.base is not None else b"\0" * 8

    @property
    def changed(self):
        if self.base is None or self.deleted:
            return True
        return self.name != self.base.name or self.active != self.base.active


class App:
    def __init__(self, root, nvram=None):
        self.root = root
        self.nv = nvram or Nvram()
        self.settings = load_settings()
        LANG[0] = self.settings.get("lang") if self.settings.get("lang") in ("es", "en") else DEFAULT_LANG
        self.base_order, self.base_entries, self.base_map = [], [], {}
        self.W, self.w_order, self.w_rest = {}, [], []
        self.new_seq = 0
        self.vcache = {}
        self.boot_current = self.boot_next = None
        self.dflt = None
        self.bm = (False, "", "")
        self.esp_letter, self.esp_mounted_by_us = None, False
        self._refreshing = False
        self._backed_up = False
        self._bcd_exported = False
        self._dlg = None
        self.ready = False
        self._toast, self._toast_job = None, None
        self.btn, self.icon_buttons, self.borders = {}, {}, []
        self._imgs = {}
        self.i18n = []

        self.theme = self.settings.get("theme") if self.settings.get("theme") in PALETTES else DEFAULT_THEME
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
        self.f_strike = tkfont.Font(root=root, family=sans, size=10, overstrike=True)
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
        self.refresh(force=True)

    # ---- escala, iconos, botones ----
    def sc(self, v):
        return int(round(v * self.k))

    def icon(self, name, color, size=None):
        n = size or self.sc(18)
        key = (name, color, n)
        if key not in self._imgs:
            self._imgs[key] = tk.PhotoImage(master=self.root, data=base64.b64encode(icon_png(name, color, n)))
        return self._imgs[key]

    def flag(self, code, h=None):
        n = h or self.sc(22)
        key = ("flag", code, n)
        if key not in self._imgs:
            self._imgs[key] = tk.PhotoImage(master=self.root, data=base64.b64encode(flag_png(code, n)))
        return self._imgs[key]

    def set_button_icon(self, btn, name, kind):
        c = self.c
        if not name:
            return
        self.icon_buttons[btn] = (name, kind)
        btn.configure(image=(self.icon(name, c[kind]), "disabled", self.icon(name, c["disabled"])),
                      compound="left")

    def rt(self, widget, src, kind="text", extra=None):
        """Registra un widget para retraducirlo al cambiar de idioma."""
        self.i18n.append((widget, src, kind, extra))
        return widget

    def lbl(self, parent, src, style, **kw):
        return self.rt(ttk.Label(parent, text=_(src), style=style, **kw), src)

    def make_button(self, parent, text, icon, cmd, style="Tool.TButton", tip="", width=None, reg=True):
        b = ttk.Button(parent, text=((" " + _(text)) if (text and icon) else _(text)), command=cmd,
                       style=style, takefocus=False)
        if width:
            b.configure(width=width)
        elif not text:
            b.configure(width=0)
        if icon:
            self.set_button_icon(b, icon, STYLE_KIND.get(style, "text"))
        if tip:
            Tooltip(b, tip, self)
        if text and reg:
            self.rt(b, text, "btn", bool(icon))
        return b

    # ---- idioma ----
    def set_lang_button(self):
        code = LANG[0]
        self.btn_lang.configure(image=self.flag(code), text=" " + code.upper(), compound="left")

    def toggle_lang(self):
        LANG[0] = "en" if LANG[0] == "es" else "es"
        self.settings["lang"] = LANG[0]
        save_settings(self.settings)
        self.apply_language()

    def apply_language(self):
        live = []
        for w, src, kind, extra in self.i18n:
            try:
                if not w.winfo_exists():
                    continue
                if kind == "text":
                    w.configure(text=_(src))
                elif kind == "btn":
                    w.configure(text=(" " + _(src)) if extra else _(src))
                elif kind == "heading":
                    w.heading(extra, text=_(src))
                elif kind == "menu":
                    w.entryconfigure(extra, label=_(src))
            except tk.TclError:
                continue
            live.append((w, src, kind, extra))
        self.i18n = live
        self.set_lang_button()
        self.render()
        self.update_toggle()
        self.set_status(_("Listo."), "info")

    # ---- construcción de la interfaz ----
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
                        focuscolor=bg, relief="flat", borderwidth=1, padding=(sc(8), sc(7)), anchor="center",
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
        s.configure("Save.TFrame", background=c["surface"])
        s.configure("SaveDirty.TFrame", background=c["dirty"])
        s.configure("Save.TLabel", background=c["surface"], foreground=c["muted"], font=self.f_body)
        s.configure("SaveDirty.TLabel", background=c["dirty"], foreground=c["warn"], font=self.f_bold)
        s.configure("SaveHint.TLabel", background=c["surface"], foreground=c["muted"], font=self.f_small)
        s.configure("SaveDirtyHint.TLabel", background=c["dirty"], foreground=c["muted"], font=self.f_small)
        s.configure("TPanedwindow", background=c["bg"])
        try:
            s.configure("Sash", sashthickness=sc(8), gripcount=0)
        except tk.TclError:
            pass

    def border_frame(self, parent):
        fr = tk.Frame(parent, bg=self.c["border"], padx=1, pady=1)
        self.borders.append(fr)
        return fr

    def group(self, parent, caption, specs):
        sc = self.sc
        if parent.winfo_children():
            sep = tk.Frame(parent, width=1, bg=self.c["border"])
            self.borders.append(sep)
            sep.pack(side="left", fill="y", padx=sc(10), pady=sc(2))
        g = ttk.Frame(parent)
        g.pack(side="left")
        self.lbl(g, caption, "Caption.TLabel").pack(anchor="w", pady=(0, sc(4)))
        row = ttk.Frame(g)
        row.pack(anchor="w")
        for key, text, icon, cmd, style, tip, *w in specs:
            b = self.make_button(row, text, icon, cmd, style, tip, width=(w[0] if w else None),
                                 reg=(key != "bm"))
            b.pack(side="left", padx=(0, sc(4)))
            self.btn[key] = b

    def build_ui(self):
        c, sc, root = self.c, self.sc, self.root

        # Encabezado
        header = ttk.Frame(root, style="Header.TFrame", padding=(sc(18), sc(10), sc(18), sc(10)))
        header.pack(fill="x")
        self.logo = tk.PhotoImage(master=root, data=base64.b64encode(app_icon_png(sc(38))))
        ttk.Label(header, image=self.logo, style="H.TLabel").pack(side="left")
        tf = ttk.Frame(header, style="Header.TFrame")
        tf.pack(side="left", padx=(sc(14), 0))
        ttk.Label(tf, text=f"{APP_NAME}", style="H.Title.TLabel").pack(anchor="w")
        self.lbl(tf, "Administrador de arranque UEFI", "H.Sub.TLabel").pack(anchor="w")
        self.btn_theme = self.make_button(header, "", "sun" if self.theme == "dark" else "moon",
                                          self.toggle_theme, "Ghost.TButton",
                                          "Cambiar entre tema oscuro y claro")
        self.btn_theme.pack(side="right")
        self.btn_lang = ttk.Button(header, style="Ghost.TButton", command=self.toggle_lang, takefocus=False)
        Tooltip(self.btn_lang, "Español ⇄ English", self)
        self.btn_lang.pack(side="right", padx=(0, sc(8)))
        self.set_lang_button()
        self.lbl_info = ttk.Label(header, text="", style="H.Chip.TLabel")
        self.lbl_info.pack(side="right", padx=(0, sc(16)))
        Tooltip(self.lbl_info, "Es la entrada UEFI desde la que el firmware arrancó esta sesión de Windows "
                               "(variable BootCurrent). Es un dato histórico: no cambia al habilitar, "
                               "deshabilitar o reordenar entradas; se actualiza en el próximo reinicio.", self)
        line = tk.Frame(root, height=1, bg=c["border"])
        self.borders.append(line)
        line.pack(fill="x")

        # Barra de herramientas agrupada (los cambios se preparan en la tabla; no se escriben aún)
        tb = ttk.Frame(root, padding=(sc(18), sc(10), sc(18), sc(2)))
        tb.pack(fill="x")
        rowA = ttk.Frame(tb)
        rowA.pack(fill="x")
        self.group(rowA, "ESTADO", [
            ("hab", "Habilitar entrada", "check", self.stage_enable, "Success.TButton",
             "Activa la entrada (atributo ACTIVE del firmware). No cambia su prioridad."),
            ("deshab", "Deshabilitar entrada", "ban", self.stage_disable, "Tool.TButton",
             "Desactiva la entrada sin borrarla; conserva su posición en el orden.")])
        self.group(rowA, "PRIORIDAD", [
            ("up", "Subir", "up", lambda: self.stage_move(-1), "Tool.TButton",
             "Sube la entrada en el orden (Ctrl+↑)."),
            ("down", "Bajar", "down", lambda: self.stage_move(1), "Tool.TButton",
             "Baja la entrada en el orden (Ctrl+↓)."),
            ("default", "Hacer predeterminada", "star", self.stage_default, "Tool.TButton",
             "La coloca en primer lugar: será la que el firmware intente primero.")])
        self.group(rowA, "ARRANQUE", [
            ("once", "Arrancar una vez", "next", self.boot_once, "Tool.TButton",
             "Arranca desde la entrada seleccionada solo en el próximo reinicio (BootNext) y reinicia."),
            ("fw", "Reiniciar a UEFI", "power", self.reboot_fw, "Tool.TButton",
             "Reinicia y abre la configuración UEFI / BIOS.")])
        rowB = ttk.Frame(tb)
        rowB.pack(fill="x", pady=(sc(8), 0))
        self.rowA, self.rowB = rowA, rowB
        self.group(rowB, "GESTIÓN", [
            ("new", "Nueva entrada", "plus", self.new_entry, "Tool.TButton",
             "Prepara una entrada nueva que apunta a un archivo .efi de la ESP."),
            ("rename", "Renombrar", "pencil", self.stage_rename, "Tool.TButton",
             "Cambia el nombre de la entrada."),
            ("delete", "Eliminar", "trash", self.stage_delete, "Danger.TButton",
             "Marca la entrada para eliminarla al guardar (Supr). Púlsalo de nuevo para deshacerlo.")])
        self.group(rowB, "RESPALDO", [
            ("backup", "Copia NVRAM", "download", self.manual_backup, "Tool.TButton",
             "Guarda un archivo con todas las entradas UEFI y el orden de arranque."),
            ("bcd", "Exportar BCD", "file", self.manual_bcd_export, "Tool.TButton",
             "Exporta la configuración BCD de Windows (bcdedit /export). Recomendado antes del primer cambio "
             "en {bootmgr}."),
            ("restore", "Restaurar copia…", "history", self.restore_backup, "Tool.TButton",
             "Restaura entradas y orden de arranque desde una copia de la NVRAM.")])
        self.group(rowB, "AVANZADO", [
            ("bm", TXT_BLINDAR_GENERIC, "shield", self.toggle_bootmgr, "Tool.TButton",
             "Redirige el {bootmgr} de Windows (BCD) a otro gestor .efi o lo restaura. "
             "Algunos firmwares no lo respetan.", 30)])

        # Barra de cambios pendientes
        self.savebar = ttk.Frame(root, style="Save.TFrame", padding=(sc(18), sc(7), sc(18), sc(7)))
        self.savebar.pack(fill="x", pady=(sc(6), 0))
        self.lbl_pending = ttk.Label(self.savebar, text="", style="Save.TLabel")
        self.lbl_pending.pack(side="left")
        self.lbl_hint = ttk.Label(self.savebar, text="", style="SaveHint.TLabel")
        self.lbl_hint.pack(side="left", padx=(sc(12), 0))
        self.btn["save"] = self.make_button(self.savebar, "Guardar cambios", "save", self.save_changes,
                                            "Primary.TButton",
                                            "Aplica a la NVRAM todos los cambios preparados en la tabla.")
        self.btn["save"].pack(side="right")
        self.btn["discard"] = self.make_button(self.savebar, "Descartar", "undo", self.discard_changes,
                                               "Tool.TButton", "Descarta los cambios preparados y vuelve al "
                                                               "estado real de la NVRAM.")
        self.btn["discard"].pack(side="right", padx=(0, sc(8)))
        line2 = tk.Frame(root, height=1, bg=c["border"])
        self.borders.append(line2)
        line2.pack(fill="x")

        # Barra de estado (se empaqueta antes del cuerpo para que siempre quede visible abajo)
        sline = tk.Frame(root, height=1, bg=c["border"])
        self.borders.append(sline)
        sline.pack(fill="x", side="bottom")
        status = ttk.Frame(root, style="Status.TFrame", padding=(sc(18), sc(6)))
        status.pack(fill="x", side="bottom")
        self.lbl_status = ttk.Label(status, text=_("Listo."), style="Status.TLabel")
        self.lbl_status.pack(side="left")
        self.lbl(status, "UEFI · Administrador", "Status.TLabel").pack(side="right")

        # Zona principal: tabla + detalle / registro (redimensionables)
        body = ttk.Frame(root, padding=(sc(18), sc(6), sc(18), 0))
        body.pack(fill="both", expand=True)
        self.paned = ttk.PanedWindow(body, orient="vertical")
        self.paned.pack(fill="both", expand=True)

        top = ttk.Frame(self.paned)
        self.top = top
        self.paned.add(top, weight=4)
        dborder = self.border_frame(top)
        dborder.pack(side="bottom", fill="x", pady=(sc(8), sc(6)))
        tborder = self.border_frame(top)
        tborder.pack(fill="both", expand=True)
        card = ttk.Frame(tborder, style="Card.TFrame")
        card.pack(fill="both", expand=True)
        cols = ("pos", "name", "id", "path", "device", "state")
        self.tree = ttk.Treeview(card, columns=cols, show="tree headings", selectmode="browse",
                                 style="BS.Treeview", height=5)
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=sc(48), minwidth=sc(48), stretch=False, anchor="center")
        for col, title, w, stretch in (("pos", "ORDEN", 64, False), ("name", "NOMBRE", 190, True),
                                       ("id", "ID", 92, False), ("path", "RUTA EFI", 240, True),
                                       ("device", "DISPOSITIVO", 150, True), ("state", "ESTADO", 300, True)):
            self.tree.heading(col, text=_(title), anchor="w")
            self.tree.column(col, width=sc(w), minwidth=sc(w // 2), anchor="w", stretch=stretch)
            self.rt(self.tree, title, "heading", col)
        sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        det = ttk.Frame(dborder, style="Card.TFrame", padding=(sc(16), sc(8)))
        det.pack(fill="x")
        self.det = {}
        for i, (key, cap) in enumerate((("name", "ENTRADA SELECCIONADA"), ("id", "ID"), ("path", "RUTA EFI"),
                                        ("device", "DISPOSITIVO"), ("state", "ESTADO"))):
            cell = ttk.Frame(det, style="Card.TFrame")
            cell.grid(row=0, column=i, sticky="w", padx=(0, sc(26)))
            self.lbl(cell, cap, "Card.Cap.TLabel").pack(anchor="w")
            lab = ttk.Label(cell, text="–", style="Card.Name.TLabel" if key == "name" else "Card.TLabel")
            lab.pack(anchor="w", pady=(sc(2), 0))
            self.det[key] = lab

        bot = ttk.Frame(self.paned)
        self.paned.add(bot, weight=1)
        lh = ttk.Frame(bot)
        lh.pack(fill="x", pady=(0, sc(5)))
        self.lbl(lh, "REGISTRO", "Caption.TLabel").pack(side="left")
        self.make_button(lh, "Copiar", "copy", self.copy_log, "Ghost.TButton",
                         "Copia todo el registro al portapapeles.").pack(side="right")
        self.make_button(lh, "Limpiar", "trash", self.clear_log, "Ghost.TButton",
                         "Borra el contenido del registro.").pack(side="right", padx=(0, sc(6)))
        lborder = self.border_frame(bot)
        lborder.pack(fill="both", expand=True)
        lcard = ttk.Frame(lborder, style="Card.TFrame")
        lcard.pack(fill="both", expand=True)
        self.log = tk.Text(lcard, height=4, state="disabled", font=self.f_mono, relief="flat", bd=0, wrap="word",
                           padx=sc(10), pady=sc(8), highlightthickness=0)
        lsb = ttk.Scrollbar(lcard, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        # Menú contextual y atajos
        self.menu = tk.Menu(root, tearoff=0, bd=0, relief="flat", font=self.f_body)
        idx = 0
        for item in (("Habilitar entrada", self.stage_enable), ("Deshabilitar entrada", self.stage_disable), None,
                     ("Subir", lambda: self.stage_move(-1)), ("Bajar", lambda: self.stage_move(1)),
                     ("Hacer predeterminada", self.stage_default), None,
                     ("Arrancar una vez", self.boot_once), ("Renombrar", self.stage_rename),
                     ("Eliminar", self.stage_delete)):
            if item is None:
                self.menu.add_separator()
            else:
                self.menu.add_command(label=_(item[0]), command=item[1])
                self.rt(self.menu, item[0], "menu", idx)
            idx += 1
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("<Delete>", lambda _e: self.stage_delete())
        self.tree.bind("<Control-Up>", lambda _e: self.stage_move(-1))
        self.tree.bind("<Control-Down>", lambda _e: self.stage_move(1))
        root.bind("<F5>", lambda _e: self.refresh())
        root.bind("<Control-s>", lambda _e: self.save_changes())
        self.apply_plain_colors()
        root.after(60, self.init_sash)
        root.after(350, self.init_sash)

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

    def place_window(self):
        r, sc = self.root, self.sc
        r.update_idletasks()
        l, t, rr, b = work_area(r)
        aw, ah = rr - l, b - t
        deco = sc(48)                                   # barra de título + bordes (se corrige al mostrar)
        need = max(self.rowA.winfo_reqwidth(), self.rowB.winfo_reqwidth()) + sc(36) + sc(6)
        w = min(max(need + sc(70), sc(1180)), aw - sc(24))
        h = min(sc(700), ah - deco - sc(16))
        x, y = l + (aw - w) // 2, t + max(0, (ah - h - deco) // 2)
        g = self.settings.get("geometry")
        if isinstance(g, list) and len(g) == 4 and all(isinstance(v, int) for v in g):
            gw, gh, gx, gy = g
            if max(sc(900), need) <= gw <= aw and sc(560) <= gh <= ah - deco and l - 20 <= gx <= rr - 120 \
                    and t <= gy <= b - 120:
                w, h, x, y = gw, gh, gx, gy
        r.minsize(min(need, aw - sc(24)), min(sc(600), ah - deco - sc(16)))
        r.geometry("%dx%d+%d+%d" % (w, h, x, y))
        if self.settings.get("zoomed"):
            try:
                r.state("zoomed")
            except tk.TclError:
                pass
        r.after(80, self.fit_to_work_area)

    def fit_to_work_area(self):
        """Mide los bordes reales de la ventana y la ajusta para que no tape la barra de tareas."""
        r, sc = self.root, self.sc
        try:
            r.update_idletasks()
            if r.state() == "zoomed":
                return
            l, t, rr, b = work_area(r)
            top = max(0, r.winfo_rooty() - r.winfo_y())
            bot = sc(8) if os.name == "nt" else 0
            w, h, x, y = r.winfo_width(), r.winfo_height(), r.winfo_x(), r.winfo_y()
            nh = min(h, (b - t) - top - bot - sc(6))
            nw = min(w, (rr - l) - sc(16))
            nx = max(l, min(x, rr - nw - sc(8)))
            ny = max(t, min(y, b - (nh + top + bot) - sc(6)))
            if (nw, nh, nx, ny) != (w, h, x, y):
                r.geometry("%dx%d+%d+%d" % (nw, nh, nx, ny))
        except tk.TclError:
            pass

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

    def logmsg(self, text, kind=None):
        text = text.rstrip()
        first = text.split("\n", 1)[0]
        tag, status = "info", None
        if kind in ("ok", "err", "warn", "info", "nv", "cancel"):
            tag = {"cancel": "warn"}.get(kind, kind)
            status = {"ok": "success", "err": "error", "warn": "warn", "cancel": "warn"}.get(kind)
        elif first.startswith(">"):
            tag = "cmd"
        self.log.configure(state="normal")
        self.log.insert("end", datetime.datetime.now().strftime("%H:%M:%S  "), "ts")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        if status:
            self.set_status(first, status)

    def lg(self, kind, text):
        self.logmsg("%s %s" % (LOGTAGS[kind][0 if LANG[0] == "es" else 1], text), kind)

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end").strip())
        self.toast(_("Registro copiado al portapapeles."), "info", 2200)

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def fail(self, titulo, ex):
        self.lg("err", "%s: %s" % (titulo, ex))
        messagebox.showerror(titulo, str(ex), parent=self.msg_parent())

    def ask_text(self, title, label, initial=""):
        d = TextPrompt(self, title, label, initial)
        d.show()
        self.root.wait_window(d)
        return d.result

    # ---- selección y botones ----
    def selected_entry(self):
        sel = self.tree.selection()
        return self.W.get(sel[0]) if sel else None

    def need_entry(self):
        e = self.selected_entry()
        if not e:
            self.toast(_("Selecciona una entrada de la lista."), "info", 2400)
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

    def busy(self, on):
        try:
            self.root.configure(cursor="watch" if on else "")
            self.root.update_idletasks()
        except tk.TclError:
            pass

    # ---- modelo de cambios pendientes ----
    def final_keys(self):
        return [k for k in self.w_order if not self.W[k].deleted]

    def base_keys(self):
        return ["Boot%04X" % n for n in self.base_order if n in self.base_map]

    def base_name(self, key):
        e = self.base_map.get(int(key[4:], 16))
        return e.name if e else key

    def dflt_of(self, keys):
        return next((k for k in keys if self.W[k].active), None)

    def order_changed(self):
        fin, base = self.final_keys(), self.base_keys()
        common_f = [k for k in fin if k in base]
        common_b = [k for k in base if k in fin]
        joined = [k for k in fin if k not in base and not self.W[k].is_new]
        return common_f != common_b or bool(joined)

    def changed_entries(self):
        return [e for e in self.W.values() if e.changed]

    def pending_count(self):
        return len(self.changed_entries()) + (1 if self.order_changed() else 0)

    @property
    def dirty(self):
        return self.pending_count() > 0

    def load_base(self):
        self.base_order, self.base_entries = self.nv.read_all()
        self.base_map = {e.num: e for e in self.base_entries}
        self.boot_current = self.nv.get_u16("BootCurrent")
        self.boot_next = self.nv.get_u16("BootNext")
        self.reset_draft()

    def reset_draft(self):
        self.W = {e.id: DraftEntry(e.id, e) for e in self.base_entries}
        self.w_order = self.base_keys()
        self.w_rest = [e.id for e in self.base_entries if e.num not in self.base_order]
        self.new_seq = 0
        self.vcache = {}

    def refresh(self, keep=None, force=False):
        if not force and self.dirty:
            if not messagebox.askyesno(_("Cambios sin guardar"),
                                       _("Hay cambios sin guardar. Si actualizas, se perderán.\n\n"
                                         "¿Actualizar de todos modos?"), icon="warning", parent=self.msg_parent()):
                return
        try:
            self.load_base()
        except NvramError as e:
            self.fail(_("No se pudo leer la NVRAM"), e)
            return
        self.reload_bm(update=False)
        self.render(keep)

    def reload_bm(self, update=True):
        ok, path, desc, _raw = get_bootmgr()
        self.bm = (ok, path, desc)
        if update:
            self.update_toggle()

    # ---- presentación ----
    def state_text(self, e):
        if e.deleted:
            s = _("Se eliminará al guardar")
        elif not e.active:
            s = _("Deshabilitada")
        elif e.key == self.dflt:
            s = _("Predeterminada")
        else:
            s = _("Habilitada")
        if e.is_new and not e.deleted:
            s = _("Nueva") + " · " + s
        elif e.changed and not e.deleted:
            s += " · " + _("modificada")
        if not e.is_new and e.num == self.boot_current:
            s += " · " + _("inició esta sesión")
        if e.num is not None and e.num == self.boot_next:
            s += " · " + _("próximo arranque")
        if not e.ok:
            s += " · " + _("ilegible")
        v = self.vcache.get(e.key)
        if v and v[0] == "fail":
            s += " · " + _("archivo no encontrado")
        return s

    def show_details(self):
        e = self.selected_entry()
        if not e:
            vals = {"name": _("Ninguna entrada seleccionada"), "id": "–", "path": "–", "device": "–", "state": "–"}
        else:
            vals = {"name": e.name, "id": e.id if not e.is_new else _("(nueva)"), "path": e.path or "–",
                    "device": e.device or "–", "state": self.state_text(e)}
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
        fin = self.final_keys()
        inorder = e.key in fin
        idx = fin.index(e.key) if inorder else -1
        alive = not e.deleted
        st("hab", alive and ((not e.active) or e.key in self.w_rest))
        st("deshab", alive and e.active)
        st("up", alive and inorder and idx > 0)
        st("down", alive and inorder and 0 <= idx < len(fin) - 1)
        st("default", alive and e.key != self.dflt)
        st("rename", alive and e.ok)
        st("delete", True)
        st("once", alive and (not e.is_new) and e.active)

    def update_savebar(self):
        n = self.pending_count()
        dirty = n > 0
        self.savebar.configure(style="SaveDirty.TFrame" if dirty else "Save.TFrame")
        if dirty:
            txt = _("1 cambio sin guardar") if n == 1 else _("%d cambios sin guardar") % n
            hint = _("Se ven en la tabla, pero no se aplican a la NVRAM hasta que pulses Guardar cambios.")
        else:
            txt, hint = _("Sin cambios pendientes"), ""
        self.lbl_pending.configure(style="SaveDirty.TLabel" if dirty else "Save.TLabel", text=txt)
        self.lbl_hint.configure(style="SaveDirtyHint.TLabel" if dirty else "SaveHint.TLabel", text=hint)
        for k in ("save", "discard"):
            self.btn[k].state(["!disabled"] if dirty else ["disabled"])
        self.root.title(APP_NAME + (" *" if dirty else ""))

    def render(self, keep=None):
        c, t = self.c, self.tree
        t.tag_configure("even", background=c["surface"], foreground=c["text"])
        t.tag_configure("odd", background=c["stripe"], foreground=c["text"])
        t.tag_configure("off_even", background=c["surface"], foreground=c["muted"])
        t.tag_configure("off_odd", background=c["stripe"], foreground=c["muted"])
        t.tag_configure("default", background=c["accent_soft"], foreground=c["text"], font=self.f_bold)
        t.tag_configure("dirty", background=c["dirty"], foreground=c["text"])
        t.tag_configure("dirty_default", background=c["dirty"], foreground=c["text"], font=self.f_bold)
        t.tag_configure("dirty_off", background=c["dirty"], foreground=c["muted"])
        t.tag_configure("deleted", background=c["danger_soft"], foreground=c["danger"], font=self.f_strike)
        if keep is None and t.selection():
            keep = t.selection()[0]
        fin = self.final_keys()
        self.dflt = self.dflt_of(fin)
        base_pos = {k: i for i, k in enumerate(self.base_keys())}
        rows = [self.W[k] for k in self.w_order] + [self.W[k] for k in self.w_rest]
        px = self.sc(14)
        self._refreshing = True
        try:
            t.delete(*t.get_children())
            for i, e in enumerate(rows):
                pos = str(fin.index(e.key) + 1) if e.key in fin else "–"
                stripe = "even" if i % 2 == 0 else "odd"
                v = self.vcache.get(e.key)
                if e.deleted:
                    tag, img = "deleted", self.icon("trash", c["danger"], px)
                else:
                    moved = e.key in base_pos and e.key in fin and fin.index(e.key) != base_pos[e.key]
                    changed = e.changed or moved
                    kind = "default" if e.key == self.dflt else ("off" if not e.active else "norm")
                    if changed:
                        tag = {"default": "dirty_default", "off": "dirty_off", "norm": "dirty"}[kind]
                    else:
                        tag = {"default": "default", "off": "off_" + stripe, "norm": stripe}[kind]
                    if v and v[0] == "fail":
                        img = self.icon("alert", c["warn"], px)
                    elif e.is_new:
                        img = self.icon("plus", c["accent"], px)
                    elif kind == "default":
                        img = self.icon("star", c["accent"], px)
                    elif kind == "off":
                        img = self.icon("ring", c["disabled"], px)
                    else:
                        img = self.icon("dot", c["success"], px)
                t.insert("", "end", iid=e.key, image=img, tags=(tag,),
                         values=(pos, e.name, e.id if not e.is_new else _("(nueva)"), e.path, e.device,
                                 self.state_text(e)))
            if keep and t.exists(keep):
                t.selection_set(keep)
                t.see(keep)
        finally:
            self._refreshing = False
        alive = [e for e in self.W.values() if not e.deleted]
        cur = self.base_map.get(self.boot_current)
        self.lbl_info.configure(text=_("%d entradas  ·  %d habilitadas  ·  esta sesión inició desde: %s") % (
            len(alive), sum(1 for e in alive if e.active), cur.name if cur else _("desconocido")))
        self.after_select()
        self.update_savebar()

    # ---- preparar cambios (solo en la tabla; no se escribe nada hasta Guardar) ----
    def staged(self, key, msg):
        self.render(key)
        self.set_status("%s %s" % (msg, _("Pulsa Guardar cambios para aplicarlo.")), "warn")

    def check_cached(self, e):
        """Verifica (una sola vez) que el .efi de la entrada exista. Solo informa: no bloquea."""
        if e.is_new or e.base is None:
            return None
        v = self.vcache.get(e.key)
        if v is None:
            self.busy(True)
            try:
                v = self.check_entry_target(e.base)
            finally:
                self.busy(False)
            self.vcache[e.key] = v
        if v[0] == "fail":
            self.lg("warn", _("%s: %s Si el archivo no existe, el firmware pasará a la siguiente entrada.") % (
                e.name, v[1]))
            self.toast(_("Archivo no encontrado: %s") % (e.path or e.name), "warn", 4200)
        return v

    def stage_move(self, delta):
        e = self.selected_entry()
        if not e or e.deleted or e.key not in self.w_order:
            return
        i = self.w_order.index(e.key)
        j = i + delta
        while 0 <= j < len(self.w_order) and self.W[self.w_order[j]].deleted:
            j += delta
        if not 0 <= j < len(self.w_order):
            return
        self.w_order[i], self.w_order[j] = self.w_order[j], self.w_order[i]
        self.staged(e.key, _("'%s' pasa a la posición %d.") % (e.name, self.final_keys().index(e.key) + 1))

    def stage_default(self):
        e = self.need_entry()
        if not e or e.deleted:
            return
        if e.key == self.dflt:
            self.toast(_("'%s' ya es la predeterminada.") % e.name, "info", 2600)
            return
        self.check_cached(e)
        e.active = True
        for lst in (self.w_rest, self.w_order):
            if e.key in lst:
                lst.remove(e.key)
        self.w_order.insert(0, e.key)
        self.staged(e.key, _("'%s' será la entrada predeterminada (posición 1).") % e.name)

    def stage_enable(self):
        e = self.need_entry()
        if not e or e.deleted:
            return
        if e.active and e.key not in self.w_rest:
            self.toast(_("'%s' ya está habilitada.") % e.name, "info", 2600)
            return
        before = self.dflt_of(self.final_keys())
        self.check_cached(e)
        e.active = True
        if e.key in self.w_rest:
            self.w_rest.remove(e.key)
            self.w_order.append(e.key)
        after = self.dflt_of(self.final_keys())
        msg = _("'%s' se habilitará.") % e.name
        if before != after:
            msg += " " + _("Ojo: pasará a ser la predeterminada, porque está por encima de la actual.")
        self.staged(e.key, msg)

    def stage_disable(self):
        e = self.need_entry()
        if not e or e.deleted:
            return
        if not e.active:
            self.toast(_("'%s' ya está deshabilitada.") % e.name, "info", 2600)
            return
        others = [k for k in self.final_keys() if k != e.key and self.W[k].active]
        if not others:
            messagebox.showwarning(_("No permitido"),
                                   _("No puedes deshabilitar la única entrada habilitada del orden de arranque: "
                                     "el equipo quedaría sin nada que iniciar."), parent=self.msg_parent())
            return
        before = self.dflt_of(self.final_keys())
        e.active = False
        after = self.dflt_of(self.final_keys())
        msg = _("'%s' se deshabilitará.") % e.name
        if before != after:
            msg += " " + _("La predeterminada pasará a ser '%s'.") % self.W[after].name
        self.staged(e.key, msg)

    def stage_rename(self):
        e = self.need_entry()
        if not e or e.deleted:
            return
        if not e.ok:
            messagebox.showerror(_("Entrada ilegible"), _("No se puede renombrar: %s") % e.error,
                                 parent=self.msg_parent())
            return
        new = self.ask_text(_("Renombrar entrada"), _("Nuevo nombre de la entrada:"), e.name)
        if not new or not new.strip() or new.strip() == e.name:
            return
        e.name = new.strip()
        self.staged(e.key, _("Se renombrará a '%s'.") % e.name)

    def stage_delete(self):
        e = self.need_entry()
        if not e:
            return
        if e.deleted:                                   # segunda pulsación: deshacer
            e.deleted = False
            self.staged(e.key, _("Se canceló la eliminación de '%s'.") % e.name)
            return
        others = [k for k in self.final_keys() if k != e.key and self.W[k].active]
        if not others:
            messagebox.showwarning(_("No permitido"),
                                   _("No puedes eliminar la única entrada habilitada del orden de arranque."),
                                   parent=self.msg_parent())
            return
        if e.is_new:                                    # una entrada nueva sin guardar simplemente desaparece
            for lst in (self.w_order, self.w_rest):
                if e.key in lst:
                    lst.remove(e.key)
            del self.W[e.key]
            self.render()
            self.set_status(_("Se quitó la entrada nueva '%s'.") % e.name, "info")
            return
        e.deleted = True
        msg = _("'%s' se eliminará.") % e.name
        if e.path.lower() == WIN_PATH.lower():
            msg += " " + _("ATENCIÓN: es el Administrador de arranque de Windows.")
        self.staged(e.key, msg)

    def new_entry(self):
        NewEntryDialog(self)

    def stage_new_entry(self, name, path, first):
        parent = self.msg_parent()
        hd = self.find_esp_hd_node()
        if not hd:
            messagebox.showerror(_("Error"), _("No se pudo determinar la partición del sistema EFI (ESP)."),
                                 parent=parent)
            return False
        self.busy(True)
        try:
            st, msg = self.check_system_esp_file(path)
        finally:
            self.busy(False)
        if not self.preflight_block(_("Nueva entrada"), st, msg, parent):
            return False
        self.new_seq += 1
        e = DraftEntry("new:%d" % self.new_seq)
        e.name, e.path, e.hd_node = name, path, hd
        e.device = describe_nodes([(4, 1, hd[4:])])[1]
        self.W[e.key] = e
        if first:
            self.w_order.insert(0, e.key)
        else:
            self.w_order.append(e.key)
        self.staged(e.key, _("Se creará la entrada '%s'.") % name)
        return True

    # ---- guardar / descartar ----
    def summary_rows(self):
        rows, fin, bf = [], self.final_keys(), self.base_keys()
        for e in self.changed_entries():
            if e.is_new:
                rows.append((_("Nueva entrada"), _("(no existe)"), "%s · %s" % (e.name, e.path)))
            elif e.deleted:
                rows.append((e.base.name, "%s · %s" % (e.id, e.base.path or "–"), _("(se elimina)")))
            else:
                if e.name != e.base.name:
                    rows.append((_("Nombre de %s") % e.id, e.base.name, e.name))
                if e.active != e.base.active:
                    rows.append((_("Estado de '%s'") % e.name,
                                 _("Habilitada") if e.base.active else _("Deshabilitada"),
                                 _("Habilitada") if e.active else _("Deshabilitada")))
        if fin != bf:
            rows.append((_("Orden de arranque"), "  →  ".join(self.base_name(k) for k in bf) or _("(vacío)"),
                         "  →  ".join(self.W[k].name for k in fin)))
        d0 = next((k for k in bf if self.base_map[int(k[4:], 16)].active), None)
        d1 = self.dflt_of(fin)
        if d0 != d1:
            rows.append((_("Entrada predeterminada"), self.base_name(d0) if d0 else _("ninguna"),
                         self.W[d1].name if d1 else _("ninguna")))
        return rows

    def snapshot_differs(self, order_now, cur):
        if order_now != self.base_order or {e.num for e in cur} != set(self.base_map):
            return True
        return any(self.base_map[e.num].raw != e.raw or self.base_map[e.num].attrs != e.attrs for e in cur)

    def save_changes(self):
        """Aplica a la NVRAM todos los cambios preparados (un solo resumen antes/después)."""
        if not self.dirty:
            self.toast(_("No hay cambios pendientes."), "info", 2200)
            return True
        parent = self.msg_parent()
        try:
            order_now, cur = self.nv.read_all()
        except NvramError as ex:
            self.fail(_("No se pudo leer la NVRAM"), ex)
            return False
        if self.snapshot_differs(order_now, cur):
            messagebox.showerror(_("La NVRAM cambió"),
                                 _("La NVRAM cambió desde que se cargó la lista (otro programa o Windows pudo "
                                   "modificarla).\n\nPulsa Descartar para recargar el estado actual y vuelve a "
                                   "preparar tus cambios. No se aplicó nada."), parent=parent)
            return False
        fin = self.final_keys()
        if not fin or not any(self.W[k].active for k in fin):
            messagebox.showwarning(_("No permitido"),
                                   _("Debe quedar al menos una entrada habilitada en el orden de arranque."),
                                   parent=parent)
            return False
        notes = [("ok", _("Antes de guardar se crea una copia de seguridad de la NVRAM."))]
        self.busy(True)
        try:
            for e in self.changed_entries():
                if e.is_new and not e.deleted:
                    st, msg = self.check_system_esp_file(e.path)
                    if not self.preflight_block(_("Guardar cambios"), st, msg, parent):
                        return False
                    notes.append(self.note_from(st, msg))
                elif e.deleted and e.path.lower() == WIN_PATH.lower():
                    notes.append(("warn", _("ATENCIÓN: se eliminará el Administrador de arranque de Windows.")))
            dk = self.dflt_of(fin)
            if dk and not self.W[dk].is_new and dk != next((k for k in self.base_keys()
                                                            if self.base_map[int(k[4:], 16)].active), None):
                v = self.check_cached(self.W[dk])
                if v:
                    notes.append(self.note_from(*v))
        finally:
            self.busy(False)
        n = self.pending_count()
        ok, _chk = self.confirm_change(_("Guardar cambios"), _("Aplicar %d cambios a la NVRAM") % n,
                                       self.summary_rows(), notes, ok_text=_("Guardar cambios"))
        if not ok:
            return False
        try:
            p = self.make_backup("antes_de_guardar")
            self._backed_up = True
            self.lg("info", _("Copia de seguridad de la NVRAM creada: %s") % p)
        except Exception as ex:
            self.lg("err", _("No se pudo crear la copia de seguridad: %s") % ex)
            if not messagebox.askyesno(_("Sin copia de seguridad"),
                                       _("No se pudo crear la copia de seguridad:\n%s\n\n"
                                         "¿Continuar de todos modos?") % ex, icon="warning", parent=parent):
                return False
        return self.apply_draft()

    def apply_draft(self):
        """Escribe los cambios como una unidad: si un paso falla, se deshacen los anteriores."""
        nv = self.nv
        undo, assigned, expect = [], {}, {}
        used = set(self.base_map) | set(self.base_order)
        old_order, old_next = nv.get("BootOrder"), nv.get("BootNext")
        nums = []
        order_written = False
        try:
            for e in self.W.values():                                    # 1) entradas existentes modificadas
                if e.is_new or e.deleted or not e.changed:
                    continue
                b = e.base
                raw = set_active_bit(b.raw, e.active)
                if e.name != b.name:
                    raw = build_load_option(struct.unpack_from("<I", raw, 0)[0], e.name, b.fpl, b.optional)
                nv.set(e.id, raw, b.attrs)
                undo.append(lambda b=b: nv.set(b.id, b.raw, b.attrs))
                expect[e.id] = raw
                self.lg("nv", "%s — %s" % (e.id, _("modificada")))
            news = sorted((e for e in self.W.values() if e.is_new and not e.deleted),
                          key=lambda x: int(x.key.split(":")[1]))
            for e in news:                                               # 2) entradas nuevas
                n = 0
                while n in used:
                    n += 1
                if n > 0xFFFF:
                    raise NvramError(_("No hay números de entrada libres."))
                used.add(n)
                assigned[e.key] = n
                var = "Boot%04X" % n
                raw = build_load_option(LOAD_OPTION_ACTIVE if e.active else 0, e.name,
                                        build_file_path_list(e.hd_node, e.path))
                nv.set(var, raw)
                undo.append(lambda var=var: nv.delete(var))
                expect[var] = raw
                self.lg("nv", "%s %s: '%s' → %s" % (var, _("creada"), e.name, e.path))
            nums = [assigned.get(k, self.W[k].num) for k in self.final_keys()]   # 3) orden de arranque
            if nums != self.base_order:
                def undo_order():
                    if old_order:
                        nv.set("BootOrder", old_order[0], old_order[1])
                    else:
                        nv.delete("BootOrder")
                nv.set_boot_order(nums)
                undo.append(undo_order)
                order_written = True
                self.lg("nv", "BootOrder: %s" % " ".join("%04X" % n for n in nums))
            for e in self.W.values():                                    # 4) eliminar (al final)
                if e.deleted and not e.is_new:
                    b = e.base
                    nv.delete(b.id)
                    undo.append(lambda b=b: nv.set(b.id, b.raw, b.attrs))
                    self.lg("nv", "%s %s" % (b.id, _("eliminada")))
                    if old_next and self.boot_next == b.num:
                        nv.delete("BootNext")
                        undo.append(lambda: nv.set("BootNext", old_next[0], old_next[1]))
        except NvramError as ex:
            self.lg("err", "%s: %s" % (_("Falló el guardado"), ex))
            self.rollback(undo)
            self.fail(_("Error al guardar los cambios"), "%s\n\n%s" % (ex, _("Se revirtieron los pasos ya aplicados.")))
            self.refresh(force=True)
            return False
        bad = [name for name, raw in expect.items() if not self.verify(name, raw)]
        if order_written and not self.verify("BootOrder", struct.pack("<%dH" % len(nums), *nums)):
            bad.append("BootOrder")
        self.refresh(force=True)
        if bad:
            self.lg("warn", _("El firmware no conservó estos cambios: %s") % ", ".join(bad))
            messagebox.showwarning(_("Verifica el resultado"),
                                   _("Se escribieron los cambios, pero el firmware no conservó: %s") % ", ".join(bad),
                                   parent=self.msg_parent())
        else:
            self.lg("ok", _("Cambios guardados en la NVRAM."))
            self.toast(_("Cambios guardados."), "success")
        return True

    def discard_changes(self):
        if not self.dirty:
            return
        if not messagebox.askyesno(_("Descartar cambios"),
                                   _("¿Descartar los cambios preparados y volver al estado actual de la NVRAM?"),
                                   parent=self.msg_parent()):
            return
        keep = self.tree.selection()[0] if self.tree.selection() else None
        self.reset_draft()
        self.render(keep)
        self.set_status(_("Cambios descartados."), "info")

    def ask_unsaved(self):
        d = UnsavedDialog(self, self.pending_count())
        d.show()
        self.root.wait_window(d)
        return d.choice or "cancel"

    def on_close(self):
        if self.dirty:
            r = self.ask_unsaved()
            if r == "cancel":
                return
            if r == "save" and not self.save_changes():
                return
        try:
            zoomed = self.root.state() == "zoomed"
            if not zoomed:
                self.settings["geometry"] = [self.root.winfo_width(), self.root.winfo_height(),
                                             self.root.winfo_x(), self.root.winfo_y()]
            self.settings["zoomed"] = zoomed
            self.settings["theme"] = self.theme
            self.settings["lang"] = LANG[0]
            save_settings(self.settings)
        except Exception:
            pass
        self.release_esp()
        self.root.destroy()

    # ---- copias de seguridad ----
    def make_backup(self, tag):
        order, entries = self.nv.read_all()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(backup_dir(), "boot_%s_%s.json" % (stamp, tag))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot_json(order, entries), f, indent=1)
        self.prune_backups()
        return path

    @staticmethod
    def prune_backups(keep=40):
        """Conserva las últimas copias automáticas; las manuales y las de restauración no se borran."""
        try:
            files = sorted(glob.glob(os.path.join(backup_dir(), "boot_*_antes_de_guardar.json")))
            for f in files[:-keep]:
                os.remove(f)
        except OSError:
            pass

    def manual_backup(self):
        try:
            p = self.make_backup("manual")
        except Exception as ex:
            self.fail(_("Copia de seguridad"), ex)
            return
        self.lg("ok", _("Copia de seguridad de la NVRAM creada: %s") % p)
        self.toast(_("Copia de la NVRAM guardada."), "success")

    def export_bcd(self, tag="manual"):
        """bcdedit /export: copia del almacén BCD de Windows. Lanza RuntimeError si falla."""
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(backup_dir(), "bcd_%s_%s.bcd" % (stamp, tag))
        rc, out = run([BCDEDIT, "/export", path])
        self.logmsg("> bcdedit /export %s\n%s" % (path, out.strip()))
        if rc != 0:
            raise RuntimeError(out.strip() or _("bcdedit /export devolvió el código %d.") % rc)
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            raise RuntimeError(_("El archivo exportado no existe o está vacío: %s") % path)
        self._bcd_exported = True
        return path

    def manual_bcd_export(self):
        try:
            p = self.export_bcd("manual")
        except Exception as ex:
            self.fail(_("No se pudo exportar el BCD"), ex)
            return
        self.lg("ok", _("BCD exportado: %s") % p)
        self.lg("info", _("Para restaurarlo (consola de administrador): bcdedit /import \"%s\"") % p)
        self.toast(_("BCD exportado."), "success")

    def apply_saved(self, saved):
        """Escribe en la NVRAM las variables de una copia y elimina las Boot#### que no estén en ella."""
        _o, cur = self.nv.read_all()
        for name in sorted(saved, key=lambda n: n == "BootOrder"):   # BootOrder al final
            raw, attrs = saved[name]
            self.nv.set(name, raw, attrs)
            self.lg("nv", _("%s restaurada (%d bytes)") % (name, len(raw)))
        for e in cur:
            if e.id not in saved:
                self.nv.delete(e.id)
                self.lg("nv", _("%s eliminada (no estaba en la copia)") % e.id)

    def restore_backup(self):
        if self.dirty:
            self.toast(_("Guarda o descarta primero los cambios pendientes."), "warn", 3600)
            return
        p = filedialog.askopenfilename(initialdir=backup_dir(), title=_("Selecciona una copia de BootScribe"),
                                       filetypes=[(_("Copia BootScribe"), "*.json"), (_("Todos"), "*.*")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                saved = parse_backup(json.load(f))
        except Exception as ex:
            self.fail(_("Copia no válida"), ex)
            return
        try:
            order_now, current = self.nv.read_all()
        except NvramError as ex:
            self.fail(_("No se pudo leer la NVRAM"), ex)
            return
        extra = [e for e in current if e.id not in saved]
        cur_names = {e.num: e.name for e in current}
        names = {}
        for name, (raw, attrs) in saved.items():
            if name != "BootOrder":
                names[int(name[4:], 16)] = BootEntry(int(name[4:], 16), raw, attrs).name
        sraw = saved["BootOrder"][0]
        saved_order = list(struct.unpack("<%dH" % (len(sraw) // 2), sraw))
        rows = [(_("Entradas"), str(len(current)), str(len(saved) - 1)),
                (_("Orden de arranque"), "  →  ".join(cur_names.get(n, "Boot%04X" % n) for n in order_now),
                 "  →  ".join(names.get(n, "Boot%04X" % n) for n in saved_order)),
                (_("Entradas que no están en la copia"), ", ".join(e.name for e in extra) or _("ninguna"),
                 _("se eliminan") if extra else "–")]
        notes = [("ok", _("Antes de restaurar se guarda una copia del estado actual.")),
                 ("warn", _("Si algo falla a mitad de camino, BootScribe intenta volver al estado actual."))]
        ok, _chk = self.confirm_change(_("Restaurar copia"),
                                       _("Restaurar la NVRAM desde '%s'") % os.path.basename(p),
                                       rows, notes, danger=True, ok_text=_("Restaurar"))
        if not ok:
            return
        try:
            safety = self.make_backup("antes_de_restaurar")
            self.lg("info", _("Copia del estado actual: %s") % safety)
            safety_map = parse_backup(snapshot_json(order_now, current))
        except Exception as ex:
            self.fail(_("No se pudo guardar la copia del estado actual"), ex)
            return
        try:
            self.apply_saved(saved)
        except NvramError as ex:
            self.lg("err", _("Falló la restauración: %s. Volviendo al estado anterior…") % ex)
            try:
                self.apply_saved(safety_map)
                self.lg("info", _("Se volvió al estado anterior a la restauración."))
            except NvramError as ex2:
                self.lg("err", _("No se pudo volver al estado anterior: %s. Usa la copia: %s") % (ex2, safety))
            self.fail(_("Error al restaurar"), ex)
            self.refresh(force=True)
            return
        self.lg("ok", _("Copia restaurada."))
        self.refresh(force=True)
        self.toast(_("Copia restaurada correctamente."), "success")

    # ---- confirmación antes/después y verificaciones previas ----
    def confirm_change(self, title, headline, rows, notes=(), danger=False, checkbox=None,
                       ok_text=None):
        """Muestra la ventana 'actual → propuesto'. Devuelve (aceptado, casilla_marcada)."""
        d = ChangeDialog(self, title, headline, rows, notes, danger, checkbox, ok_text or _("Aplicar cambio"))
        d.show()
        self.root.wait_window(d)
        return d.result, d.checked

    def msg_parent(self):
        d = getattr(self, "_dlg", None)
        try:
            if d is not None and d.winfo_exists():
                return d
        except tk.TclError:
            pass
        return self.root

    @staticmethod
    def note_from(status, msg):
        return ("ok", msg) if status == "ok" else ("warn", msg)

    def preflight_block(self, titulo, status, msg, parent=None):
        """Aborta (False) si la verificación previa falló. 'na' (no verificable) no bloquea."""
        if status != "fail":
            return True
        self.lg("err", "%s: %s %s" % (titulo, msg, _("No se aplicó ningún cambio.")))
        messagebox.showerror(_("Verificación previa fallida"), "%s\n\n%s" % (msg, _("No se aplicó ningún cambio.")),
                             parent=parent or self.msg_parent())
        return False

    def check_system_esp_file(self, path):
        """Monta la ESP del sistema y comprueba que el .efi exista y sea válido. -> ('ok'|'fail', mensaje)"""
        try:
            letter = self.ensure_esp()
        except Exception as ex:
            return "fail", _("La partición del sistema EFI (ESP) no está accesible: %s") % ex
        try:
            ok, msg = validate_efi_file(letter + ":" + path)
        finally:
            self.release_esp()
        if ok:
            return "ok", _("Verificado en la ESP: %s (%s).") % (path, msg)
        return "fail", _("El archivo %s no es válido en la ESP: %s.") % (path, msg)

    def open_partition(self, guid):
        """Da acceso temporal a una partición por su GUID. -> (letra, limpieza, error)."""
        script = ("$g='{%s}'; $p = Get-Partition | Where-Object { $_.Guid -ieq $g } | Select-Object -First 1; "
                  "if ($p) { [pscustomobject]@{found=$true; disk=[int]$p.DiskNumber; "
                  "part=[int]$p.PartitionNumber; letter=[string]$p.DriveLetter} | ConvertTo-Json -Compress } "
                  "else { '{\"found\":false}' }") % guid
        info, err = ps_json(script)
        if info is None:
            return None, None, _("no se pudo consultar las particiones (%s)") % err
        if not info.get("found"):
            return None, None, _("la partición {%s} no está conectada (disco desconectado o partición "
                                 "eliminada)") % guid[:8]
        if info.get("letter"):
            return info["letter"], (lambda: None), ""
        letter = next((l for l in "STUVWXYZRQPONMLKJIHGFED" if not os.path.exists(l + ":\\")), None)
        if not letter:
            return None, None, _("no hay letras de unidad libres")
        base = "-DiskNumber %d -PartitionNumber %d -AccessPath '%s:'" % (info["disk"], info["part"], letter)
        rc, out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Add-PartitionAccessPath " + base])
        self.logmsg("> Add-PartitionAccessPath %s\n%s" % (base, out.strip()))

        def cleanup():
            r, o = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Remove-PartitionAccessPath " + base])
            self.logmsg("> Remove-PartitionAccessPath %s\n%s" % (base, o.strip()))

        if rc != 0 or not os.path.exists(letter + ":\\"):
            if rc == 0:
                cleanup()
            return None, None, _("no se pudo montar la partición (%s)") % (out.strip() or _("sin detalle"))
        return letter, cleanup, ""

    def check_entry_target(self, e):
        """Comprueba que el archivo .efi de una entrada exista en su partición.
        -> ('ok' verificado | 'na' no verificable | 'fail' inaccesible o inexistente, mensaje)"""
        if not e.path or not e.hd_node:
            return "na", _("Entrada sin archivo en disco (USB, red u otro dispositivo): no se puede verificar.")
        guid = hd_guid(e.hd_node)
        if not guid:
            return "na", _("Partición MBR: no se puede localizar por GUID, el archivo no se verificó.")
        letter, cleanup, err = self.open_partition(guid)
        if not letter:
            return "fail", _("La partición de destino no está accesible: %s.") % err
        try:
            ok, msg = validate_efi_file(letter + ":" + e.path)
        finally:
            cleanup()
        if ok:
            return "ok", _("Verificado en su partición: %s (%s).") % (e.path, msg)
        return "fail", _("El archivo %s no es válido en la partición de destino: %s.") % (e.path, msg)

    def verify(self, name, data):
        v = self.nv.get(name)
        return bool(v) and v[0] == bytes(data)

    def rollback(self, undo):
        """Deshace, en orden inverso, los pasos ya aplicados cuando uno posterior falla."""
        for fn in reversed(undo):
            try:
                fn()
            except Exception as ex:
                self.lg("err", _("No se pudo revertir un paso: %s") % ex)
        if undo:
            self.lg("info", _("Se revirtieron los pasos ya aplicados para no dejar un estado a medias."))

    # ---- acciones inmediatas ----
    def boot_once(self):
        """Fija BootNext: el próximo arranque (solo uno) usará esta entrada."""
        e = self.need_entry()
        if not e:
            return
        if self.dirty:
            self.toast(_("Guarda o descarta primero los cambios pendientes."), "warn", 3600)
            return
        if e.is_new or e.deleted or not e.active:
            self.toast(_("Habilita la entrada antes de usarla para arrancar."), "warn", 3000)
            return
        self.busy(True)
        try:
            st, msg = self.check_entry_target(e.base)
        finally:
            self.busy(False)
        prev = self.boot_next
        cur_names = {x.num: x.name for x in self.base_entries}
        rows = [(_("Próximo arranque (BootNext)"),
                 cur_names.get(prev, "Boot%04X" % prev) if prev is not None else
                 _("ninguno (se usa el orden normal)"), e.name),
                (_("Orden de arranque permanente"), "  →  ".join(self.W[k].name for k in self.final_keys()),
                 _("sin cambios"))]
        notes = [self.note_from(st, msg), ("warn", _("El equipo se reiniciará de inmediato."))]
        ok, _chk = self.confirm_change(_("Arrancar una vez"),
                                       _("Arrancar solo la próxima vez desde '%s'") % e.name, rows, notes,
                                       ok_text=_("Aplicar y reiniciar"))
        if not ok:
            return
        try:
            self.nv.set_u16("BootNext", e.num)
        except NvramError as ex:
            self.fail(_("No se pudo fijar BootNext"), ex)
            return
        self.lg("nv", "BootNext = %s (%s)" % (e.id, e.name))
        rc, out = run(["shutdown", "/r", "/t", "0"])
        if rc != 0:
            try:                                           # no dejar BootNext fijado si no se reinició
                if prev is None:
                    self.nv.delete("BootNext")
                else:
                    self.nv.set_u16("BootNext", prev)
            except NvramError as ex2:
                self.lg("err", _("No se pudo revertir BootNext: %s") % ex2)
            self.fail(_("No se pudo reiniciar"), out.strip() or _("error desconocido"))
            self.refresh(force=True)

    def reboot_fw(self):
        extra = ""
        if self.dirty:
            extra = "\n\n" + _("Tienes cambios sin guardar: se perderán.")
        if messagebox.askyesno(_("Reiniciar"), _("El equipo se reiniciará de inmediato en la configuración UEFI.\n"
                                                 "Guarda tu trabajo. ¿Continuar?") + extra, icon="warning",
                               parent=self.msg_parent()):
            rc, out = run(["shutdown", "/r", "/fw", "/t", "0"])
            if rc != 0:
                self.fail(_("No se pudo reiniciar al firmware"), out.strip() or _("error desconocido"))

    # ---- ESP ----
    def ensure_esp(self):
        if self.esp_letter and os.path.exists(self.esp_letter + ":\\"):
            return self.esp_letter
        for l in "STUVWXYZRQPONMLKJIHGFED":
            if not os.path.exists(l + ":\\"):
                rc, out = run(["mountvol", l + ":", "/S"])
                self.logmsg("> mountvol %s: /S\n%s" % (l, out.strip()))
                if rc != 0:
                    raise RuntimeError(_("No se pudo montar la partición del sistema EFI:\n") + out.strip())
                self.esp_letter, self.esp_mounted_by_us = l, True
                return l
        raise RuntimeError(_("No hay letras de unidad libres para montar la ESP."))

    def release_esp(self):
        if self.esp_letter and self.esp_mounted_by_us:
            rc, out = run(["mountvol", self.esp_letter + ":", "/D"])
            self.logmsg("> mountvol %s: /D\n%s" % (self.esp_letter, out.strip()))
        self.esp_letter, self.esp_mounted_by_us = None, False

    def find_esp_hd_node(self):
        """Nodo 'disco duro' de la partición ESP. Primero se copia el de la entrada de Windows
        (lo creó el propio firmware); si no existe, se consulta con PowerShell."""
        for e in self.base_entries:
            if e.hd_node and e.path.lower() == WIN_PATH.lower():
                return e.hd_node
        ps = ("Get-Partition | Where-Object { $_.GptType -eq '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' } | "
              "ForEach-Object { $d = Get-Disk -Number $_.DiskNumber; [pscustomobject]@{ n=$_.PartitionNumber; "
              "off=[int64]$_.Offset; size=[int64]$_.Size; guid=[string]$_.Guid; ls=[int]$d.LogicalSectorSize; "
              "sys=[bool]$_.IsSystem } } | ConvertTo-Json -Compress")
        data, err = ps_json(ps)
        if data is None:
            self.lg("warn", _("No se pudo consultar la ESP con PowerShell: %s") % err)
            return None
        try:
            parts = data if isinstance(data, list) else [data]
            parts.sort(key=lambda p: not p.get("sys"))
            p = parts[0]
            ls = int(p["ls"]) or 512
            return build_hd_node(int(p["n"]), int(p["off"]) // ls, int(p["size"]) // ls, p["guid"])
        except Exception as ex:
            self.lg("warn", _("No se pudo interpretar la partición ESP: %s") % ex)
            return None

    # ---- {bootmgr} (vía bcdedit) ----
    def update_toggle(self):
        """Actualiza texto, icono y rol del botón según el {bootmgr} leído (en caché, sin lanzar bcdedit)."""
        ok, path, desc = self.bm
        b = self.btn["bm"]
        if not ok:
            b.configure(text=" " + _(TXT_BLINDAR_GENERIC))
            self.set_button_icon(b, "shield", "text")
            return
        if is_windows_path(path) or not path:
            e = self.selected_entry()
            if e and e.path.strip() and not is_windows_path(e.path):
                nm = e.name if len(e.name) <= 11 else e.name[:10] + "…"
                b.configure(text=" " + _("Blindar {bootmgr}: %s") % nm)
            else:
                b.configure(text=" " + _(TXT_BLINDAR_GENERIC))
            self.set_button_icon(b, "shield", "text")
        else:
            b.configure(text=" " + _(TXT_LIBERAR))
            self.set_button_icon(b, "undo", "text")

    def bcd(self, args):
        """Ejecuta bcdedit capturando salida y código de retorno (nunca lanza excepciones)."""
        rc, out = run([BCDEDIT] + args)
        self.logmsg("> bcdedit " + " ".join(args) + "\n" + out.strip())
        return rc == 0, out

    def toggle_bootmgr(self):
        """Estado A (ruta de Windows): blinda {bootmgr} con la entrada/archivo .efi elegido.
        Estado B (ruta alternativa): restaura {bootmgr} a la ruta estándar de Windows.
        Nota: modifica el BCD de Windows; algunos firmwares no lo respetan."""
        ok, path, desc, raw = get_bootmgr()
        self.logmsg("> bcdedit /enum {bootmgr}\n" + raw.strip())
        if not ok:
            self.fail(_("No se pudo leer {bootmgr}"), raw.strip() or _("error desconocido"))
            return
        if is_windows_path(path) or not path:
            self.blindar_con_seleccion(path, desc)
        else:
            self.liberar_bootmgr(path, desc)

    def blindar_con_seleccion(self, old_path, old_desc):
        e = self.selected_entry()
        if e and e.path.strip():
            target_path, target_name = norm_path(e.path), e.name
        else:
            if not messagebox.askokcancel(_("Elegir archivo .efi"),
                                          _("No hay una entrada con ruta .efi seleccionada.\n"
                                            "¿Quieres elegir un archivo .efi de la partición EFI?")):
                return
            try:
                letter = self.ensure_esp()
            except Exception as ex:
                messagebox.showerror(_("Error"), str(ex))
                return
            p = filedialog.askopenfilename(initialdir=letter + ":\\EFI",
                                           title=_("Selecciona el gestor .efi (rEFInd, GRUB, etc.)"),
                                           filetypes=[(_("Aplicación EFI"), "*.efi"), (_("Todos"), "*.*")])
            self.release_esp()
            if not p:
                return
            target_path = norm_path(p)
            base = os.path.splitext(os.path.basename(target_path))[0]
            target_name = self.ask_text(_("Descripción"), _("Descripción para {bootmgr}:"), base)
            if not target_name or not target_name.strip():
                return
            target_name = target_name.strip()
        if is_windows_path(target_path):
            self.toast(_("Esa es la ruta estándar de Windows. Selecciona otra entrada (rEFInd, GRUB, etc.)."),
                       "info", 4200)
            return
        self.busy(True)
        try:
            st, msg = self.check_system_esp_file(target_path)
        finally:
            self.busy(False)
        if not self.preflight_block(_("Blindaje de {bootmgr}"), st, msg):
            return
        self.confirm_and_apply_bootmgr(_("Blindaje de {bootmgr}"), old_path, old_desc, target_path, target_name,
                                       self.note_from(st, msg))

    def liberar_bootmgr(self, old_path, old_desc):
        self.busy(True)
        try:
            st, msg = self.check_system_esp_file(WIN_PATH)
        finally:
            self.busy(False)
        if not self.preflight_block(_("Restauración de {bootmgr}"), st,
                                    msg + " " + _("Sin ese archivo Windows no podría arrancar; repara el arranque "
                                                  "antes (por ejemplo con bcdboot).")):
            return
        self.confirm_and_apply_bootmgr(_("Restauración de {bootmgr}"), old_path, old_desc, WIN_PATH, WIN_DESC,
                                       self.note_from(st, msg))

    def confirm_and_apply_bootmgr(self, titulo, old_path, old_desc, new_path, new_desc, pre_note):
        rows = [(_("Ruta de {bootmgr}"), old_path or _("(sin ruta)"), new_path),
                (_("Descripción"), old_desc or _("(sin descripción)"), new_desc)]
        notes = [pre_note]
        checkbox = None
        if not self._bcd_exported:
            checkbox = _("Exportar una copia del BCD antes de aplicar (recomendado)")
            notes.append(("warn", _("Aún no hay copia del BCD en esta sesión. Si algo sale mal, podrás "
                                    "restaurarla con bcdedit /import.")))
        else:
            notes.append(("ok", _("Ya exportaste una copia del BCD en esta sesión.")))
        ok, checked = self.confirm_change(titulo, titulo, rows, notes, checkbox=checkbox,
                                          ok_text=_("Aplicar cambio"))
        if not ok:
            return
        if checkbox and checked:
            try:
                p = self.export_bcd("antes_de_cambiar")
                self.lg("info", _("BCD exportado antes del cambio: %s") % p)
            except Exception as ex:
                self.fail(_("No se pudo exportar el BCD"), "%s\n\n%s" % (ex, _("No se aplicó ningún cambio.")))
                return
        self.aplicar_bootmgr(new_path, new_desc, titulo, old_path, old_desc)

    def aplicar_bootmgr(self, path, desc, titulo, old_path, old_desc):
        """Aplica ruta + descripción como una unidad: si algo falla o la verificación posterior no
        coincide, se vuelve a la ruta y descripción anteriores para no dejar {bootmgr} a medias."""
        self.logmsg("%s…" % titulo)
        problem = None
        try:
            ok1, out1 = self.bcd(["/set", "{bootmgr}", "path", path])
            if not ok1:
                self.fail(titulo, _("No se pudo cambiar la ruta; no se modificó nada.\n%s") % out1.strip())
                self.reload_bm()
                return False
            ok2, out2 = self.bcd(["/set", "{bootmgr}", "description", desc])
            if not ok2:
                problem = _("No se pudo cambiar la descripción: %s") % (out2.strip() or _("sin detalle"))
            else:
                ok3, p3, _d3, _raw3 = get_bootmgr()
                if not ok3 or norm_path(p3).lower() != norm_path(path).lower():
                    problem = _("La verificación posterior no coincide (ruta actual: %s).") % (
                        p3 or _("desconocida"))
        except Exception as ex:                      # cualquier fallo inesperado también se revierte
            problem = _("Error inesperado: %s") % ex
        if problem is None:
            self.lg("ok", "%s: {bootmgr} → %s (%s)." % (titulo, path, desc))
            self.toast(_("%s completado.") % titulo, "success")
            self.reload_bm()
            return True
        self.lg("err", "%s: %s %s" % (titulo, problem, _("Revirtiendo al estado anterior…")))
        r1, _o1 = self.bcd(["/set", "{bootmgr}", "path", old_path])
        r2, _o2 = self.bcd(["/set", "{bootmgr}", "description", old_desc]) if old_desc else (True, "")
        ok4, p4, _d4, _raw4 = get_bootmgr()
        reverted = r1 and r2 and ok4 and norm_path(p4).lower() == norm_path(old_path).lower()
        if reverted:
            self.lg("info", _("{bootmgr} volvió a su estado anterior (%s).") % old_path)
            messagebox.showerror(titulo, "%s\n\n%s" % (problem, _("Se revirtió al estado anterior; no quedó nada "
                                                                  "a medias.")), parent=self.msg_parent())
        else:
            self.lg("err", _("No se pudo revertir {bootmgr} automáticamente."))
            messagebox.showerror(titulo, _("%s\n\nNo se pudo revertir automáticamente. Restaura el BCD con:\n"
                                           "bcdedit /import \"<archivo .bcd exportado>\"\n"
                                           "(o vuelve a poner la ruta a %s).") % (problem, old_path or WIN_PATH),
                                 parent=self.msg_parent())
        self.reload_bm()
        return False


# ----------------------------- Traducciones al inglés -----------------------------
# Las claves son los textos originales en español; si falta una, se muestra el original.
EN.update({
    # --- barra de herramientas, captions y cabeceras ---
    "ESTADO": "STATE", "PRIORIDAD": "PRIORITY", "ARRANQUE": "BOOT", "GESTIÓN": "MANAGE",
    "RESPALDO": "BACKUP", "AVANZADO": "ADVANCED", "REGISTRO": "LOG",
    "ORDEN": "ORDER", "NOMBRE": "NAME", "ID": "ID", "RUTA EFI": "EFI PATH", "DISPOSITIVO": "DEVICE",
    "ENTRADA SELECCIONADA": "SELECTED ENTRY", "ACTUAL": "CURRENT", "PROPUESTO": "PROPOSED",
    "Administrador de arranque UEFI": "UEFI boot manager",
    "UEFI · Administrador": "UEFI · Administrator",
    "Habilitar entrada": "Enable entry", "Deshabilitar entrada": "Disable entry",
    "Subir": "Move up", "Bajar": "Move down", "Hacer predeterminada": "Make default",
    "Arrancar una vez": "Boot once", "Reiniciar a UEFI": "Restart to UEFI",
    "Nueva entrada": "New entry", "Renombrar": "Rename", "Eliminar": "Delete",
    "Copia NVRAM": "NVRAM backup", "Exportar BCD": "Export BCD", "Restaurar copia…": "Restore backup…",
    "Blindar {bootmgr} con selección": "Lock {bootmgr} to selection",
    "Blindar {bootmgr}: %s": "Lock {bootmgr}: %s",
    "Restaurar / Liberar {bootmgr}": "Restore / Release {bootmgr}",
    "Guardar cambios": "Save changes", "Descartar": "Discard",
    "Copiar": "Copy", "Limpiar": "Clear", "Aceptar": "OK", "Cancelar": "Cancel",
    "Examinar ESP…": "Browse ESP…", "Añadir a la lista": "Add to list",
    "Guardar y salir": "Save and exit", "Salir sin guardar": "Exit without saving",
    "Listo.": "Ready.",
    # --- ayudas (tooltips) ---
    "Activa la entrada (atributo ACTIVE del firmware). No cambia su prioridad.":
        "Activates the entry (firmware ACTIVE attribute). It does not change its priority.",
    "Desactiva la entrada sin borrarla; conserva su posición en el orden.":
        "Deactivates the entry without deleting it; it keeps its position in the order.",
    "Sube la entrada en el orden (Ctrl+↑).": "Moves the entry up in the order (Ctrl+↑).",
    "Baja la entrada en el orden (Ctrl+↓).": "Moves the entry down in the order (Ctrl+↓).",
    "La coloca en primer lugar: será la que el firmware intente primero.":
        "Puts it first: it will be the one the firmware tries first.",
    "Arranca desde la entrada seleccionada solo en el próximo reinicio (BootNext) y reinicia.":
        "Boots from the selected entry only on the next restart (BootNext) and restarts.",
    "Reinicia y abre la configuración UEFI / BIOS.": "Restarts and opens the UEFI / BIOS setup.",
    "Prepara una entrada nueva que apunta a un archivo .efi de la ESP.":
        "Prepares a new entry pointing to an .efi file on the ESP.",
    "Cambia el nombre de la entrada.": "Changes the entry name.",
    "Marca la entrada para eliminarla al guardar (Supr). Púlsalo de nuevo para deshacerlo.":
        "Marks the entry for deletion on save (Del). Press it again to undo.",
    "Guarda un archivo con todas las entradas UEFI y el orden de arranque.":
        "Saves a file with all UEFI entries and the boot order.",
    "Exporta la configuración BCD de Windows (bcdedit /export). Recomendado antes del primer cambio en {bootmgr}.":
        "Exports the Windows BCD configuration (bcdedit /export). Recommended before the first {bootmgr} change.",
    "Restaura entradas y orden de arranque desde una copia de la NVRAM.":
        "Restores entries and boot order from a NVRAM backup.",
    "Redirige el {bootmgr} de Windows (BCD) a otro gestor .efi o lo restaura. Algunos firmwares no lo respetan.":
        "Redirects the Windows {bootmgr} (BCD) to another .efi manager, or restores it. Some firmwares ignore this.",
    "Aplica a la NVRAM todos los cambios preparados en la tabla.":
        "Applies all the changes prepared in the table to the NVRAM.",
    "Descarta los cambios preparados y vuelve al estado real de la NVRAM.":
        "Discards the prepared changes and goes back to the real NVRAM state.",
    "Cambiar entre tema oscuro y claro": "Switch between dark and light theme",
    "Copia todo el registro al portapapeles.": "Copies the whole log to the clipboard.",
    "Borra el contenido del registro.": "Clears the log contents.",
    "Es la entrada UEFI desde la que el firmware arrancó esta sesión de Windows (variable BootCurrent). "
    "Es un dato histórico: no cambia al habilitar, deshabilitar o reordenar entradas; se actualiza en el próximo reinicio.":
        "This is the UEFI entry the firmware used to start this Windows session (BootCurrent variable). "
        "It is historical data: it does not change when you enable, disable or reorder entries; it updates on the next restart.",
    # --- estados y tabla ---
    "Predeterminada": "Default", "Habilitada": "Enabled", "Deshabilitada": "Disabled",
    "Nueva": "New", "modificada": "modified", "inició esta sesión": "started this session",
    "próximo arranque": "next boot", "ilegible": "unreadable", "archivo no encontrado": "file not found",
    "Se eliminará al guardar": "Will be deleted on save",
    "Ninguna entrada seleccionada": "No entry selected", "(nueva)": "(new)",
    "%d entradas  ·  %d habilitadas  ·  esta sesión inició desde: %s":
        "%d entries  ·  %d enabled  ·  this session started from: %s",
    "desconocido": "unknown", "desconocida": "unknown",
    # --- barra de cambios pendientes ---
    "Sin cambios pendientes": "No pending changes", "1 cambio sin guardar": "1 unsaved change",
    "%d cambios sin guardar": "%d unsaved changes",
    "Se ven en la tabla, pero no se aplican a la NVRAM hasta que pulses Guardar cambios.":
        "They show in the table, but are not applied to the NVRAM until you press Save changes.",
    "Pulsa Guardar cambios para aplicarlo.": "Press Save changes to apply it.",
    "Cambios descartados.": "Changes discarded.", "No hay cambios pendientes.": "There are no pending changes.",
    "Descartar cambios": "Discard changes",
    "¿Descartar los cambios preparados y volver al estado actual de la NVRAM?":
        "Discard the prepared changes and go back to the current NVRAM state?",
    "Cambios sin guardar": "Unsaved changes", "No se guardaron los cambios": "Changes were not saved",
    "Tienes 1 cambio sin guardar.": "You have 1 unsaved change.",
    "Tienes %d cambios sin guardar.": "You have %d unsaved changes.",
    "Si cierras BootScribe ahora, NO se aplicarán a la NVRAM.":
        "If you close BootScribe now, they will NOT be applied to the NVRAM.",
    "Hay cambios sin guardar. Si actualizas, se perderán.\n\n¿Actualizar de todos modos?":
        "There are unsaved changes. If you refresh, they will be lost.\n\nRefresh anyway?",
    "Tienes cambios sin guardar: se perderán.": "You have unsaved changes: they will be lost.",
    "Guarda o descarta primero los cambios pendientes.": "Save or discard the pending changes first.",
    # --- preparar cambios ---
    "Selecciona una entrada de la lista.": "Select an entry from the list.",
    "'%s' pasa a la posición %d.": "'%s' moves to position %d.",
    "'%s' será la entrada predeterminada (posición 1).": "'%s' will be the default entry (position 1).",
    "'%s' ya es la predeterminada.": "'%s' is already the default.",
    "'%s' se habilitará.": "'%s' will be enabled.", "'%s' ya está habilitada.": "'%s' is already enabled.",
    "Ojo: pasará a ser la predeterminada, porque está por encima de la actual.":
        "Note: it will become the default, because it sits above the current one.",
    "'%s' se deshabilitará.": "'%s' will be disabled.", "'%s' ya está deshabilitada.": "'%s' is already disabled.",
    "La predeterminada pasará a ser '%s'.": "The default will become '%s'.",
    "No permitido": "Not allowed",
    "No puedes deshabilitar la única entrada habilitada del orden de arranque: el equipo quedaría sin nada que iniciar.":
        "You cannot disable the only enabled entry in the boot order: the computer would have nothing to start.",
    "No puedes eliminar la única entrada habilitada del orden de arranque.":
        "You cannot delete the only enabled entry in the boot order.",
    "Entrada ilegible": "Unreadable entry", "No se puede renombrar: %s": "Cannot rename: %s",
    "Renombrar entrada": "Rename entry", "Nuevo nombre de la entrada:": "New entry name:",
    "Se renombrará a '%s'.": "It will be renamed to '%s'.",
    "'%s' se eliminará.": "'%s' will be deleted.",
    "Se canceló la eliminación de '%s'.": "Deletion of '%s' was cancelled.",
    "Se quitó la entrada nueva '%s'.": "The new entry '%s' was removed.",
    "ATENCIÓN: es el Administrador de arranque de Windows.": "WARNING: this is the Windows Boot Manager.",
    "ATENCIÓN: se eliminará el Administrador de arranque de Windows.":
        "WARNING: the Windows Boot Manager will be deleted.",
    "Se creará la entrada '%s'.": "The entry '%s' will be created.",
    "Archivo no encontrado: %s": "File not found: %s",
    "%s: %s Si el archivo no existe, el firmware pasará a la siguiente entrada.":
        "%s: %s If the file does not exist, the firmware will move on to the next entry.",
    "Habilita la entrada antes de usarla para arrancar.": "Enable the entry before using it to boot.",
    # --- nueva entrada ---
    "Nueva entrada de arranque UEFI": "New UEFI boot entry", "Nueva entrada de arranque": "New boot entry",
    "Prepara una entrada UEFI que apunta a un archivo .efi de la partición EFI (ESP). Se creará al pulsar Guardar cambios.":
        "Prepares a UEFI entry pointing to an .efi file on the EFI partition (ESP). It will be created when you press Save changes.",
    "Nombre": "Name", "Archivo .efi": "File .efi",
    "Colocar al inicio de la lista (hacerla predeterminada)": "Put at the top of the list (make it default)",
    "Selecciona un archivo .efi": "Select an .efi file", "Aplicación EFI": "EFI application", "Todos": "All",
    "Datos incompletos": "Incomplete data", "Indica nombre y ruta .efi.": "Enter a name and an .efi path.",
    "Error": "Error", "No se pudo determinar la partición del sistema EFI (ESP).":
        "Could not determine the EFI system partition (ESP).",
    "Verificación previa fallida": "Pre-flight check failed", "No se aplicó ningún cambio.": "No changes were applied.",
    "Verificado en la ESP: %s (%s).": "Verified on the ESP: %s (%s).",
    "Verificado en su partición: %s (%s).": "Verified on its partition: %s (%s).",
    "El archivo %s no es válido en la ESP: %s.": "The file %s is not valid on the ESP: %s.",
    "El archivo %s no es válido en la partición de destino: %s.":
        "The file %s is not valid on the target partition: %s.",
    "La partición del sistema EFI (ESP) no está accesible: %s": "The EFI system partition (ESP) is not accessible: %s",
    "La partición de destino no está accesible: %s.": "The target partition is not accessible: %s.",
    "Entrada sin archivo en disco (USB, red u otro dispositivo): no se puede verificar.":
        "Entry without a file on disk (USB, network or other device): it cannot be verified.",
    "Partición MBR: no se puede localizar por GUID, el archivo no se verificó.":
        "MBR partition: it cannot be located by GUID, the file was not verified.",
    "la partición {%s} no está conectada (disco desconectado o partición eliminada)":
        "partition {%s} is not connected (disk unplugged or partition removed)",
    "no hay letras de unidad libres": "no free drive letters",
    "no se pudo consultar las particiones (%s)": "could not query the partitions (%s)",
    "no se pudo montar la partición (%s)": "could not mount the partition (%s)", "sin detalle": "no detail",
    # --- guardar cambios ---
    "Aplicar %d cambios a la NVRAM": "Apply %d changes to the NVRAM",
    "Antes de guardar se crea una copia de seguridad de la NVRAM.":
        "A NVRAM backup is created before saving.",
    "La NVRAM cambió": "The NVRAM changed",
    "La NVRAM cambió desde que se cargó la lista (otro programa o Windows pudo modificarla).\n\n"
    "Pulsa Descartar para recargar el estado actual y vuelve a preparar tus cambios. No se aplicó nada.":
        "The NVRAM changed since the list was loaded (another program or Windows may have modified it).\n\n"
        "Press Discard to reload the current state and prepare your changes again. Nothing was applied.",
    "Debe quedar al menos una entrada habilitada en el orden de arranque.":
        "At least one enabled entry must remain in the boot order.",
    "Nueva entrada": "New entry", "(no existe)": "(does not exist)", "(se elimina)": "(deleted)",
    "Nombre de %s": "Name of %s", "Estado de '%s'": "State of '%s'", "Orden de arranque": "Boot order",
    "Entrada predeterminada": "Default entry", "ninguna": "none", "(vacío)": "(empty)",
    "Falló el guardado": "Saving failed", "Error al guardar los cambios": "Error saving the changes",
    "Se revirtieron los pasos ya aplicados.": "The steps already applied were rolled back.",
    "Se revirtieron los pasos ya aplicados para no dejar un estado a medias.":
        "The steps already applied were rolled back so nothing is left half-done.",
    "No se pudo revertir un paso: %s": "Could not roll back a step: %s",
    "modificada": "modified", "creada": "created", "eliminada": "deleted",
    "No hay números de entrada libres.": "There are no free entry numbers.",
    "El firmware no conservó estos cambios: %s": "The firmware did not keep these changes: %s",
    "Se escribieron los cambios, pero el firmware no conservó: %s":
        "The changes were written, but the firmware did not keep: %s",
    "Verifica el resultado": "Check the result",
    "Cambios guardados en la NVRAM.": "Changes saved to the NVRAM.", "Cambios guardados.": "Changes saved.",
    "Sin copia de seguridad": "No backup",
    "No se pudo crear la copia de seguridad:\n%s\n\n¿Continuar de todos modos?":
        "The backup could not be created:\n%s\n\nContinue anyway?",
    "No se pudo crear la copia de seguridad: %s": "Could not create the backup: %s",
    "Copia de seguridad de la NVRAM creada: %s": "NVRAM backup created: %s",
    "Aplicar cambio": "Apply change",
    # --- copias de seguridad y BCD ---
    "Copia de seguridad": "Backup", "Copia de la NVRAM guardada.": "NVRAM backup saved.",
    "bcdedit /export devolvió el código %d.": "bcdedit /export returned code %d.",
    "El archivo exportado no existe o está vacío: %s": "The exported file does not exist or is empty: %s",
    "No se pudo exportar el BCD": "Could not export the BCD", "BCD exportado: %s": "BCD exported: %s",
    "BCD exportado.": "BCD exported.",
    "Para restaurarlo (consola de administrador): bcdedit /import \"%s\"":
        "To restore it (administrator console): bcdedit /import \"%s\"",
    "%s restaurada (%d bytes)": "%s restored (%d bytes)",
    "%s eliminada (no estaba en la copia)": "%s deleted (it was not in the backup)",
    "Selecciona una copia de BootScribe": "Select a BootScribe backup", "Copia BootScribe": "BootScribe backup",
    "Copia no válida": "Invalid backup", "No se pudo leer la NVRAM": "Could not read the NVRAM",
    "Entradas": "Entries", "Entradas que no están en la copia": "Entries not in the backup",
    "se eliminan": "will be deleted",
    "Antes de restaurar se guarda una copia del estado actual.":
        "A backup of the current state is saved before restoring.",
    "Si algo falla a mitad de camino, BootScribe intenta volver al estado actual.":
        "If something fails halfway, BootScribe tries to go back to the current state.",
    "Restaurar copia": "Restore backup", "Restaurar la NVRAM desde '%s'": "Restore the NVRAM from '%s'",
    "Restaurar": "Restore",
    "Copia del estado actual: %s": "Backup of the current state: %s",
    "No se pudo guardar la copia del estado actual": "Could not save the backup of the current state",
    "Falló la restauración: %s. Volviendo al estado anterior…": "Restore failed: %s. Going back to the previous state…",
    "Se volvió al estado anterior a la restauración.": "Went back to the state before the restore.",
    "No se pudo volver al estado anterior: %s. Usa la copia: %s":
        "Could not go back to the previous state: %s. Use the backup: %s",
    "Error al restaurar": "Error restoring", "Copia restaurada.": "Backup restored.",
    "Copia restaurada correctamente.": "Backup restored successfully.",
    "El archivo no es una copia de BootScribe.": "The file is not a BootScribe backup.",
    "Variable no permitida en la copia: %r": "Variable not allowed in the backup: %r",
    "Variable vacía en la copia: %s": "Empty variable in the backup: %s",
    "BootOrder dañado en la copia.": "Damaged BootOrder in the backup.",
    "La copia no contiene BootOrder.": "The backup does not contain BootOrder.",
    # --- arrancar una vez / reiniciar ---
    "Próximo arranque (BootNext)": "Next boot (BootNext)",
    "ninguno (se usa el orden normal)": "none (the normal order is used)",
    "Orden de arranque permanente": "Permanent boot order", "sin cambios": "unchanged",
    "El equipo se reiniciará de inmediato.": "The computer will restart immediately.",
    "Arrancar solo la próxima vez desde '%s'": "Boot only next time from '%s'",
    "Aplicar y reiniciar": "Apply and restart", "No se pudo fijar BootNext": "Could not set BootNext",
    "No se pudo revertir BootNext: %s": "Could not roll back BootNext: %s",
    "No se pudo reiniciar": "Could not restart", "Reiniciar": "Restart",
    "El equipo se reiniciará de inmediato en la configuración UEFI.\nGuarda tu trabajo. ¿Continuar?":
        "The computer will restart immediately into the UEFI setup.\nSave your work. Continue?",
    "No se pudo reiniciar al firmware": "Could not restart into the firmware",
    "error desconocido": "unknown error",
    # --- ESP / PowerShell ---
    "No se pudo montar la partición del sistema EFI:\n": "Could not mount the EFI system partition:\n",
    "No hay letras de unidad libres para montar la ESP.": "There are no free drive letters to mount the ESP.",
    "No se pudo consultar la ESP con PowerShell: %s": "Could not query the ESP with PowerShell: %s",
    "No se pudo interpretar la partición ESP: %s": "Could not interpret the ESP partition: %s",
    "PowerShell devolvió el código %d": "PowerShell returned code %d",
    "PowerShell no devolvió datos": "PowerShell returned no data",
    "respuesta de PowerShell no válida: %s": "invalid PowerShell response: %s",
    "Tiempo de espera agotado (%d s) ejecutando: %s": "Timed out (%d s) running: %s",
    "No se pudo ejecutar %s: %s": "Could not run %s: %s",
    "Error inesperado ejecutando %s: %s": "Unexpected error running %s: %s",
    "el archivo no existe (¿fue borrado o la unidad está desconectada?)":
        "the file does not exist (was it deleted or is the drive unplugged?)",
    "la ruta no es un archivo": "the path is not a file",
    "el archivo está vacío o dañado (%d bytes)": "the file is empty or damaged (%d bytes)",
    "no es un ejecutable EFI válido (cabecera incorrecta)": "not a valid EFI executable (wrong header)",
    "no se pudo leer el archivo: %s": "could not read the file: %s",
    "entrada demasiado corta (%d bytes)": "entry too short (%d bytes)",
    "descripción sin terminador": "description without terminator", "lista de rutas truncada": "truncated path list",
    # --- {bootmgr} ---
    "No se pudo leer {bootmgr}": "Could not read {bootmgr}",
    "Elegir archivo .efi": "Choose .efi file",
    "No hay una entrada con ruta .efi seleccionada.\n¿Quieres elegir un archivo .efi de la partición EFI?":
        "No entry with an .efi path is selected.\nDo you want to choose an .efi file from the EFI partition?",
    "Selecciona el gestor .efi (rEFInd, GRUB, etc.)": "Select the .efi manager (rEFInd, GRUB, etc.)",
    "Descripción": "Description", "Descripción para {bootmgr}:": "Description for {bootmgr}:",
    "Esa es la ruta estándar de Windows. Selecciona otra entrada (rEFInd, GRUB, etc.).":
        "That is the standard Windows path. Select another entry (rEFInd, GRUB, etc.).",
    "Blindaje de {bootmgr}": "Locking {bootmgr}", "Restauración de {bootmgr}": "Restoring {bootmgr}",
    "Sin ese archivo Windows no podría arrancar; repara el arranque antes (por ejemplo con bcdboot).":
        "Without that file Windows could not start; repair the boot first (for example with bcdboot).",
    "Ruta de {bootmgr}": "{bootmgr} path", "(sin ruta)": "(no path)", "(sin descripción)": "(no description)",
    "Exportar una copia del BCD antes de aplicar (recomendado)": "Export a BCD backup before applying (recommended)",
    "Aún no hay copia del BCD en esta sesión. Si algo sale mal, podrás restaurarla con bcdedit /import.":
        "There is no BCD backup in this session yet. If something goes wrong, you can restore it with bcdedit /import.",
    "Ya exportaste una copia del BCD en esta sesión.": "You already exported a BCD backup in this session.",
    "BCD exportado antes del cambio: %s": "BCD exported before the change: %s",
    "No se pudo cambiar la ruta; no se modificó nada.\n%s": "Could not change the path; nothing was modified.\n%s",
    "No se pudo cambiar la descripción: %s": "Could not change the description: %s",
    "La verificación posterior no coincide (ruta actual: %s).": "The follow-up check does not match (current path: %s).",
    "Error inesperado: %s": "Unexpected error: %s", "%s completado.": "%s completed.",
    "Revirtiendo al estado anterior…": "Rolling back to the previous state…",
    "{bootmgr} volvió a su estado anterior (%s).": "{bootmgr} went back to its previous state (%s).",
    "Se revirtió al estado anterior; no quedó nada a medias.": "Rolled back to the previous state; nothing was left half-done.",
    "No se pudo revertir {bootmgr} automáticamente.": "Could not roll back {bootmgr} automatically.",
    "%s\n\nNo se pudo revertir automáticamente. Restaura el BCD con:\nbcdedit /import \"<archivo .bcd exportado>\"\n"
    "(o vuelve a poner la ruta a %s).":
        "%s\n\nIt could not be rolled back automatically. Restore the BCD with:\nbcdedit /import \"<exported .bcd file>\"\n"
        "(or set the path back to %s).",
    # --- mensajes generales ---
    "Registro copiado al portapapeles.": "Log copied to the clipboard.",
    "Revisa el antes y el después. No se modificará nada hasta que lo confirmes.":
        "Review the before and after. Nothing will be changed until you confirm.",
    "Permisos": "Permissions", "Se requieren permisos de Administrador para continuar.":
        "Administrator permissions are required to continue.",
    "Sistema no compatible": "Unsupported system",
    "Esta herramienta solo funciona en Windows.": "This tool only works on Windows.",
    "Este equipo arrancó en modo BIOS heredado (Legacy/CSM), no en UEFI. No hay variables UEFI que administrar.":
        "This computer started in legacy BIOS mode (Legacy/CSM), not UEFI. There are no UEFI variables to manage.",
    "No se pudo abrir el token del proceso. ": "Could not open the process token. ",
    "No se pudo buscar el privilegio. ": "Could not look up the privilege. ",
    "No se pudo activar el privilegio SeSystemEnvironmentPrivilege. Ejecuta BootScribe como Administrador.":
        "Could not enable the SeSystemEnvironmentPrivilege privilege. Run BootScribe as Administrator.",
    "No se pueden leer las variables UEFI. ": "Cannot read the UEFI variables. ",
    "No se pudo escribir '%s'. %s": "Could not write '%s'. %s", "No se pudo eliminar '%s'. %s": "Could not delete '%s'. %s",
    "BootOrder no puede quedar vacío.": "BootOrder cannot be left empty.",
    "Error %d: %s": "Error %d: %s",
    # --- errores de Windows ---
    "El equipo no arrancó en modo UEFI o Windows no puede acceder al firmware.":
        "The computer did not start in UEFI mode, or Windows cannot access the firmware.",
    "Acceso denegado por el firmware.": "Access denied by the firmware.",
    "Parámetro no válido.": "Invalid parameter.", "Variable no encontrada.": "Variable not found.",
    "Falta el privilegio SeSystemEnvironmentPrivilege (ejecuta como Administrador).":
        "The SeSystemEnvironmentPrivilege privilege is missing (run as Administrator).",
})



def main():
    if "--make-icon" in sys.argv:
        print("Icono generado:", make_icon_file())
        return
    lang = load_settings().get("lang")
    LANG[0] = lang if lang in ("es", "en") else DEFAULT_LANG
    if os.name != "nt":
        r = tk.Tk(); r.withdraw()
        messagebox.showerror(_("Sistema no compatible"), _("Esta herramienta solo funciona en Windows."))
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BootScribe.App")
    except Exception:
        pass
    if not is_admin():
        if elevate():
            sys.exit(0)
        r = tk.Tk(); r.withdraw()
        messagebox.showerror(_("Permisos"), _("Se requieren permisos de Administrador para continuar."))
        sys.exit(1)
    enable_dpi_awareness()
    load_private_fonts()
    root = tk.Tk()
    app = App(root)
    if app.ready:
        root.mainloop()


if __name__ == "__main__":
    main()