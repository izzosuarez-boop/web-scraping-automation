# -*- coding: utf-8 -*-
"""Interfaz de la venta automatica en el sitio comprador.

Pestanas:
  - Monitoreo:    servidores a vigilar (desplegable con SERVIDORES), precio
                  minimo que dispara la venta y cuantos millones vender. Cada
                  INTERVALO_S lee los precios (lector_precios, sin navegador)
                  y, si un servidor llega al minimo, encarga la venta al
                  Vendedor (navegador).
  - Datos venta:  lo que pide el formulario del sitio comprador (metodo de pago,
                  beneficiario, cuenta/direccion, personaje, codigo de
                  intercambio, WhatsApp) + boton para iniciar sesion una vez.
  - Registro:     el log de ventas (ventas.csv) en una tabla.

Cada servidor dispara UNA sola vez por monitoreo: para volver a vender en el
mismo servidor, para y arranca el monitoreo. Asi un precio que se queda
arriba no abre una venta tras otra.

El captcha y el boton "Vender" son tuyos (ver vendedor.py): cuando el
formulario esta relleno, la ventana de Chrome pasa al frente y suena un aviso.
"""

import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox

import lector_precios
import registro
from vendedor import Orden, Vendedor

# --- AJUSTES -----------------------------------------------------------------
INTERVALO_S = 60          # cada cuanto se leen precios (no martillear Cloudflare)
REFRESCO_COLA_MS = 300    # cada cuanto la interfaz mira si hay noticias

# Servidores del desplegable, agrupados por juego (nombres EXACTOS del sitio comprador;
# en la web "Juego B" se llama "JuegoB"). Anadir aqui para que salgan.
SERVIDORES = {
    "Juego A": ["Servidor1", "Servidor2", "Servidor3"],
    "Juego B":     ["Servidor4", "Servidor5"],
    "Juego C": ["Servidor6", "Servidor7"],
}
METODOS = ["Usdt", "Usdc", "BitCoin", "Paypal", "Skrill", "SEPA", "Alipay",
           "CIH-banque", "BMCE-banque", "Attijari-banque", "Wafacash", "CashPlus"]

# Paleta (corporativa sobria).
C = {
    "fondo": "#F3F5F9", "tarjeta": "#FFFFFF", "borde": "#DCE2EB",
    "cabecera": "#0F1E36", "cabecera_txt": "#FFFFFF", "cabecera_sub": "#9FB0C8",
    "texto": "#1B2433", "suave": "#6B7689", "acento": "#2457C5",
    "acento_hover": "#1C47A3", "fila_par": "#F8FAFC", "seleccion": "#DCE7FB",
    "ok": "#1E8E5A", "ok_fondo": "#E3F4EC", "alerta": "#B86E00",
    "alerta_fondo": "#FFF2DC", "mal": "#C62F3B", "mal_fondo": "#FBE6E8",
    "neutro_fondo": "#EEF1F5",
}
FUENTE = "Segoe UI"

DIR = Path(__file__).parent
# OJO: datos_venta.json guarda TRC-20, WhatsApp y beneficiario. Es LOCAL, no
# sube al repo (esta en el .gitignore del portafolio).
FICHERO_DATOS_VENTA = DIR / "datos_venta.json"
FICHERO_SERVIDORES  = DIR / "servidores.json"

# Campos del formulario de venta: (clave_interna, etiqueta visible, seccion).
CAMPOS_VENTA = [
    ("metodo_pago",        "Método de pago",                 "Cobro"),
    ("beneficiario",       "Nombre del beneficiario",        "Cobro"),
    ("direccion_trc20",    "Dirección / cuenta (TRC-20)",    "Cobro"),
    ("personaje",          "Personaje en el juego",          "Entrega"),
    ("codigo_intercambio", "Código temporal de intercambio", "Entrega"),
    ("whatsapp",           "WhatsApp (+00 número)",          "Entrega"),
]
OBLIGATORIOS = ("metodo_pago", "beneficiario", "direccion_trc20")

