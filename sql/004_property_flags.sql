-- ============================================================
-- 004_property_flags.sql
-- Estado de "me interesa" / "descartada" por propiedad, persistido en
-- la base para que se vea igual desde cualquier navegador o dispositivo
-- (antes vivía en localStorage, por-dispositivo).
-- Python-managed, igual que silver.events / silver.health_alerts.
-- Idempotente: se puede correr más de una vez sin error.
-- ============================================================

CREATE TABLE IF NOT EXISTS silver.property_flags (
    fuente          TEXT        NOT NULL,
    id_publicacion  TEXT        NOT NULL,
    estado          TEXT        NOT NULL,   -- 'interesa' | 'descartada'
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (fuente, id_publicacion)
);
