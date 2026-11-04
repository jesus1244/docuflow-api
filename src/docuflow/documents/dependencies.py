from typing import Annotated
from uuid import UUID

from fastapi import Depends

from docuflow.auth.dependencies import CurrentUser, SessionDep
from docuflow.core.exceptions import NotFoundError
from docuflow.documents.models import Document


async def valid_owned_document(
    document_id: UUID, user: CurrentUser, session: SessionDep
) -> Document:
    """Load a document and check ownership. 404 (not 403) avoids leaking that the id exists."""
    document = await session.get(Document, document_id)
    if document is None or document.owner_id != user.id:
        raise NotFoundError("Document not found")
    return document


OwnedDocument = Annotated[Document, Depends(valid_owned_document)]
