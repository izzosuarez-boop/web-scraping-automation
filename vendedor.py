# -*- coding: utf-8 -*-
"""Vende en el sitio comprador con un Chrome de verdad (Playwright).

QUE HACE SOLO Y QUE DEJA AL HUMANO
- Solo: abre la pagina de venta con la sesion guardada, elige juego y
  servidor, pone la cantidad, el metodo de pago y los datos del formulario.
- Humano: el reCAPTCHA y el boton "Vender". Cuando el formulario esta listo
  la ventana pasa al frente y suena un aviso; tienes TIEMPO_CAPTCHA_S para
  marcar el captcha y enviar.
- Solo otra vez: escucha la respuesta del POST a route=orders/sell (el mismo
  fetch que hace el JS del formulario) y devuelve si la venta entro o el error que dio la
  web. Si la web rechaza (captcha caducado, campo mal), puedes corregir y
  reenviar dentro del mismo plazo: cuenta el ULTIMO envio.

COMO ESTA HECHO EL FORMULARIO (leido de el JS del formulario el 2026-11-23)
- Pestanas de juego [data-game], fichas de servidor .sv-tile[data-server],
  cantidad #sv-qty (en MILLONES), metodo #sv-payment.
- Datos: [data-sv=name] (beneficiario), [data-sv=account] (cuenta/direccion),
  #sv-character, #sv-comment, #sv-whatsapp. Si la cuenta ya tiene datos
  guardados, el bloque sale plegado en un resumen; se despliega con
  [data-sv=edit].
- Cambiar de metodo de pago REESCRIBE el campo account (loadPayout), por eso
  el orden es: juego, servidor, metodo, cantidad y solo despues los datos.
- Sin sesion, el formulario no existe: sale "Inicia sesion para vender" y el
  sv-config trae logged=false. Se inicia sesion UNA vez con pedir_login(); la
  cookie queda en perfil_chrome/ (local, fuera del repo).

Playwright (API sincrona) vive entero en UN hilo propio: la interfaz le pasa
encargos por una cola y recibe los resultados por el callback.
"""

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import winsound
except ImportError:          # fuera de Windows no hay pitido, no pasa nada
    winsound = None

from lector_precios import URL_VENTA

# --- AJUSTES ---
DIR = Path(__file__).parent
PERFIL = DIR / "perfil_chrome"          # cookies del sitio comprador: NUNCA al repo
CANAL = "chrome"                        # el Chrome instalado; None = chromium de Playwright
URL_LOGIN = "https://buyer-site.example/acceso.html"
TIEMPO_CAPTCHA_S = 300                  # plazo para marcar el captcha y enviar
TIMEOUT_CARGA_MS = 30_000
RUTA_ENVIO = "route=orders/sell"      # el POST de el JS del formulario


@dataclass
class Orden:
    servidor: str
    millones: float
    metodo: str                          # clave exacta del sitio comprador: "Usdt", "Paypal"...
    datos: dict                          # el contenido de datos_venta.json
    precio_visto: float | None = None    # el precio que disparo la orden (para el log)


@dataclass
class Resultado:
    estado: str          # completada | rechazada | sin_confirmar | cancelada | sin_sesion | cerrado | error
    detalle: str
    orden: Orden | None = None
    respuestas: list = field(default_factory=list)   # JSON de cada envio, en orden


