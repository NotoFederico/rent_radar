# Rent Radar — Sistema de Monitoreo Inmobiliario

Sistema automatizado que monitorea publicaciones de alquiler en múltiples portales argentinos, transforma y enriquece los datos con dbt, y genera un mapa interactivo de propiedades candidatas.

## 📑 Índice

- [🎯 Descripción](#-descripción)
- [🏗️ Arquitectura](#-arquitectura)
- [✨ Estado actual](#-estado-actual)
- [🛠️ Stack tecnológico](#-stack-tecnológico)
- [🚀 Instalación](#-instalación)
- [▶️ Uso](#-uso)

---

## 🎯 Descripción

Rent Radar scrapea publicaciones de alquiler de ZonaProp, ArgenProp y MercadoLibre, normaliza y guarda los datos crudos en Neon Postgres, los transforma con dbt en capas silver y gold, y expone un mapa interactivo con las propiedades que cumplen el presupuesto y los criterios de superficie.

## 🏗️ Arquitectura

```
Spiders ──► raw.snapshots ──► dbt silver ──► dbt gold ──► mapa
              (Neon)          publicaciones    objetivo    generar_mapa.py
                              rechazadas
```

**Portales scrapeados:** ZonaProp, ArgenProp, MercadoLibre

**Schemas en Neon Postgres:**
- `raw` — salida directa de los spiders: `pipeline_runs`, `snapshots`, `events`, `notifications`
- `silver` — datos limpios y enriquecidos: `publicaciones`, `publicaciones_rechazadas`
- `gold` — propiedades candidatas filtradas por presupuesto y superficie: `objetivo`

## ✨ Estado actual (v0.4)

### ✅ Implementado

**Spiders**
- `zonaprop.py` — curl_cffi con impersonación de Chrome
- `argenprop.py` — HTTP liviano con requests
- `mercadolibre.py` — Playwright + playwright-stealth con manejo de bot detection
- Los tres extraen: precio, moneda, expensas, ubicación, coordenadas, ambientes, especificaciones (array de texto por portal)

**Ingesta**
- `run_ingest.py` — corre los tres spiders en paralelo con `threading`
- `main.py` — CLI con flags `--ingest`, `--source`, `--start-url`, `--max-pages`
- Reconexión automática a Neon si la conexión SSL expira durante un scrape largo

**Capa silver (dbt)**
- `publicaciones.sql`: pipeline completo de limpieza y enriquecimiento:
  - Deduplicación intra-portal por `(fuente, id_publicacion)`, quedándose con el snapshot más reciente
  - Deduplicación cross-portal: detecta la misma propiedad publicada en más de un portal (match por bucket lat/lon ±0.001°, mismos ambientes, misma moneda, precio ±10%)
  - Filtro de publicaciones comerciales y venta/permuta antes del cruce cross-portal
  - Parseo de `especificaciones` por portal con regex → `superficie_cubierta`, `superficie_total`, `cocheras`, `antiguedad`
  - Fallback `COALESCE(raw, specs)` para `ambientes` (ML key-value)
  - Rechazo con motivo explícito: precios fuera de rango, coordenadas inválidas, campos clave nulos, placeholder 9.999.999
- `publicaciones_rechazadas.sql`: espejo de la misma lógica, retiene solo los rechazados para auditoría

**Capa gold (dbt)**
- `objetivo.sql`: filtra por superficie cubierta > 70 m², ambientes > 2 y presupuesto configurable (ARS o USD, con conversión automática)

**Pipeline dbt**
- `run_dbt.py`: obtiene el tipo de cambio USD oficial desde dolarapi.com y lo pasa como variable dbt; fallback a valor configurable
- Variables dbt: `tipo_cambio_usd` (auto-fetched) y `presupuesto_ars` (configurable)
- `generate_schema_name.sql` macro para materializar en `silver` y `gold` directamente (sin prefijo)

**Mapa interactivo**
- `generar_mapa.py`: consulta `gold.objetivo` y genera `mapa.html` con Leaflet + Stadia Maps
- Popup por propiedad: título, precio, ambientes, superficie, cocheras, antigüedad
- Marcador de referencia fijo (amarillo) configurable
- Flag `--serve`: levanta servidor HTTP local con live-reload automático al regenerar el HTML

### 📋 Pendiente

- Detección de eventos: `NEW`, `PRICE_UP`, `PRICE_DOWN`, `OFF_MARKET`
- Notificaciones Telegram
- Orquestación con Prefect Cloud

## 🛠️ Stack tecnológico

- **Scraping:** Python 3.13, Playwright, curl_cffi, requests, BeautifulSoup4
- **Base de datos:** Neon Postgres (serverless) — schemas `raw`, `silver`, `gold`
- **Transformaciones:** dbt-postgres (`analytics/`)
- **Mapa:** Leaflet.js + Stadia Maps tiles
- **Orquestación:** Prefect Cloud *(pendiente)*
- **Notificaciones:** Telegram Bot API *(pendiente)*

## 🚀 Instalación

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

Crear un proyecto en [neon.tech](https://neon.tech) y correr el schema inicial:

```bash
psql $NEON_DATABASE_URL -f sql/001_init_schemas.sql
```

### dbt

El proyecto dbt vive en `analytics/`. Requiere un perfil de conexión en `~/.dbt/profiles.yml`:

```yaml
analytics:
  target: dev
  outputs:
    dev:
      type: postgres
      url: "{{ env_var('NEON_DATABASE_URL') }}"
      schema: silver
```

### Variables de entorno

```env
NEON_DATABASE_URL=postgresql://...
TELEGRAM_BOT_TOKEN=...   # pendiente
TELEGRAM_CHAT_ID=...     # pendiente
```

## ▶️ Uso

### 1. Scrape (los tres portales en paralelo)

```bash
python run_ingest.py
```

O un portal individual:

```bash
python main.py --ingest --source argenprop --start-url "https://..." --max-pages 5
```

### 2. Transformar con dbt

```bash
python run_dbt.py
```

Obtiene el tipo de cambio USD oficial automáticamente y corre `dbt run` contra Neon. Para sobreescribir el presupuesto:

```bash
python run_dbt.py --vars '{"presupuesto_ars": 2000000}'
```

O directamente con dbt:

```bash
cd analytics
dbt run --vars '{"tipo_cambio_usd": 1480, "presupuesto_ars": 1500000}'
```

### 3. Ver el mapa

```bash
python generar_mapa.py --serve
```

Genera `mapa.html` y levanta un servidor local con live-reload. Abrir en el browser la URL que imprime en consola. Para regenerar el mapa con datos frescos, correr `python generar_mapa.py` en otra terminal — el browser se actualiza solo.
