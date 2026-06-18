-- ============================================================
-- 003_health_alerts.sql
-- Estado de alertas de portales desactualizados (ver check_health.py).
-- Python-managed, igual que silver.events / silver.notifications.
-- Idempotente: se puede correr más de una vez sin error.
-- ============================================================

-- ============================================================
-- silver.health_alerts
-- Una fila por fuente mientras está en estado "desactualizado".
-- Su sola existencia es el flag: evita reenviar la alerta en cada
-- corrida y dispara un aviso de recuperación cuando se borra.
-- ============================================================
CREATE TABLE IF NOT EXISTS silver.health_alerts (
    fuente      TEXT        PRIMARY KEY,
    alertado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
