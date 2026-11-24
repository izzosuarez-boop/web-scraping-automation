# -*- coding: utf-8 -*-
"""Pruebas sin red: la decision de disparar y el rellenado del formulario.

El formulario real solo existe con sesion iniciada, asi que aqui se usa una
pagina SIMULADA que copia lo que importa de el JS del formulario (2026-11-23):
  - las pestanas de juego REDIBUJAN las fichas de servidor,
  - cambiar el metodo de pago REESCRIBE la cuenta (loadPayout),
  - con datos guardados el bloque sale plegado y se abre con [data-sv=edit],
  - el envio es un fetch POST a cfg.urls.add que responde JSON.
Todas las demas peticiones se abortan: ni una sale a internet.

Ejecutar desde web-scraping-automation/:  python -m unittest discover -s tests -v
"""

import importlib.machinery
import importlib.util
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DIR))

from vendedor import Orden, Vendedor  # noqa: E402

_cargador = importlib.machinery.SourceFileLoader("interfaz", str(DIR / "interfaz.pyw"))
_spec = importlib.util.spec_from_loader("interfaz", _cargador)
interfaz = importlib.util.module_from_spec(_spec)
_cargador.exec_module(interfaz)

URL_FALSA = "https://simulado.test/vender.html"
URL_ENVIO = "https://simulado.test/index.php?route=orders/sell"

CFG = {
    "servers": {
        "Servidor2":     {"game": "Juego A", "open": True,
                      "prices": {"Usdt": {"p": 7.5, "s": "USDT", "o": True},
                                 "Paypal": {"p": 7.0, "s": "EUR", "o": True}}},
        "Servidor5":  {"game": "JuegoB", "open": True,
                      "prices": {"Usdt": {"p": 0.9, "s": "USDT", "o": True}}},
        "Servidor4": {"game": "JuegoB", "open": False,
                       "prices": {"Usdt": {"p": 0.6, "s": "USDT", "o": False}}},
    },
    "minimums": {"Usdt": 20, "Paypal": 20},
    "urls": {"add": URL_ENVIO},
    "logged": True,
}

DATOS = {"metodo_pago": "Usdt", "beneficiario": "Ana Prueba",
         "direccion_trc20": "TPruebaDireccionFalsa1234567890abc",
         "personaje": "Pjprueba", "codigo_intercambio": "XK-77",
         "whatsapp": "+34 600000000"}


def pagina(cfg):
    """HTML minimo que se comporta como el formulario real en lo que importa."""
    return """<!doctype html><html><body>
<script type="application/json" id="sv-config">%s</script>
<div data-sv="tabs">
  <button type="button" role="tab" data-game="JuegoB">JuegoB</button>
  <button type="button" role="tab" data-game="Juego A">Juego A</button>
</div>
<div data-sv="tiles"></div>
<input id="sv-qty" type="text" value="100" data-sv="qty" form="sv-form" name="quantity">
<form id="sv-form" novalidate>
  <input type="hidden" name="server" value="Servidor5" data-sv="server">
  <select id="sv-payment" name="payment" data-sv="payment">
    <option value="Paypal" selected>Paypal</option><option value="Usdt">USDT</option>
  </select>
  <div data-sv="summary">Guardado: <button type="button" data-sv="edit">Cambiar</button></div>
  <div id="bloque" hidden>
    <input data-sv="name" name="name" value="Nombre Viejo">
    <input data-sv="account" name="account" value="viejo@correo.com">
  </div>
  <input id="sv-character" name="character">
  <input id="sv-comment" name="comment">
  <input id="sv-whatsapp" name="whatsapp">
  <button type="submit" data-sv="submit">Vender</button>
</form>
<script>
  var cfg = JSON.parse(document.getElementById('sv-config').textContent);
  var state = {server: 'Servidor5', game: 'JuegoB'};
  var tiles = document.querySelector('[data-sv=tiles]');
  function renderTiles() {            // como el JS del formulario: se redibujan al cambiar de juego
    tiles.innerHTML = '';
    Object.keys(cfg.servers).forEach(function (n) {
      if (cfg.servers[n].game !== state.game) return;
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'sv-tile'; b.dataset.server = n; b.textContent = n;
      b.onclick = function () { state.server = n; document.querySelector('[data-sv=server]').value = n; };
      tiles.appendChild(b);
    });
  }
  document.querySelectorAll('[role=tab]').forEach(function (t) {
    t.onclick = function () { state.game = t.dataset.game; renderTiles(); };
  });
  renderTiles();
  document.getElementById('sv-payment').onchange = function () {   // loadPayout
    document.querySelector('[data-sv=account]').value = '';
  };
  document.querySelector('[data-sv=edit]').onclick = function () {
    document.getElementById('bloque').hidden = false;
  };
  window.__envios = 0;
  document.getElementById('sv-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var data = new FormData(this);
    data.set('server', state.server);
    window.__envios++;
    fetch(cfg.urls.add, {method: 'POST', body: data}).then(function (r) { return r.json(); });
  });
</script></body></html>""" % json.dumps(cfg)


# El "humano": cuando el formulario esta relleno, envia; si la web rechaza
# (captcha), vuelve a enviar una vez, como haria una persona.
HUMANO = """
(function () {
  var t = setInterval(function () {
    var acc = document.querySelector('[data-sv=account]');
    var com = document.getElementById('sv-comment');
    if (!acc || !acc.value || !com || !com.value) return;
    if (window.__envios >= %d) { clearInterval(t); return; }
    if (window.__ocupado) return;
    window.__ocupado = true;
    setTimeout(function () { document.querySelector('[data-sv=submit]').click(); window.__ocupado = false; }, 250);
  }, 200);
})();
"""


