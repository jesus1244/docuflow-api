import re
from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from docuflow.core.schemas import CustomModel

_STRONG_PASSWORD = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,128}$")


class RegisterRequest(CustomModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if not _STRONG_PASSWORD.match(value):
            raise ValueError("Password needs at least one lowercase, one uppercase and one digit")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class LoginRequest(CustomModel):
    email: EmailStr
    password: str


class RefreshRequest(CustomModel):
    refresh_token: str


class TokenPair(CustomModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Access token lifetime in seconds")


class UserRead(CustomModel):
    id: UUID
    email: EmailStr
    full_name: str
    created_at: datetime


class ApiKeyCreate(CustomModel):
    name: str = Field(min_length=1, max_length=80)


class ApiKeyRead(CustomModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyCreated(ApiKeyRead):
    key: str = Field(description="Shown only once. Store it securely.")
