"""Genera dashboard.html con propiedades de gold.candidatas y métricas de gold.metricas."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("NEON_DATABASE_URL") or os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    try:
        from app.config import NEON_DATABASE_URL as DATABASE_URL
    except ImportError:
        sys.exit("No se encontró DATABASE_URL ni app.config.NEON_DATABASE_URL")


def fetch_listings() -> list[dict]:
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            select id_publicacion, titulo, url, precio, moneda, fuente,
                   ambientes, superficie_cubierta, superficie_total,
                   cocheras, antiguedad, ubicacion, latitud, longitud
            from gold.candidatas
            where latitud is not null and longitud is not null
        """)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def attach_price_history(rows: list[dict]) -> None:
    """Agrega `historial` (lista de precios cronológica) a cada row, in-place.

    Solo incluye puntos con la misma moneda que el precio actual: un cambio
    de moneda en el medio haría que la línea se vea como un salto gigante
    sin serlo en términos reales.
    """
    if not rows:
        return
    pairs = [(r["fuente"], r["id_publicacion"]) for r in rows]

    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # fetch=True es necesario: execute_values pagina `pairs` de a 100 (default
        # page_size) y sin fetch=True cada página pisa el resultado de la anterior,
        # así que cur.fetchall() después solo devolvía la última página.
        result = psycopg2.extras.execute_values(
            cur,
            """
            select s.fuente, s.id_publicacion, s.precio, s.moneda, s.fecha_scraping
            from raw.snapshots s
            join (values %s) as v(fuente, id_publicacion)
              on s.fuente = v.fuente and s.id_publicacion = v.id_publicacion
            where s.precio is not null
            order by s.fuente, s.id_publicacion, s.fecha_scraping
            """,
            pairs,
            fetch=True,
        )
        history: dict[tuple[str, str], list[dict]] = {}
        for r in result:
            key = (r["fuente"], r["id_publicacion"])
            history.setdefault(key, []).append(r)
    conn.close()

    for row in rows:
        puntos = history.get((row["fuente"], row["id_publicacion"]), [])
        puntos = [p for p in puntos if p["moneda"] == row["moneda"]]
        row["historial"] = [p["precio"] for p in puntos] if len(puntos) > 1 else []


def fetch_metricas() -> dict:
    conn = psycopg2.connect(DATABASE_URL)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("select * from gold.metricas limit 1")
        row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def _n(v, default="—") -> str:
    """Formatea un número entero con puntos de miles."""
    if v is None:
        return default
    return f"{int(v):,}".replace(",", ".")


def _p(v, moneda="ARS", default="—") -> str:
    """Formatea un precio con símbolo de moneda."""
    if v is None:
        return default
    n = _n(v)
    return f"USD {n}" if moneda == "USD" else f"$ {n}"


def _bar(val, min_val, max_val, default=50) -> int:
    """Porcentaje de posición de val entre min y max (5–95%)."""
    if None in (val, min_val, max_val) or max_val == min_val:
        return default
    val, min_val, max_val = float(val), float(min_val), float(max_val)
    return max(5, min(95, round((val - min_val) / (max_val - min_val) * 100)))


def _pct(part, total, default=0) -> int:
    if not total:
        return default
    return round(part / total * 100)


def _stale_badge(ts, threshold_min: int) -> str:
    """Badge de alerta si el portal no trajo datos nuevos en threshold_min minutos."""
    if not isinstance(ts, datetime):
        return ""
    ts_utc = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    elapsed_min = (datetime.now(timezone.utc) - ts_utc).total_seconds() / 60
    if elapsed_min <= threshold_min:
        return ""
    label = f"hace {int(elapsed_min)} min" if elapsed_min < 60 else f"hace {elapsed_min / 60:.1f}h"
    return f'<span class="fuente-stale" title="Sin datos nuevos desde {label}">⚠ {label}</span>'


