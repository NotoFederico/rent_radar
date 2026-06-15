{{ config(materialized='table') }}

-- Espeja la lógica de publicaciones.sql pero retiene los registros inválidos.
-- Útil para auditar calidad de datos y ajustar reglas.

with latest_runs as (
    select distinct on (fuente)
        id_ejecucion,
        fuente
    from raw.pipeline_runs
    where fuente != 'unknown'
      and estado = 'ok'
    order by fuente, finalizado_en desc
),

base as (
    select s.*
    from raw.snapshots s
    inner join latest_runs lr on s.id_ejecucion = lr.id_ejecucion
),

deduped as (
    select distinct on (fuente, id_publicacion)
        *
    from base
    order by fuente, id_publicacion, fecha_scraping desc
),

flagged as (
    select
        *,
        case
            when url           is null then 'url_nula'
            when titulo        is null then 'titulo_nulo'
            when precio        is null then 'precio_nulo'
            when moneda        is null then 'moneda_nula'
            when ubicacion     is null then 'ubicacion_nula'
            when latitud       is null then 'latitud_nula'
            when longitud      is null then 'longitud_nula'
            when ambientes     is null then 'ambientes_nulo'
            when dormitorios   is null then 'dormitorios_nulo'
            when banos         is null then 'banos_nulo'
            when superficie_m2 is null then 'superficie_nula'
            when latitud  >= 0         then 'latitud_invalida'
            when longitud >= 0         then 'longitud_invalida'
            when ambientes     = 0     then 'ambientes_cero'
            when dormitorios   = 0     then 'dormitorios_cero'
            when banos         = 0     then 'banos_cero'
            when superficie_m2 = 0     then 'superficie_cero'
            when moneda = 'ARS' and precio < 100000   then 'precio_ars_bajo'
            when moneda = 'ARS' and precio > 4000000  then 'precio_ars_alto'
            when moneda = 'USD' and precio < 100      then 'precio_usd_bajo'
            when moneda = 'USD' and precio > 5000     then 'precio_usd_alto'
        end as motivo_rechazo
    from deduped
)

select *
from flagged
where motivo_rechazo is not null
