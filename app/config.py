from __future__ import annotations

import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
COLLECTION_NAME = os.getenv("COLLECTION_NAME")

# Database collections
RUNS_COLLECTION = "scrapings"
SNAPSHOTS_COLLECTION = "snapshots_publicaciones"
EVENTS_COLLECTION = "eventos"
NOTIFICATIONS_COLLECTION = "notificaciones"
