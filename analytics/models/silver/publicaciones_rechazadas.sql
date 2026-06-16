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

specs_parsed as (
    select
        d.id,
        max(case
            when d.fuente = 'zonaprop'     and s.spec ~* '\d+\s*m²\s*cub'
                then (regexp_match(s.spec, '(\d+)\s*m', 'i'))[1]::numeric
            when d.fuente = 'argenprop'    and s.spec ~* 'm²\s*cubierta'
                then (regexp_match(s.spec, '(\d+)\s*m', 'i'))[1]::numeric
            when d.fuente = 'argenprop'    and s.spec ~* '^sup\.\s*cubierta\s*:'
                then (regexp_match(s.spec, ':\s*(\d+)', 'i'))[1]::numeric
            when d.fuente = 'mercadolibre' and s.spec ~* '^superficie\s+cubierta\s*:'
                then (regexp_match(s.spec, ':\s*(\d+)', 'i'))[1]::numeric
        end) as superficie_cubierta,
        max(case
            when d.fuente = 'zonaprop'     and s.spec ~* '\d+\s*m²\s*tot'
                then (regexp_match(s.spec, '(\d+)\s*m', 'i'))[1]::numeric
            when d.fuente = 'argenprop'    and s.spec ~* 'm²\s*terreno'
                then (regexp_match(s.spec, '(\d+)\s*m', 'i'))[1]::numeric
            when d.fuente = 'argenprop'    and s.spec ~* '^sup\.\s*total\s*:'
                then (regexp_match(s.spec, ':\s*(\d+)', 'i'))[1]::numeric
            when d.fuente = 'mercadolibre' and s.spec ~* '^superficie\s+total\s*:'
                then (regexp_match(s.spec, ':\s*(\d+)', 'i'))[1]::numeric
        end) as superficie_total,
        max(case
            when d.fuente = 'zonaprop'     and s.spec ~* '^\d+\s*coch'
                then (regexp_match(s.spec, '^(\d+)', 'i'))[1]::numeric
            when d.fuente = 'argenprop'    and s.spec ~* '^\d+\s*cochera'
                then (regexp_match(s.spec, '^(\d+)', 'i'))[1]::numeric
            when d.fuente = 'mercadolibre' and s.spec ~* '^cocheras\s*:'
                then (regexp_match(s.spec, ':\s*(\d+)', 'i'))[1]::numeric
        end) as cocheras,
        max(case
            when d.fuente in ('zonaprop', 'argenprop') and s.spec ~* '^\d+\s*años'
                then (regexp_match(s.spec, '^(\d+)', 'i'))[1]::numeric
            when d.fuente = 'mercadolibre' and s.spec ~* '^antig'
                then (regexp_match(s.spec, '(\d+)\s*años?', 'i'))[1]::numeric
        end) as antiguedad,
        max(case
            when d.fuente = 'mercadolibre' and s.spec ~* '^ambientes\s*:'
                then (regexp_match(s.spec, ':\s*(\d+)', 'i'))[1]::numeric
        end) as spec_ambientes
    from deduped d
    left join lateral unnest(d.especificaciones) as s(spec) on true
    group by d.id
),

enriched as (
    select
        d.id,
        d.id_ejecucion,
        d.fuente,
        d.id_publicacion,
        d.url,
        d.titulo,
        d.precio,
        d.moneda,
        d.expensas,
        d.ubicacion,
        d.latitud,
        d.longitud,
        coalesce(d.ambientes, sp.spec_ambientes) as ambientes,
        sp.superficie_cubierta,
        sp.superficie_total,
        sp.cocheras,
        sp.antiguedad,
        d.publicado_en,
        d.fecha_scraping,
        d.vendedor,
        d.especificaciones
    from deduped d
    left join specs_parsed sp on d.id = sp.id
),

flagged as (
    select
        *,
        case
            when url           is null                                   then 'url_nula'
            when titulo        is null                                   then 'titulo_nulo'
            when precio        is null                                   then 'precio_nulo'
            when moneda        is null                                   then 'moneda_nula'
            when ubicacion     is null                                   then 'ubicacion_nula'
            when latitud       is null                                   then 'latitud_nula'
            when longitud      is null                                   then 'longitud_nula'
            when ambientes     is null                                   then 'ambientes_nulo'
            when coalesce(superficie_cubierta, superficie_total) is null then 'superficie_nula'
            when latitud  >= 0                                           then 'latitud_invalida'
            when longitud >= 0                                           then 'longitud_invalida'
            when ambientes     = 0                                       then 'ambientes_cero'
            when coalesce(superficie_cubierta, superficie_total) = 0    then 'superficie_cero'
            when precio = 9999999                                        then 'precio_placeholder'
            when moneda = 'ARS' and precio < 100000                     then 'precio_ars_bajo'
            when moneda = 'ARS' and precio > 4000000                    then 'precio_ars_alto'
            when moneda = 'USD' and precio < 100                        then 'precio_usd_bajo'
            when moneda = 'USD' and precio > 5000                       then 'precio_usd_alto'
        end as motivo_rechazo
    from enriched
)

select *
from flagged
where motivo_rechazo is not null
