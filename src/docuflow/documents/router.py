from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from docuflow.auth.dependencies import CurrentUser, SessionDep
from docuflow.core.config import get_settings
from docuflow.core.rate_limit import rate_limit
from docuflow.core.schemas import ErrorResponse, Page
from docuflow.core.storage import get_storage
from docuflow.documents import service
from docuflow.documents.dependencies import OwnedDocument
from docuflow.documents.models import DocumentStatus, DocumentType
from docuflow.documents.schemas import (
    DocumentDetail,
    DocumentRead,
    DocumentStats,
    DocumentUpdate,
    SheetsExportRequest,
)
from docuflow.integrations import sheets
from docuflow.webhooks.models import WebhookEvent
from docuflow.webhooks.service import dispatch_event

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document for AI extraction",
    description="Accepts PDF, PNG, JPEG or WEBP. Processing is asynchronous: subscribe to the "
    "WebSocket or a webhook to know when it finishes.",
    dependencies=[Depends(rate_limit("upload", get_settings().rate_limit_upload_per_minute))],
    responses={
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
    },
)
async def upload_document(
    user: CurrentUser, session: SessionDep, file: Annotated[UploadFile, File()]
) -> DocumentRead:
    max_bytes = get_settings().max_upload_bytes
    content = await file.read(max_bytes + 1)
    document = await service.create_document(
        session, user.id, file.filename or "document", content, max_bytes
    )
    return DocumentRead.model_validate(document)


@router.get("", response_model=Page[DocumentRead])
async def list_documents(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    document_type: DocumentType | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[DocumentRead]:
    items, total = await service.list_documents(
        session,
        user.id,
        limit=limit,
        offset=offset,
        status=status_filter,
        document_type=document_type,
        search=search,
    )
    return Page(
        items=[DocumentRead.model_validate(d) for d in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=DocumentStats)
async def get_stats(user: CurrentUser, session: SessionDep) -> DocumentStats:
    return await service.get_stats(session, user.id)


@router.get(
    "/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV with one row per document"}},
)
async def export_csv(user: CurrentUser, session: SessionDep) -> Response:
    body = await service.export_csv(session, user.id)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="docuflow-documents.csv"'},
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document: OwnedDocument) -> DocumentDetail:
    return DocumentDetail.model_validate(document)


@router.get("/{document_id}/file", response_class=Response)
async def download_file(document: OwnedDocument) -> Response:
    content = await get_storage().get(document.storage_key)
    return Response(
        content=content,
        media_type=document.content_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )


@router.put("/{document_id}/extracted-data", response_model=DocumentDetail)
async def review_document(
    data: DocumentUpdate, document: OwnedDocument, session: SessionDep
) -> DocumentDetail:
    """Save human corrections. Fires the `document.reviewed` webhook."""
    document = await service.update_extracted_data(session, document, data.extracted_data)
    await dispatch_event(
        session,
        document.owner_id,
        WebhookEvent.DOCUMENT_REVIEWED,
        {"document_id": str(document.id), "extracted_data": document.extracted_data},
    )
    return DocumentDetail.model_validate(document)


@router.post(
    "/{document_id}/reprocess", response_model=DocumentRead, status_code=status.HTTP_202_ACCEPTED
)
async def reprocess_document(document: OwnedDocument, session: SessionDep) -> DocumentRead:
    return DocumentRead.model_validate(await service.reprocess(session, document))


@router.post("/{document_id}/export/sheets", status_code=status.HTTP_200_OK)
async def export_to_sheets(document: OwnedDocument, data: SheetsExportRequest) -> dict[str, int]:
    row = service.document_to_row(document)
    written = await sheets.append_rows(
        data.spreadsheet_id, data.worksheet, service.CSV_COLUMNS, [list(row.values())]
    )
    return {"rows_written": written}


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document: OwnedDocument, session: SessionDep) -> None:
    await service.delete_document(session, document)
