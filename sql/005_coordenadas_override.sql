-- ============================================================
-- 005_coordenadas_override.sql
-- Coordenadas corregidas para publicaciones cuyo portal de origen las
-- geocodificó mal (dirección correcta, lat/lon lejos de la zona buscada).
-- Se completa via geocode_fallback.py re-consultando `ubicacion` contra
-- Nominatim; publicaciones.sql las usa con preferencia sobre las del portal.
-- Python-managed, igual que silver.events / silver.property_flags.
-- Idempotente: se puede correr más de una vez sin error.
-- ============================================================

CREATE TABLE IF NOT EXISTS silver.coordenadas_override (
    fuente          TEXT        NOT NULL,
    id_publicacion  TEXT        NOT NULL,
    latitud         NUMERIC     NOT NULL,
    longitud        NUMERIC     NOT NULL,
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (fuente, id_publicacion)
);
