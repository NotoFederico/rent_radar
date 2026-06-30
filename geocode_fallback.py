"""
Reintenta ubicar publicaciones rechazadas por 'coordenadas_fuera_de_zona':
el problema es la geocodificación del portal de origen, no la dirección en sí
(ver `ubicacion`), así que se re-geocodifica esa dirección contra Nominatim
(OpenStreetMap, el mismo proveedor que ya usa el mapa del dashboard).

Si el resultado cae dentro de la zona esperada, se guarda en
silver.coordenadas_override; publicaciones.sql la usa con preferencia sobre
la coordenada del portal en el próximo `dbt run`. Si Nominatim no encuentra
nada o el resultado también cae fuera de zona, la publicación sigue rechazada.
"""
from __future__ import annotations

import math
import os
import sys
import time

import psycopg2
import psycopg2.extras
import requests

DATABASE_URL = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    try:
        from app.config import NEON_DATABASE_URL as DATABASE_URL
    except ImportError:
        sys.exit("No se encontró DATABASE_URL ni app.config.NEON_DATABASE_URL")

from app.telegram import TelegramNotifier

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "rent-radar/1.0 (uso personal - github.com/fede-noto/rent_radar)"
PAUSE_BETWEEN_REQUESTS_SEC = 1.0  # política de uso de Nominatim: máx 1 req/seg

# Kilómetros por grado de latitud (circunferencia terrestre ≈ 40.008km / 360°).
# Un grado de longitud equivale a esto mismo * cos(latitud) (los meridianos se
# acercan cerca de los polos). Mismo valor que km_por_grado_lat() en
# analytics/macros/distancia.sql — no hay forma de compartir la constante
# entre SQL y Python sin un servicio aparte, así que se repite a propósito.
KM_PER_DEGREE_LAT = 111.32


def _median(values: list[float]) -> float:
    values = sorted(values)
    n = len(values)
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def zone_center_and_radius(cur: psycopg2.extensions.cursor) -> tuple[float, float, float]:
    """Mismo cálculo que zone_center/zone_radius en publicaciones.sql: mediana de
    lat/lon de las publicaciones ya aceptadas (robusta a outliers, sin punto fijo
    hardcodeado), y radio = 4x la mediana de distancia a ese centro (piso 5km).
    """
    cur.execute("select latitud, longitud from gold.candidatas where latitud is not null and longitud is not null")
    points = [(float(r["latitud"]), float(r["longitud"])) for r in cur.fetchall()]
    center_lat = _median([lat for lat, _ in points])
    center_lon = _median([lon for _, lon in points])

    def dist(lat: float, lon: float) -> float:
        dlat = (lat - center_lat) * KM_PER_DEGREE_LAT
        dlon = (lon - center_lon) * KM_PER_DEGREE_LAT * math.cos(math.radians(center_lat))
        return math.hypot(dlat, dlon)

    radius_km = max(_median([dist(lat, lon) for lat, lon in points]) * 4, 5)
    return center_lat, center_lon, radius_km


def get_pending(cur: psycopg2.extensions.cursor) -> list[dict]:
    """Publicaciones rechazadas por coordenadas, sin override todavía."""
    cur.execute(
        """
        SELECT DISTINCT r.fuente, r.id_publicacion, r.titulo, r.url, r.ubicacion
        FROM silver.publicaciones_rechazadas r
        WHERE r.motivo_rechazo = 'coordenadas_fuera_de_zona'
          AND r.ubicacion IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM silver.coordenadas_override co
              WHERE co.fuente = r.fuente AND co.id_publicacion = r.id_publicacion
          )
        """
    )
    return cur.fetchall()


def already_notified(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str) -> bool:
    cur.execute(
        "SELECT 1 FROM silver.coordenadas_no_resueltas WHERE fuente = %s AND id_publicacion = %s",
        (fuente, id_publicacion),
    )
    return cur.fetchone() is not None