def campo(cuerpo, nombre):
    m = re.search(r'name="%s"\r\n\r\n(.*?)\r\n' % re.escape(nombre), cuerpo, re.S)
    return m.group(1) if m else None


class TestEvaluar(unittest.TestCase):
    def test_dispara_en_el_minimo(self):
        self.assertEqual(interfaz.evaluar(CFG, "servidor2", "7.5", "10", "Usdt")[0], "vender")

    def test_espera_por_debajo(self):
        self.assertEqual(interfaz.evaluar(CFG, "Servidor2", "7.6", "10", "Usdt")[:2], ("esperar", 7.5))

    def test_servidor_cerrado_no_dispara_aunque_el_minimo_sea_cero(self):
        self.assertEqual(interfaz.evaluar(CFG, "Servidor4", "0", "100", "Usdt")[2], "no compra ahora")

    def test_bajo_el_minimo_de_la_web(self):
        # 0.9 USDT/M x 10 M = 9 USDT < 20 de minimo
        acc, _p, est = interfaz.evaluar(CFG, "Servidor5", "0.5", "10", "Usdt")
        self.assertEqual(acc, "esperar")
        self.assertIn("minimo", est)

    def test_servidor_inexistente(self):
        self.assertEqual(interfaz.evaluar(CFG, "Nope", "1", "1", "Usdt")[2], "no existe en el sitio comprador")

    def test_desplegable_y_nombres_viejos_en_minusculas(self):
        self.assertEqual(interfaz.juego_de("servidor2"), ("Servidor2", "Juego A"))
        self.assertEqual(interfaz.juego_de("Servidor7"), ("Servidor7", "Juego C"))
        self.assertEqual(interfaz.juego_de("Servidor5"), ("Servidor5", "Juego B"))
        self.assertEqual(sum(len(v) for v in interfaz.SERVIDORES.values()), 7)

    def test_metodo_en_minusculas(self):
        self.assertEqual(interfaz.normalizar_metodo("usdt"), "Usdt")
        self.assertEqual(interfaz.normalizar_metodo("algo"), "")


class TestVendedorSimulado(unittest.TestCase):
    def vender(self, cfg, respuestas, envios_humanos=2, orden=None, tiempo=20):
        recibidos, externas = [], []
        hecho, resultado = threading.Event(), {}
        cola_resp = list(respuestas)

        def preparar(ctx):
            ctx.add_init_script(HUMANO % envios_humanos)

            def ruta(route):
                url = route.request.url
                if url == URL_FALSA:
                    return route.fulfill(status=200, content_type="text/html",
                                         body=pagina(cfg))
                if url == URL_ENVIO and route.request.method == "POST":
                    recibidos.append(route.request.post_data)
                    return route.fulfill(status=200, content_type="application/json",
                                         body=json.dumps(cola_resp.pop(0)))
                externas.append(url)
                return route.abort()
            ctx.route("**/*", ruta)

        def fin(r):
            resultado["r"] = r
            hecho.set()

        with tempfile.TemporaryDirectory() as perfil:
            v = Vendedor(al_terminar=fin, perfil=perfil, canal="chrome", headless=True,
                         url_venta=URL_FALSA, preparar_contexto=preparar,
                         tiempo_captcha_s=tiempo, avisar=False)
            v.pedir_venta(orden or Orden("Servidor2", 12.5, "Usdt", DATOS, 7.5))
            self.assertTrue(hecho.wait(tiempo + 40), "el vendedor no termino")
            v.cerrar(10)
        return resultado["r"], recibidos, externas

    def test_rellena_bien_y_cuenta_el_reintento(self):
        r, recibidos, externas = self.vender(CFG, [
            {"ok": False, "error": "Captcha no valido."},
            {"ok": True, "message": "Venta registrada."}])
        self.assertEqual(r.estado, "completada", r.detalle)
        self.assertEqual(len(recibidos), 2)
        self.assertEqual(r.respuestas[0]["error"], "Captcha no valido.")
        cuerpo = recibidos[-1]
        esperado = {"server": "Servidor2", "payment": "Usdt", "quantity": "12.5",
                    "name": "Ana Prueba", "account": DATOS["direccion_trc20"],
                    "character": "Pjprueba", "comment": "XK-77",
                    "whatsapp": "+34 600000000"}
        for k, val in esperado.items():
            self.assertEqual(campo(cuerpo, k), val, k)
        self.assertEqual(externas, [], "hubo peticiones fuera de la simulacion")

    def test_sin_sesion_no_toca_el_formulario(self):
        r, recibidos, _ = self.vender(dict(CFG, logged=False), [])
        self.assertEqual(r.estado, "sin_sesion")
        self.assertEqual(recibidos, [])

    def test_servidor_que_dejo_de_comprar(self):
        r, recibidos, _ = self.vender(CFG, [], orden=Orden("Servidor4", 50, "Usdt", DATOS))
        self.assertEqual(r.estado, "cerrado")
        self.assertEqual(recibidos, [])

    def test_sin_envio_caduca(self):
        r, recibidos, _ = self.vender(CFG, [], envios_humanos=0, tiempo=3)
        self.assertEqual(r.estado, "sin_confirmar")
        self.assertEqual(recibidos, [])


if __name__ == "__main__":
    unittest.main()