class Vendedor:
    """Dueno unico del navegador. Encargos: pedir_login(), pedir_venta()."""

    def __init__(self, al_terminar, perfil=PERFIL, canal=CANAL, headless=False,
                 url_venta=URL_VENTA, preparar_contexto=None,
                 tiempo_captcha_s=TIEMPO_CAPTCHA_S, avisar=True):
        self._al_terminar = al_terminar            # callback(Resultado), se llama desde el hilo del navegador
        self._perfil = Path(perfil)
        self._canal = canal
        self._headless = headless
        self._url_venta = url_venta
        self._preparar = preparar_contexto         # gancho para las pruebas (rutas simuladas)
        self._tiempo_captcha_s = tiempo_captcha_s
        self._avisar = avisar
        self._cola = queue.Queue()
        self._ctx = None
        self.ocupado = False
        self._hilo = threading.Thread(target=self._bucle, daemon=True,
                                      name="vendedor")
        self._hilo.start()

    # ── API para la interfaz (cualquier hilo) ────────────────────────────
    def pedir_login(self):
        self._cola.put(("login", None))

    def pedir_venta(self, orden):
        self._cola.put(("venta", orden))

    def cerrar(self, esperar_s=5):
        self._cola.put(("fin", None))
        self._hilo.join(esperar_s)

    # ── Hilo del navegador ───────────────────────────────────────────────
    def _bucle(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            self._pw = p
            while True:
                tarea, arg = self._cola.get()
                if tarea == "fin":
                    break
                self.ocupado = True
                try:
                    if tarea == "login":
                        self._login()
                    elif tarea == "venta":
                        self._al_terminar(self._vender(arg))
                except Exception as e:                       # nunca tumbar el hilo
                    self._al_terminar(Resultado("error", f"{type(e).__name__}: {e}",
                                                arg if tarea == "venta" else None))
                finally:
                    self.ocupado = False
            if self._ctx:
                try:
                    self._ctx.close()
                except Exception:
                    pass

    def _contexto(self):
        """Reutiliza la ventana abierta; si la cerraste, abre otra."""
        if self._ctx is not None:
            try:
                self._ctx.pages            # lanza si el contexto murio
                if self._ctx.browser is None or self._ctx.browser.is_connected():
                    return self._ctx
            except Exception:
                pass
            self._ctx = None
        self._perfil.mkdir(exist_ok=True)
        ctx = self._pw.chromium.launch_persistent_context(
            str(self._perfil), channel=self._canal, headless=self._headless,
            no_viewport=True, args=["--start-maximized"])
        ctx.on("close", lambda _c: setattr(self, "_ctx", None))
        if self._preparar:
            self._preparar(ctx)
        self._ctx = ctx
        return ctx

    def _pagina(self):
        ctx = self._contexto()
        vivas = [pg for pg in ctx.pages if not pg.is_closed()]
        return vivas[0] if vivas else ctx.new_page()

    def _login(self):
        pg = self._pagina()
        pg.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=TIMEOUT_CARGA_MS)
        pg.bring_to_front()
        self._al_terminar(Resultado(
            "login", "Ventana abierta: inicia sesion en el sitio comprador. La sesion queda "
                     "guardada en el perfil; puedes dejar la ventana abierta."))

    def _vender(self, o):
        pg = self._pagina()
        pg.goto(self._url_venta, wait_until="domcontentloaded",
                timeout=TIMEOUT_CARGA_MS)
        cfg = pg.evaluate(
            "() => JSON.parse(document.getElementById('sv-config').textContent)")

        if not cfg.get("logged"):
            return Resultado("sin_sesion", "No hay sesion iniciada en el sitio comprador: "
                             "pulsa 'Iniciar sesion en el sitio' una vez.", o)
        srv = cfg.get("servers", {}).get(o.servidor)
        if not srv or not srv.get("open"):
            return Resultado("cerrado", f"{o.servidor} ya no compra.", o)
        if not srv.get("prices", {}).get(o.metodo, {}).get("o"):
            return Resultado("cerrado", f"{o.servidor} no paga en {o.metodo}.", o)

        # Orden obligado: juego -> servidor -> metodo -> cantidad -> datos.
        pg.click(f'[role=tab][data-game="{srv["game"]}"]')
        pg.click(f'.sv-tile[data-server="{o.servidor}"]')
        pg.select_option("#sv-payment", o.metodo)
        pg.fill("#sv-qty", _num(o.millones))

        editar = pg.locator('[data-sv="edit"]')
        if editar.count() and editar.first.is_visible():
            editar.first.click()

        d = o.datos
        _rellenar(pg, '[data-sv="name"]', d.get("beneficiario", ""))
        _rellenar(pg, '[data-sv="account"]', d.get("direccion_trc20", ""))
        _rellenar(pg, "#sv-character", d.get("personaje", ""))
        _rellenar(pg, "#sv-comment", d.get("codigo_intercambio", ""))
        _rellenar(pg, "#sv-whatsapp", d.get("whatsapp", ""))

        # Turno del humano: captcha + enviar. Nosotros solo escuchamos.
        pg.bring_to_front()
        if self._avisar and winsound:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

        respuestas = []
        limite = time.monotonic() + self._tiempo_captcha_s
        es_envio = (lambda r: RUTA_ENVIO in r.url
                    and r.request.method == "POST")
        while True:
            queda_ms = int((limite - time.monotonic()) * 1000)
            if queda_ms <= 0:
                ultimo = respuestas[-1].get("error") if respuestas else None
                return Resultado("sin_confirmar",
                                 "No se envio a tiempo" + (f" (ultimo error: {ultimo})" if ultimo else ""),
                                 o, respuestas)
            try:
                with pg.expect_response(es_envio, timeout=queda_ms) as info:
                    pass
            except Exception as e:
                if "Timeout" in type(e).__name__:
                    continue                    # el bucle cierra por el limite
                return Resultado("cancelada", "Se cerro la ventana antes de enviar.",
                                 o, respuestas)
            try:
                js = info.value.json()
            except Exception:
                js = {"ok": False, "error": f"respuesta no JSON (HTTP {info.value.status})"}
            respuestas.append(js)
            if js.get("ok"):
                return Resultado("completada", js.get("message") or "Venta registrada.",
                                 o, respuestas)
            if js.get("redirect"):              # p. ej. la sesion caduco
                return Resultado("rechazada", f"La web redirigio a {js['redirect']}",
                                 o, respuestas)
            # Rechazo con error: se puede corregir y reenviar dentro del plazo.


def _rellenar(pg, selector, valor):
    """Escribe en el campo si existe y hay valor (no todos salen en todas las cuentas)."""
    if not valor:
        return
    loc = pg.locator(selector)
    if loc.count() == 0:
        return
    loc.first.fill(str(valor))
    loc.first.dispatch_event("blur")     # el JS del formulario valida la cuenta al salir del campo


def _num(x):
    """100.0 -> '100', 12.5 -> '12.5' (el campo de la web es texto)."""
    return f"{x:g}"
