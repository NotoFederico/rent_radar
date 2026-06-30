{{ config(materialized='table') }}

with candidatas_stats as (
    select
        count(*)                                                          as total_candidatas,
        count(*) filter (where moneda = 'ARS')                           as total_ars,
        count(*) filter (where moneda = 'USD')                           as total_usd,
        count(*) filter (where fuente = 'zonaprop')                      as cnt_zonaprop,
        count(*) filter (where fuente = 'argenprop')                     as cnt_argenprop,
        count(*) filter (where fuente = 'mercadolibre')                  as cnt_mercadolibre,
        round(avg(precio) filter (where moneda = 'ARS'))                 as precio_promedio_ars,
        round(percentile_cont(0.5) within group (order by precio) filter (where moneda = 'ARS')) as precio_mediana_ars,
        round(min(precio) filter (where moneda = 'ARS'))                 as precio_min_ars,
        round(max(precio) filter (where moneda = 'ARS'))                 as precio_max_ars,
        round(avg(precio) filter (where moneda = 'USD'))                 as precio_promedio_usd,
        round(percentile_cont(0.5) within group (order by precio) filter (where moneda = 'USD')) as precio_mediana_usd,
        round(min(precio) filter (where moneda = 'USD'))                 as precio_min_usd,
        round(max(precio) filter (where moneda = 'USD'))                 as precio_max_usd,
        round(avg(coalesce(superficie_cubierta, superficie_total)))      as sup_promedio_m2,
        round(avg(ambientes), 1)                                         as ambientes_promedio,
        count(*) filter (where cocheras > 0)                             as con_cochera,
        count(*) filter (where antiguedad is not null and antiguedad = 0) as a_estrenar
    from {{ ref('candidatas') }}
),

eventos_recientes as (
    -- Eventos acumulados desde la medianoche en horario argentino (no una ventana
    -- deslizante): así el dashboard no "pierde" eventos entre refrescos.
    select
        count(*) filter (where tipo_evento = 'NEW')              as nuevas_hoy,
        count(*) filter (where tipo_evento = 'PRICE_DOWN')       as bajas_precio,
        count(*) filter (where tipo_evento = 'PRICE_UP')         as subas_precio,
        count(*) filter (where tipo_evento = 'OFF_MARKET')       as fuera_mercado,
        count(*) filter (where tipo_evento = 'EXPENSES_CHANGE')  as cambios_expensas,
        count(*) filter (where tipo_evento = 'CURRENCY_CHANGE')  as cambios_moneda
    from silver.events
    where detectado_en >= date_trunc('day', now() AT TIME ZONE 'America/Argentina/Buenos_Aires')
                            AT TIME ZONE 'America/Argentina/Buenos_Aires'
),

rechazadas_stats as (
    select
        count(*)                                                  as total_rechazadas,
        mode() within group (order by motivo_rechazo)            as motivo_mas_frecuente
    from {{ ref('publicaciones_rechazadas') }}
),

fechas_fuente as (
    -- Última corrida con datos reales por portal (estado='ok' en raw.pipeline_runs,
    -- ver silver/publicaciones.sql). Permite detectar portales con datos desactualizados
    -- cuando el scraper viene fallando (ej. bloqueo anti-bot).
    select
        max(fecha_scraping) filter (where fuente = 'zonaprop')     as ultima_zonaprop,
        max(fecha_scraping) filter (where fuente = 'argenprop')    as ultima_argenprop,
        max(fecha_scraping) filter (where fuente = 'mercadolibre') as ultima_mercadolibre
    from {{ ref('publicaciones') }}
)

select
    now()                            as calculado_en,
    {{ var('tipo_cambio_usd') }}     as tipo_cambio_usd_usado,
    cs.*,
    er.*,
    rs.*,
    ff.*
from candidatas_stats cs
cross join eventos_recientes er
cross join rechazadas_stats rs
cross join fechas_fuente ff
