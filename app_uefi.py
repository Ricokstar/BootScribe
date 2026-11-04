# -*- coding: utf-8 -*-
"""
UEFI Boot Manager - alternativa ligera y portátil a EasyUEFI (usa bcdedit).
Requiere Windows en modo UEFI. Solicita permisos de Administrador automáticamente.
"""
import ctypes
import os
import re
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

CREATE_NO_WINDOW = 0x08000000
GUID_FIND = re.compile(r"\{[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}")
ONLY_ID = re.compile(r"^\{[^}]+\}$")
REFIND_PATH = r"\EFI\refind\refind_x64.efi"


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


# ----------------------------- Utilidades de sistema -----------------------------
def _oem_encoding():
    try:
        return "cp%d" % ctypes.windll.kernel32.GetOEMCP()
    except Exception:
        return "cp437"


ENC = _oem_encoding()
_sysnative = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Sysnative", "bcdedit.exe")
BCDEDIT = _sysnative if os.path.exists(_sysnative) else "bcdedit"


def run(args):
    p = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL,
                       creationflags=CREATE_NO_WINDOW)
    out = (p.stdout + p.stderr).decode(ENC, errors="replace")
    return p.returncode, out


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


def parse_firmware():
    """Devuelve (entradas, orden_de_arranque). Cada entrada es un dict."""
    rc, out = run([BCDEDIT, "/enum", "firmware"])
    if rc != 0:
        raise RuntimeError(out.strip() or "bcdedit devolvió un error (¿el sistema es UEFI?)")
    lines = out.splitlines()
    dashes = [i for i, l in enumerate(lines) if re.fullmatch(r"-{3,}", l.strip())]
    order, entries = [], []
    for n, d in enumerate(dashes):
        end = dashes[n + 1] - 1 if n + 1 < len(dashes) else len(lines)
        header = lines[d - 1].strip() if d > 0 else ""
        fields = parse_block(lines[d + 1:end])
        if not fields:
            continue
        ident = fields[0][1][0]
        if ident.lower() == "{fwbootmgr}":
            order = [x for x in (get_field(fields, "displayorder") or []) if ONLY_ID.match(x)]
            continue
        desc = get_field(fields, "descri", prefix=True)
        entries.append({
            "id": ident,
            "name": desc[0] if desc else "(sin descripción) " + header,
            "path": (get_field(fields, "path", "ruta", "rutadeacceso") or [""])[0],
            "device": (get_field(fields, "device", "dispositivo") or [""])[0],
        })
    by_id = {e["id"]: e for e in entries}
    ordered = [by_id[i] for i in order if i in by_id]
    hidden = [e for e in entries if e["id"] not in order]
    return ordered + hidden, [i for i in order if i in by_id]


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
        ttk.Checkbutton(f, text="Colocar como primera prioridad", variable=self.first_var)\
            .grid(row=2, column=1, sticky="w", pady=4)
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
            p = p.replace("/", "\\")
            if p[1:2] == ":":
                p = p[2:]
            self.path_var.set(p)
            if not self.name_var.get().strip():
                self.name_var.set(os.path.splitext(os.path.basename(p))[0])

    def ok(self):
        name = self.name_var.get().strip()
        path = self.path_var.get().strip().replace("/", "\\")
        if not name or not path:
            messagebox.showwarning("Datos incompletos", "Indica nombre y ruta .efi.", parent=self)
            return
        if not path.startswith("\\"):
            path = "\\" + path
        if self.app.create_entry(name, path, self.first_var.get()):
            self.close()

    def close(self):
        self.app.release_esp()
        self.destroy()


