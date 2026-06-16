{{ config(materialized='table') }}

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
    -- Por (fuente, id_publicacion) nos quedamos con el snapshot más reciente
    select distinct on (fuente, id_publicacion)
        *
    from base
    order by fuente, id_publicacion, fecha_scraping desc
),

specs_parsed as (
    -- Extrae campos estructurados desde el array especificaciones según el formato de cada portal.
    -- ZonaProp:     "200 m² tot." / "115 m² cub." / "1 coch." / "55 años"
    -- Argenprop:    "82 m² Cubierta" o "Sup. Cubierta: 82 m2"
    --               "385 m² Terreno" o "Sup. Total: 115 m2"
    --               "1 cochera" / "55 años"
    -- MercadoLibre: "Superficie cubierta: 118 m²" / "Superficie total: 151 m²"
    --               "Cocheras: 1" / "Antigüedad: 55 años" / "Ambientes: 3"
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

candidatos as (
    -- Solo publicaciones residenciales de alquiler participan en el cruce cross-portal.
    -- Uso comercial y venta/permuta generan falsos positivos al compartir coordenadas
    -- con propiedades residenciales distintas en el mismo bucket.
    select * from deduped
    where titulo not ilike '%uso comercial%'
      and titulo not ilike '%solo comercial%'
      and titulo not ilike '%venta permuta%'
),

duplicados_cross_portal as (
    -- Detecta pares en distintos portales que son la misma propiedad:
    -- mismo bucket lat/lon (±0.001° ≈ 111m), mismos ambientes,
    -- misma moneda y precio dentro del ±10%.
    -- De cada par se marca para eliminar el que tenga especificaciones más cortas;
    -- en empate, se descarta el de id mayor (más nuevo).
    select distinct
        case
            when coalesce(array_length(a.especificaciones, 1), 0)
               > coalesce(array_length(b.especificaciones, 1), 0)
                then b.id
            when coalesce(array_length(b.especificaciones, 1), 0)
               > coalesce(array_length(a.especificaciones, 1), 0)
                then a.id
            else greatest(a.id, b.id)
        end as id_a_eliminar
    from candidatos a
    join candidatos b
        on a.id < b.id
        and a.fuente    != b.fuente
        and a.moneda     = b.moneda
        and a.ambientes  = b.ambientes
        and a.moneda    is not null
        and a.ambientes is not null
        and a.precio    is not null
        and b.precio    is not null
        and round(a.latitud::numeric,  3) = round(b.latitud::numeric,  3)
        and round(a.longitud::numeric, 3) = round(b.longitud::numeric, 3)
        and abs(a.precio::float - b.precio::float)
            / greatest(a.precio, b.precio)::float <= 0.10
),

enriched as (
    -- Aplica COALESCE(raw, specs) para ambientes y añade columnas nuevas desde specs.
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

filtered as (
    select
        *,
        case
            when titulo ilike '%uso comercial%'
              or titulo ilike '%solo comercial%'                          then 'uso_comercial'
            when titulo ilike '%venta permuta%'                          then 'venta_permuta'
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
            when moneda = 'ARS' and precio < 500000                     then 'precio_ars_bajo'
            when moneda = 'ARS' and precio > 3000000                    then 'precio_ars_alto'
            when moneda = 'USD' and precio < 100                        then 'precio_usd_bajo'
            when moneda = 'USD' and precio > 3000                       then 'precio_usd_alto'
            else null
        end as motivo_rechazo
    from enriched
    where id not in (select id_a_eliminar from duplicados_cross_portal)
)

select
    id,
    id_ejecucion,
    fuente,
    id_publicacion,
    url,
    titulo,
    precio,
    moneda,
    expensas,
    ubicacion,
    latitud,
    longitud,
    ambientes,
    superficie_cubierta,
    superficie_total,
    cocheras,
    antiguedad,
    publicado_en,
    fecha_scraping,
    vendedor,
    especificaciones
from filtered
where motivo_rechazo is null
