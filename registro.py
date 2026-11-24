# -*- coding: utf-8 -*-
"""Log de ventas en CSV (append-only). Una fila por venta.

CSV = texto plano, una fila por linea, columnas separadas por comas. Lo abre
Excel/LibreOffice/Sheets directo y Python lo maneja con el modulo `csv` de la
libreria estandar (cero dependencias). Append-only: si la interfaz se cierra a
mitad, lo ya escrito queda.
"""

import csv
from datetime import datetime
from pathlib import Path

# --- AJUSTES ---
FICHERO = Path(__file__).parent / "ventas.csv"
COLUMNAS = ["fecha_hora", "servidor", "precio_tomado", "monedas_vendidas",
            "direccion_trc20", "estado"]


def _asegurar_cabecera():
    """Escribe la cabecera si el fichero no existe o esta vacio."""
    if not FICHERO.exists() or FICHERO.stat().st_size == 0:
        with open(FICHERO, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(COLUMNAS)


def registrar(servidor, precio_tomado, monedas_vendidas, direccion_trc20,
              estado="completada"):
    """Anade una fila de venta. Devuelve la fila escrita."""
    _asegurar_cabecera()
    fila = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            servidor, precio_tomado, monedas_vendidas, direccion_trc20, estado]
    with open(FICHERO, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(fila)
    return fila


def leer_todo():
    """Devuelve todas las filas como lista de dicts (mas nueva al final)."""
    if not FICHERO.exists():
        return []
    with open(FICHERO, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


if __name__ == "__main__":
    # Demo: escribe una fila de prueba y relee.
    registrar("Servidor2", "7.50", "10000000", "TXxxDEMOxxx", "prueba")
    for fila in leer_todo():
        print(fila)
