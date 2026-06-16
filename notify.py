"""
Lee silver.events con fue_notificado=FALSE, envía por Telegram y registra en silver.notifications.
En caso de fallo de envío, el evento NO se marca como notificado → se reintenta en la próxima corrida.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    try:
        from app.config import NEON_DATABASE_URL as DATABASE_URL
    except ImportError:
        sys.exit("No se encontró DATABASE_URL ni app.config.NEON_DATABASE_URL")

from app.telegram import TelegramNotifier


# ── Formateo de mensajes ──────────────────────────────────────────────────────

def _fmt_precio(precio: float | None, moneda: str | None) -> str:
    if precio is None:
        return "—"
    num = f"{int(precio):,}".replace(",", ".")
    return f"USD {num}" if moneda == "USD" else f"$ {num} ARS"


def _fmt_delta(ant: float | None, nue: float | None) -> str:
    if not ant or not nue:
        return ""
    pct = (nue - ant) / ant * 100
    return f" ({pct:+.1f}%)"


def _detalles(pub: dict | None) -> str:
    if not pub:
        return ""
    parts = []
    if pub.get("ambientes"):
        parts.append(f"{int(pub['ambientes'])} amb")
    sup = pub.get("superficie_cubierta") or pub.get("superficie_total")
    if sup:
        parts.append(f"{int(sup)} m²")
    if pub.get("cocheras"):
        parts.append(f"{int(pub['cocheras'])} coch")
    if pub.get("antiguedad") is not None:
        parts.append(f"{int(pub['antiguedad'])} años")
    return " · ".join(parts)


def build_message(ev: dict, pub: dict | None) -> str:
    titulo = (ev.get("titulo") or "Sin título")[:70]
    url    = ev.get("url") or ""
    moneda = ev.get("moneda")
    tipo   = ev["tipo_evento"]

    if tipo == "NEW":
        precio = _fmt_precio(ev.get("precio_nuevo"), moneda)
        det    = _detalles(pub)
        ubicacion = pub.get("ubicacion", "") if pub else ""
        msg  = f"*Nueva propiedad*\n\n"
        msg += f"*{titulo}*\n\n"
        msg += f"💰 {precio}/mes\n"
        if ubicacion:
            msg += f"📍 {ubicacion}\n"
        if det:
            msg += f"🏠 {det}\n"
        if url:
            msg += f"\n[Ver propiedad]({url})"

    elif tipo == "PRICE_DOWN":
        ant  = _fmt_precio(ev.get("precio_anterior"), moneda)
        nue  = _fmt_precio(ev.get("precio_nuevo"), moneda)
        delta = _fmt_delta(ev.get("precio_anterior"), ev.get("precio_nuevo"))
        msg  = f"*Bajó el precio 📉{delta}*\n\n"
        msg += f"*{titulo}*\n\n"
        msg += f"💰 ~{ant}~ → *{nue}*/mes\n"
        if url:
            msg += f"\n[Ver propiedad]({url})"

    elif tipo == "PRICE_UP":
        ant  = _fmt_precio(ev.get("precio_anterior"), moneda)
        nue  = _fmt_precio(ev.get("precio_nuevo"), moneda)
        delta = _fmt_delta(ev.get("precio_anterior"), ev.get("precio_nuevo"))
        msg  = f"*Subió el precio 📈{delta}*\n\n"
        msg += f"*{titulo}*\n\n"
        msg += f"💰 ~{ant}~ → *{nue}*/mes\n"
        if url:
            msg += f"\n[Ver propiedad]({url})"

    elif tipo == "EXPENSES_CHANGE":
        ant_val = ev.get("expensas_anterior")
        nue_val = ev.get("expensas_nuevo")
        nue = _fmt_precio(nue_val, "ARS")
        msg  = f"*Cambio de expensas*\n\n"
        msg += f"*{titulo}*\n\n"
        if ant_val is None:
            msg += f"Expensas informadas: {nue}/mes\n"
        else:
            ant = _fmt_precio(ant_val, "ARS")
            msg += f"Anterior: {ant}/mes\n"
            msg += f"Nuevo: {nue}/mes\n"
        if url:
            msg += f"\n[Ver propiedad]({url})"

    elif tipo == "CURRENCY_CHANGE":
        ant = _fmt_precio(ev.get("precio_anterior"), None)
        nue = _fmt_precio(ev.get("precio_nuevo"), moneda)
        msg  = f"*Cambio de moneda*\n\n"
        msg += f"*{titulo}*\n\n"
        msg += f"Precio: {ant} → *{nue}*/mes\n"
        if url:
            msg += f"\n[Ver propiedad]({url})"

    elif tipo == "OFF_MARKET":
        ultimo = _fmt_precio(ev.get("precio_anterior"), moneda)
        msg  = f"*Propiedad no disponible ❌*\n"
        msg += f"_(Probablemente alquilada o removida)_\n\n"
        msg += f"*{titulo}*\n\n"
        msg += f"💰 Último precio: {ultimo}/mes\n"
        if url:
            msg += f"\n[Ver propiedad]({url})"

    else:
        msg = f"*{tipo}*\n\n*{titulo}*"
        if url:
            msg += f"\n\n[Ver propiedad]({url})"

    return msg


# ── Pipeline principal ────────────────────────────────────────────────────────

def fetch_pending(cur) -> list[dict]:
    cur.execute("""
        SELECT id_evento, fuente, id_publicacion, tipo_evento,
               titulo, url, moneda,
               precio_anterior, precio_nuevo,
               expensas_anterior, expensas_nuevo
        FROM silver.events
        WHERE fue_notificado = FALSE
        ORDER BY detectado_en
    """)
    return [dict(r) for r in cur.fetchall()]


def fetch_publicacion(cur, fuente: str, id_publicacion: str) -> dict | None:
    cur.execute("""
        SELECT ambientes, superficie_cubierta, superficie_total,
               cocheras, antiguedad, ubicacion
        FROM gold.candidatas
        WHERE fuente = %s AND id_publicacion = %s
        LIMIT 1
    """, (fuente, id_publicacion))
    row = cur.fetchone()
    return dict(row) if row else None


def mark_notified(cur, id_evento: str) -> None:
    cur.execute(
        "UPDATE silver.events SET fue_notificado = TRUE WHERE id_evento = %s",
        (id_evento,),
    )


def log_notification(cur, id_evento: str, mensaje: str, estado: str = "sent") -> None:
    cur.execute(
        """
        INSERT INTO silver.notifications (id_evento, canal, mensaje, estado)
        VALUES (%s, 'telegram', %s, %s)
        """,
        (id_evento, mensaje, estado),
    )


def main() -> None:
    try:
        notifier = TelegramNotifier()
    except ValueError as exc:
        sys.exit(str(exc))

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    eventos = fetch_pending(cur)
    if not eventos:
        print("Sin eventos pendientes.")
        cur.close()
        conn.close()
        return

    # OFF_MARKET temporalmente silenciado en Telegram (inunda cada corrida) — se
    # sigue marcando como notificado para no acumular backlog.
    off_market = [e for e in eventos if e["tipo_evento"] == "OFF_MARKET"]
    otros = [e for e in eventos if e["tipo_evento"] != "OFF_MARKET"]

    for ev in off_market:
        mensaje = build_message(ev, None)
        mark_notified(cur, ev["id_evento"])
        log_notification(cur, ev["id_evento"], mensaje, estado="skipped")
        conn.commit()
    if off_market:
        print(f"  SKIP [OFF_MARKET] {len(off_market)} evento(s) silenciado(s)")

    print(f"{len(otros)} evento(s) pendiente(s):")
    ok = err = 0

    if otros:
        n = len(otros)
        header = f"🔍 *Corrida {datetime.now().strftime('%d/%m %H:%M')}* — {n} novedad{'es' if n != 1 else ''}"
        notifier.send(header)

    for ev in otros:
        pub = fetch_publicacion(cur, ev["fuente"], ev["id_publicacion"])

        mensaje = build_message(ev, pub)
        enviado = notifier.send(mensaje)

        if enviado:
            mark_notified(cur, ev["id_evento"])
            log_notification(cur, ev["id_evento"], mensaje)
            conn.commit()
            print(f"  OK  [{ev['tipo_evento']}] {(ev.get('titulo') or '')[:55]}")
            ok += 1
        else:
            conn.rollback()
            print(f"  ERR [{ev['tipo_evento']}] {(ev.get('titulo') or '')[:55]}")
            err += 1

    cur.close()
    conn.close()
    print(f"Enviados: {ok}  Fallidos: {err}")


if __name__ == "__main__":
    main()