COLS_SRV = {   # clave: (cabecera, ancho, alineacion)
    "servidor":      ("Servidor",        120, "w"),
    "juego":         ("Juego",           105, "w"),
    "precio_min":    ("Precio objetivo",  110, "e"),
    "precio_actual": ("Precio actual",    105, "e"),
    "distancia":     ("Distancia",         85, "e"),
    "millones":      ("Millones",          80, "e"),
    "valor":         ("Valor estimado",   120, "e"),
    "estado":        ("Estado",           230, "w"),
}
COLS_LOG = {
    "fecha_hora":      ("Fecha y hora",     150, "w"),
    "servidor":        ("Servidor",         110, "w"),
    "precio_tomado":   ("Precio /M",         90, "e"),
    "monedas_vendidas":  ("Monedas",            120, "e"),
    "direccion_trc20": ("Cuenta de cobro",  150, "w"),
    "estado":          ("Resultado",        300, "w"),
}


# ── Utilidades puras (se prueban sin Tk) ────────────────────────────────────
def cargar_json(ruta, por_defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return por_defecto


def guardar_json(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)


def normalizar_metodo(texto):
    """'usdt' -> 'Usdt': la web solo entiende la clave exacta."""
    t = (texto or "").strip().lower()
    return next((m for m in METODOS if m.lower() == t), "")


def juego_de(servidor):
    """('Servidor2' | 'servidor2') -> ('Servidor2', 'Juego A'); ('x') -> ('x', '')."""
    for juego, lista in SERVIDORES.items():
        for s in lista:
            if s.lower() == str(servidor).strip().lower():
                return s, juego
    return str(servidor).strip(), ""


def a_float(texto):
    try:
        return float(str(texto).replace(",", "."))
    except ValueError:
        return None


def ocultar_cuenta(c):
    """'TAbcdefghijkl1234' -> 'TAbcd…1234' (la tabla se puede ensenar)."""
    c = str(c or "")
    return c if len(c) <= 10 else f"{c[:5]}…{c[-4:]}"


def evaluar(cfg, servidor, precio_min, millones, metodo):
    """Decide que hacer con una fila. Devuelve (accion, precio, texto_estado).

    accion: 'vender' | 'esperar'. Funcion pura: se prueba sin red ni Tk.
    """
    srvs = cfg.get("servers", {})
    nombre = next((n for n in srvs if n.lower() == str(servidor).lower()), None)
    if nombre is None:
        return "esperar", None, "no existe en el sitio comprador"
    p = lector_precios.precio(cfg, nombre, metodo)
    if p is None:
        return "esperar", None, "no compra ahora"
    pmin, mill = a_float(precio_min), a_float(millones)
    if pmin is None or not mill or mill <= 0:
        return "esperar", p, "precio min / millones no validos"
    if p < pmin:
        return "esperar", p, "esperando"
    minimo = cfg.get("minimums", {}).get(metodo)
    if minimo and p * mill < minimo:
        return "esperar", p, f"bajo el minimo de {minimo} (sube millones)"
    return "vender", p, "dispara"


def tono(estado):
    """Color de la fila segun el estado: espera | alerta | ok | mal | ''."""
    e = (estado or "").lower()
    if not e:
        return ""
    if e.startswith("completada"):
        return "ok"
    if e.startswith(("dispara", "en cola", "vendiendo")):
        return "alerta"
    if e.startswith("esperando"):
        return "espera"
    return "mal"


# ── Estilo ──────────────────────────────────────────────────────────────────
def aplicar_estilo(raiz):
    raiz.configure(bg=C["fondo"])
    st = ttk.Style(raiz)
    st.theme_use("clam")
    base = (FUENTE, 10)
    st.configure(".", font=base, background=C["fondo"], foreground=C["texto"],
                 bordercolor=C["borde"], lightcolor=C["borde"], darkcolor=C["borde"],
                 focuscolor=C["acento"])
    st.configure("TFrame", background=C["fondo"])
    st.configure("Tarjeta.TFrame", background=C["tarjeta"])
    st.configure("TLabel", background=C["fondo"])
    st.configure("Tarjeta.TLabel", background=C["tarjeta"])
    st.configure("Suave.TLabel", background=C["tarjeta"], foreground=C["suave"])
    st.configure("Titulo.TLabel", background=C["tarjeta"], font=(FUENTE, 12, "bold"))
    st.configure("Seccion.TLabel", background=C["tarjeta"], foreground=C["acento"],
                 font=(FUENTE, 9, "bold"))
    st.configure("KpiValor.TLabel", background=C["tarjeta"], font=(FUENTE, 18, "bold"))
    st.configure("KpiTitulo.TLabel", background=C["tarjeta"], foreground=C["suave"],
                 font=(FUENTE, 9))

    st.configure("TNotebook", background=C["fondo"], borderwidth=0, tabmargins=(0, 6, 0, 0))
    st.configure("TNotebook.Tab", padding=(18, 8), font=(FUENTE, 10),
                 background=C["fondo"], foreground=C["suave"], borderwidth=0)
    st.map("TNotebook.Tab",
           background=[("selected", C["tarjeta"])],
           foreground=[("selected", C["acento"])],
           font=[("selected", (FUENTE, 10, "bold"))])

    st.configure("TButton", padding=(14, 7), background=C["tarjeta"],
                 foreground=C["texto"], borderwidth=1, relief="solid")
    st.map("TButton", background=[("active", C["neutro_fondo"])])
    st.configure("TButton", bordercolor=C["borde"], lightcolor=C["tarjeta"],
                 darkcolor=C["tarjeta"])
    st.configure("Vertical.TScrollbar", background=C["borde"], troughcolor=C["tarjeta"],
                 bordercolor=C["tarjeta"], arrowcolor=C["suave"],
                 lightcolor=C["borde"], darkcolor=C["borde"], gripcount=0, arrowsize=12)
    st.configure("Primario.TButton", background=C["acento"], foreground="#FFFFFF",
                 bordercolor=C["acento"], font=(FUENTE, 10, "bold"))
    st.map("Primario.TButton",
           background=[("disabled", C["borde"]), ("active", C["acento_hover"])],
           bordercolor=[("active", C["acento_hover"])])
    st.configure("Peligro.TButton", background=C["mal"], foreground="#FFFFFF",
                 bordercolor=C["mal"], font=(FUENTE, 10, "bold"))
    st.map("Peligro.TButton", background=[("active", "#A42530")])

    st.configure("TEntry", padding=6, fieldbackground="#FFFFFF")
    st.configure("TCombobox", padding=5, fieldbackground="#FFFFFF",
                 arrowsize=14)
    st.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")],
           foreground=[("readonly", C["texto"])],
           selectbackground=[("readonly", "#FFFFFF")],
           selectforeground=[("readonly", C["texto"])])
    raiz.option_add("*TCombobox*Listbox.font", base)
    raiz.option_add("*TCombobox*Listbox.selectBackground", C["acento"])

    st.configure("Treeview", rowheight=30, background=C["tarjeta"],
                 fieldbackground=C["tarjeta"], borderwidth=0, font=base)
    st.map("Treeview", background=[("selected", C["seleccion"])],
           foreground=[("selected", C["texto"])])
    st.configure("Treeview.Heading", background=C["neutro_fondo"],
                 foreground=C["suave"], font=(FUENTE, 9, "bold"),
                 relief="flat", padding=(8, 6))
    st.map("Treeview.Heading", background=[("active", C["borde"])])
    st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])


