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

# Tiempo real de ausencia continua requerido antes de emitir OFF_MARKET.
# No usamos "N corridas seguidas": zonaprop/argenprop corren cada ~10 min y
# argenprop reordena resultados durante la paginación (avisos que se ven
# duplicados en una página y salteados en otra dentro de la misma corrida),
# lo que genera ausencias de 1-2 corridas sin que la propiedad haya salido
# del mercado. Un umbral en horas reales no depende de cuán seguido corre
# cada spider.
OFF_MARKET_MIN_HOURS = 2.0


def get_last_runs(cur: psycopg2.extensions.cursor, fuente: str, n: int = 2) -> list[dict]:
    """Retorna los últimos N runs exitosos (más reciente primero), con id y timestamp."""
    cur.execute(
        """
        SELECT id_ejecucion, finalizado_en
        FROM raw.pipeline_runs
        WHERE fuente = %s AND estado = 'ok'
        ORDER BY finalizado_en DESC
        LIMIT %s
        """,
        (fuente, n),
    )
    return cur.fetchall()


def get_off_market_reference(
    cur: psycopg2.extensions.cursor, fuente: str, before, min_hours: float
) -> dict | None:
    """Corrida 'ok' más reciente que sea al menos `min_hours` más vieja que `before`.

    Sirve como punto de referencia: si la propiedad estaba ahí y no aparece en
    ninguna corrida desde entonces hasta `before`, pasaron >= min_hours de
    ausencia continua real.
    """
    cur.execute(
        """
        SELECT id_ejecucion, finalizado_en
        FROM raw.pipeline_runs
        WHERE fuente = %s AND estado = 'ok'
          AND finalizado_en <= %s - (%s * interval '1 hour')
        ORDER BY finalizado_en DESC
        LIMIT 1
        """,
        (fuente, before, min_hours),
    )
    return cur.fetchone()


def get_ids_since(cur: psycopg2.extensions.cursor, fuente: str, after, upto) -> set[str]:
    """IDs vistos en cualquier corrida 'ok' de esta fuente en el rango (after, upto]."""
    cur.execute(
        """
        SELECT DISTINCT s.id_publicacion
        FROM raw.snapshots s
        JOIN raw.pipeline_runs pr ON pr.id_ejecucion = s.id_ejecucion
        WHERE pr.fuente = %s AND pr.estado = 'ok'
          AND pr.finalizado_en > %s AND pr.finalizado_en <= %s
        """,
        (fuente, after, upto),
    )
    return {r["id_publicacion"] for r in cur.fetchall()}


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


def get_ever_seen_ids(cur: psycopg2.extensions.cursor, fuente: str, exclude_run: str) -> set[str]:
    """IDs que alguna vez aparecieron en cualquier run anterior a exclude_run, para esta fuente."""
    cur.execute(
        """
        SELECT DISTINCT s.id_publicacion
        FROM raw.snapshots s
        JOIN raw.pipeline_runs pr ON pr.id_ejecucion = s.id_ejecucion
        WHERE s.fuente = %s AND s.id_ejecucion != %s
        """,
        (fuente, exclude_run),
    )
    return {r["id_publicacion"] for r in cur.fetchall()}


def get_silver_ids(cur: psycopg2.extensions.cursor, fuente: str) -> set[str]:
    """IDs de publicaciones que pasan el filtro de gold.candidatas."""
    cur.execute(
        "SELECT id_publicacion FROM gold.candidatas WHERE fuente = %s",
        (fuente,),
    )
    return {r["id_publicacion"] for r in cur.fetchall()}


def detect(
    snaps_a: dict[str, dict],
    snaps_b: dict[str, dict],
    silver_ids: set[str],
    fuente: str,
    run_b: str,
    snaps_ref: dict[str, dict] | None = None,
    recent_ids: set[str] | None = None,
    ever_seen: set[str] | None = None,
) -> list[dict]:
    """
    snaps_a / snaps_b: par más reciente para NEW / PRICE / EXPENSES comparisons.
    snaps_ref: snapshot de la corrida de referencia, al menos OFF_MARKET_MIN_HOURS más
      vieja que la corrida actual, donde la propiedad existía.
    recent_ids: unión de IDs vistos en cualquier corrida entre snaps_ref y la actual.
      OFF_MARKET se emite solo si la propiedad está en snaps_ref pero ausente de recent_ids
      (es decir, ausente durante todo ese período, no solo en la última corrida).
    ever_seen: IDs ya vistos en cualquier run anterior a run_b. NEW solo se emite si no está aquí.
    """
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

    # NEW — apareció en B, no estaba en A, está en silver, y nunca se había visto antes
    truly_new = (ids_b - ids_a) & silver_ids
    if ever_seen is not None:
        truly_new -= ever_seen
    for pid in truly_new:
        e = base(pid, snaps_b[pid], "NEW")
        e["precio_nuevo"] = snaps_b[pid].get("precio")
        events.append(e)

    # OFF_MARKET — ausente desde snaps_ref (>= OFF_MARKET_MIN_HOURS atrás) hasta ahora,
    # en TODAS las corridas intermedias (recent_ids es la unión de IDs vistos en ese período).
    if snaps_ref is not None and recent_ids is not None:
        confirmed_off = set(snaps_ref) - recent_ids
    else:
        confirmed_off = ids_a - ids_b  # fallback si no hay suficiente historial

    for pid in confirmed_off:
        snap = snaps_ref.get(pid) if snaps_ref else snaps_a.get(pid, {})
        e = base(pid, snap, "OFF_MARKET")
        e["precio_anterior"] = snap.get("precio")
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
        runs = get_last_runs(cur, fuente, n=2)
        if len(runs) < 2:
            print(f"  {fuente}: sin corrida anterior, saltando")
            continue

        run_b, run_a = runs[0]["id_ejecucion"], runs[1]["id_ejecucion"]
        run_b_ts = runs[0]["finalizado_en"]

        print(f"  {fuente}: {run_a[:8]}… → {run_b[:8]}…", end="", flush=True)

        snaps_a = get_snapshots(cur, run_a)
        snaps_b = get_snapshots(cur, run_b)
        silver_ids = get_silver_ids(cur, fuente)
        ever_seen = get_ever_seen_ids(cur, fuente, run_b)

        # OFF_MARKET: referencia = corrida >= OFF_MARKET_MIN_HOURS más vieja que run_b.
        ref_run = get_off_market_reference(cur, fuente, run_b_ts, OFF_MARKET_MIN_HOURS)
        if ref_run:
            snaps_ref = get_snapshots(cur, ref_run["id_ejecucion"])
            recent_ids = get_ids_since(cur, fuente, ref_run["finalizado_en"], run_b_ts)
        else:
            snaps_ref = None
            recent_ids = None

        events = detect(snaps_a, snaps_b, silver_ids, fuente, run_b, snaps_ref, recent_ids, ever_seen)

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
