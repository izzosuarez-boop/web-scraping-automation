# -*- coding: utf-8 -*-
"""Lee los precios del sitio comprador SIN navegador (scraping directo).

La pagina de venta incrusta un <script type="application/json" id="sv-config">
con TODO: los servidores, si estan comprando y el precio por metodo de pago.
No hay AJAX ni captcha para LEER, asi que basta un requests.get() y parsear ese
JSON. El navegador (Playwright) solo hace falta para VENDER (login + captcha).

Devuelve datos crudos; quien llame decide que precio mirar (p. ej. el de USDT,
porque el cobro es a una direccion TRC-20).
"""

import json
import re
import urllib.request

# --- AJUSTES ---
URL_VENTA = "https://buyer-site.example/vender.html"
# User-Agent de navegador real: Cloudflare esta delante y una peticion "pelada"
# (user-agent de python) es justo lo que filtra. Con este devuelve 200.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
TIMEOUT_S = 15
# El cobro va a una direccion TRC-20, asi que el precio que de verdad importa
# es el de USDT. Se deja arriba por si algun dia se cobra en otra cosa.
METODO_POR_DEFECTO = "Usdt"

_RE_CONFIG = re.compile(
    r'<script type="application/json" id="sv-config">(.*?)</script>', re.S)


def descargar_config(url=URL_VENTA):
    """Baja la pagina y devuelve el JSON sv-config ya parseado (dict)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        html = r.read().decode("utf-8", errors="replace")
    m = _RE_CONFIG.search(html)
    if not m:
        raise RuntimeError(
            "No encuentro el bloque sv-config: la pagina cambio de formato "
            "o Cloudflare devolvio un challenge en vez del HTML.")
    return json.loads(m.group(1))


def servidores(cfg):
    """Dict {nombre: info} de todos los servidores del config."""
    return cfg.get("servers", {})


def precio(cfg, servidor, metodo=METODO_POR_DEFECTO):
    """Precio por millon de `servidor` con `metodo` de pago.

    Devuelve float, o None si el servidor/metodo no existe o esta cerrado.
    """
    srv = cfg.get("servers", {}).get(servidor)
    if not srv or not srv.get("open"):
        return None
    p = srv.get("prices", {}).get(metodo)
    if not p or not p.get("o"):
        return None
    return p.get("p")


def comprando(cfg, juego=None):
    """Lista de servidores que compran ahora mismo (open=True).

    Si se pasa `juego` (p. ej. 'Juego A') filtra por ese juego.
    """
    out = []
    for nombre, info in cfg.get("servers", {}).items():
        if not info.get("open"):
            continue
        if juego and info.get("game") != juego:
            continue
        out.append(nombre)
    return out


if __name__ == "__main__":
    cfg = descargar_config()
    srv = servidores(cfg)
    print(f"{len(srv)} servidores. Comprando ahora: {len(comprando(cfg))}")
    print(f"Metodos de pago: {sorted({m for s in srv.values() for m in s.get('prices', {})})}")
    print()
    print(f"{'Servidor':<14}{'Juego':<14}{'EUR':>8}{'USDT':>9}  estado")
    for nombre, info in srv.items():
        eur = precio(cfg, nombre, "Paypal")
        usdt = precio(cfg, nombre, "Usdt")
        estado = "comprando" if info.get("open") else "completo"
        print(f"{nombre:<14}{info.get('game',''):<14}"
              f"{(eur if eur else 0):>8.3f}{(usdt if usdt else 0):>9.3f}  {estado}")