def tarjeta(padre, **grid):
    """Panel blanco con borde fino. Devuelve el frame interior (con padding)."""
    marco = tk.Frame(padre, bg=C["tarjeta"], highlightthickness=1,
                     highlightbackground=C["borde"])
    if grid:
        marco.grid(**grid)
    interior = ttk.Frame(marco, style="Tarjeta.TFrame", padding=16)
    interior.pack(fill="both", expand=True)
    return marco, interior


def tabla(padre, columnas, alto):
    marco = ttk.Frame(padre, style="Tarjeta.TFrame")
    tv = ttk.Treeview(marco, columns=list(columnas), show="headings", height=alto)
    for c, (texto, ancho, ali) in columnas.items():
        tv.heading(c, text=texto, anchor=ali)
        tv.column(c, width=ancho, anchor=ali, stretch=(c == "estado"))
    barra = ttk.Scrollbar(marco, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=barra.set)
    tv.pack(side="left", fill="both", expand=True)
    barra.pack(side="right", fill="y")
    tv.tag_configure("par", background=C["fila_par"])
    tv.tag_configure("ok", background=C["ok_fondo"], foreground=C["ok"])
    tv.tag_configure("alerta", background=C["alerta_fondo"], foreground=C["alerta"])
    tv.tag_configure("mal", background=C["mal_fondo"], foreground=C["mal"])
    tv.tag_configure("espera", foreground=C["texto"])
    return marco, tv


