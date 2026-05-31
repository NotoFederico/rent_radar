# Rent Radar - Sistema de Monitoreo Inmobiliario

Sistema automatizado que monitorea publicaciones de alquiler en múltiples portales argentinos, transforma y almacena los datos, y envía alertas instantáneas por Telegram.

## 📑 Índice

- [🎯 Descripción](#-descripción)
- [🏗️ Arquitectura](#-arquitectura)
- [✨ Funcionalidades](#-funcionalidades)
- [🛠️ Stack tecnológico](#-stack-tecnológico)
- [🚀 Instalación](#-instalación)
- [📋 Caso de uso](#-caso-de-uso)

---

## 🎯 Descripción

Rent Radar scrapea publicaciones de alquiler de ZonaProp, ArgenProp y MercadoLibre, normaliza y guarda los datos crudos en Neon Postgres, los transforma con dbt, y te avisa al instante cuando aparece una propiedad que cumple tus criterios. El pipeline corre en un servidor local orquestado desde Prefect Cloud.

## 🏗️ Arquitectura

El sistema sigue un modelo de dos planos:

**Plano de control — Prefect Cloud**
Maneja el scheduling, la UI, los logs y las alertas sin que ningún dato pase por ahí. El tier gratuito es suficiente para esta carga.

**Plano de datos — servidor local (PC de escritorio 24/7)**
- **Spiders** (Scrapy + Playwright): scrapean los cuatro sitios y normalizan las publicaciones en Python antes de persistirlas.
- **dbt**: transforma los datos crudos en modelos listos para análisis, corre tests y genera documentación.
- **Prefect worker**: consulta Prefect Cloud por runs programados y los ejecuta localmente.

**Almacenamiento — Neon Postgres (serverless cloud)**
Dos schemas: `raw` (salida de los spiders) y `analytics` (transformado por dbt). Escala a cero entre ejecuciones.

## ✨ Funcionalidades

- 🔍 **Scraping multi-sitio** - Cubre ZonaProp, ArgenProp y MercadoLibre con filtros personalizados (ubicación, precio, superficie, cochera)
- 🎭 **Scrapy + Playwright** - Maneja páginas estáticas y renderizadas con JavaScript
- 💾 **Almacenamiento en dos capas** - Schemas raw y analytics en Neon Postgres para trazabilidad completa
- 🔄 **Transformaciones con dbt** - Modelos tipados, tests y documentación autogenerada sobre los datos crudos
- 🤖 **Orquestación con Prefect** - Prefect Cloud como plano de control; el worker corre localmente y los datos nunca salen del servidor
- 📱 **Notificaciones por Telegram** - Alertas instantáneas ante nuevas publicaciones o cambios de precio
- 💱 **Conversión de moneda** - Conversión automática ARS/USD con validación

## 🛠️ Stack tecnológico

- **Scraping:** Python 3.13, Scrapy, Playwright
- **Base de datos:** Neon Postgres (serverless) — schemas `raw` + `analytics`
- **Transformaciones:** dbt
- **Orquestación:** Prefect 2 (Prefect Cloud como control plane + worker local)
- **Notificaciones:** Telegram Bot API

## 🚀 Instalación

### Requisitos previos

- Python 3.13 (instalado vía [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa))
- Node.js >= 18 (requerido por el CLI de Neon)

```bash
# Python 3.13 en Ubuntu/Debian
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt-get update
sudo apt-get install -y python3.13 python3.13-venv python3.13-dev

# Node.js 22 en Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
```

### Entorno Python

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -e .
```

Las dependencias se leen del `pyproject.toml`. No se usa `requirements.txt`.

### Neon Postgres

Crear y configurar el proyecto de base de datos desde el CLI oficial:

```bash
npx neonctl@latest init
```

Esto genera la connection string que luego va en el archivo `.env`.

### Variables de entorno

Crear un archivo `.env` en la raíz del proyecto (no se versiona):

```env
NEON_DATABASE_URL=postgresql://...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## 📋 Caso de uso

Ideal para quienes buscan departamento en el Gran Buenos Aires y quieren ser los primeros en enterarse de nuevas publicaciones que cumplan sus requisitos exactos, sin tener que revisar manualmente varios portales inmobiliarios varias veces al día.
