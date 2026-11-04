"""Document data extraction.

`ClaudeExtractor` sends the PDF/image to Claude and gets back JSON validated against
`ExtractedData` (structured outputs). `MockExtractor` is a deterministic, offline extractor
so the whole product can be demoed and tested without an API key.
"""

import asyncio
import base64
import io
import re
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from anthropic.types.beta import BetaMessageParam
from pypdf import PdfReader

from docuflow.core.config import Settings, get_settings
from docuflow.documents.models import DocumentType
from docuflow.documents.schemas import ExtractedData, LineItem, Party

SYSTEM_PROMPT = """\
You extract structured data from business documents (invoices, receipts, contracts, purchase \
orders) for an accounting automation product.

Rules:
- Read the whole document, including tables and small print.
- Use null for anything that is not present. Never invent values.
- Dates must be ISO format (YYYY-MM-DD). Amounts are plain numbers without currency symbols.
- Infer the currency from symbols or context and return its ISO 4217 code.
- Write the summary in the document's own language.
- Set confidence lower when the scan is blurry, values are ambiguous or totals do not add up.\
"""


class ExtractionError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class ExtractionResult:
    data: ExtractedData
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class Extractor(Protocol):
    async def extract(
        self, content: bytes, content_type: str, filename: str
    ) -> ExtractionResult: ...


class ClaudeExtractor:
    def __init__(self, settings: Settings) -> None:
        if settings.anthropic_api_key is None:
            raise ExtractionError("ANTHROPIC_API_KEY is not configured")
        self.settings = settings
        self.client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(), max_retries=2
        )

    @staticmethod
    def _content_block(content: bytes, content_type: str) -> dict[str, Any]:
        data = base64.standard_b64encode(content).decode()
        if content_type == "application/pdf":
            return {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            }
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": content_type, "data": data},
        }

    async def extract(self, content: bytes, content_type: str, filename: str) -> ExtractionResult:
        user_content: list[Any] = [
            self._content_block(content, content_type),
            {"type": "text", "text": f"File name: {filename}\nExtract the data."},
        ]
        messages: list[BetaMessageParam] = [{"role": "user", "content": user_content}]
        try:
            response = await self.client.beta.messages.parse(
                model=self.settings.anthropic_model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={"effort": self.settings.anthropic_effort},
                # If a safety classifier declines, the API retries on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_format=ExtractedData,
                messages=messages,
            )
        except (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
        ) as exc:
            raise ExtractionError(
                f"AI provider temporarily unavailable: {exc}", retryable=True
            ) from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"AI provider rejected the request: {exc.message}") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("The AI model declined to process this document")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise ExtractionError("The AI response was incomplete")

        return ExtractionResult(
            data=response.parsed_output,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class MockExtractor:
    """Offline heuristic extractor (regex over the PDF text layer). Good enough for demos/tests."""

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds

    async def extract(self, content: bytes, content_type: str, filename: str) -> ExtractionResult:
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)  # makes the live progress UI visible in demos
        text = _pdf_text(content) if content_type == "application/pdf" else ""
        return ExtractionResult(data=_heuristic_extract(text, filename), model="mock-extractor")


def _pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # corrupt or scanned PDFs simply yield no text
        return ""


_AMOUNT = r"([\d.,]+\d)"


def _find(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else None


def _to_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    # The right-most separator is decimal if 1-2 digits follow: "1.234,56" or "1,234.56"
    last = max(raw.rfind("."), raw.rfind(","))
    if last != -1 and len(raw) - last - 1 in (1, 2):
        integer, decimals = raw[:last], raw[last + 1 :]
        cleaned = f"{integer.replace('.', '').replace(',', '')}.{decimals}"
    else:
        cleaned = raw.replace(".", "").replace(",", "")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _guess_type(text: str, filename: str) -> DocumentType:
    haystack = f"{filename} {text}".lower()
    for doc_type, words in (
        (DocumentType.INVOICE, ("invoice", "factura")),
        (DocumentType.RECEIPT, ("receipt", "recibo", "ticket")),
        (DocumentType.CONTRACT, ("contract", "contrato", "agreement")),
        (DocumentType.PURCHASE_ORDER, ("purchase order", "orden de compra")),
    ):
        if any(w in haystack for w in words):
            return doc_type
    return DocumentType.OTHER


def _heuristic_extract(text: str, filename: str) -> ExtractedData:
    doc_type = _guess_type(text, filename)
    total = _to_number(_find(rf"^\s*total[^\d\n]*{_AMOUNT}", text))
    subtotal = _to_number(_find(rf"sub\s*-?total[^\d\n]*{_AMOUNT}", text))
    tax = _to_number(_find(rf"(?:tax|iva|vat)[^\d\n]*{_AMOUNT}", text))
    currency = next(
        (
            code
            for sym, code in (("€", "EUR"), ("EUR", "EUR"), ("COP", "COP"), ("$", "USD"))
            if sym in text
        ),
        None,
    )
    items = [
        LineItem(
            description=m.group(1).strip(),
            quantity=float(m.group(2)),
            amount=_to_number(m.group(3)),
        )
        for m in re.finditer(r"^(.{3,60}?)\s+(\d+)\s+[\d.,]+\s+([\d.,]+\d)\s*$", text, re.MULTILINE)
    ]
    issuer = _find(r"(?:from|vendor|seller|proveedor)\s*:\s*(.+)", text)
    recipient = _find(r"(?:bill to|customer|cliente)\s*:\s*(.+)", text)
    found = sum(v is not None for v in (total, issuer, recipient, currency))
    return ExtractedData(
        document_type=doc_type,
        language="es" if re.search(r"\b(factura|fecha|cliente)\b", text, re.I) else "en",
        title=filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title(),
        document_number=_find(
            r"(?:invoice|factura|receipt|order)\s*(?:no\.?|#|number|n[º°])\s*:?\s*([\w-]+)", text
        ),
        issue_date=_find(r"(?:date|fecha)\s*:?\s*(\d{4}-\d{2}-\d{2})", text),
        due_date=_find(r"(?:due|vence)[^\d\n]*(\d{4}-\d{2}-\d{2})", text),
        issuer=Party(name=issuer) if issuer else None,
        recipient=Party(name=recipient) if recipient else None,
        currency=currency,
        subtotal=subtotal,
        tax=tax,
        total=total,
        line_items=items,
        summary=f"{doc_type.value.replace('_', ' ').capitalize()} read by the mock extractor.",
        confidence=round(0.3 + 0.15 * found, 2),
    )


def get_extractor() -> Extractor:
    settings = get_settings()
    if settings.extractor == "claude":
        return ClaudeExtractor(settings)
    return MockExtractor(delay_seconds=0 if settings.environment == "test" else 2.0)