# ── Aplicacion ──────────────────────────────────────────────────────────────
class App(ttk.Frame):
    def __init__(self, maestro):
        super().__init__(maestro)
        self.pack(fill="both", expand=True)
        self.maestro = maestro
        self.monitoreando = False
        self.parar = threading.Event()
        self.noticias = queue.Queue()        # hilos -> interfaz
        self.disparados = set()              # servidores ya encargados en este monitoreo
        self.filas = {}                      # item -> {"servidor", "precio_min", "millones"}
        self.ultima_lectura = None
        self.vendedor = Vendedor(al_terminar=lambda r: self.noticias.put(("venta", r)))

        self._construir_cabecera()
        self._construir_pie()
        cuerpo = ttk.Frame(self, padding=(20, 16, 20, 12))
        cuerpo.pack(fill="both", expand=True)
        self._construir_kpis(cuerpo)

        nb = ttk.Notebook(cuerpo)
        nb.pack(fill="both", expand=True, pady=(14, 0))
        self.tab_monitoreo = ttk.Frame(nb, padding=(0, 12, 0, 0))
        self.tab_datos     = ttk.Frame(nb, padding=(0, 12, 0, 0))
        self.tab_registro  = ttk.Frame(nb, padding=(0, 12, 0, 0))
        nb.add(self.tab_monitoreo, text="Monitoreo")
        nb.add(self.tab_datos,     text="Datos de venta")
        nb.add(self.tab_registro,  text="Registro de ventas")

        self._construir_monitoreo()
        self._construir_datos()
        self._construir_registro()
        self._refrescar_kpis()
        self.after(REFRESCO_COLA_MS, self._leer_noticias)

    # ── Cabecera, indicadores y pie ──────────────────────────────────────
    def _construir_cabecera(self):
        cab = tk.Frame(self, bg=C["cabecera"], height=72)
        cab.pack(fill="x")
        cab.pack_propagate(False)
        izq = tk.Frame(cab, bg=C["cabecera"])
        izq.pack(side="left", padx=20)
        tk.Label(izq, text="Venta automática", bg=C["cabecera"], fg=C["cabecera_txt"],
                 font=(FUENTE, 16, "bold")).pack(anchor="w", pady=(12, 0))
        tk.Label(izq, text="Monitor de precios y venta asistida",
                 bg=C["cabecera"], fg=C["cabecera_sub"], font=(FUENTE, 9)).pack(anchor="w")
        self.pastilla = tk.Label(cab, text="●  Detenido", bg="#24324A",
                                 fg=C["cabecera_sub"], font=(FUENTE, 10, "bold"),
                                 padx=14, pady=5)
        self.pastilla.pack(side="right", padx=20)

    def _construir_kpis(self, padre):
        fila = ttk.Frame(padre)
        fila.pack(fill="x")
        self.kpi = {}
        defs = [("vigilados", "Servidores vigilados"),
                ("objetivo", "En precio objetivo"),
                ("ventas", "Ventas completadas"),
                ("lectura", "Última lectura")]
        for col, (clave, titulo) in enumerate(defs):
            marco, t = tarjeta(fila, row=0, column=col, sticky="nsew",
                               padx=(0 if col == 0 else 12, 0))
            ttk.Label(t, text=titulo.upper(), style="KpiTitulo.TLabel").pack(anchor="w")
            v = ttk.Label(t, text="—", style="KpiValor.TLabel")
            v.pack(anchor="w", pady=(4, 0))
            self.kpi[clave] = v
            fila.columnconfigure(col, weight=1, uniform="kpi")

    def _construir_pie(self):
        pie = tk.Frame(self, bg=C["tarjeta"], highlightthickness=1,
                       highlightbackground=C["borde"])
        pie.pack(fill="x", side="bottom")
        self.lbl_pie = tk.Label(pie, text="Listo.", bg=C["tarjeta"], fg=C["suave"],
                                font=(FUENTE, 9), anchor="w", padx=20, pady=6)
        self.lbl_pie.pack(side="left", fill="x", expand=True)
        tk.Label(pie, text=f"Lectura cada {INTERVALO_S} s  ·  el captcha y el envío "
                           "los confirma una persona", bg=C["tarjeta"], fg=C["suave"],
                 font=(FUENTE, 9), padx=20).pack(side="right")

    def _avisar(self, texto, color=None):
        self.lbl_pie.config(text=texto, fg=color or C["suave"])

    def _refrescar_kpis(self, en_objetivo=None):
        self.kpi["vigilados"].config(text=str(len(self.filas)))
        if en_objetivo is not None:
            self.kpi["objetivo"].config(text=str(en_objetivo))
        hechas = sum(1 for f in registro.leer_todo()
                     if f.get("estado", "").startswith("completada"))
        self.kpi["ventas"].config(text=str(hechas))
        self.kpi["lectura"].config(
            text=self.ultima_lectura.strftime("%H:%M:%S") if self.ultima_lectura else "—")

    # ── Pestana Monitoreo ────────────────────────────────────────────────
    def _construir_monitoreo(self):
        t = self.tab_monitoreo
        t.columnconfigure(0, weight=1)
        t.rowconfigure(1, weight=1)

        # Formulario de alta
        _m, f = tarjeta(t, row=0, column=0, sticky="ew")
        ttk.Label(f, text="Añadir servidor", style="Titulo.TLabel").grid(
            row=0, column=0, columnspan=8, sticky="w", pady=(0, 10))
        etiquetas = ("Servidor", "Precio objetivo (por millón)", "Millones a vender")
        for i, txt in enumerate(etiquetas):
            ttk.Label(f, text=txt, style="Suave.TLabel").grid(
                row=1, column=i, sticky="w", padx=(0, 12))

        opciones = [f"{s}   ·   {j}" for j, lista in SERVIDORES.items() for s in lista]
        self.cmb_srv = ttk.Combobox(f, values=opciones, state="readonly", width=28)
        self.cmb_srv.grid(row=2, column=0, sticky="w", padx=(0, 12), pady=(4, 0))
        self.ent_precio = ttk.Entry(f, width=16)
        self.ent_precio.grid(row=2, column=1, sticky="w", padx=(0, 12), pady=(4, 0))
        self.ent_millones = ttk.Entry(f, width=16)
        self.ent_millones.grid(row=2, column=2, sticky="w", padx=(0, 12), pady=(4, 0))
        ttk.Button(f, text="Añadir", style="Primario.TButton",
                   command=self._anadir_srv).grid(row=2, column=3, sticky="w", pady=(4, 0))
        f.columnconfigure(4, weight=1)
        ttk.Button(f, text="Quitar seleccionado",
                   command=self._quitar_srv).grid(row=2, column=5, sticky="e", pady=(4, 0))
        self.ent_millones.bind("<Return>", lambda _e: self._anadir_srv())

        # Tabla
        _m, tt = tarjeta(t, row=1, column=0, sticky="nsew", pady=(12, 0))
        cab = ttk.Frame(tt, style="Tarjeta.TFrame")
        cab.pack(fill="x", pady=(0, 10))
        ttk.Label(cab, text="Servidores vigilados", style="Titulo.TLabel").pack(side="left")
        self.btn_monitor = ttk.Button(cab, text="▶  Iniciar monitoreo",
                                      style="Primario.TButton",
                                      command=self._toggle_monitoreo)
        self.btn_monitor.pack(side="right")
        self.lbl_metodo = ttk.Label(cab, text="", style="Suave.TLabel")
        self.lbl_metodo.pack(side="right", padx=12)

        marco, self.tabla_srv = tabla(tt, COLS_SRV, 7)
        marco.pack(fill="both", expand=True)

        for s in cargar_json(FICHERO_SERVIDORES, []):
            self._insertar_fila(s.get("servidor", ""), s.get("precio_min", ""),
                                s.get("millones", ""))

    def _insertar_fila(self, servidor, precio_min, millones):
        nombre, juego = juego_de(servidor)
        item = self.tabla_srv.insert("", "end", values=(
            nombre, juego, _precio(precio_min), "—", "—", _millones(millones), "—", ""))
        self.filas[item] = {"servidor": nombre, "precio_min": str(precio_min),
                            "millones": str(millones)}
        self._pintar_filas()
        return item

    def _pintar_filas(self):
        for n, item in enumerate(self.tabla_srv.get_children()):
            est = self.tabla_srv.set(item, "estado")
            tag = tono(est)
            tags = [tag] if tag and tag != "espera" else []
            if not tags and n % 2:
                tags = ["par"]
            self.tabla_srv.item(item, tags=tags)

    def _filas(self):
        """[(item, servidor, precio_min, millones)] en orden de la tabla."""
        return [(i, self.filas[i]["servidor"], self.filas[i]["precio_min"],
                 self.filas[i]["millones"]) for i in self.tabla_srv.get_children()]

    def _poner(self, item, precio=None, estado=None, simbolo=""):
        f = self.filas[item]
        if precio is not None:
            pmin, mill = a_float(f["precio_min"]), a_float(f["millones"])
            if precio == "":
                self.tabla_srv.set(item, "precio_actual", "—")
                self.tabla_srv.set(item, "distancia", "—")
                self.tabla_srv.set(item, "valor", "—")
            else:
                self.tabla_srv.set(item, "precio_actual", _precio(precio))
                if pmin:
                    self.tabla_srv.set(item, "distancia", f"{(precio - pmin) / pmin * 100:+.1f} %")
                if mill:
                    self.tabla_srv.set(item, "valor", f"{precio * mill:,.2f} {simbolo}".strip())
        if estado is not None:
            self.tabla_srv.set(item, "estado", estado[:1].upper() + estado[1:])
        self._pintar_filas()

    def _anadir_srv(self):
        elegido = self.cmb_srv.get().split("·")[0].strip()
        precio = self.ent_precio.get().strip()
        millones = self.ent_millones.get().strip()
        if not elegido or a_float(precio) is None or not a_float(millones):
            messagebox.showwarning("Faltan datos",
                                   "Elige un servidor e indica precio objetivo y millones (números).")
            return
        if any(s.lower() == elegido.lower() for _i, s, _p, _m in self._filas()):
            messagebox.showinfo("Ya está en la lista",
                                f"{elegido} ya se está vigilando. Quítalo primero para cambiarlo.")
            return
        self._insertar_fila(elegido, precio, millones)
        self.cmb_srv.set("")
        for e in (self.ent_precio, self.ent_millones):
            e.delete(0, "end")
        self._guardar_srv()
        self._refrescar_kpis()

    def _quitar_srv(self):
        for item in self.tabla_srv.selection():
            self.tabla_srv.delete(item)
            self.filas.pop(item, None)
        self._pintar_filas()
        self._guardar_srv()
        self._refrescar_kpis()

    def _guardar_srv(self):
        guardar_json(FICHERO_SERVIDORES, [
            {"servidor": s, "precio_min": p, "millones": m}
            for _i, s, p, m in self._filas()])

    def _toggle_monitoreo(self):
        if self.monitoreando:
            self.monitoreando = False
            self.parar.set()
            self.btn_monitor.config(text="▶  Iniciar monitoreo", style="Primario.TButton")
            self.pastilla.config(text="●  Detenido", bg="#24324A", fg=C["cabecera_sub"])
            self._avisar("Monitoreo detenido.")
            return

        datos = self._datos_actuales()
        faltan = [k for k in OBLIGATORIOS if not datos.get(k)]
        if faltan:
            nombres = {k: e for k, e, _s in CAMPOS_VENTA}
            messagebox.showwarning("Faltan datos de venta",
                                   "Completa en 'Datos de venta': " +
                                   ", ".join(nombres[k] for k in faltan))
            return
        if not self._filas():
            messagebox.showwarning("Sin servidores", "Añade al menos un servidor.")
            return

        self.monitoreando = True
        self.disparados.clear()
        self.parar = threading.Event()
        self.btn_monitor.config(text="■  Detener monitoreo", style="Peligro.TButton")
        self.pastilla.config(text="●  Monitoreando", bg="#123D2B", fg="#4CD08D")
        self.lbl_metodo.config(text=f"Precios en {datos['metodo_pago'].upper()}")
        self._avisar("Leyendo precios…", C["acento"])
        threading.Thread(target=self._bucle_precios, args=(self.parar,),
                         daemon=True, name="precios").start()

    def _bucle_precios(self, parar):
        """Hilo: lee el sv-config cada INTERVALO_S y lo pasa a la interfaz."""
        while not parar.is_set():
            try:
                self.noticias.put(("precios", lector_precios.descargar_config()))
            except Exception as e:
                self.noticias.put(("fallo_lectura", f"{type(e).__name__}: {e}"))
            parar.wait(INTERVALO_S)

    def _con_precios(self, cfg):
        if not self.monitoreando:
            return
        datos = self._datos_actuales()
        metodo = datos["metodo_pago"]
        en_objetivo = 0
        for item, srv, pmin, mill in self._filas():
            accion, p, estado = evaluar(cfg, srv, pmin, mill, metodo)
            nombre = next((n for n in cfg.get("servers", {}) if n.lower() == srv.lower()), srv)
            simbolo = (cfg.get("servers", {}).get(nombre, {}).get("prices", {})
                       .get(metodo, {}).get("s", ""))
            if p is not None and a_float(pmin) is not None and p >= a_float(pmin):
                en_objetivo += 1
            if srv.lower() in self.disparados:
                self._poner(item, p if p is not None else "", simbolo=simbolo)
                continue                         # el estado lo pone la venta
            if accion == "vender":
                self.disparados.add(srv.lower())
                estado = "en cola: abriendo Chrome…"
                self.vendedor.pedir_venta(Orden(
                    servidor=nombre, millones=float(a_float(mill)), metodo=metodo,
                    datos=datos, precio_visto=p))
            self._poner(item, p if p is not None else "", estado, simbolo)
        self.ultima_lectura = datetime.now()
        self._refrescar_kpis(en_objetivo)
        self._avisar(f"Precios actualizados a las {self.ultima_lectura:%H:%M:%S}.")

    def _con_venta(self, r):
        if r.estado == "login":
            self.lbl_datos.config(text=r.detalle, foreground=C["acento"])
            return
        o = r.orden
        if o is not None:
            for item, srv, _p, _m in self._filas():
                if srv.lower() == o.servidor.lower():
                    self._poner(item, estado=f"{r.estado}: {r.detalle}"[:120])
            # Al log solo lo que llego a tocar la web de venta.
            if r.estado in ("completada", "rechazada", "sin_confirmar", "cancelada"):
                registro.registrar(o.servidor, o.precio_visto,
                                   int(o.millones * 1_000_000),
                                   o.datos.get("direccion_trc20", ""),
                                   r.estado if r.estado == "completada"
                                   else f"{r.estado}: {r.detalle}")
                self._cargar_registro()
                self._refrescar_kpis()
        if r.estado == "completada":
            self._avisar(f"Venta completada en {o.servidor}.", C["ok"])
            messagebox.showinfo("Venta completada", f"{o.servidor}: {r.detalle}")
        elif r.estado in ("sin_sesion", "error"):
            self._avisar(r.detalle, C["mal"])
            messagebox.showwarning("Venta no realizada", r.detalle)

    def _leer_noticias(self):
        try:
            while True:
                tipo, dato = self.noticias.get_nowait()
                if tipo == "precios":
                    self._con_precios(dato)
                elif tipo == "fallo_lectura" and self.monitoreando:
                    self._avisar(f"No se pudieron leer los precios: {dato}"[:140], C["mal"])
                elif tipo == "venta":
                    self._con_venta(dato)
        except queue.Empty:
            pass
        self.after(REFRESCO_COLA_MS, self._leer_noticias)

    # ── Pestana Datos de venta ───────────────────────────────────────────
    def _construir_datos(self):
        t = self.tab_datos
        _m, f = tarjeta(t)
        _m.pack(anchor="nw", fill="x")
        ttk.Label(f, text="Datos del formulario de venta", style="Titulo.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Se guardan solo en este equipo (datos_venta.json).",
                  style="Suave.TLabel").grid(row=1, column=0, columnspan=2,
                                             sticky="w", pady=(2, 6))
        self.campos = {}
        datos = cargar_json(FICHERO_DATOS_VENTA, {})
        fila, seccion = 2, None
        for clave, etiqueta, sec in CAMPOS_VENTA:
            if sec != seccion:
                seccion = sec
                ttk.Label(f, text=sec.upper(), style="Seccion.TLabel").grid(
                    row=fila, column=0, columnspan=2, sticky="w", pady=(14, 4))
                fila += 1
            ttk.Label(f, text=etiqueta, style="Tarjeta.TLabel").grid(
                row=fila, column=0, sticky="w", pady=5, padx=(0, 24))
            if clave == "metodo_pago":
                ent = ttk.Combobox(f, values=METODOS, state="readonly", width=42)
                ent.set(normalizar_metodo(datos.get(clave, "")) or "Usdt")
            else:
                ent = ttk.Entry(f, width=45)
                ent.insert(0, datos.get(clave, ""))
            ent.grid(row=fila, column=1, sticky="w", pady=5)
            self.campos[clave] = ent
            fila += 1

        barra = ttk.Frame(f, style="Tarjeta.TFrame")
        barra.grid(row=fila, column=1, sticky="w", pady=(18, 0))
        ttk.Button(barra, text="Guardar datos", style="Primario.TButton",
                   command=self._guardar_datos).pack(side="left")
        ttk.Button(barra, text="Iniciar sesión en el sitio",
                   command=self.vendedor.pedir_login).pack(side="left", padx=10)
        self.lbl_datos = ttk.Label(f, text="", style="Tarjeta.TLabel", wraplength=520)
        self.lbl_datos.grid(row=fila + 1, column=1, sticky="w", pady=(10, 0))

    def _datos_actuales(self):
        d = {clave: ent.get().strip() for clave, ent in self.campos.items()}
        d["metodo_pago"] = normalizar_metodo(d["metodo_pago"])
        return d

    def _guardar_datos(self):
        guardar_json(FICHERO_DATOS_VENTA, self._datos_actuales())
        self.lbl_datos.config(text="✓  Datos guardados en este equipo.", foreground=C["ok"])

    # ── Pestana Registro ─────────────────────────────────────────────────
    def _construir_registro(self):
        t = self.tab_registro
        _m, f = tarjeta(t)
        _m.pack(fill="both", expand=True)
        cab = ttk.Frame(f, style="Tarjeta.TFrame")
        cab.pack(fill="x", pady=(0, 10))
        ttk.Label(cab, text="Historial de ventas", style="Titulo.TLabel").pack(side="left")
        ttk.Button(cab, text="Actualizar", command=self._cargar_registro).pack(side="right")
        marco, self.tabla_log = tabla(f, COLS_LOG, 10)
        marco.pack(fill="both", expand=True)
        self.lbl_vacio = ttk.Label(marco, text="Aún no hay ventas registradas.",
                                   style="Suave.TLabel")
        self._cargar_registro()

    def _cargar_registro(self):
        for i in self.tabla_log.get_children():
            self.tabla_log.delete(i)
        filas = registro.leer_todo()
        if filas:
            self.lbl_vacio.place_forget()
        else:
            self.lbl_vacio.place(relx=0.5, rely=0.45, anchor="center")
        for n, fila in enumerate(reversed(filas)):   # la mas nueva arriba
            monedas = a_float(fila.get("monedas_vendidas", ""))
            valores = [fila.get("fecha_hora", ""), fila.get("servidor", ""),
                       _precio(fila.get("precio_tomado", "")),
                       f"{monedas:,.0f}" if monedas else fila.get("monedas_vendidas", ""),
                       ocultar_cuenta(fila.get("direccion_trc20", "")),
                       fila.get("estado", "")]
            tag = tono(fila.get("estado", ""))
            tags = [tag] if tag in ("ok", "mal") else (["par"] if n % 2 else [])
            self.tabla_log.insert("", "end", values=valores, tags=tags)

    def cerrar(self):
        self.parar.set()
        self.vendedor.cerrar()
        self.maestro.destroy()


def _precio(x):
    v = a_float(x)
    return f"{v:.3f}" if v is not None else str(x or "")


def _millones(x):
    v = a_float(x)
    return f"{v:g} M" if v is not None else str(x or "")


def main():
    try:                      # texto nitido en pantallas con escalado (Windows)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    raiz = tk.Tk()
    raiz.title("Venta automática")
    raiz.geometry("1080x720")
    raiz.minsize(940, 620)
    aplicar_estilo(raiz)
    app = App(raiz)
    raiz.protocol("WM_DELETE_WINDOW", app.cerrar)
    raiz.mainloop()


if __name__ == "__main__":
    main()
