-- ============================================================
-- 007_snapshots_fecha_index.sql
-- Indice sobre fecha_scraping: lo usa prune_snapshots.py (flow diario propio,
-- ver pipeline.py:prune_pipeline) para borrar snapshots viejos sin escanear
-- toda la tabla, y de paso acelera otras consultas que ya filtran por esta
-- columna (historial de precio del dashboard, chequeos de salud).
-- Idempotente: se puede correr más de una vez sin error.
-- ============================================================

CREATE INDEX IF NOT EXISTS snapshots_fecha_scraping_idx
    ON raw.snapshots (fecha_scraping);
