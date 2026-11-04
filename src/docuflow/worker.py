"""ARQ worker entrypoint: `arq docuflow.worker.WorkerSettings`."""

from typing import Any, ClassVar

import httpx

import docuflow.models  # noqa: F401  - registers all ORM models
from docuflow.core.config import get_settings
from docuflow.core.logging import configure_logging
from docuflow.core.queue import redis_settings
from docuflow.documents.extraction import get_extractor
from docuflow.documents.tasks import process_document
from docuflow.webhooks.tasks import deliver_webhook


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    ctx["http"] = httpx.AsyncClient(
        timeout=get_settings().webhook_timeout_seconds, follow_redirects=False
    )
    ctx["extractor"] = get_extractor()


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()


class WorkerSettings:
    functions: ClassVar = [process_document, deliver_webhook]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = 10
    max_tries = 10  # each task decides its own retry policy via arq.Retry
    job_timeout = 300
