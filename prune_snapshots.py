"""
Borra de raw.snapshots los snapshots de más de RETENTION_DAYS dias.

Sin esto, raw.snapshots crece sin tope (una fila por publicacion en cada
corrida, cada ~10 min para zonaprop/argenprop) hasta chocar el limite de
almacenamiento del proyecto en Neon (ver incidente: "could not extend file
because project size limit (512 MB) has been exceeded", que tira abajo
ingest y dbt por igual). RETENTION_DAYS deja de sobra lo que necesita la
logica actual (ventana de 2h de OFF_MARKET, historial de precio del
sparkline) sin volver a acercarse al limite.
"""
from __future__ import annotations

import os
import sys

import psycopg2

DATABASE_URL = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    try:
        from app.config import NEON_DATABASE_URL as DATABASE_URL
    except ImportError:
        sys.exit("No se encontró DATABASE_URL ni app.config.NEON_DATABASE_URL")

RETENTION_DAYS = 7


def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(f"DELETE FROM raw.snapshots WHERE fecha_scraping < now() - interval '{RETENTION_DAYS} days'")
    print(f"raw.snapshots: {cur.rowcount} filas de mas de {RETENTION_DAYS} dias borradas")

    # VACUUM (no FULL: no requiere lock exclusivo) para que el espacio liberado
    # quede disponible para reuso por los proximos inserts, sin esperar a que
    # autovacuum lo note por su cuenta.
    cur.execute("VACUUM raw.snapshots")
    print("VACUUM completado")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