def mark_notified(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str) -> None:
    cur.execute(
        """
        INSERT INTO silver.coordenadas_no_resueltas (fuente, id_publicacion)
        VALUES (%s, %s)
        ON CONFLICT (fuente, id_publicacion) DO NOTHING
        """,
        (fuente, id_publicacion),
    )


def clear_notified(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str) -> None:
    cur.execute(
        "DELETE FROM silver.coordenadas_no_resueltas WHERE fuente = %s AND id_publicacion = %s",
        (fuente, id_publicacion),
    )


def geocode(address: str) -> tuple[float, float] | None:
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": f"{address}, Argentina", "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"    error consultando Nominatim: {exc}")
        return None
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def save_override(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str, lat: float, lon: float) -> None:
    cur.execute(
        """
        INSERT INTO silver.coordenadas_override (fuente, id_publicacion, latitud, longitud)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (fuente, id_publicacion)
        DO UPDATE SET latitud = excluded.latitud, longitud = excluded.longitud, actualizado_en = now()
        """,
        (fuente, id_publicacion, lat, lon),
    )


def _notify_unresolved(notifier: TelegramNotifier, row: dict) -> bool:
    titulo = (row.get("titulo") or "Sin título")[:70]
    msg = (
        f"📍 *No se pudo ubicar en el mapa*\n\n"
        f"*{titulo}*\n"
        f"Dirección: {row['ubicacion']}\n\n"
        f"El portal de origen le dio coordenadas muy alejadas de la zona y "
        f"Nominatim no pudo corregirla automáticamente. Revisar a mano.\n"
    )
    if row.get("url"):
        msg += f"\n[Ver propiedad]({row['url']})"
    return notifier.send(msg)


def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    pending = get_pending(cur)
    if not pending:
        print("Sin publicaciones pendientes de re-geocode.")
        cur.close()
        conn.close()
        return

    center_lat, center_lon, radius_km = zone_center_and_radius(cur)
    print(f"Zona: centro=({center_lat:.5f}, {center_lon:.5f}) radio={radius_km:.1f}km")

    notifier: TelegramNotifier | None = None

    print(f"Re-geocodificando {len(pending)} publicacion(es)...")
    resolved = 0
    for i, row in enumerate(pending):
        if i > 0:
            time.sleep(PAUSE_BETWEEN_REQUESTS_SEC)

        label = f"{row['fuente']}:{row['id_publicacion']}"
        coords = geocode(row["ubicacion"])
        out_of_zone = False
        if coords is not None:
            lat, lon = coords
            dlat = (lat - center_lat) * KM_PER_DEGREE_LAT
            dlon = (lon - center_lon) * KM_PER_DEGREE_LAT * math.cos(math.radians(center_lat))
            out_of_zone = math.hypot(dlat, dlon) > radius_km

        if coords is not None and not out_of_zone:
            lat, lon = coords
            save_override(cur, row["fuente"], row["id_publicacion"], lat, lon)
            clear_notified(cur, row["fuente"], row["id_publicacion"])
            conn.commit()
            resolved += 1
            print(f"  {label} -> corregida ({lat:.5f}, {lon:.5f})")
            continue

        reason = "sin resultado de Nominatim" if coords is None else "geocodificado pero sigue fuera de zona"
        print(f"  {label} -> {reason}, se mantiene rechazada")

        if already_notified(cur, row["fuente"], row["id_publicacion"]):
            continue
        if notifier is None:
            try:
                notifier = TelegramNotifier()
            except ValueError as exc:
                print(f"  (sin Telegram configurado, no se avisa: {exc})")
                continue
        if _notify_unresolved(notifier, row):
            mark_notified(cur, row["fuente"], row["id_publicacion"])
            conn.commit()
        else:
            print(f"  {label} -> fallo el envio a Telegram, se reintenta la proxima corrida")

    print(f"Resueltas: {resolved}/{len(pending)}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
