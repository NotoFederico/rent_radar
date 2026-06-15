"""
Compara los dos últimos runs exitosos por fuente y emite eventos en silver.events.
Debe correr después de run_dbt.py (necesita silver.publicaciones actualizado).

Eventos detectados:
  NEW            - propiedad nueva en el portal (y en silver)
  PRICE_DOWN     - bajó el precio (misma moneda)
  PRICE_UP       - subió el precio (misma moneda)
  EXPENSES_CHANGE - cambio en expensas
  CURRENCY_CHANGE - cambio de moneda
  OFF_MARKET     - desapareció del portal
"""
from __future__ import annotations

import os
import sys
from collections import Counter

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    try:
        from app.config import NEON_DATABASE_URL as DATABASE_URL
    except ImportError:
        sys.exit("No se encontró DATABASE_URL ni app.config.NEON_DATABASE_URL")

FUENTES = ["zonaprop", "argenprop", "mercadolibre"]


def get_last_two_runs(cur: psycopg2.extensions.cursor, fuente: str) -> tuple[str | None, str | None]:
    """Retorna (run_anterior_id, run_actual_id) ordenados por finalizado_en desc."""
    cur.execute(
        """
        SELECT id_ejecucion
        FROM raw.pipeline_runs
        WHERE fuente = %s AND estado = 'ok'
        ORDER BY finalizado_en DESC
        LIMIT 2
        """,
        (fuente,),
    )
    rows = cur.fetchall()
    if len(rows) < 2:
        return None, None
    return rows[1]["id_ejecucion"], rows[0]["id_ejecucion"]  # (anterior, actual)


def get_snapshots(cur: psycopg2.extensions.cursor, id_ejecucion: str) -> dict[str, dict]:
    """Snapshots de una corrida, indexados por id_publicacion. Toma el más reciente por id si hay duplicados."""
    cur.execute(
        """
        SELECT DISTINCT ON (id_publicacion)
            id_publicacion, titulo, url, precio, moneda, expensas
        FROM raw.snapshots
        WHERE id_ejecucion = %s
        ORDER BY id_publicacion, id DESC
        """,
        (id_ejecucion,),
    )
    return {r["id_publicacion"]: dict(r) for r in cur.fetchall()}


def get_silver_ids(cur: psycopg2.extensions.cursor, fuente: str) -> set[str]:
    """IDs de publicaciones que pasan el filtro de gold.objetivo."""
    cur.execute(
        "SELECT id_publicacion FROM gold.objetivo WHERE fuente = %s",
        (fuente,),
    )
    return {r["id_publicacion"] for r in cur.fetchall()}


def detect(
    snaps_a: dict[str, dict],
    snaps_b: dict[str, dict],
    silver_ids: set[str],
    fuente: str,
    run_b: str,
) -> list[dict]:
    events: list[dict] = []

    ids_a = set(snaps_a)
    ids_b = set(snaps_b)

    def base(pid: str, snap: dict, tipo: str) -> dict:
        return {
            "id_ejecucion": run_b,
            "fuente": fuente,
            "id_publicacion": pid,
            "tipo_evento": tipo,
            "titulo": snap.get("titulo"),
            "url": snap.get("url"),
            "moneda": snap.get("moneda"),
            "precio_anterior": None,
            "precio_nuevo": None,
            "expensas_anterior": None,
            "expensas_nuevo": None,
        }

    # NEW — apareció en B, no estaba en A, y está en silver
    for pid in (ids_b - ids_a) & silver_ids:
        e = base(pid, snaps_b[pid], "NEW")
        e["precio_nuevo"] = snaps_b[pid].get("precio")
        events.append(e)

    # OFF_MARKET — estaba en A, no está en B
    # No filtramos por silver: si desapareció del portal nos interesa igual
    for pid in ids_a - ids_b:
        e = base(pid, snaps_a[pid], "OFF_MARKET")
        e["precio_anterior"] = snaps_a[pid].get("precio")
        events.append(e)

    # Propiedades en ambas corridas y en silver → cambios
    for pid in ids_a & ids_b & silver_ids:
        sa = snaps_a[pid]
        sb = snaps_b[pid]

        moneda_a = sa.get("moneda")
        moneda_b = sb.get("moneda")
        precio_a = sa.get("precio")
        precio_b = sb.get("precio")
        expensas_a = sa.get("expensas")
        expensas_b = sb.get("expensas")

        # Cambio de moneda
        if moneda_a and moneda_b and moneda_a != moneda_b:
            e = base(pid, sb, "CURRENCY_CHANGE")
            e["precio_anterior"] = precio_a
            e["precio_nuevo"] = precio_b
            events.append(e)
            continue  # no emitir PRICE_* si cambió la moneda (no comparables)

        # Cambio de precio (misma moneda)
        if precio_a and precio_b and precio_a != precio_b:
            tipo = "PRICE_DOWN" if precio_b < precio_a else "PRICE_UP"
            e = base(pid, sb, tipo)
            e["precio_anterior"] = precio_a
            e["precio_nuevo"] = precio_b
            events.append(e)

        # Cambio de expensas
        if expensas_a != expensas_b and (expensas_a is not None or expensas_b is not None):
            e = base(pid, sb, "EXPENSES_CHANGE")
            e["expensas_anterior"] = expensas_a
            e["expensas_nuevo"] = expensas_b
            events.append(e)

    return events


def insert_events(cur: psycopg2.extensions.cursor, events: list[dict]) -> int:
    inserted = 0
    for e in events:
        cur.execute(
            """
            INSERT INTO silver.events (
                id_ejecucion, fuente, id_publicacion, tipo_evento,
                titulo, url, moneda,
                precio_anterior, precio_nuevo,
                expensas_anterior, expensas_nuevo
            ) VALUES (
                %(id_ejecucion)s, %(fuente)s, %(id_publicacion)s, %(tipo_evento)s,
                %(titulo)s, %(url)s, %(moneda)s,
                %(precio_anterior)s, %(precio_nuevo)s,
                %(expensas_anterior)s, %(expensas_nuevo)s
            )
            ON CONFLICT (fuente, id_publicacion, tipo_evento, id_ejecucion) DO NOTHING
            """,
            e,
        )
        inserted += cur.rowcount
    return inserted


def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    print("Detectando eventos...")
    total = 0

    for fuente in FUENTES:
        run_a, run_b = get_last_two_runs(cur, fuente)
        if not run_a:
            print(f"  {fuente}: sin corrida anterior, saltando")
            continue

        print(f"  {fuente}: {run_a[:8]}… → {run_b[:8]}…", end="", flush=True)

        snaps_a = get_snapshots(cur, run_a)
        snaps_b = get_snapshots(cur, run_b)
        silver_ids = get_silver_ids(cur, fuente)

        events = detect(snaps_a, snaps_b, silver_ids, fuente, run_b)

        if not events:
            print(" — sin cambios")
            continue

        count = insert_events(cur, events)
        conn.commit()
        total += count

        resumen = Counter(e["tipo_evento"] for e in events)
        detalle = ", ".join(f"{c} {t}" for t, c in sorted(resumen.items()))
        print(f" — {count} nuevos ({detalle})")

    cur.close()
    conn.close()
    print(f"Total eventos nuevos: {total}")


if __name__ == "__main__":
    main()
