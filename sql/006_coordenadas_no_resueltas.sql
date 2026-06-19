-- ============================================================
-- 006_coordenadas_no_resueltas.sql
-- Evita reenviar por Telegram la misma alerta de "coordenadas fuera de zona,
-- no se pudo corregir" en cada corrida de geocode_fallback.py mientras sigue
-- sin resolverse. Misma idea que silver.health_alerts: la sola existencia de
-- la fila es el flag.
-- Python-managed, igual que silver.events / silver.health_alerts.
-- Idempotente: se puede correr más de una vez sin error.
-- ============================================================

CREATE TABLE IF NOT EXISTS silver.coordenadas_no_resueltas (
    fuente          TEXT        NOT NULL,
    id_publicacion  TEXT        NOT NULL,
    notificado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (fuente, id_publicacion)
);
