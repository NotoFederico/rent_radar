-- ============================================================
-- 001_init_schemas.sql
-- Crea los schemas y tablas del schema raw.
-- El schema analytics lo gestiona dbt, solo se crea el schema.
-- Idempotente: se puede correr más de una vez sin error.
-- ============================================================

-- Schemas
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS analytics;

-- ============================================================
-- raw.pipeline_runs
-- Una fila por ejecución del pipeline (por fuente).
-- ============================================================
CREATE TABLE IF NOT EXISTS raw.pipeline_runs (
    id_ejecucion        TEXT        PRIMARY KEY,
    fuente              TEXT        NOT NULL,
    estado              TEXT        NOT NULL DEFAULT 'running',
    iniciado_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalizado_en       TIMESTAMPTZ,
    total_publicaciones INTEGER     NOT NULL DEFAULT 0
);

-- ============================================================
-- raw.snapshots
-- Publicaciones tal como las devuelve el spider, una por scrape.
-- Puede haber múltiples snapshots del mismo id_publicacion.
-- ============================================================
CREATE TABLE IF NOT EXISTS raw.snapshots (
    id              BIGSERIAL   PRIMARY KEY,
    id_ejecucion    TEXT        NOT NULL REFERENCES raw.pipeline_runs(id_ejecucion),
    fuente          TEXT        NOT NULL,
    id_publicacion  TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    titulo          TEXT        NOT NULL,
    precio          INTEGER,
    moneda          TEXT,
    expensas        INTEGER,
    ubicacion       TEXT,
    latitud         NUMERIC,
    longitud        NUMERIC,
    ambientes       INTEGER,
    dormitorios     INTEGER,
    banos           INTEGER,
    superficie_m2   INTEGER,
    publicado_en    TIMESTAMPTZ,
    fecha_scraping  TIMESTAMPTZ NOT NULL DEFAULT now(),
    vendedor        TEXT,
    especificaciones TEXT[]
);

CREATE INDEX IF NOT EXISTS snapshots_id_publicacion_idx ON raw.snapshots (id_publicacion);

-- ============================================================
-- raw.events
-- Cambios detectados entre snapshots: nueva publicación,
-- suba/baja de precio, salida del mercado.
-- ============================================================
CREATE TABLE IF NOT EXISTS raw.events (
    id_evento       TEXT        PRIMARY KEY,
    id_ejecucion    TEXT        NOT NULL REFERENCES raw.pipeline_runs(id_ejecucion),
    fuente          TEXT        NOT NULL,
    id_publicacion  TEXT        NOT NULL,
    tipo_evento     TEXT        NOT NULL,
    titulo          TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    precio_anterior INTEGER,
    precio_nuevo    INTEGER,
    detectado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    fue_notificado  BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS events_pendientes_idx ON raw.events (fue_notificado) WHERE fue_notificado = FALSE;

-- ============================================================
-- raw.notifications
-- Log de cada mensaje enviado por Telegram u otro canal.
-- ============================================================
CREATE TABLE IF NOT EXISTS raw.notifications (
    id                BIGSERIAL   PRIMARY KEY,
    id_evento         TEXT,
    canal             TEXT        NOT NULL,
    tipo_notificacion TEXT        NOT NULL,
    ids_notificados   TEXT[]      NOT NULL DEFAULT '{}',
    mensaje           TEXT,
    estado            TEXT        NOT NULL DEFAULT 'sent',
    enviado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
);

