{% macro km_por_grado_lat() %}
{#
    Kilómetros por grado de latitud (circunferencia terrestre ≈ 40.008km / 360°).
    Sirve para aproximar distancias cortas sin Haversine/PostGIS: 1 grado de
    longitud equivale a esto mismo multiplicado por cos(latitud), porque los
    meridianos se acercan entre sí cerca de los polos.
    Usado por publicaciones.sql y publicaciones_rechazadas.sql para detectar
    coordenadas mal geocodificadas por el portal de origen (ver zona_centro /
    zona_radio y geocode_fallback.py, que replica este mismo cálculo en Python).
#}
111.32
{% endmacro %}
