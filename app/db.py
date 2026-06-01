from __future__ import annotations

from typing import Any, Iterable

import psycopg2
import psycopg2.extras

from app.config import NEON_DATABASE_URL


class ScraperDB:
    def __init__(self) -> None:
        self.conn = self._connect()

    def _connect(self) -> psycopg2.extensions.connection:
        conn = psycopg2.connect(
            NEON_DATABASE_URL,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        psycopg2.extras.register_uuid(conn)
        return conn

    def _ensure_connected(self) -> None:
        if self.conn.closed:
            self.conn = self._connect()
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = self._connect()

    def initialize(self) -> None:
        pass

    def close(self) -> None:
        if not self.conn.closed:
            self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # ------------------------------------------------------------------ writes

    def insert_run(self, row: dict[str, Any]) -> None:
        self._ensure_connected()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO raw.pipeline_runs
                    (id_ejecucion, fuente, estado, iniciado_en, finalizado_en, total_publicaciones)
                VALUES
                    (%(id_ejecucion)s, %(fuente)s, %(estado)s, %(iniciado_en)s,
                     %(finalizado_en)s, %(total_publicaciones)s)
                ON CONFLICT (id_ejecucion) DO UPDATE
                    SET estado               = EXCLUDED.estado,
                        finalizado_en        = EXCLUDED.finalizado_en,
                        total_publicaciones  = EXCLUDED.total_publicaciones
                """,
                row,
            )
        self.conn.commit()

    def insert_snapshots(self, rows: Iterable[dict[str, Any]]) -> None:
        records = list(rows)
        if not records:
            return
        self._ensure_connected()
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO raw.snapshots
                    (id_ejecucion, fuente, id_publicacion, url, titulo, precio, moneda,
                     expensas, ubicacion, latitud, longitud, ambientes, dormitorios,
                     banos, superficie_m2, publicado_en, fecha_scraping, vendedor, especificaciones)
                VALUES %s
                """,
                [
                    (
                        r["id_ejecucion"], r["fuente"], r["id_publicacion"], r["url"],
                        r["titulo"], r.get("precio"), r.get("moneda"), r.get("expensas"),
                        r.get("ubicacion"), r.get("latitud"), r.get("longitud"),
                        r.get("ambientes"), r.get("dormitorios"), r.get("banos"),
                        r.get("superficie_m2"), r.get("publicado_en"), r.get("fecha_scraping"),
                        r.get("vendedor"), r.get("especificaciones", []),
                    )
                    for r in records
                ],
            )
        self.conn.commit()

    def insert_events(self, rows: Iterable[dict[str, Any]]) -> None:
        records = list(rows)
        if not records:
            return
        self._ensure_connected()
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO raw.events
                    (id_evento, id_ejecucion, fuente, id_publicacion, tipo_evento,
                     titulo, url, precio_anterior, precio_nuevo, detectado_en, fue_notificado)
                VALUES %s
                ON CONFLICT (id_evento) DO NOTHING
                """,
                [
                    (
                        r["id_evento"], r["id_ejecucion"], r["fuente"], r["id_publicacion"],
                        r["tipo_evento"], r["titulo"], r["url"],
                        r.get("precio_anterior"), r.get("precio_nuevo"),
                        r["detectado_en"], r.get("fue_notificado", False),
                    )
                    for r in records
                ],
            )
        self.conn.commit()

    def insert_notifications(self, rows: Iterable[dict[str, Any]]) -> None:
        records = list(rows)
        if not records:
            return
        self._ensure_connected()
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO raw.notifications
                    (id_evento, canal, tipo_notificacion, ids_notificados, mensaje, estado, enviado_en)
                VALUES %s
                """,
                [
                    (
                        r.get("id_evento"), r["canal"], r["tipo_notificacion"],
                        r.get("ids_notificados", []), r.get("mensaje"),
                        r.get("estado", "sent"), r["enviado_en"],
                    )
                    for r in records
                ],
            )
        self.conn.commit()

    # ------------------------------------------------------------------ reads

    def get_latest_run(self, source: str) -> dict[str, Any] | None:
        self._ensure_connected()
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM raw.pipeline_runs
                WHERE fuente = %s
                ORDER BY iniciado_en DESC
                LIMIT 1
                """,
                (source,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_run_snapshots(self, run_id: str) -> list[dict[str, Any]]:
        self._ensure_connected()
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM raw.snapshots WHERE id_ejecucion = %s",
                (run_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_pending_events(self) -> list[dict[str, Any]]:
        self._ensure_connected()
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM raw.events WHERE fue_notificado = FALSE ORDER BY detectado_en"
            )
            return [dict(r) for r in cur.fetchall()]

    def mark_event_as_notified(self, event_id: str) -> None:
        self._ensure_connected()
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE raw.events SET fue_notificado = TRUE WHERE id_evento = %s",
                (event_id,),
            )
        self.conn.commit()
