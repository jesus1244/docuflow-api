import csv
import hashlib
import io
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Float, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from docuflow.core.database import utcnow
from docuflow.core.exceptions import AppError, ConflictError
from docuflow.core.queue import get_queue
from docuflow.core.storage import get_storage
from docuflow.documents.models import Document, DocumentStatus, DocumentType
from docuflow.documents.schemas import (
    CurrencyTotal,
    DailyCount,
    DocumentStats,
    ExtractedData,
    StatusCount,
    TypeCount,
)

# Magic bytes are checked instead of trusting the client-provided content type.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "application/pdf": (b"%PDF-",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}


class UnsupportedFileError(AppError):
    status_code = 415
    code = "unsupported_file"
    message = "Only PDF, PNG, JPEG and WEBP files are supported"


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"


def detect_content_type(content: bytes) -> str:
    for content_type, signatures in _SIGNATURES.items():
        if any(content.startswith(sig) for sig in signatures):
            if content_type == "image/webp" and content[8:12] != b"WEBP":
                continue
            return content_type
    raise UnsupportedFileError()


async def create_document(
    session: AsyncSession, owner_id: UUID, filename: str, content: bytes, max_bytes: int
) -> Document:
    if len(content) > max_bytes:
        raise FileTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit")
    if not content:
        raise UnsupportedFileError("The file is empty")
    content_type = detect_content_type(content)

    document_id = uuid4()
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:255] or "document"
    storage_key = f"{owner_id}/{document_id}"
    await get_storage().put(storage_key, content, content_type)

    document = Document(
        id=document_id,
        owner_id=owner_id,
        filename=safe_name,
        content_type=content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=storage_key,
    )
    session.add(document)
    await session.commit()
    await get_queue().enqueue("process_document", str(document.id))
    return document


def _owned(owner_id: UUID) -> Select[Document]:
    return select(Document).where(Document.owner_id == owner_id)


async def list_documents(
    session: AsyncSession,
    owner_id: UUID,
    *,
    limit: int,
    offset: int,
    status: DocumentStatus | None = None,
    document_type: DocumentType | None = None,
    search: str | None = None,
) -> tuple[Sequence[Document], int]:
    query = _owned(owner_id)
    if status:
        query = query.where(Document.status == status)
    if document_type:
        query = query.where(Document.document_type == document_type)
    if search:
        query = query.where(Document.filename.ilike(f"%{search}%"))
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(
        query.order_by(Document.created_at.desc()).limit(limit).offset(offset)
    )
    return rows.all(), total


async def update_extracted_data(
    session: AsyncSession, document: Document, data: ExtractedData
) -> Document:
    if document.status != DocumentStatus.COMPLETED:
        raise ConflictError("Only completed documents can be reviewed")
    document.extracted_data = data.model_dump(mode="json")
    document.document_type = data.document_type
    document.is_reviewed = True
    await session.commit()
    return document


async def reprocess(session: AsyncSession, document: Document) -> Document:
    if document.status == DocumentStatus.PROCESSING:
        raise ConflictError("Document is already being processed")
    document.status = DocumentStatus.PENDING
    document.error = None
    await session.commit()
    await get_queue().enqueue("process_document", str(document.id))
    return document


async def delete_document(session: AsyncSession, document: Document) -> None:
    await get_storage().delete(document.storage_key)
    await session.delete(document)
    await session.commit()


async def get_stats(session: AsyncSession, owner_id: UUID) -> DocumentStats:
    """All aggregation happens in SQL; Python only shapes the result."""
    owned = Document.owner_id == owner_id

    by_status = await session.execute(
        select(Document.status, func.count()).where(owned).group_by(Document.status)
    )
    by_type = await session.execute(
        select(Document.document_type, func.count())
        .where(owned, Document.document_type.is_not(None))
        .group_by(Document.document_type)
    )
    currency = Document.extracted_data["currency"].astext
    amount = cast(Document.extracted_data["total"].astext, Float)
    totals = await session.execute(
        select(currency, func.sum(amount))
        .where(owned, Document.status == DocumentStatus.COMPLETED, currency.is_not(None))
        .group_by(currency)
    )
    since = utcnow() - timedelta(days=29)
    day = func.date_trunc("day", Document.created_at)
    daily = await session.execute(
        select(day, func.count())
        .where(owned, Document.created_at >= since)
        .group_by(day)
        .order_by(day)
    )
    usage = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Document.input_tokens), 0),
                func.coalesce(func.sum(Document.output_tokens), 0),
                func.avg(
                    func.extract("epoch", Document.processed_at - Document.processing_started_at)
                ),
            ).where(owned)
        )
    ).one()

    daily_map = {d.date().isoformat(): c for d, c in daily.all()}
    days = [(since + timedelta(days=i)).date().isoformat() for i in range(30)]
    return DocumentStats(
        total_documents=usage[0],
        by_status=[StatusCount(status=s, count=c) for s, c in by_status.all()],
        by_type=[TypeCount(document_type=t, count=c) for t, c in by_type.all()],
        totals_by_currency=[
            CurrencyTotal(currency=cur, total=round(t or 0, 2)) for cur, t in totals.all()
        ],
        last_30_days=[DailyCount(day=d, count=daily_map.get(d, 0)) for d in days],
        total_input_tokens=int(usage[1]),
        total_output_tokens=int(usage[2]),
        avg_processing_seconds=round(float(usage[3]), 2) if usage[3] is not None else None,
    )


CSV_COLUMNS = [
    "id",
    "filename",
    "status",
    "document_type",
    "document_number",
    "issue_date",
    "due_date",
    "issuer",
    "recipient",
    "currency",
    "subtotal",
    "tax",
    "total",
    "confidence",
    "created_at",
]


def document_to_row(doc: Document) -> dict[str, Any]:
    data = doc.extracted_data or {}
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        "status": doc.status.value,
        "document_type": doc.document_type.value if doc.document_type else "",
        "document_number": data.get("document_number"),
        "issue_date": data.get("issue_date"),
        "due_date": data.get("due_date"),
        "issuer": (data.get("issuer") or {}).get("name"),
        "recipient": (data.get("recipient") or {}).get("name"),
        "currency": data.get("currency"),
        "subtotal": data.get("subtotal"),
        "tax": data.get("tax"),
        "total": data.get("total"),
        "confidence": data.get("confidence"),
        "created_at": doc.created_at.isoformat(),
    }


async def export_csv(session: AsyncSession, owner_id: UUID) -> str:
    rows = await session.scalars(_owned(owner_id).order_by(Document.created_at.desc()))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for doc in rows:
        writer.writerow(document_to_row(doc))
    return buffer.getvalue()
