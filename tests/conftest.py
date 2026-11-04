"""Test setup.

- Postgres is real (testcontainers, or TEST_DATABASE_URL in CI) and the schema is built by
  running the Alembic migrations, so migrations are tested too.
- Redis is replaced by fakeredis; the job queue runs tasks inline so flows are end-to-end.
- docuflow is imported lazily: settings and the engine must see the env vars set here first.
"""

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_container: Any = None


def pytest_configure(config: pytest.Config) -> None:
    global _container  # noqa: PLW0603
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        from testcontainers.postgres import PostgresContainer

        _container = PostgresContainer("postgres:17-alpine", driver="asyncpg").start()
        url = _container.get_connection_url()
    os.environ.update(
        {
            "DATABASE_URL": url,
            "ENVIRONMENT": "test",
            "EXTRACTOR": "mock",
            "STORAGE_BACKEND": "local",
            "STORAGE_LOCAL_PATH": tempfile.mkdtemp(prefix="docuflow-test-"),
            "RATE_LIMIT_AUTH_PER_MINUTE": "1000",
            "RATE_LIMIT_UPLOAD_PER_MINUTE": "1000",
            "WEBHOOK_ALLOW_PRIVATE_HOSTS": "false",
            "WEBHOOK_MAX_ATTEMPTS": "3",
            "JWT_SECRET": "test-secret-that-is-long-enough-for-hs256-signing",
        }
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    if _container is not None:
        _container.stop()


class InlineQueue:
    """Runs jobs immediately, in-process. Records every call for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.retries: list[str] = []

    async def enqueue(self, function: str, *args: Any, **kwargs: Any) -> None:
        from arq import Retry

        from docuflow.documents.tasks import process_document
        from docuflow.webhooks.tasks import deliver_webhook

        self.calls.append((function, args))
        functions = {"process_document": process_document, "deliver_webhook": deliver_webhook}
        try:
            await functions[function]({"job_try": 1}, *args, **kwargs)
        except Retry:
            self.retries.append(function)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture(scope="session", autouse=True)
def _migrate_database() -> Iterator[None]:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncIterator[None]:
    import fakeredis
    from sqlalchemy import text

    from docuflow.core import queue, redis
    from docuflow.core.database import engine

    redis.set_redis(fakeredis.FakeAsyncRedis(decode_responses=True))
    queue.set_queue(InlineQueue())
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE auth_user, auth_refresh_token, auth_api_key, document, "
                "webhook_endpoint, webhook_delivery CASCADE"
            )
        )


@pytest.fixture
def inline_queue() -> InlineQueue:
    from docuflow.core.queue import get_queue

    q = get_queue()
    assert isinstance(q, InlineQueue)
    return q


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from docuflow.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as ac:
        yield ac


async def register_and_login(
    client: AsyncClient, email: str = "ana@example.com", password: str = "Secret123"
) -> dict[str, str]:
    await client.post(
        "/auth/register", json={"email": email, "full_name": "Ana Test", "password": password}
    )
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    return await register_and_login(client)


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal valid single-page PDF with a text layer (no external deps)."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text_ops = (
        "BT /F1 12 Tf 14 TL 50 800 Td " + " ".join(f"({esc(line)}) '" for line in lines) + " ET"
    )
    stream = text_ops.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


INVOICE_LINES = [
    "INVOICE",
    "Invoice No: INV-2026-001",
    "Date: 2026-10-01",
    "Due date: 2026-10-31",
    "From: Acme Supplies LLC",
    "Bill to: Globex Corp",
    "Widget A 2 50.00 100.00",
    "Widget B 1 25.50 25.50",
    "Subtotal: $125.50",
    "Tax: $12.55",
    "Total: $138.05",
]


@pytest.fixture
def invoice_pdf() -> bytes:
    return make_pdf(INVOICE_LINES)
