from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    NEW = "NEW"
    PRICE_UP = "PRICE_UP"
    PRICE_DOWN = "PRICE_DOWN"
    OFF_MARKET = "OFF_MARKET"


class NotificationType(str, Enum):
    LISTING_UPDATE = "LISTING_UPDATE"
    APP_HEALTH = "APP_HEALTH"


class Listing(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str
    listing_id: str
    url: str
    title: str
    price: int | None = None
    currency: str | None = None
    expenses: int | None = None
    location: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    rooms: int | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    surface_m2: int | None = None
    published_at: datetime | None = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    seller: str | None = None
    specifications: list[str] = Field(default_factory=list)

    def to_snapshot_row(self, run_id: str) -> dict[str, Any]:
        return {
            "id_ejecucion": run_id,
            "fuente": self.source,
            "id_publicacion": self.listing_id,
            "url": self.url,
            "titulo": self.title,
            "precio": self.price,
            "moneda": self.currency,
            "expensas": self.expenses,
            "ubicacion": self.location,
            "latitud": self.latitude,
            "longitud": self.longitude,
            "ambientes": self.rooms,
            "dormitorios": self.bedrooms,
            "banos": self.bathrooms,
            "superficie_m2": self.surface_m2,
            "publicado_en": self.published_at,
            "fecha_scraping": self.scraped_at,
            "vendedor": self.seller,
            "especificaciones": self.specifications,
        }


class PipelineRun(BaseModel):
    source: str
    status: str = "running"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    listings_found: int = 0
    run_id: str = Field(default_factory=lambda: str(uuid4()))

    def to_run_row(self) -> dict[str, Any]:
        return {
            "id_ejecucion": self.run_id,
            "fuente": self.source,
            "estado": self.status,
            "iniciado_en": self.started_at,
            "finalizado_en": self.finished_at,
            "total_publicaciones": self.listings_found,
        }


class Event(BaseModel):
    run_id: str
    source: str
    listing_id: str
    event_type: EventType
    title: str
    url: str
    old_price: int | None = None
    new_price: int | None = None
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    notified: bool = False
    event_id: str = Field(default_factory=lambda: str(uuid4()))

    def to_event_row(self) -> dict[str, Any]:
        return {
            "id_evento": self.event_id,
            "id_ejecucion": self.run_id,
            "fuente": self.source,
            "id_publicacion": self.listing_id,
            "tipo_evento": self.event_type,
            "titulo": self.title,
            "url": self.url,
            "precio_anterior": self.old_price,
            "precio_nuevo": self.new_price,
            "detectado_en": self.detected_at,
            "fue_notificado": self.notified,
        }


class Notification(BaseModel):
    event_id: str | None = None
    channel: str
    notification_type: NotificationType = NotificationType.LISTING_UPDATE
    notified_ids: list[str] = Field(default_factory=list)
    message: str | None = None
    status: str = "sent"
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_notification_row(self) -> dict[str, Any]:
        return {
            "id_evento": self.event_id,
            "canal": self.channel,
            "tipo_notificacion": self.notification_type,
            "ids_notificados": self.notified_ids,
            "mensaje": self.message,
            "estado": self.status,
            "enviado_en": self.sent_at,
        }
