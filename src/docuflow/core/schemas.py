from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CustomModel(BaseModel):
    """Base schema for the API: ORM-friendly and strict about unknown fields."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Any | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class Timestamped(CustomModel):
    created_at: datetime
