select *
from {{ ref('publicaciones') }}
where superficie_m2 > 70
  and ambientes > 2
  and (
      (moneda = 'USD' and (precio + coalesce(expensas, 0) / {{ var('tipo_cambio_usd') }}) <= {{ var('presupuesto_ars') }} / {{ var('tipo_cambio_usd') }})
   or (moneda = 'ARS' and (precio + coalesce(expensas, 0)) <= {{ var('presupuesto_ars') }})
  )