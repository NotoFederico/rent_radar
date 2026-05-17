from __future__ import annotations

from typing import Any, Iterable
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from app.config import MONGO_URI, DB_NAME, RUNS_COLLECTION, SNAPSHOTS_COLLECTION, EVENTS_COLLECTION, NOTIFICATIONS_COLLECTION


class ScraperDB:
	"""Acceso a MongoDB del proyecto."""

	def __init__(self):
		"""Conecta a MongoDB Cloud."""
		try:
			self.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
			# Verifica la conexión
			self.client.admin.command("ping")
			self.db = self.client[DB_NAME]
		except (ConnectionFailure, ServerSelectionTimeoutError) as e:
			raise ConnectionError(f"No se pudo conectar a MongoDB: {e}")

	def close(self) -> None:
		"""Cierra la conexion activa."""
		if self.client:
			self.client.close()

	def initialize(self) -> None:
		"""Crea las colecciones e índices base del proyecto. Actualiza los validators si ya existen."""
		from app.models import Listing, PipelineRun, Event, Notification

		existing = set(self.db.list_collection_names())

		for name, model_cls in (
			(RUNS_COLLECTION, PipelineRun),
			(SNAPSHOTS_COLLECTION, Listing),
			(EVENTS_COLLECTION, Event),
			(NOTIFICATIONS_COLLECTION, Notification),
		):
			validator = model_cls.mongo_validator()
			if name not in existing:
				self.db.create_collection(name, validator=validator)
			else:
				self.db.command("collMod", name, validator=validator)
		
		# Crear índices para optimización
		self.db[RUNS_COLLECTION].create_index("id_ejecucion", unique=True)
		self.db[RUNS_COLLECTION].create_index("fuente")
		self.db[RUNS_COLLECTION].create_index("iniciado_en")
		
		self.db[SNAPSHOTS_COLLECTION].create_index("id_ejecucion")
		self.db[SNAPSHOTS_COLLECTION].create_index("id_publicacion")
		
		self.db[EVENTS_COLLECTION].create_index("id_evento", unique=True)
		self.db[EVENTS_COLLECTION].create_index("id_ejecucion")
		self.db[EVENTS_COLLECTION].create_index("fuente")
		self.db[EVENTS_COLLECTION].create_index("fue_notificado")
		
		self.db[NOTIFICATIONS_COLLECTION].create_index("id_evento")

	
	def insert_run(self, run_data: dict[str, Any]) -> None:
		"""Guarda una corrida."""
		self.db[RUNS_COLLECTION].insert_one(run_data)

	def insert_snapshots(self, rows: Iterable[dict[str, Any]]) -> None:
		"""Guarda publicaciones de una corrida."""
		records = list(rows)
		if not records:
			return
		self.db[SNAPSHOTS_COLLECTION].insert_many(records)

	def insert_events(self, rows: Iterable[dict[str, Any]]) -> None:
		"""Guarda eventos detectados."""
		records = list(rows)
		if not records:
			return
		self.db[EVENTS_COLLECTION].insert_many(records)

	def insert_notifications(self, rows: Iterable[dict[str, Any]]) -> None:
		"""Guarda el log de notificaciones."""
		records = list(rows)
		if not records:
			return
		self.db[NOTIFICATIONS_COLLECTION].insert_many(records)

	def get_latest_run(self, source: str) -> dict[str, Any] | None:
		"""Trae la ultima corrida de una fuente."""
		return self.db[RUNS_COLLECTION].find_one(
			{"fuente": source},
			sort=[("iniciado_en", -1)]
		)

	def get_run_snapshots(self, run_id: str) -> list[dict[str, Any]]:
		"""Trae las publicaciones de una corrida."""
		return list(self.db[SNAPSHOTS_COLLECTION].find(
			{"id_ejecucion": run_id}
		))

	def mark_event_as_notified(self, event_id: str) -> None:
		"""Marca un evento como ya notificado."""
		self.db[EVENTS_COLLECTION].update_one(
			{"id_evento": event_id},
			{"$set": {"fue_notificado": True}}
		)

	def commit(self) -> None:
		"""Método para compatibilidad. MongoDB confirma automáticamente."""
		pass
