from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from docuflow.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookEvent(StrEnum):
    DOCUMENT_COMPLETED = "document.completed"
    DOCUMENT_FAILED = "document.failed"
    DOCUMENT_REVIEWED = "document.reviewed"
    PING = "ping"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WebhookEndpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "webhook_endpoint"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth_user.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(String(200))
    secret: Mapped[str] = mapped_column(String(100))
    events: Mapped[list[str]] = mapped_column(ARRAY(String(50)))
    is_active: Mapped[bool] = mapped_column(default=True)


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "webhook_delivery"

    endpoint_id: Mapped[UUID] = mapped_column(
        ForeignKey("webhook_endpoint.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(
            DeliveryStatus, name="delivery_status", values_callable=lambda e: [m.value for m in e]
        ),
        default=DeliveryStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(default=0)
    response_status: Mapped[int | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None]
