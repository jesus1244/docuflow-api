"""File storage: local disk for development/tests, S3-compatible (AWS S3, MinIO, R2) for prod."""

from pathlib import Path
from typing import Any, Protocol

import aioboto3
import anyio

from docuflow.core.config import Settings, get_settings


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):  # path traversal guard
            raise ValueError("Invalid storage key")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        path = anyio.Path(self._path(key))
        await path.parent.mkdir(parents=True, exist_ok=True)
        await path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        return await anyio.Path(self._path(key)).read_bytes()

    async def delete(self, key: str) -> None:
        await anyio.Path(self._path(key)).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.session = aioboto3.Session()
        secret = settings.s3_secret_access_key
        self.client_kwargs: dict[str, Any] = {
            "endpoint_url": settings.s3_endpoint_url,
            "region_name": settings.s3_region,
            "aws_access_key_id": settings.s3_access_key_id,
            "aws_secret_access_key": secret.get_secret_value() if secret else None,
        }

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        async with self.session.client("s3", **self.client_kwargs) as s3:
            await s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        async with self.session.client("s3", **self.client_kwargs) as s3:
            obj = await s3.get_object(Bucket=self.bucket, Key=key)
            body: bytes = await obj["Body"].read()
            return body

    async def delete(self, key: str) -> None:
        async with self.session.client("s3", **self.client_kwargs) as s3:
            await s3.delete_object(Bucket=self.bucket, Key=key)

    async def ensure_bucket(self) -> None:
        async with self.session.client("s3", **self.client_kwargs) as s3:
            buckets = await s3.list_buckets()
            if not any(b.get("Name") == self.bucket for b in buckets.get("Buckets", [])):
                await s3.create_bucket(Bucket=self.bucket)


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage  # noqa: PLW0603
    if _storage is None:
        settings = get_settings()
        _storage = (
            S3Storage(settings)
            if settings.storage_backend == "s3"
            else LocalStorage(settings.storage_local_path)
        )
    return _storage


def set_storage(storage: Storage | None) -> None:
    global _storage  # noqa: PLW0603
    _storage = storage
