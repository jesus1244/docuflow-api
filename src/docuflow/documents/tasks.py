from typing import Any
from uuid import UUID

import structlog
from arq import Retry

from docuflow.core.database import SessionFactory, utcnow
from docuflow.core.events import publish_event
from docuflow.core.storage import get_storage
from docuflow.documents.extraction import ExtractionError, Extractor, get_extractor
from docuflow.documents.models import Document, DocumentStatus
from docuflow.documents.schemas import DocumentRead
from docuflow.webhooks.models import WebhookEvent
from docuflow.webhooks.service import dispatch_event

log = structlog.get_logger("tasks.documents")

MAX_AI_TRIES = 3


async def _notify(document: Document, event: str, **extra: Any) -> None:
    payload = DocumentRead.model_validate(document).model_dump(mode="json") | extra
    await publish_event(document.owner_id, event, payload)


async def process_document(ctx: dict[str, Any], document_id: str) -> str:
    """Download the file, extract data with the configured extractor, store it, notify everyone."""
    extractor: Extractor = ctx.get("extractor") or get_extractor()
    job_try: int = ctx.get("job_try", 1)

    async with SessionFactory() as session:
        document = await session.get(Document, UUID(document_id))
        if document is None:
            return "missing"
        bound = log.bind(document_id=document_id, try_=job_try)

        document.status = DocumentStatus.PROCESSING
        document.processing_started_at = utcnow()
        await session.commit()
        await _notify(document, "document.processing", step="extracting")

        try:
            content = await get_storage().get(document.storage_key)
            result = await extractor.extract(content, document.content_type, document.filename)
        except ExtractionError as exc:
            if exc.retryable and job_try < MAX_AI_TRIES:
                bound.warning("extraction.retry", error=str(exc))
                document.status = DocumentStatus.PENDING
                await session.commit()
                raise Retry(defer=job_try * 15) from exc
            return await _fail(document, str(exc))
        except Exception:
            bound.exception("extraction.crashed")
            return await _fail(document, "Unexpected error while processing the document")

        document.status = DocumentStatus.COMPLETED
        document.document_type = result.data.document_type
        document.extracted_data = result.data.model_dump(mode="json")
        document.ai_model = result.model
        document.input_tokens = result.input_tokens
        document.output_tokens = result.output_tokens
        document.processed_at = utcnow()
        document.error = None
        await session.commit()
        bound.info("extraction.completed", model=result.model)

        await _notify(document, "document.completed")
        await dispatch_event(
            session,
            document.owner_id,
            WebhookEvent.DOCUMENT_COMPLETED,
            {
                "document": DocumentRead.model_validate(document).model_dump(mode="json"),
                "extracted_data": document.extracted_data,
            },
        )
        return "completed"


async def _fail(document: Document, message: str) -> str:
    async with SessionFactory() as session:
        fresh = await session.get(Document, document.id)
        if fresh is None:
            return "missing"
        fresh.status = DocumentStatus.FAILED
        fresh.error = message
        fresh.processed_at = utcnow()
        await session.commit()
        await _notify(fresh, "document.failed")
        await dispatch_event(
            session,
            fresh.owner_id,
            WebhookEvent.DOCUMENT_FAILED,
            {"document": DocumentRead.model_validate(fresh).model_dump(mode="json")},
        )
    return "failed"
