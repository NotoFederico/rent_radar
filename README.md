# Rent Radar — Sistema de Monitoreo Inmobiliario

Sistema automatizado que monitorea publicaciones de alquiler en múltiples portales argentinos, transforma y enriquece los datos con dbt, detecta eventos de cambio, envía notificaciones por Telegram y genera un mapa interactivo de propiedades candidatas.

## Índice

- [Descripción](#descripción)
- [Arquitectura](#arquitectura)
- [Estado actual](#estado-actual-v06)
- [Stack tecnológico](#stack-tecnológico)
- [Instalación](#instalación)
- [Uso](#uso)

---

## Descripción

Rent Radar scrapea publicaciones de alquiler de ZonaProp, ArgenProp y MercadoLibre, normaliza y guarda los datos crudos en Neon Postgres, los transforma con dbt en capas silver y gold, detecta cambios entre corridas (nuevas propiedades, bajadas de precio, off-market) y notifica por Telegram. El pipeline corre cada 45 minutos orquestado por Prefect self-hosted.

## Arquitectura

```
Spiders ──► raw.snapshots ──► dbt silver ──► dbt gold ──► mapa.html
              (Neon)          publicaciones    objetivo    (Leaflet/OSM)
                              rechazadas
                                  │
                            detect_events.py
                                  │
                            silver.events ──► notify.py ──► Telegram
```

**Portales scrapeados:** ZonaProp, ArgenProp, MercadoLibre

**Schemas en Neon Postgres:**
- `raw` — salida directa de los spiders: `pipeline_runs`, `snapshots`
- `silver` — datos limpios y enriquecidos: `publicaciones`, `publicaciones_rechazadas`, `events`, `notifications`
- `gold` — propiedades candidatas filtradas por presupuesto y superficie: `objetivo`

## Estado actual (v1.0)

### Implementado

**Spiders**
- `zonaprop.py` — curl_cffi con impersonación de Chrome
- `argenprop.py` — HTTP liviano con requests
- `mercadolibre.py` — Playwright + playwright-stealth con manejo de bot detection; timeout de inactividad de 120 s para evitar cuelgues; extracción de título desde JSON-LD como fallback
- Los tres extraen: precio, moneda, expensas, ubicación, coordenadas, ambientes, especificaciones

**Ingesta**
- `run_ingest.py` — corre los tres spiders en paralelo con `threading`; acepta `--source` para correr un solo portal
- `main.py` — CLI con flags `--ingest`, `--source`, `--start-url`, `--max-pages`
- Reconexión automática a Neon si la conexión SSL expira durante un scrape largo

**Capa silver (dbt)**
- `publicaciones.sql`: pipeline completo de limpieza y enriquecimiento
  - Deduplicación intra-portal por `(fuente, id_publicacion)`, quedándose con el snapshot más reciente
  - Deduplicación cross-portal: detecta la misma propiedad publicada en más de un portal (match por bucket lat/lon ±0.001°, mismos ambientes, misma moneda, precio ±10%)
  - Filtro de publicaciones comerciales y venta/permuta antes del cruce cross-portal
  - Parseo de `especificaciones` por portal con regex → `superficie_cubierta`, `superficie_total`, `cocheras`, `antiguedad`
  - Fallback `COALESCE(raw, specs)` para `ambientes`
  - Rechazo con motivo explícito: precios fuera de rango, coordenadas inválidas, campos clave nulos
- `publicaciones_rechazadas.sql`: espejo de la misma lógica, retiene solo los rechazados para auditoría

**Capa gold (dbt)**
- `objetivo.sql`: filtra por superficie cubierta > 70 m², ambientes > 2 y presupuesto configurable (ARS o USD con conversión automática)

**Pipeline dbt**
- `run_dbt.py`: obtiene el tipo de cambio USD oficial desde dolarapi.com y lo pasa como variable dbt; fallback a valor configurable
- Variables dbt: `tipo_cambio_usd` (auto-fetched) y `presupuesto_ars` (configurable)

**Detección de eventos**
- `detect_events.py`: compara los últimos runs exitosos por portal y emite eventos en `silver.events`
- Tipos: `NEW`, `PRICE_DOWN`, `PRICE_UP`, `EXPENSES_CHANGE`, `CURRENCY_CHANGE`, `OFF_MARKET`
- Filtra por `gold.objetivo`: solo genera eventos para propiedades dentro del presupuesto y criterios
- OFF_MARKET requiere 3 ausencias consecutivas (~2h15m) para evitar falsos positivos por reordenamientos del portal
- Idempotente: `ON CONFLICT DO NOTHING` evita duplicados

**Notificaciones Telegram**
- `notify.py`: lee `silver.events WHERE fue_notificado = FALSE`, envía mensajes formateados y registra en `silver.notifications`
- Si el envío falla, el evento no se marca → reintento automático en la próxima corrida
- `app/telegram.py`: `TelegramNotifier` con soporte multi-chat

**Mapa interactivo**
- `generar_mapa.py`: consulta `gold.objetivo` y genera `mapa.html` con Leaflet + OpenStreetMap
- Popup por propiedad: título, precio, ambientes, superficie, cocheras, antigüedad, ubicación
- Marcador de referencia fijo (amarillo) configurable
- Live-reload automático: el mapa se recarga solo cuando se regenera el HTML

**Orquestación**
- Prefect self-hosted: servidor + worker corriendo como servicios systemd
- Pipeline cada 45 minutos: 3 tasks de ingest en paralelo → dbt → detect_events → notify → mapa
- UI de Prefect accesible desde la LAN sin SSH tunnel

### Pendiente

- Frontend del mapa con filtros y sidebar
- Tabla de métricas en gold

## Stack tecnológico

- **Scraping:** Python 3.13, Playwright, curl_cffi, requests, BeautifulSoup4
- **Base de datos:** Neon Postgres (serverless) — schemas `raw`, `silver`, `gold`
- **Transformaciones:** dbt-postgres (`analytics/`)
- **Orquestación:** Prefect self-hosted (server + worker via systemd)
- **Mapa:** Leaflet.js + OpenStreetMap tiles
- **Notificaciones:** Telegram Bot API

## Instalación

### Requisitos

- Python 3.13
- Playwright browsers: `playwright install chromium`

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -e .
playwright install chromium
```

### Neon Postgres

Crear un proyecto en [neon.tech](https://neon.tech) y correr las migraciones en orden:

```bash
psql $NEON_DATABASE_URL -f sql/001_init_schemas.sql
psql $NEON_DATABASE_URL -f sql/002_silver_events.sql
```

### dbt

El proyecto dbt vive en `analytics/`. Requiere `~/.dbt/profiles.yml`:

```yaml
analytics:
  target: dev
  outputs:
    dev:
      type: postgres
      host: <host>.neon.tech
      user: neondb_owner
      password: "{{ env_var('DBT_PASSWORD') }}"
      port: 5432
      dbname: neondb
      schema: public
      threads: 4
      sslmode: require
```

### Variables de entorno

Crear `.env` en la raíz (sin `export`, sin comentarios inline — requerido por systemd):

```env
NEON_DATABASE_URL=postgresql://...
DBT_PASSWORD=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### Servicios systemd

Los archivos en `systemd/` son plantillas. Antes de copiarlos, reemplazar los placeholders:

- `YOUR_USER` → usuario Linux (ej. `noto`)
- `YOUR_PROJECT_DIR` → ruta absoluta del repo (ej. `/home/noto/github/rent_radar`)
- `YOUR_SERVER_IP` → IP del servidor en la LAN (solo en `prefect-server.service`)

```bash
# Editar cada archivo con los valores reales, luego:
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prefect-server prefect-worker rent-radar-map
```

Servicios:
- `prefect-server` — UI en `http://YOUR_SERVER_IP:4200`
- `prefect-worker` — ejecuta los runs del pipeline
- `rent-radar-map` — sirve `mapa.html` en `http://YOUR_SERVER_IP:8080`

### Registrar el pipeline en Prefect

Solo la primera vez:

```bash
PREFECT_API_URL=http://127.0.0.1:4200/api prefect work-pool create --type process local
PREFECT_API_URL=http://127.0.0.1:4200/api prefect deploy pipeline.py:pipeline \
  --name cada_45_min --pool local --interval 2700
```

## Uso

### Pipeline manual (paso a paso)

```bash
python run_ingest.py                        # scrape los tres portales en paralelo
python run_ingest.py --source zonaprop      # solo un portal
python run_dbt.py                           # transforma con dbt (tipo de cambio auto)
python detect_events.py                     # detecta cambios entre la última y anteúltima corrida
python notify.py                            # envía eventos pendientes por Telegram
python generar_mapa.py                      # regenera mapa.html
```

### Operación con Prefect

Ver logs en vivo:

```bash
sudo journalctl -u prefect-worker -f
```

Estado de servicios:

```bash
sudo systemctl status prefect-server prefect-worker rent-radar-map
```

Cancelar un run colgado:

```bash
PREFECT_API_URL=http://127.0.0.1:4200/api prefect flow-run cancel <id>
```
