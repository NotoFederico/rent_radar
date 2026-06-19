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
PAUSA_ENTRE_REQUESTS_SEG = 1.0  # política de uso de Nominatim: máx 1 req/seg

# Kilómetros por grado de latitud (circunferencia terrestre ≈ 40.008km / 360°).
# Un grado de longitud equivale a esto mismo * cos(latitud) (los meridianos se
# acercan cerca de los polos). Mismo valor que km_por_grado_lat() en
# analytics/macros/distancia.sql — no hay forma de compartir la constante
# entre SQL y Python sin un servicio aparte, así que se repite a propósito.
KM_POR_GRADO_LAT = 111.32


def _mediana(valores: list[float]) -> float:
    valores = sorted(valores)
    n = len(valores)
    return valores[n // 2] if n % 2 else (valores[n // 2 - 1] + valores[n // 2]) / 2


def zona_centro_y_radio(cur: psycopg2.extensions.cursor) -> tuple[float, float, float]:
    """Mismo cálculo que zona_centro/zona_radio en publicaciones.sql: mediana de
    lat/lon de las publicaciones ya aceptadas (robusta a outliers, sin punto fijo
    hardcodeado), y radio = 4x la mediana de distancia a ese centro (piso 5km).
    """
    cur.execute("select latitud, longitud from gold.candidatas where latitud is not null and longitud is not null")
    puntos = [(float(r["latitud"]), float(r["longitud"])) for r in cur.fetchall()]
    centro_lat = _mediana([lat for lat, _ in puntos])
    centro_lon = _mediana([lon for _, lon in puntos])

    def dist(lat: float, lon: float) -> float:
        dlat = (lat - centro_lat) * KM_POR_GRADO_LAT
        dlon = (lon - centro_lon) * KM_POR_GRADO_LAT * math.cos(math.radians(centro_lat))
        return math.hypot(dlat, dlon)

    radio_km = max(_mediana([dist(lat, lon) for lat, lon in puntos]) * 4, 5)
    return centro_lat, centro_lon, radio_km


def get_pendientes(cur: psycopg2.extensions.cursor) -> list[dict]:
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


def ya_notificado(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str) -> bool:
    cur.execute(
        "SELECT 1 FROM silver.coordenadas_no_resueltas WHERE fuente = %s AND id_publicacion = %s",
        (fuente, id_publicacion),
    )
    return cur.fetchone() is not None


def marcar_notificado(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str) -> None:
    cur.execute(
        """
        INSERT INTO silver.coordenadas_no_resueltas (fuente, id_publicacion)
        VALUES (%s, %s)
        ON CONFLICT (fuente, id_publicacion) DO NOTHING
        """,
        (fuente, id_publicacion),
    )


def limpiar_notificado(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str) -> None:
    cur.execute(
        "DELETE FROM silver.coordenadas_no_resueltas WHERE fuente = %s AND id_publicacion = %s",
        (fuente, id_publicacion),
    )


def geocodificar(direccion: str) -> tuple[float, float] | None:
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"q": f"{direccion}, Argentina", "format": "json", "limit": 1},
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


def guardar_override(cur: psycopg2.extensions.cursor, fuente: str, id_publicacion: str, lat: float, lon: float) -> None:
    cur.execute(
        """
        INSERT INTO silver.coordenadas_override (fuente, id_publicacion, latitud, longitud)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (fuente, id_publicacion)
        DO UPDATE SET latitud = excluded.latitud, longitud = excluded.longitud, actualizado_en = now()
        """,
        (fuente, id_publicacion, lat, lon),
    )


def _notificar_no_resuelta(notifier: TelegramNotifier, row: dict) -> bool:
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

    pendientes = get_pendientes(cur)
    if not pendientes:
        print("Sin publicaciones pendientes de re-geocodificar.")
        cur.close()
        conn.close()
        return

    centro_lat, centro_lon, radio_km = zona_centro_y_radio(cur)
    print(f"Zona: centro=({centro_lat:.5f}, {centro_lon:.5f}) radio={radio_km:.1f}km")

    notifier: TelegramNotifier | None = None

    print(f"Re-geocodificando {len(pendientes)} publicacion(es)...")
    resueltas = 0
    for i, row in enumerate(pendientes):
        if i > 0:
            time.sleep(PAUSA_ENTRE_REQUESTS_SEG)

        etiqueta = f"{row['fuente']}:{row['id_publicacion']}"
        coords = geocodificar(row["ubicacion"])
        fuera_de_zona = False
        if coords is not None:
            lat, lon = coords
            dlat = (lat - centro_lat) * KM_POR_GRADO_LAT
            dlon = (lon - centro_lon) * KM_POR_GRADO_LAT * math.cos(math.radians(centro_lat))
            fuera_de_zona = math.hypot(dlat, dlon) > radio_km

        if coords is not None and not fuera_de_zona:
            lat, lon = coords
            guardar_override(cur, row["fuente"], row["id_publicacion"], lat, lon)
            limpiar_notificado(cur, row["fuente"], row["id_publicacion"])
            conn.commit()
            resueltas += 1
            print(f"  {etiqueta} -> corregida ({lat:.5f}, {lon:.5f})")
            continue

        motivo = "sin resultado de Nominatim" if coords is None else "geocodificado pero sigue fuera de zona"
        print(f"  {etiqueta} -> {motivo}, se mantiene rechazada")

        if ya_notificado(cur, row["fuente"], row["id_publicacion"]):
            continue
        if notifier is None:
            try:
                notifier = TelegramNotifier()
            except ValueError as exc:
                print(f"  (sin Telegram configurado, no se avisa: {exc})")
                continue
        if _notificar_no_resuelta(notifier, row):
            marcar_notificado(cur, row["fuente"], row["id_publicacion"])
            conn.commit()
        else:
            print(f"  {etiqueta} -> fallo el envio a Telegram, se reintenta la proxima corrida")

    print(f"Resueltas: {resueltas}/{len(pendientes)}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