def build_ctx(rows: list[dict], m: dict) -> dict:
    """Construye el dict de variables para el template."""
    total = m.get("total_candidatas") or len(rows)
    cnt_z = m.get("cnt_zonaprop") or 0
    cnt_a = m.get("cnt_argenprop") or 0
    cnt_ml = m.get("cnt_mercadolibre") or 0

    ART = timezone(timedelta(hours=-3))
    calculado = m.get("calculado_en")
    if isinstance(calculado, datetime):
        calculado = calculado.replace(tzinfo=timezone.utc).astimezone(ART)
        ultima = calculado.strftime("%d/%m %H:%M")
    else:
        ultima = "—"

    return dict(
        datos_json=json.dumps(rows, default=str, ensure_ascii=False),
        ultima_corrida=ultima,
        # Métricas
        total_candidatas=_n(total, "—"),
        sup_promedio=_n(m.get("sup_promedio_m2"), "—"),
        ambientes_promedio=str(m.get("ambientes_promedio") or "—"),
        con_cochera=_n(m.get("con_cochera"), "—"),
        # eventos (acumulados desde la medianoche ART)
        nuevas=_n(m.get("nuevas_hoy"), "0"),
        bajas=_n(m.get("bajas_precio"), "0"),
        subas=_n(m.get("subas_precio"), "0"),
        off_market=_n(m.get("fuera_mercado"), "0"),
        # precios ARS (mediana, más robusta a outliers que el promedio)
        precio_avg_ars=_p(m.get("precio_mediana_ars"), "ARS"),
        precio_min_ars=_p(m.get("precio_min_ars"), "ARS"),
        precio_max_ars=_p(m.get("precio_max_ars"), "ARS"),
        bar_ars=_bar(m.get("precio_mediana_ars"), m.get("precio_min_ars"), m.get("precio_max_ars")),
        # precios USD (mediana)
        precio_avg_usd=_p(m.get("precio_mediana_usd"), "USD"),
        precio_min_usd=_p(m.get("precio_min_usd"), "USD"),
        precio_max_usd=_p(m.get("precio_max_usd"), "USD"),
        bar_usd=_bar(m.get("precio_mediana_usd"), m.get("precio_min_usd"), m.get("precio_max_usd")),
        # portales
        cnt_zonaprop=cnt_z,
        cnt_argenprop=cnt_a,
        cnt_mercadolibre=cnt_ml,
        bar_zonaprop=_pct(cnt_z, total),
        bar_argenprop=_pct(cnt_a, total),
        bar_mercadolibre=_pct(cnt_ml, total),
        badge_zonaprop=_stale_badge(m.get("ultima_zonaprop"), 90),
        badge_argenprop=_stale_badge(m.get("ultima_argenprop"), 90),
        badge_mercadolibre=_stale_badge(m.get("ultima_mercadolibre"), 150),
    )


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<title>Rent Radar</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  :root {{
    --bg:#F0F2F5; --sidebar-w:340px; --nav-h:52px;
    --white:#fff; --border:#E2E6EA; --text:#1A1D23; --muted:#6B7280;
    --blue:#1D6BE5; --green:#16A34A; --red:#DC2626;
    --orange:#D97706; --purple:#7C3AED; --teal:#0891B2;
    --radius:10px;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); height:100vh; display:flex; flex-direction:column; overflow:hidden; }}

  nav {{ height:var(--nav-h); background:var(--white); border-bottom:1px solid var(--border); display:flex; align-items:center; padding:0 16px; gap:12px; flex-shrink:0; z-index:1000; }}
  .nav-logo {{ font-size:17px; font-weight:700; color:var(--blue); display:flex; align-items:center; gap:7px; }}
  .nav-sep {{ width:1px; height:22px; background:var(--border); }}
  .nav-run {{ font-size:12px; color:var(--muted); display:flex; align-items:center; gap:6px; }}
  .nav-dot {{ width:7px; height:7px; border-radius:50%; background:var(--green); box-shadow:0 0 0 2px #dcfce7; flex-shrink:0; }}
  .nav-spacer {{ flex:1; }}
  .nav-pill {{ font-size:11px; font-weight:600; padding:3px 10px; border-radius:20px; background:#EEF2FF; color:var(--blue); border:1px solid #C7D7FA; }}
  .nav-tg {{ font-size:11px; font-weight:600; padding:3px 10px; border-radius:20px; background:#E8F4FD; color:#229ED9; border:1px solid #A8D5F5; display:flex; align-items:center; gap:5px; }}
  .nav-credit {{ font-size:11px; color:var(--muted); white-space:nowrap; }}

  @media (max-width:700px) {{
    :root {{ --sidebar-w:100%; }}
    .layout {{ flex-direction:column; }}
    aside {{ width:100%; border-right:none; border-bottom:1px solid var(--border); max-height:42vh; }}
    #mapa {{ flex:1; min-height:0; }}
    .nav-credit, .nav-tg, .nav-sep {{ display:none; }}
    .nav-run {{ font-size:11px; }}
    .metrics-grid {{ grid-template-columns:repeat(4,1fr); }}
    .eventos-strip {{ grid-template-columns:repeat(4,1fr); }}
  }}


  .layout {{ display:flex; flex:1; overflow:hidden; }}

  aside {{ width:var(--sidebar-w); flex-shrink:0; background:var(--bg); display:flex; flex-direction:column; overflow:hidden; border-right:1px solid var(--border); }}
  .sidebar-inner {{ flex:1; overflow-y:auto; padding:14px 12px; display:flex; flex-direction:column; gap:14px; scrollbar-gutter:stable; mask-image:linear-gradient(to bottom, black 88%, transparent 100%); -webkit-mask-image:linear-gradient(to bottom, black 88%, transparent 100%); }}
  .sidebar-inner::-webkit-scrollbar {{ width:5px; }}
  .sidebar-inner::-webkit-scrollbar-track {{ background:#E8EBF0; border-radius:4px; }}
  .sidebar-inner::-webkit-scrollbar-thumb {{ background:var(--blue); border-radius:4px; opacity:.7; }}
  .sidebar-inner::-webkit-scrollbar-thumb:hover {{ background:#1558c0; }}

  .section-label {{ font-size:10.5px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.6px; padding:0 2px; }}

  .metrics-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
  .metric-card {{ background:var(--white); border:1px solid var(--border); border-radius:var(--radius); padding:11px 12px; display:flex; flex-direction:column; gap:3px; }}
  .metric-icon {{ font-size:17px; line-height:1; }}
  .metric-label {{ font-size:10.5px; color:var(--muted); font-weight:500; text-transform:uppercase; letter-spacing:.3px; }}
  .metric-value {{ font-size:22px; font-weight:800; line-height:1.1; }}
  .metric-sub {{ font-size:10.5px; color:var(--muted); }}

  .eventos-strip {{ display:grid; grid-template-columns:repeat(2,1fr); gap:6px; }}
  .evento-chip {{ background:var(--white); border:1px solid var(--border); border-radius:8px; padding:8px 10px; display:flex; flex-direction:column; align-items:center; gap:2px; }}
  .ev-icon {{ font-size:16px; }}
  .ev-num {{ font-size:18px; font-weight:800; }}
  .ev-label {{ font-size:9.5px; color:var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.3px; text-align:center; }}

  .precios-strip {{ background:var(--white); border:1px solid var(--border); border-radius:var(--radius); padding:11px 12px; display:flex; flex-direction:column; gap:8px; }}
  .precio-row {{ display:flex; align-items:center; gap:10px; }}
  .precio-moneda {{ font-size:11px; font-weight:700; width:32px; padding:2px 6px; border-radius:4px; text-align:center; }}
  .moneda-ars {{ background:#F0FDF4; color:#166534; }}
  .moneda-usd {{ background:#EFF6FF; color:#1e40af; }}
  .precio-range {{ flex:1; display:flex; flex-direction:column; gap:2px; }}
  .precio-vals {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .precio-avg {{ font-size:15px; font-weight:800; }}
  .precio-minmax {{ font-size:10px; color:var(--muted); }}
  .precio-track {{ height:3px; background:var(--border); border-radius:3px; }}
  .precio-track-fill {{ height:100%; border-radius:3px; background:var(--blue); }}

  .fuentes-strip {{ background:var(--white); border:1px solid var(--border); border-radius:var(--radius); padding:11px 12px; display:flex; flex-direction:column; gap:7px; }}
  .fuente-row {{ display:flex; align-items:center; gap:8px; }}
  .fuente-name {{ font-size:11.7px; font-weight:600; width:100px; }}
  .fuente-track {{ flex:1; height:6px; background:var(--border); border-radius:6px; overflow:hidden; }}
  .fuente-fill {{ height:100%; border-radius:6px; }}
  .fuente-count {{ font-size:11px; font-weight:700; color:var(--muted); width:24px; text-align:right; }}
  .fill-zona  {{ background:#F59E0B; }}
  .fill-argen {{ background:#22C55E; }}
  .fill-ml    {{ background:#F97316; }}
  .fuente-stale {{ font-size:9.5px; font-weight:700; color:#92400E; background:#FEF3C7; border:1px solid #FDE68A; border-radius:10px; padding:1px 6px; white-space:nowrap; }}

  .chips {{ display:flex; gap:6px; flex-wrap:wrap; }}
  .chip {{ font-size:11px; font-weight:600; padding:4px 10px; border-radius:20px; border:1.7px solid transparent; cursor:pointer; transition:opacity .15s; }}
  .chip:not(.active) {{ opacity:.35; }}
  .chip-all   {{ background:#EEF2FF; color:var(--blue);  border-color:#C7D7FA; }}
  .chip-zona  {{ background:#FEF3C7; color:#92400E;      border-color:#FDE68A; }}
  .chip-argen {{ background:#F0FDF4; color:#166534;      border-color:#BBF7D0; }}
  .chip-ml    {{ background:#FFF7ED; color:#9A3412;      border-color:#FED7AA; }}

  .prop-list {{ display:flex; flex-direction:column; gap:7px; }}
  .prop-card {{ background:var(--white); border:1px solid var(--border); border-radius:var(--radius); padding:11px 12px; cursor:pointer; transition:box-shadow .15s,border-color .15s; display:flex; flex-direction:column; gap:5px; }}
  .prop-card:hover {{ box-shadow:0 2px 10px rgba(0,0,0,.08); border-color:#c5cfe8; }}
  .prop-card.selected {{ border-color:var(--blue); box-shadow:0 0 0 2px #c7d7fa; }}
  .prop-top {{ display:flex; align-items:flex-start; justify-content:space-between; gap:6px; }}
  .prop-titulo {{ font-size:12.5px; font-weight:600; line-height:1.75; flex:1; }}
  .prop-badge {{ font-size:10px; font-weight:700; padding:2px 7px; border-radius:20px; white-space:nowrap; flex-shrink:0; margin-top:1px; }}
  .prop-precio {{ font-size:14px; font-weight:700; }}
  .prop-detalle {{ font-size:11px; color:var(--muted); }}
  .prop-footer {{ display:flex; align-items:center; justify-content:space-between; }}
  .fuente-tag {{ font-size:10px; font-weight:600; padding:2px 7px; border-radius:20px; }}
  .tag-zonaprop     {{ background:#FEF3C7; color:#92400E; }}
  .tag-argenprop    {{ background:#F0FDF4; color:#166534; }}
  .tag-mercadolibre {{ background:#FFF7ED; color:#9A3412; }}
  .prop-ubicacion {{ font-size:10.5px; color:var(--muted); }}

  #mapa {{ flex:1; z-index:1; }}

  .mk {{ width:26px; height:26px; border-radius:50% 50% 50% 0; transform:rotate(-45deg); border:2.5px solid white; box-shadow:0 2px 8px rgba(0,0,0,.35); }}
  .mk-default    {{ background:#6366F1; }}
  .mk-NEW        {{ background:#16A34A; }}
  .mk-PRICE_DOWN {{ background:#1D6BE5; }}
  .mk-PRICE_UP   {{ background:#DC2626; }}
  .mk-ref {{ width:24px; height:24px; border-radius:50%; background:#FBBF24; border:3px solid white; box-shadow:0 2px 10px rgba(0,0,0,.4); }}

  .popup-titulo {{ font-weight:700; font-size:13px; margin-bottom:4px; line-height:1.7; }}
  .popup-precio {{ font-size:15px; font-weight:800; color:var(--blue); margin-bottom:3px; }}
  .popup-det    {{ font-size:11.7px; color:#555; margin-bottom:2px; }}
  .popup-loc    {{ font-size:11px; color:#888; margin-bottom:7px; }}
  .popup-spark  {{ display:block; margin-bottom:7px; }}
  .popup-actions {{ display:flex; gap:6px; margin-bottom:7px; }}
  .popup-act-btn {{ flex:1; font-size:11px; font-weight:600; padding:4px 6px; border-radius:6px; border:1.5px solid var(--border); background:var(--white); cursor:pointer; text-align:center; }}
  .popup-act-btn.act-interesa.active   {{ background:#DCFCE7; border-color:#86EFAC; color:#166534; }}
  .popup-act-btn.act-descartada.active {{ background:#FEE2E2; border-color:#FCA5A5; color:#991B1B; }}
  .leaflet-popup-content .popup-link {{ display:inline-block; font-size:11.7px; font-weight:600; color:white !important; background:var(--blue); padding:4px 12px; border-radius:6px; text-decoration:none; }}
  .leaflet-popup-content {{ min-width:190px; }}

  .mk-interesa    {{ background:var(--green) !important; }}
  .mk-descartada  {{ opacity:.35; filter:grayscale(.6); }}
  .prop-card.estado-interesa   {{ border-left:3px solid var(--green); }}
  .prop-card.estado-descartada {{ opacity:.5; }}

  .c-blue   {{ color:var(--blue); }}
  .c-green  {{ color:var(--green); }}
  .c-red    {{ color:var(--red); }}
  .c-purple {{ color:var(--purple); }}
  .c-teal   {{ color:var(--teal); }}
  .c-orange {{ color:var(--orange); }}
</style>
</head>
<body>

<nav>
  <div class="nav-logo">🏠 Rent Radar</div>
  <div class="nav-sep"></div>
  <div class="nav-run">
    <span class="nav-dot"></span>
    Última corrida: {ultima_corrida}
  </div>
  <div class="nav-spacer"></div>
  <span class="nav-credit">by Federico Noto</span>
  <div class="nav-sep"></div>
  <div class="nav-tg"><svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="currentColor" style="flex-shrink:0"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.753-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.724 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.727 4.476-1.735z"/></svg> Notificaciones vía Telegram</div>
</nav>

<div class="layout">
<aside><div class="sidebar-inner">

  <div class="section-label">Métricas</div>
  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-icon">🏘️</div>
      <div class="metric-label">Candidatas</div>
      <div class="metric-value c-blue">{total_candidatas}</div>
      
    </div>
    <div class="metric-card">
      <div class="metric-icon">📐</div>
      <div class="metric-label">Sup. promedio</div>
      <div class="metric-value c-purple">{sup_promedio}</div>
      <div class="metric-sub">m² cubiertos</div>
    </div>
    <div class="metric-card">
      <div class="metric-icon">🛏️</div>
      <div class="metric-label">Amb. promedio</div>
      <div class="metric-value c-teal">{ambientes_promedio}</div>
      <div class="metric-sub">ambientes</div>
    </div>
    <div class="metric-card">
      <div class="metric-icon">🚗</div>
      <div class="metric-label">Con cochera</div>
      <div class="metric-value c-orange">{con_cochera}</div>
      <div class="metric-sub">de {total_candidatas}</div>
    </div>
  </div>

  <div class="section-label">Eventos de hoy</div>
  <div class="eventos-strip">
    <div class="evento-chip">
      <span class="ev-icon">✨</span>
      <span class="ev-num c-green">{nuevas}</span>
      <span class="ev-label">Nuevas</span>
    </div>
    <div class="evento-chip">
      <span class="ev-icon">📉</span>
      <span class="ev-num c-blue">{bajas}</span>
      <span class="ev-label">Bajaron</span>
    </div>
    <div class="evento-chip">
      <span class="ev-icon">📈</span>
      <span class="ev-num c-red">{subas}</span>
      <span class="ev-label">Subieron</span>
    </div>
    <div class="evento-chip">
      <span class="ev-icon">❌</span>
      <span class="ev-num c-orange">{off_market}</span>
      <span class="ev-label">Off-market</span>
    </div>
  </div>
  <div class="section-label">Precios (mediana)</div>
  <div class="precios-strip">
    <div class="precio-row">
      <span class="precio-moneda moneda-ars">ARS</span>
      <div class="precio-range">
        <div class="precio-vals">
          <span class="precio-avg">{precio_avg_ars}</span>
          <span class="precio-minmax">{precio_min_ars} – {precio_max_ars}</span>
        </div>
        <div class="precio-track"><div class="precio-track-fill" style="width:{bar_ars}%"></div></div>
      </div>
    </div>
    <div class="precio-row">
      <span class="precio-moneda moneda-usd">USD</span>
      <div class="precio-range">
        <div class="precio-vals">
          <span class="precio-avg">{precio_avg_usd}</span>
          <span class="precio-minmax">{precio_min_usd} – {precio_max_usd}</span>
        </div>
        <div class="precio-track"><div class="precio-track-fill" style="width:{bar_usd}%"></div></div>
      </div>
    </div>
  </div>

  <div class="section-label">Por portal</div>
  <div class="fuentes-strip">
    <div class="fuente-row">
      <span class="fuente-name">ZonaProp</span>
      <div class="fuente-track"><div class="fuente-fill fill-zona" style="width:{bar_zonaprop}%"></div></div>
      <span class="fuente-count">{cnt_zonaprop}</span>
      {badge_zonaprop}
    </div>
    <div class="fuente-row">
      <span class="fuente-name">ArgenProp</span>
      <div class="fuente-track"><div class="fuente-fill fill-argen" style="width:{bar_argenprop}%"></div></div>
      <span class="fuente-count">{cnt_argenprop}</span>
      {badge_argenprop}
    </div>
    <div class="fuente-row">
      <span class="fuente-name">MercadoLibre</span>
      <div class="fuente-track"><div class="fuente-fill fill-ml" style="width:{bar_mercadolibre}%"></div></div>
      <span class="fuente-count">{cnt_mercadolibre}</span>
      {badge_mercadolibre}
    </div>
  </div>

  <div class="section-label">Filtrar</div>
  <div class="chips">
    <span class="chip chip-all active" onclick="filtrar(this,'all')">Todas</span>
    <span class="chip chip-zona active" onclick="filtrar(this,'zonaprop')">ZonaProp</span>
    <span class="chip chip-argen active" onclick="filtrar(this,'argenprop')">ArgenProp</span>
    <span class="chip chip-ml active" onclick="filtrar(this,'mercadolibre')">MercadoLibre</span>
  </div>

  <div class="section-label">Propiedades</div>
  <div class="prop-list" id="lista"></div>

</div>
</aside>

<div id="mapa"></div>
</div>


<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATOS = {datos_json};

const BADGE = {{ NEW:"Nueva ✨", PRICE_DOWN:"Bajó 📉", PRICE_UP:"Subió 📈" }};
const BADGE_CLS = {{ NEW:"badge-NEW", PRICE_DOWN:"badge-PRICE_DOWN", PRICE_UP:"badge-PRICE_UP" }};

function fmtPrecio(p, m) {{
  if (p == null) return "—";
  const n = new Intl.NumberFormat("es-AR").format(p);
  return m === "USD" ? `USD ${{n}}` : `$ ${{n}} ARS`;
}}
function fmtDet(p) {{
  const sup = p.superficie_cubierta ?? p.superficie_total;
  return [
    p.ambientes          ? `${{p.ambientes}} amb.`   : null,
    sup                  ? `${{sup}} m²`              : null,
    p.cocheras           ? `${{p.cocheras}} coch.`    : null,
    p.antiguedad === 0   ? "a estrenar"               : p.antiguedad ? `${{p.antiguedad}} años` : null,
  ].filter(Boolean).join(" · ");
}}
function sparkline(historial) {{
  if (!historial || historial.length < 2) return "";
  const w = 160, h = 26, pad = 3;
  const min = Math.min(...historial), max = Math.max(...historial);
  const span = max - min || 1;
  const pts = historial.map((v, i) => {{
    const x = pad + (i / (historial.length - 1)) * (w - pad * 2);
    const y = pad + (1 - (v - min) / span) * (h - pad * 2);
    return `${{x.toFixed(1)}},${{y.toFixed(1)}}`;
  }});
  const subio = historial[historial.length - 1] > historial[0];
  const bajo = historial[historial.length - 1] < historial[0];
  const color = subio ? "#DC2626" : bajo ? "#16A34A" : "#6B7280";
  return `<svg class="popup-spark" width="${{w}}" height="${{h}}" viewBox="0 0 ${{w}} ${{h}}">
    <polyline points="${{pts.join(" ")}}" fill="none" stroke="${{color}}" stroke-width="1.7"/>
  </svg>`;
}}

// ── Estados ("me interesa" / "descartada"), persistidos en el navegador ──
const ESTADOS_KEY = "rentradar_estados";
function loadEstados() {{
  try {{ return JSON.parse(localStorage.getItem(ESTADOS_KEY)) || {{}}; }} catch (e) {{ return {{}}; }}
}}
const estados = loadEstados();
const keyOf = p => `${{p.fuente}}:${{p.id_publicacion}}`;

function setEstado(i, valor) {{
  const p = DATOS[i];
  const k = keyOf(p);
  estados[k] = estados[k] === valor ? null : valor;
  if (!estados[k]) delete estados[k];
  localStorage.setItem(ESTADOS_KEY, JSON.stringify(estados));
  aplicarEstado(i);
  markers[i].getPopup().setContent(popupHtml(i));
}}

function aplicarEstado(i) {{
  const p = DATOS[i];
  const estado = estados[keyOf(p)];
  markers[i].setIcon(mkIcon(p.evento, estado));
  cards[i].classList.remove("estado-interesa", "estado-descartada");
  if (estado) cards[i].classList.add(`estado-${{estado}}`);
}}

// ── Mapa ──
const mapa = L.map("mapa");
L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 20,
}}).addTo(mapa);

L.marker([-34.6662631, -58.5766229], {{
  icon: L.divIcon({{ className:"", html:'<div class="mk-ref"></div>', iconSize:[24,24], iconAnchor:[12,12] }})
}}).bindPopup("<b>Referencia</b><br/>Dr. Ignacio Arieta 1638, Villa Luzuriaga").addTo(mapa);

const mkIcon = (ev, estado) => L.divIcon({{
  className:"",
  html: `<div class="mk mk-${{ev || 'default'}} ${{estado ? 'mk-' + estado : ''}}"></div>`,
  iconSize:[26,26], iconAnchor:[13,26],
}});

function popupHtml(i) {{
  const p = DATOS[i];
  const estado = estados[keyOf(p)];
  return `
    <div class="popup-titulo">${{p.titulo}}</div>
    <div class="popup-precio">${{fmtPrecio(p.precio, p.moneda)}}/mes</div>
    <div class="popup-det">${{fmtDet(p)}}</div>
    ${{sparkline(p.historial)}}
    ${{p.ubicacion ? `<div class="popup-loc">📍 ${{p.ubicacion}}</div>` : ""}}
    <div class="popup-actions">
      <span class="popup-act-btn act-interesa ${{estado === 'interesa' ? 'active' : ''}}" onclick="setEstado(${{i}},'interesa')">⭐ Me interesa</span>
      <span class="popup-act-btn act-descartada ${{estado === 'descartada' ? 'active' : ''}}" onclick="setEstado(${{i}},'descartada')">🚫 Descartar</span>
    </div>
    <a class="popup-link" href="${{p.url}}" target="_blank">Ver propiedad →</a>
  `;
}}

const markers = DATOS.map((p, i) => {{
  return L.marker([p.latitud, p.longitud], {{ icon: mkIcon(p.evento, estados[keyOf(p)]) }})
    .bindPopup(() => popupHtml(i)).addTo(mapa);
}});

if (DATOS.length) {{
  const lats = DATOS.map(p => p.latitud).sort((a,b) => a-b);
  const lons = DATOS.map(p => p.longitud).sort((a,b) => a-b);
  const i10 = Math.floor(lats.length*.1), i90 = Math.ceil(lats.length*.9)-1;
  mapa.fitBounds([[lats[i10],lons[i10]],[lats[i90],lons[i90]]], {{ padding:[40,40] }});
}} else {{
  mapa.setView([-34.675,-58.570], 13);
}}

// ── Lista ──
const lista = document.getElementById("lista");
const cards = [];

DATOS.forEach((p, i) => {{
  const card = document.createElement("div");
  card.className = "prop-card";
  card.dataset.fuente = p.fuente;
  card.innerHTML = `
    <div class="prop-top">
      <div class="prop-titulo">${{p.titulo}}</div>
      ${{p.evento && BADGE[p.evento] ? `<span class="prop-badge ${{BADGE_CLS[p.evento]}}">${{BADGE[p.evento]}}</span>` : ""}}
    </div>
    <div class="prop-precio">${{fmtPrecio(p.precio, p.moneda)}}<span style="font-weight:400;font-size:11px;color:var(--muted)">/mes</span></div>
    <div class="prop-detalle">${{fmtDet(p)}}</div>
    <div class="prop-footer">
      <span class="fuente-tag tag-${{p.fuente}}">${{p.fuente}}</span>
      ${{p.ubicacion ? `<span class="prop-ubicacion">📍 ${{p.ubicacion}}</span>` : ""}}
    </div>
  `;
  card.addEventListener("click", () => {{
    cards.forEach(c => c.classList.remove("selected"));
    card.classList.add("selected");
    mapa.setView([p.latitud, p.longitud], 16, {{ animate:true }});
    markers[i].openPopup();
  }});
  lista.appendChild(card);
  cards.push(card);
  aplicarEstado(i);
}});

// ── Filtros ──
const activeFuentes = new Set(["zonaprop","argenprop","mercadolibre"]);
function filtrar(chip, fuente) {{
  if (fuente === "all") {{
    const allOn = activeFuentes.size === 3;
    activeFuentes.clear();
    if (allOn) {{
      document.querySelectorAll(".chip:not(.chip-all)").forEach(c => c.classList.remove("active"));
    }} else {{
      ["zonaprop","argenprop","mercadolibre"].forEach(f => activeFuentes.add(f));
      document.querySelectorAll(".chip").forEach(c => c.classList.add("active"));
    }}
  }} else {{
    if (activeFuentes.has(fuente)) activeFuentes.delete(fuente);
    else activeFuentes.add(fuente);
    chip.classList.toggle("active");
    const allChip = document.querySelector(".chip-all");
    allChip.classList.toggle("active", activeFuentes.size === 3);
  }}
  cards.forEach((card, i) => {{
    const visible = activeFuentes.has(card.dataset.fuente);
    card.style.display = visible ? "" : "none";
    if (visible) markers[i].addTo(mapa); else markers[i].remove();
  }});
}}
</script>
<script>
(function() {{
  let _mtime = null;
  setInterval(async () => {{
    try {{
      const t = await (await fetch("/mtime")).text();
      if (_mtime === null) {{ _mtime = t; return; }}
      if (t !== _mtime) location.reload();
    }} catch(e) {{}}
  }}, 1500);
}})();
</script>
</body>
</html>
"""


def get_local_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    print("Consultando gold.candidatas…")
    rows = fetch_listings()
    print(f"  {len(rows)} propiedades encontradas")

    print("Consultando historial de precios…")
    attach_price_history(rows)

    print("Consultando gold.metricas…")
    metricas = fetch_metricas()
    if not metricas:
        print("  (sin datos de métricas — corré dbt primero)")

    ctx = build_ctx(rows, metricas)
    html = HTML_TEMPLATE.format(**ctx)

    out = "dashboard.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Generado: {out}")

    if args.serve:
        import http.server
        import socketserver

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/mtime":
                    try:
                        mtime = str(os.path.getmtime(out))
                    except Exception:
                        mtime = "0"
                    data = mtime.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    super().do_GET()

        ip = get_local_ip()
        print(f"\n  Servidor en http://{ip}:{args.port}/dashboard.html")
        print(f"  (también http://localhost:{args.port}/dashboard.html)")
        print("  Ctrl+C para detener\n")
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("0.0.0.0", args.port), Handler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n  Servidor detenido.")


if __name__ == "__main__":
    main()