# ----------------------------- Aplicación principal -----------------------------
class App:
    def __init__(self, root):
        self.root = root
        self.entries = []
        self.order = []
        self.esp_letter = None
        self.esp_mounted_by_us = False

        root.title("UEFI Boot Manager (bcdedit)")
        root.geometry("1000x580")
        root.minsize(840, 480)
        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        bar = ttk.Frame(root, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        btns = [
            ("⟳ Actualizar", self.refresh),
            ("▲ Subir", lambda: self.move(-1)),
            ("▼ Bajar", lambda: self.move(1)),
            ("★ Establecer predeterminada", self.set_default),
            ("＋ Nueva entrada", self.new_entry),
            ("👁 Ocultar / Mostrar", self.toggle_hide),
            ("✖ Eliminar", self.delete_entry),
        ]
        for text, cmd in btns:
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)

        bar2 = ttk.Frame(root, padding=(8, 4, 8, 4))
        bar2.pack(fill="x")
        ttk.Button(bar2, text="✔ Habilitar / Activar Entrada",
                   command=self.habilitar_entrada).pack(side="left", padx=2)
        ttk.Button(bar2, text="🛡 Blindar rEFInd contra Windows",
                   command=self.blindar_refind).pack(side="left", padx=2)
        ttk.Button(bar2, text="⏻ Reiniciar a UEFI", command=self.reboot_fw).pack(side="right", padx=2)

        mid = ttk.Frame(root, padding=(8, 4))
        mid.pack(fill="both", expand=True)
        cols = ("pos", "name", "id", "path", "state")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("pos", "#", 40), ("name", "Nombre", 210), ("id", "GUID / ID", 270),
                        ("path", "Ruta EFI", 230), ("state", "Estado", 150)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w", stretch=(c != "pos"))
        self.tree.tag_configure("hidden", foreground="#888888")
        self.tree.tag_configure("default", font=("Segoe UI", 9, "bold"))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        lf = ttk.LabelFrame(root, text="Registro", padding=4)
        lf.pack(fill="x", padx=8, pady=(4, 8))
        self.log = tk.Text(lf, height=7, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh()

    # ---- helpers ----
    def logmsg(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def bcd(self, args, quiet=False):
        rc, out = run([BCDEDIT] + args)
        self.logmsg("> bcdedit " + " ".join(args) + "\n" + out.strip())
        if rc != 0 and not quiet:
            messagebox.showerror("bcdedit falló", out.strip() or "Error desconocido")
        return rc == 0, out

    def selected_id(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Selecciona una entrada", "Primero selecciona una entrada de la lista.")
            return None
        return sel[0]

    def refresh(self, keep=None):
        try:
            self.entries, self.order = parse_firmware()
        except Exception as e:
            messagebox.showerror("No se pudo leer el firmware", str(e))
            return
        self.tree.delete(*self.tree.get_children())
        for e in self.entries:
            if e["id"] in self.order:
                idx = self.order.index(e["id"])
                state = "Predeterminada (1.ª)" if idx == 0 else "Activa"
                tags = ("default",) if idx == 0 else ()
                pos = str(idx + 1)
            else:
                state, tags, pos = "Oculta / deshabilitada", ("hidden",), "–"
            self.tree.insert("", "end", iid=e["id"], tags=tags,
                             values=(pos, e["name"], e["id"], e["path"], state))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)

    def apply_order(self, new_order, keep=None):
        ok, _ = self.bcd(["/set", "{fwbootmgr}", "displayorder"] + new_order)
        self.refresh(keep)
        return ok

    # ---- acciones ----
    def move(self, delta):
        eid = self.selected_id()
        if not eid:
            return
        if eid not in self.order:
            messagebox.showinfo("Entrada oculta", "Esta entrada está oculta. Habilítala primero.")
            return
        order = list(self.order)
        i = order.index(eid)
        j = i + delta
        if not 0 <= j < len(order):
            return
        order[i], order[j] = order[j], order[i]
        self.apply_order(order, eid)

    def set_default(self):
        eid = self.selected_id()
        if not eid:
            return
        order = [eid] + [x for x in self.order if x != eid]
        if self.apply_order(order, eid):
            self.logmsg("Entrada establecida como predeterminada (primera en el orden de arranque).")

    def toggle_hide(self):
        eid = self.selected_id()
        if not eid:
            return
        order = list(self.order)
        if eid in order:
            if len(order) == 1:
                messagebox.showwarning("No permitido", "No puedes ocultar la única entrada activa.")
                return
            order.remove(eid)
        else:
            order.append(eid)
        self.apply_order(order, eid)

    def habilitar_entrada(self):
        """Habilita una entrada deshabilitada:
        1) bcdedit /set {fwbootmgr} displayorder {GUID} /addfirst
        2) bcdedit /set {GUID} active yes  (best-effort)"""
        eid = self.selected_id()
        if not eid:
            return
        name = self.tree.set(eid, "name")
        if eid in self.order:
            self.logmsg("[INFO] '%s' ya está activa en el orden de arranque." % name)
            messagebox.showinfo("Ya activa", "La entrada '%s' ya está activa.\n"
                                             "Usa 'Subir' / 'Bajar' para cambiar su prioridad." % name)
            return

        self.logmsg("Habilitando entrada '%s' (%s)…" % (name, eid))
        ok, out = self.bcd(["/set", "{fwbootmgr}", "displayorder", eid, "/addfirst"], quiet=True)
        if not ok:
            self.logmsg("[ERROR] No se pudo incluir la entrada en el orden de arranque: %s"
                        % (out.strip() or "error desconocido"))
            messagebox.showerror("Error al habilitar", out.strip() or "Error desconocido")
            self.refresh(eid)
            return

        # Paso 2: marcar como activa. En entradas de firmware este elemento puede no
        # estar soportado; si falla no es crítico, la presencia en displayorder es lo que cuenta.
        ok2, out2 = self.bcd(["/set", eid, "active", "yes"], quiet=True)
        if not ok2:
            self.logmsg("[AVISO] 'active yes' no es aplicable a esta entrada (no es crítico): %s"
                        % (out2.strip() or "sin detalle"))

        self.refresh(eid)
        if eid in self.order:
            self.logmsg("[ÉXITO] Entrada '%s' habilitada y colocada en primera posición." % name)
            messagebox.showinfo("Entrada habilitada",
                                "'%s' ahora está Activa (colocada en primera posición)." % name)
        else:
            self.logmsg("[ERROR] El comando terminó sin error pero la entrada no aparece en el orden de arranque.")
            messagebox.showwarning("Verifica el resultado",
                                   "bcdedit no reportó error, pero la entrada no figura como activa.")

    def delete_entry(self):
        eid = self.selected_id()
        if not eid:
            return
        if eid.lower() == "{bootmgr}":
            messagebox.showwarning("Protegida", "No se elimina el Administrador de arranque de Windows "
                                                "({bootmgr}). Usa 'Ocultar' si no quieres que aparezca.")
            return
        name = self.tree.set(eid, "name")
        if not messagebox.askyesno("Confirmar eliminación",
                                   "¿Eliminar definitivamente la entrada?\n\n%s\n%s" % (name, eid),
                                   icon="warning"):
            return
        self.bcd(["/delete", eid, "/f"])
        self.refresh()

    def blindar_refind(self):
        """Fuerza que {bootmgr} apunte a rEFInd:
        bcdedit /set {bootmgr} path \\EFI\\refind\\refind_x64.efi"""
        try:
            letter = self.ensure_esp()
            existe = os.path.exists(letter + ":" + REFIND_PATH)
        except Exception as e:
            existe = None
            self.logmsg("[AVISO] No se pudo verificar la ESP: %s" % e)
        finally:
            self.release_esp()

        if existe is False:
            if not messagebox.askyesno(
                    "rEFInd no encontrado",
                    "No se encontró %s en la partición EFI.\n\n"
                    "Si continúas, el equipo podría no arrancar correctamente.\n"
                    "¿Aplicar el cambio de todos modos?" % REFIND_PATH,
                    icon="warning"):
                self.logmsg("[CANCELADO] Blindaje de rEFInd cancelado: archivo no encontrado en la ESP.")
                return
        elif not messagebox.askyesno(
                "Blindar rEFInd contra Windows",
                "Se ejecutará:\n\nbcdedit /set {bootmgr} path %s\n\n¿Continuar?" % REFIND_PATH):
            return

        self.logmsg("Aplicando blindaje de rEFInd…")
        ok, out = self.bcd(["/set", "{bootmgr}", "path", REFIND_PATH], quiet=True)
        if ok:
            self.logmsg("[ÉXITO] {bootmgr} ahora apunta a %s. "
                        "Windows ya no debería reemplazar a rEFInd como gestor principal." % REFIND_PATH)
            messagebox.showinfo("Blindaje aplicado", "rEFInd quedó configurado como destino de {bootmgr}.")
        else:
            self.logmsg("[ERROR] No se pudo blindar rEFInd: %s" % (out.strip() or "error desconocido"))
            messagebox.showerror("Error al blindar rEFInd", out.strip() or "Error desconocido")
        self.refresh()

    def reboot_fw(self):
        if messagebox.askyesno("Reiniciar", "El equipo se reiniciará de inmediato en la configuración UEFI.\n"
                                            "Guarda tu trabajo. ¿Continuar?", icon="warning"):
            rc, out = run(["shutdown", "/r", "/fw", "/t", "0"])
            if rc != 0:
                self.logmsg("[ERROR] shutdown /r /fw: " + out.strip())
                messagebox.showerror("Error", out.strip() or "No se pudo reiniciar al firmware.")

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

    def new_entry(self):
        NewEntryDialog(self)

    def create_entry(self, name, path, first):
        try:
            letter = self.ensure_esp()
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return False
        if not os.path.exists(letter + ":" + path):
            if not messagebox.askyesno("Archivo no encontrado",
                                       "No se encontró %s en la ESP.\n¿Crear la entrada de todos modos?" % path):
                return False
        ok, out = self.bcd(["/copy", "{bootmgr}", "/d", name])
        m = GUID_FIND.search(out)
        if not ok or not m:
            return False
        guid = m.group(0)
        ok1, _ = self.bcd(["/set", guid, "path", path])
        ok2, _ = self.bcd(["/set", guid, "device", "partition=%s:" % letter])
        if not (ok1 and ok2):
            self.bcd(["/delete", guid, "/f"], quiet=True)
            return False
        order = [x for x in self.order if x != guid]
        order = [guid] + order if first else order + [guid]
        ok3, _ = self.bcd(["/set", "{fwbootmgr}", "displayorder"] + order)
        if not ok3:
            self.bcd(["/delete", guid, "/f"], quiet=True)
            return False
        self.refresh(guid)
        messagebox.showinfo("Listo", "Entrada creada: %s\n%s" % (name, guid))
        return True

    def on_close(self):
        self.release_esp()
        self.root.destroy()


def main():
    if os.name != "nt":
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("Sistema no compatible", "Esta herramienta solo funciona en Windows.")
        return
    if not is_admin():
        if elevate():
            sys.exit(0)
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("Permisos", "Se requieren permisos de Administrador para continuar.")
        sys.exit(1)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()