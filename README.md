# Rent Radar - Sistema de Monitoreo Inmobiliario

Sistema automatizado que monitorea publicaciones de alquiler en múltiples portales argentinos, transforma y almacena los datos, y envía alertas instantáneas por Telegram.

## 📑 Índice

- [🎯 Descripción](#-descripción)
- [🏗️ Arquitectura](#-arquitectura)
- [✨ Estado actual](#-estado-actual)
- [🛠️ Stack tecnológico](#-stack-tecnológico)
- [🚀 Instalación](#-instalación)
- [▶️ Uso](#-uso)
- [📋 Caso de uso](#-caso-de-uso)

---

## 🎯 Descripción

Rent Radar scrapea publicaciones de alquiler de ZonaProp, ArgenProp y MercadoLibre, normaliza y guarda los datos crudos en Neon Postgres, los transforma con dbt, y envía alertas cuando aparece una propiedad que cumple tus criterios. El pipeline corre en un servidor local y será orquestado desde Prefect Cloud.

## 🏗️ Arquitectura

**Plano de control — Prefect Cloud** *(pendiente)*  
Manejará el scheduling, la UI y los logs sin que ningún dato pase por ahí.

**Plano de datos — servidor local**
- **Spiders** (Playwright + curl_cffi + requests): scrapean los tres portales y normalizan las publicaciones antes de persistirlas.
- **dbt**: transforma los datos crudos en modelos listos para análisis. *(en construcción)*
- **Prefect worker**: consultará Prefect Cloud por runs programados. *(pendiente)*

**Almacenamiento — Neon Postgres (serverless cloud)**
Tres schemas: `raw` (salida de los spiders), `silver` (limpieza y enriquecimiento con dbt) y `gold` (agregaciones analíticas).

## ✨ Estado actual (v0.2.0)

### ✅ Implementado

**Spiders**
- `mercadolibre.py` — Playwright + playwright-stealth, maneja bot detection, paginación por click
- `argenprop.py` — HTTP liviano con requests
- `zonaprop.py` — curl_cffi con impersonación de Chrome para bypassear protecciones
- Logging estructurado en los tres spiders: progreso por página, conteo de publicaciones, errores de HTTP
- Delay anti-bot configurable por spider

**Ingesta**
- `main.py` — CLI con flags `--ingest`, `--source`, `--start-url`, `--max-pages`
- `run_ingest.py` — corre los tres spiders en paralelo con `threading`, `max_pages` configurado por fuente (zonaprop=10, argenprop=5, mercadolibre=3)
- Reconexión automática a Neon si la conexión SSL expira durante un scrape largo

**Base de datos**
- Schema `raw`: tablas `pipeline_runs`, `snapshots`, `events`, `notifications`
- `raw.snapshots` almacena especificaciones completas de cada portal como array de texto

### 🚧 En construcción

- **Capa silver (dbt)**: modelo `analytics.listados` con extracción de especificaciones, normalización cross-portal, limpieza de precios placeholder, superficie cubierta vs total
- **Detección de eventos**: comparación entre snapshots para detectar `NEW`, `PRICE_UP`, `PRICE_DOWN`, `OFF_MARKET`
- **Notificaciones Telegram**

### 📋 Pendiente

- Orquestación con Prefect Cloud
- Scheduling automático

## 🛠️ Stack tecnológico

- **Scraping:** Python 3.13, Playwright, curl_cffi, requests, BeautifulSoup4
- **Base de datos:** Neon Postgres (serverless) — schemas `raw`, `silver`, `gold`
- **Transformaciones:** dbt-postgres
- **Orquestación:** Prefect Cloud *(pendiente)*
- **Notificaciones:** Telegram Bot API *(pendiente)*

## 🚀 Instalación

### Requisitos previos

- Python 3.13 (instalado vía [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa))
- Playwright browsers: `playwright install chromium`

```bash
# Python 3.13 en Ubuntu/Debian
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update
sudo apt-get install -y python3.13 python3.13-venv python3.13-dev
```

### Entorno Python

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -e .
playwright install chromium
```

Las dependencias se leen del `pyproject.toml`.

### Neon Postgres

Crear un proyecto en [neon.tech](https://neon.tech) y correr el schema inicial:

```bash
psql $NEON_DATABASE_URL -f sql/001_init_schemas.sql
```

### dbt

dbt transforma los datos de `raw` y los materializa como tablas en Neon directamente (schemas `silver` y `gold`). Para configurarlo:

```bash
# Inicializar el proyecto dbt dentro del repo
source venv/bin/activate
dbt init dbt --skip-profile-setup
```

Esto crea la carpeta `dbt/` con la estructura del proyecto. Luego configurar el perfil de conexión en `~/.dbt/profiles.yml`:

```yaml
rent_radar:
  target: dev
  outputs:
    dev:
      type: postgres
      url: "{{ env_var('NEON_DATABASE_URL') }}"
      schema: silver
```

Para correr los modelos:

```bash
cd dbt && dbt run
```

dbt leerá los `.sql` de `dbt/models/` y creará las tablas correspondientes en Neon. Los schemas `silver` y `gold` se crean automáticamente si no existen.

### Variables de entorno

Crear un archivo `.env` en la raíz del proyecto (no se versiona):

```env
NEON_DATABASE_URL=postgresql://...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## ▶️ Uso

### Scrape individual

```bash
source venv/bin/activate

python main.py --ingest \
  --source mercadolibre \
  --start-url "https://inmuebles.mercadolibre.com.ar/..." \
  --max-pages 3
```

Fuentes disponibles: `mercadolibre`, `argenprop`, `zonaprop`.

### Scrape completo (tres portales en paralelo)

Configurar las URLs y `max_pages` por fuente en `run_ingest.py`, luego:

```bash
python run_ingest.py
```

## 📋 Caso de uso

Ideal para quienes buscan alquiler en el Gran Buenos Aires y quieren ser los primeros en enterarse de nuevas publicaciones que cumplan sus requisitos exactos, sin revisar manualmente varios portales varias veces al día.
