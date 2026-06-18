"""
Avisa por Telegram cuando una fuente lleva sin una corrida `ok` más tiempo del
esperado (ej. bloqueo anti-bot persistente). Usa los mismos umbrales que el
badge "desactualizado" del dashboard (ver run_dashboard.py).

La alerta se envía una sola vez por episodio: mientras la fuente sigue
desactualizada no se repite, y al recuperarse se avisa que volvió a andar.
Estado en silver.health_alerts (ver sql/003_health_alerts.sql).
"""
from __future__ import annotations

import os
import sys

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    try:
        from app.config import NEON_DATABASE_URL as DATABASE_URL
    except ImportError:
        sys.exit("No se encontró DATABASE_URL ni app.config.NEON_DATABASE_URL")

from app.telegram import TelegramNotifier

# Mismos umbrales que _stale_badge en run_dashboard.py.
THRESHOLDS_MIN = {
    "zonaprop": 90,
    "argenprop": 90,
    "mercadolibre": 150,
}


def get_last_ok(cur: psycopg2.extensions.cursor, fuente: str) -> dict | None:
    cur.execute(
        """
        SELECT finalizado_en, now() - finalizado_en AS antiguedad
        FROM raw.pipeline_runs
        WHERE fuente = %s AND estado = 'ok'
        ORDER BY finalizado_en DESC
        LIMIT 1
        """,
        (fuente,),
    )
    return cur.fetchone()


def already_alerted(cur: psycopg2.extensions.cursor, fuente: str) -> bool:
    cur.execute("SELECT 1 FROM silver.health_alerts WHERE fuente = %s", (fuente,))
    return cur.fetchone() is not None


def mark_alerted(cur: psycopg2.extensions.cursor, fuente: str) -> None:
    cur.execute(
        "INSERT INTO silver.health_alerts (fuente) VALUES (%s) ON CONFLICT DO NOTHING",
        (fuente,),
    )


def clear_alert(cur: psycopg2.extensions.cursor, fuente: str) -> None:
    cur.execute("DELETE FROM silver.health_alerts WHERE fuente = %s", (fuente,))


def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    notifier = TelegramNotifier()

    print("Chequeando salud de fuentes...")

    for fuente, threshold_min in THRESHOLDS_MIN.items():
        last_ok = get_last_ok(cur, fuente)
        antiguedad_min = last_ok["antiguedad"].total_seconds() / 60 if last_ok else None
        is_stale = antiguedad_min is None or antiguedad_min > threshold_min

        if is_stale and not already_alerted(cur, fuente):
            detalle = "nunca tuvo una corrida ok" if last_ok is None else f"hace {int(antiguedad_min)} min"
            notifier.send(f"⚠️ *{fuente}* sin datos nuevos ({detalle}, umbral {threshold_min} min)")
            mark_alerted(cur, fuente)
            conn.commit()
            print(f"  {fuente}: ALERTA enviada ({detalle})")
        elif not is_stale and already_alerted(cur, fuente):
            notifier.send(f"✅ *{fuente}* volvió a tener corridas ok")
            clear_alert(cur, fuente)
            conn.commit()
            print(f"  {fuente}: recuperado")
        else:
            print(f"  {fuente}: ok" if not is_stale else f"{fuente}: sigue desactualizado, ya alertado")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
