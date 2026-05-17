from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
	"""Tipos de evento del pipeline."""

	NEW = "NEW"
	PRICE_UP = "PRICE_UP"
	PRICE_DOWN = "PRICE_DOWN"
	OFF_MARKET = "OFF_MARKET"


class NotificationType(str, Enum):
	"""Tipos de notificacion soportados por la app."""

	LISTING_UPDATE = "LISTING_UPDATE"
	APP_HEALTH = "APP_HEALTH"


class MongoModel(BaseModel):
	"""Base comun para modelos persistidos en MongoDB."""

	model_config = ConfigDict(
		populate_by_name=True,
		extra="forbid",
		use_enum_values=True,
	)
	mongo_required: ClassVar[list[str]] = []
	mongo_properties: ClassVar[dict[str, dict[str, Any]]] = {}

	@classmethod
	def mongo_validator(cls) -> dict[str, Any]:
		"""Retorna el esquema jsonSchema de Mongo para la colección."""
		properties = {"_id": {}, **cls.mongo_properties}
		return {
			"$jsonSchema": {
				"bsonType": "object",
				"required": cls.mongo_required,
				"additionalProperties": False,
				"properties": properties,
			}
		}

	def to_mongo_doc(self) -> dict[str, Any]:
		"""Serializa usando aliases de MongoDB."""
		return self.model_dump(by_alias=True)


class Listing(MongoModel):
	"""Publicacion normalizada para usar en Python."""

	source: str = Field(serialization_alias="fuente")
	listing_id: str = Field(serialization_alias="id_publicacion")
	url: str
	title: str = Field(serialization_alias="titulo")
	price: float | None = Field(default=None, serialization_alias="precio")
	currency: str | None = Field(default=None, serialization_alias="moneda")
	expenses: float | None = Field(default=None, serialization_alias="expensas")
	location: str | None = Field(default=None, serialization_alias="ubicacion")
	latitude: float | None = Field(default=None, serialization_alias="latitud")
	longitude: float | None = Field(default=None, serialization_alias="longitud")
	rooms: float | None = Field(default=None, serialization_alias="ambientes")
	bedrooms: float | None = Field(default=None, serialization_alias="dormitorios")
	bathrooms: float | None = Field(default=None, serialization_alias="banos")
	surface_m2: float | None = Field(default=None, serialization_alias="superficie_m2")
	published_at: datetime | None = Field(default=None, serialization_alias="publicado_en")
	scraped_at: datetime = Field(default_factory=datetime.utcnow, serialization_alias="fecha_scraping")
	seller: str | None = Field(default=None, serialization_alias="vendedor")
	specifications: list[str] = Field(default_factory=list, serialization_alias="especificaciones")

	mongo_required: ClassVar[list[str]] = [
		"id_ejecucion",
		"fuente",
		"id_publicacion",
		"url",
		"titulo",
		"fecha_scraping",
	]
	mongo_properties: ClassVar[dict[str, dict[str, Any]]] = {
		"id_ejecucion": {"bsonType": "string"},
		"fuente": {"bsonType": "string"},
		"id_publicacion": {"bsonType": "string"},
		"url": {"bsonType": "string"},
		"titulo": {"bsonType": "string"},
		"precio": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"moneda": {"bsonType": ["string", "null"]},
		"expensas": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"ubicacion": {"bsonType": ["string", "null"]},
		"latitud": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"longitud": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"ambientes": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"dormitorios": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"banos": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"superficie_m2": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"publicado_en": {"bsonType": ["date", "null"]},
		"fecha_scraping": {"bsonType": "date"},
		"vendedor": {"bsonType": ["string", "null"]},
		"especificaciones": {"bsonType": "array", "items": {"bsonType": "string"}},
	}

	def to_snapshot_row(self, run_id: str) -> dict[str, Any]:
		"""Mapea el modelo."""
		row = self.to_mongo_doc()
		row["id_ejecucion"] = run_id
		return row


