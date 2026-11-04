from datetime import datetime
from uuid import UUID

from pydantic import AnyHttpUrl, Field, field_validator

from docuflow.core.schemas import CustomModel
from docuflow.webhooks.models import DeliveryStatus, WebhookEvent

SUBSCRIBABLE_EVENTS: list[WebhookEvent] = [e for e in WebhookEvent if e != WebhookEvent.PING]


class WebhookCreate(CustomModel):
    url: AnyHttpUrl
    description: str | None = Field(default=None, max_length=200)
    events: list[WebhookEvent] = Field(
        default_factory=lambda: list(SUBSCRIBABLE_EVENTS), min_length=1
    )

    @field_validator("events")
    @classmethod
    def no_ping(cls, events: list[WebhookEvent]) -> list[WebhookEvent]:
        return sorted(set(events) - {WebhookEvent.PING})


class WebhookUpdate(CustomModel):
    description: str | None = Field(default=None, max_length=200)
    events: list[WebhookEvent] | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class WebhookRead(CustomModel):
    id: UUID
    url: str
    description: str | None
    events: list[str]
    is_active: bool
    created_at: datetime


class WebhookCreated(WebhookRead):
    secret: str = Field(description="Use it to verify the X-DocuFlow-Signature header")


class DeliveryRead(CustomModel):
    id: UUID
    event: str
    status: DeliveryStatus
    attempts: int
    response_status: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
