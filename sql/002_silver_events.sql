-- ============================================================
-- 002_silver_events.sql
-- Crea las tablas de eventos y notificaciones en silver.
-- Los schemas silver y gold los gestiona dbt; las tablas de
-- events y notifications son Python-managed (no son modelos dbt).
-- Idempotente: se puede correr más de una vez sin error.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS silver;

-- ============================================================
-- silver.events
-- Cambios detectados al comparar dos corridas consecutivas.
-- Una fila por (fuente, id_publicacion, tipo_evento, id_ejecucion):
-- garantiza idempotencia si el script de detección corre más de una vez.
-- ============================================================
CREATE TABLE IF NOT EXISTS silver.events (
    id_evento         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    id_ejecucion      TEXT        NOT NULL,   -- run "B" que disparó la detección
    fuente            TEXT        NOT NULL,
    id_publicacion    TEXT        NOT NULL,
    tipo_evento       TEXT        NOT NULL,   -- NEW, PRICE_DOWN, PRICE_UP, EXPENSES_CHANGE, CURRENCY_CHANGE, OFF_MARKET
    titulo            TEXT,
    url               TEXT,
    moneda            TEXT,
    precio_anterior   NUMERIC,
    precio_nuevo      NUMERIC,
    expensas_anterior NUMERIC,
    expensas_nuevo    NUMERIC,
    detectado_en      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fue_notificado    BOOLEAN     NOT NULL DEFAULT FALSE,

    UNIQUE (fuente, id_publicacion, tipo_evento, id_ejecucion)
);

CREATE INDEX IF NOT EXISTS silver_events_pendientes_idx
    ON silver.events (fue_notificado) WHERE fue_notificado = FALSE;

CREATE INDEX IF NOT EXISTS silver_events_publicacion_idx
    ON silver.events (fuente, id_publicacion);

-- ============================================================
-- silver.notifications
-- Log de cada mensaje enviado (Telegram u otro canal).
-- ============================================================
CREATE TABLE IF NOT EXISTS silver.notifications (
    id          BIGSERIAL   PRIMARY KEY,
    id_evento   UUID        REFERENCES silver.events(id_evento),
    canal       TEXT        NOT NULL,
    mensaje     TEXT,
    estado      TEXT        NOT NULL DEFAULT 'sent',
    enviado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);
