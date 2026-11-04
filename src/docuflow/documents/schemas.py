from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from docuflow.core.schemas import CustomModel
from docuflow.documents.models import DocumentStatus, DocumentType

# ------------------------------------------------------------------ extraction
# These models double as the JSON schema Claude must follow (structured outputs),
# so field descriptions are written as instructions for the model.


class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class Party(BaseModel):
    name: str | None = None
    tax_id: str | None = Field(default=None, description="VAT/NIT/RFC/EIN or similar, if present")
    address: str | None = None
    email: str | None = None


class ExtractedData(BaseModel):
    document_type: DocumentType
    language: str | None = Field(default=None, description="ISO 639-1 code, e.g. 'en', 'es'")
    title: str | None = Field(default=None, description="Short human-readable title")
    document_number: str | None = Field(
        default=None, description="Invoice/receipt/PO/contract number"
    )
    issue_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    due_date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    issuer: Party | None = Field(default=None, description="Vendor / seller / first party")
    recipient: Party | None = Field(default=None, description="Customer / buyer / second party")
    currency: str | None = Field(default=None, description="ISO 4217 code, e.g. USD, EUR, COP")
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    key_terms: list[str] = Field(
        default_factory=list, description="For contracts: notable clauses, obligations, dates"
    )
    summary: str = Field(description="One or two sentence summary of the document")
    confidence: float = Field(ge=0, le=1, description="Your confidence in the extraction, 0 to 1")


# ------------------------------------------------------------------ API


class DocumentRead(CustomModel):
    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    document_type: DocumentType | None
    is_reviewed: bool
    error: str | None
    ai_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime
    processed_at: datetime | None


class DocumentDetail(DocumentRead):
    extracted_data: dict[str, Any] | None


class DocumentUpdate(CustomModel):
    """Manual review: a human corrects the extracted data."""

    extracted_data: ExtractedData


class StatusCount(CustomModel):
    status: DocumentStatus
    count: int


class TypeCount(CustomModel):
    document_type: DocumentType
    count: int


class CurrencyTotal(CustomModel):
    currency: str
    total: float


class DailyCount(CustomModel):
    day: str
    count: int


class DocumentStats(CustomModel):
    total_documents: int
    by_status: list[StatusCount]
    by_type: list[TypeCount]
    totals_by_currency: list[CurrencyTotal]
    last_30_days: list[DailyCount]
    total_input_tokens: int
    total_output_tokens: int
    avg_processing_seconds: float | None


class SheetsExportRequest(CustomModel):
    spreadsheet_id: str = Field(min_length=10, max_length=200)
    worksheet: str = Field(default="DocuFlow", max_length=100)
