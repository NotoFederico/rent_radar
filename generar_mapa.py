"""Genera mapa.html con todas las propiedades de gold.objetivo."""
from __future__ import annotations

import json
import os
import sys

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
            select titulo, url, precio, moneda, fuente,
                   ambientes, superficie_cubierta, superficie_total,
                   cocheras, antiguedad, ubicacion, latitud, longitud
            from gold.objetivo
            where latitud is not null and longitud is not null
        """)
        rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows



HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<title>Rent Radar — Mapa</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; }}
  #mapa {{ height: 100vh; width: 100%; }}
  .leyenda {{
    background: white;
    padding: 10px 14px;
    border-radius: 6px;
    box-shadow: 0 1px 5px rgba(0,0,0,.3);
    line-height: 1.8;
    font-size: 13px;
  }}
  .leyenda-dot {{
    display: inline-block;
    width: 12px; height: 12px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
  }}
  .popup-link {{ color: #1565C0; text-decoration: none; font-weight: 600; }}
  .popup-link:hover {{ text-decoration: underline; }}
  .popup-precio {{ font-size: 15px; font-weight: 700; margin: 4px 0 6px; }}
  .popup-detalle {{ font-size: 12px; color: #555; }}
</style>
</head>
<body>
<div id="mapa"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const datos = {datos_json};

const mapa = L.map("mapa");
L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 20,
}}).addTo(mapa);

function circulo() {{
  return L.divIcon({{
    className: "",
    html: `<div style="
      width:20px; height:20px;
      background:#EA4335;
      border-radius:50% 50% 50% 0;
      transform:rotate(-45deg);
      border:2px solid white;
      box-shadow:0 2px 6px rgba(0,0,0,.5)"></div>`,
    iconSize: [20, 20],
    iconAnchor: [10, 20],
  }});
}}

function formatPrecio(precio, moneda) {{
  if (precio == null) return "—";
  const fmt = new Intl.NumberFormat("es-AR");
  return (moneda === "USD" ? "USD " : "$ ") + fmt.format(precio);
}}

// Marcador de referencia fijo
const iconRef = L.divIcon({{
  className: "",
  html: `<div style="
    background:#FFD600;
    width:22px; height:22px; border-radius:50%;
    border:3px solid white;
    outline:2px solid #FFD600;
    box-shadow:0 2px 8px rgba(0,0,0,.55)"></div>`,
  iconSize: [22, 22],
  iconAnchor: [11, 11],
}});
L.marker([-34.6662631, -58.5766229], {{ icon: iconRef }})
  .bindPopup("<b>Referencia</b><br/>Dr. Ignacio Arieta 1638, Villa Luzuriaga")
  .addTo(mapa);

datos.forEach(p => {{
  const m = L.marker([p.latitud, p.longitud], {{ icon: circulo() }});
  const precio = formatPrecio(p.precio, p.moneda);
  const sup = p.superficie_cubierta ?? p.superficie_total;
  const detalle = [
    p.ambientes          ? `${{p.ambientes}} amb.`      : null,
    sup                  ? `${{sup}} m² cub.`            : null,
    p.cocheras           ? `${{p.cocheras}} coch.`       : null,
    p.antiguedad != null ? `${{p.antiguedad}} años`      : null,
  ].filter(Boolean).join(" · ");
  m.bindPopup(`
    <b><a class="popup-link" href="${{p.url}}" target="_blank">${{p.titulo}}</a></b>
    <div class="popup-precio">${{precio}}</div>
    <div class="popup-detalle">${{detalle}}</div>
    ${{p.ubicacion ? `<div class="popup-detalle">${{p.ubicacion}}</div>` : ""}}
  `);
  m.addTo(mapa);
}});

if (datos.length) {{
  const lats = datos.map(p => p.latitud).sort((a, b) => a - b);
  const lons = datos.map(p => p.longitud).sort((a, b) => a - b);
  const i10 = Math.floor(lats.length * 0.10);
  const i90 = Math.ceil(lats.length * 0.90) - 1;
  mapa.fitBounds(
    [[lats[i10], lons[i10]], [lats[i90], lons[i90]]],
    {{ padding: [40, 40] }}
  );
}} else {{
  mapa.setView([-34.675, -58.570], 13);
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
    parser.add_argument("--serve", action="store_true", help="Levanta servidor HTTP local después de generar")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    print("Consultando gold.objetivo…")
    rows = fetch_listings()
    print(f"  {len(rows)} propiedades encontradas")

    html = HTML_TEMPLATE.format(
        datos_json=json.dumps(rows, default=str, ensure_ascii=False),
    )

    out = "mapa.html"
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
        print(f"\n  Servidor en http://{ip}:{args.port}/mapa.html")
        print(f"  (también http://localhost:{args.port}/mapa.html)")
        print("  Ctrl+C para detener\n")
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("0.0.0.0", args.port), Handler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n  Servidor detenido.")


if __name__ == "__main__":
    main()
