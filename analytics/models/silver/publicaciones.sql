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

filtered as (
    select
        *,
        case
            when titulo ilike '%uso comercial%'
              or titulo ilike '%solo comercial%' then 'uso_comercial'
            when titulo ilike '%venta permuta%'  then 'venta_permuta'
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
            when moneda = 'ARS' and precio < 500000   then 'precio_ars_bajo'
            when moneda = 'ARS' and precio > 3000000  then 'precio_ars_alto'
            when moneda = 'USD' and precio < 100      then 'precio_usd_bajo'
            when moneda = 'USD' and precio > 3000     then 'precio_usd_alto'
            else null
        end as motivo_rechazo
    from deduped
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
    dormitorios,
    banos,
    superficie_m2,
    publicado_en,
    fecha_scraping,
    vendedor,
    especificaciones
from filtered
where motivo_rechazo is null
