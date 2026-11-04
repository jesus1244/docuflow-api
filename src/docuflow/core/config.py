from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_deployed(self) -> bool:
        return self in {Environment.STAGING, Environment.PRODUCTION}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app
    app_name: str = "DocuFlow API"
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:4200"]
    api_prefix: str = "/api/v1"

    # --- infrastructure
    database_url: str = "postgresql+asyncpg://docuflow:docuflow@localhost:5432/docuflow"
    database_pool_size: int = 10
    redis_url: str = "redis://localhost:6379/0"

    # --- auth
    jwt_secret: SecretStr = SecretStr("change-me-in-production-please-32-bytes-min")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14

    # --- rate limiting (requests per window, per user/IP)
    rate_limit_auth_per_minute: int = 10
    rate_limit_upload_per_minute: int = 20

    # --- storage
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./var/uploads"
    s3_bucket: str = "docuflow"
    s3_endpoint_url: str | None = None  # set for MinIO / R2
    s3_region: str = "us-east-1"
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    max_upload_mb: int = 20

    # --- AI extraction
    extractor: Literal["claude", "gemini", "mock"] = "mock"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    gemini_api_key: SecretStr | None = None  # Google AI Studio key
    gemini_model: str = "gemini-3.8-flash"
    gemini_use_vertexai: bool = False  # True: Vertex AI with Application Default Credentials
    google_cloud_project: str | None = None
    google_cloud_location: str = "global"

    # --- webhooks
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 5
    webhook_allow_private_hosts: bool = False  # True only for local development

    # --- Google Sheets (optional)
    google_service_account_file: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def docs_enabled(self) -> bool:
        return not self.environment.is_deployed or self.expose_docs

    expose_docs: bool = Field(default=True, description="Expose OpenAPI docs in deployed envs")


@lru_cache
def get_settings() -> Settings:
    return Settings()