class PipelineRun(MongoModel):
	"""Metadatos."""

	source: str = Field(serialization_alias="fuente")
	status: str = Field(default="running", serialization_alias="estado")
	started_at: datetime = Field(default_factory=datetime.utcnow, serialization_alias="iniciado_en")
	finished_at: datetime | None = Field(default=None, serialization_alias="finalizado_en")
	listings_found: int = Field(default=0, serialization_alias="total_publicaciones")
	run_id: str = Field(default_factory=lambda: str(uuid4()), serialization_alias="id_ejecucion")

	mongo_required: ClassVar[list[str]] = [
		"id_ejecucion",
		"fuente",
		"iniciado_en",
		"estado",
		"total_publicaciones",
	]
	mongo_properties: ClassVar[dict[str, dict[str, Any]]] = {
		"id_ejecucion": {"bsonType": "string"},
		"fuente": {"bsonType": "string"},
		"iniciado_en": {"bsonType": "date"},
		"finalizado_en": {"bsonType": ["date", "null"]},
		"estado": {"bsonType": "string"},
		"total_publicaciones": {"bsonType": "int"},
	}

	def to_run_row(self) -> dict[str, Any]:
		"""Mapea la corrida a columnas en español."""
		return self.to_mongo_doc()


class Event(MongoModel):
	"""Cambio detectado entre snapshots para usar en Python."""

	run_id: str = Field(serialization_alias="id_ejecucion")
	source: str = Field(serialization_alias="fuente")
	listing_id: str = Field(serialization_alias="id_publicacion")
	event_type: EventType = Field(serialization_alias="tipo_evento")
	title: str = Field(serialization_alias="titulo")
	url: str
	old_price: float | None = Field(default=None, serialization_alias="precio_anterior")
	new_price: float | None = Field(default=None, serialization_alias="precio_nuevo")
	detected_at: datetime = Field(default_factory=datetime.utcnow, serialization_alias="detectado_en")
	notified: bool = Field(default=False, serialization_alias="fue_notificado")
	event_id: str = Field(default_factory=lambda: str(uuid4()), serialization_alias="id_evento")

	mongo_required: ClassVar[list[str]] = [
		"id_evento",
		"id_ejecucion",
		"fuente",
		"id_publicacion",
		"tipo_evento",
		"detectado_en",
		"url",
		"titulo",
		"fue_notificado",
	]
	mongo_properties: ClassVar[dict[str, dict[str, Any]]] = {
		"id_evento": {"bsonType": "string"},
		"id_ejecucion": {"bsonType": "string"},
		"fuente": {"bsonType": "string"},
		"id_publicacion": {"bsonType": "string"},
		"tipo_evento": {"enum": [e.value for e in EventType]},
		"precio_anterior": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"precio_nuevo": {"bsonType": ["double", "int", "long", "decimal", "null"]},
		"detectado_en": {"bsonType": "date"},
		"url": {"bsonType": "string"},
		"titulo": {"bsonType": "string"},
		"fue_notificado": {"bsonType": "bool"},
	}

	def to_event_row(self) -> dict[str, Any]:
		"""Mapea el evento a columnas en español."""
		return self.to_mongo_doc()


class Notification(MongoModel):
	"""Registro de envio por canal para usar en Python."""

	event_id: str | None = Field(default=None, serialization_alias="id_evento")
	channel: str = Field(serialization_alias="canal")
	notification_type: NotificationType = Field(
		default=NotificationType.LISTING_UPDATE,
		serialization_alias="tipo_notificacion",
	)
	notified_ids: list[str] = Field(default_factory=list, serialization_alias="ids_notificados")
	message: str | None = Field(default=None, serialization_alias="mensaje")
	status: str = Field(default="sent", serialization_alias="estado")
	sent_at: datetime = Field(default_factory=datetime.utcnow, serialization_alias="enviado_en")

	mongo_required: ClassVar[list[str]] = [
		"canal",
		"tipo_notificacion",
		"ids_notificados",
		"enviado_en",
		"estado",
	]
	mongo_properties: ClassVar[dict[str, dict[str, Any]]] = {
		"id_evento": {"bsonType": ["string", "null"]},
		"canal": {"bsonType": "string"},
		"tipo_notificacion": {"enum": [e.value for e in NotificationType]},
		"ids_notificados": {
			"bsonType": "array",
			"items": {"bsonType": "string"},
		},
		"mensaje": {"bsonType": ["string", "null"]},
		"enviado_en": {"bsonType": "date"},
		"estado": {"bsonType": "string"},
	}

	def to_notification_row(self) -> dict[str, Any]:
		"""Mapea la notificacion a columnas en español."""
		return self.to_mongo_doc()
