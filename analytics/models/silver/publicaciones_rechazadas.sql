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
        coalesce(co.latitud,  d.latitud)  as latitud,
        coalesce(co.longitud, d.longitud) as longitud,
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
    left join silver.coordenadas_override co
        on co.fuente = d.fuente and co.id_publicacion = d.id_publicacion
),

zone_center as (
    -- Mismo cálculo que publicaciones.sql: mediana de lat/lon (robusta a outliers),
    -- sin centro fijo hardcodeado.
    select
        percentile_cont(0.5) within group (order by latitud)  as center_lat,
        percentile_cont(0.5) within group (order by longitud) as center_lon
    from enriched
    where latitud is not null and longitud is not null
      and latitud < 0 and longitud < 0
),

distances as (
    select
        e.id,
        sqrt(
            power((e.latitud - zc.center_lat) * {{ km_per_degree_lat() }}, 2)
          + power((e.longitud - zc.center_lon) * {{ km_per_degree_lat() }} * cos(radians(zc.center_lat)), 2)
        ) as distance_to_center_km
    from enriched e
    cross join zone_center zc
    where e.latitud is not null and e.longitud is not null
),

zone_radius as (
    select greatest(percentile_cont(0.5) within group (order by distance_to_center_km) * 4, 5) as radius_km
    from distances
),

flagged as (
    select
        e.*,
        case
            when e.url           is null                                 then 'url_nula'
            when e.titulo        is null                                 then 'titulo_nulo'
            when e.precio        is null                                 then 'precio_nulo'
            when e.moneda        is null                                 then 'moneda_nula'
            when e.ubicacion     is null                                 then 'ubicacion_nula'
            when e.latitud       is null                                 then 'latitud_nula'
            when e.longitud      is null                                 then 'longitud_nula'
            when e.ambientes     is null                                 then 'ambientes_nulo'
            when coalesce(e.superficie_cubierta, e.superficie_total) is null then 'superficie_nula'
            when e.latitud  >= 0                                         then 'latitud_invalida'
            when e.longitud >= 0                                         then 'longitud_invalida'
            when d.distance_to_center_km > zr.radius_km                     then 'coordenadas_fuera_de_zona'
            when e.ambientes     = 0                                     then 'ambientes_cero'
            when coalesce(e.superficie_cubierta, e.superficie_total) = 0 then 'superficie_cero'
            when e.precio = 9999999                                      then 'precio_placeholder'
            when e.moneda = 'ARS' and e.precio < 100000                  then 'precio_ars_bajo'
            when e.moneda = 'ARS' and e.precio > 4000000                 then 'precio_ars_alto'
            when e.moneda = 'USD' and e.precio < 100                     then 'precio_usd_bajo'
            when e.moneda = 'USD' and e.precio > 5000                    then 'precio_usd_alto'
        end as motivo_rechazo
    from enriched e
    left join distances d on d.id = e.id
    cross join zone_radius zr
)

select *
from flagged
where motivo_rechazo is not null
