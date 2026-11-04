import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import SecretStr

from docuflow.core.config import Settings
from docuflow.documents.extraction import (
    ClaudeExtractor,
    ExtractionError,
    GeminiExtractor,
    MockExtractor,
)
from docuflow.documents.models import DocumentType
from docuflow.documents.schemas import ExtractedData
from tests.conftest import INVOICE_LINES, make_pdf


async def test_mock_extractor_reads_invoice_fields() -> None:
    result = await MockExtractor().extract(make_pdf(INVOICE_LINES), "application/pdf", "inv.pdf")
    assert result.data.document_type == DocumentType.INVOICE
    assert result.data.subtotal == 125.5
    assert result.data.tax == 12.55
    assert result.data.recipient is not None
    assert result.data.recipient.name == "Globex Corp"
    assert 0 < result.data.confidence <= 1


async def test_mock_extractor_handles_european_number_format() -> None:
    pdf = make_pdf(["Factura No: F-77", "Total: 1.234,56 EUR"])
    result = await MockExtractor().extract(pdf, "application/pdf", "factura.pdf")
    assert result.data.total == 1234.56
    assert result.data.currency == "EUR"
    assert result.data.language == "es"


async def test_mock_extractor_tolerates_corrupt_pdf() -> None:
    result = await MockExtractor().extract(b"%PDF-garbage", "application/pdf", "contract.pdf")
    assert result.data.document_type == DocumentType.CONTRACT


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def claude_with(messages: FakeMessages) -> ClaudeExtractor:
    extractor = ClaudeExtractor(Settings(anthropic_api_key=SecretStr("sk-ant-test")))
    extractor.client = SimpleNamespace(beta=SimpleNamespace(messages=messages))  # type: ignore[assignment]
    return extractor


def fake_response(stop_reason: str = "end_turn", parsed: ExtractedData | None = None) -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        parsed_output=parsed,
        model="claude-opus-5-5",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    )


async def test_claude_extractor_sends_pdf_with_structured_output() -> None:
    parsed = ExtractedData(
        document_type=DocumentType.INVOICE, summary="ok", confidence=0.9, total=10
    )
    messages = FakeMessages(fake_response(parsed=parsed))
    result = await claude_with(messages).extract(b"%PDF-1.4 data", "application/pdf", "a.pdf")

    assert result.data.total == 10
    assert result.input_tokens == 1200
    sent = messages.kwargs
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_format"] is ExtractedData
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["fallbacks"] == "default"
    block = sent["messages"][0]["content"][0]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


async def test_claude_extractor_sends_images_as_image_blocks() -> None:
    parsed = ExtractedData(document_type=DocumentType.RECEIPT, summary="ok", confidence=0.9)
    messages = FakeMessages(fake_response(parsed=parsed))
    await claude_with(messages).extract(b"\x89PNG", "image/png", "r.png")
    assert messages.kwargs["messages"][0]["content"][0]["type"] == "image"


async def test_claude_extractor_raises_on_refusal() -> None:
    extractor = claude_with(FakeMessages(fake_response(stop_reason="refusal")))
    with pytest.raises(ExtractionError, match="declined") as info:
        await extractor.extract(b"%PDF", "application/pdf", "a.pdf")
    assert info.value.retryable is False


async def test_claude_rate_limit_is_retryable() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)  # type: ignore[arg-type]
    extractor = claude_with(FakeMessages(error=error))
    with pytest.raises(ExtractionError) as info:
        await extractor.extract(b"%PDF", "application/pdf", "a.pdf")
    assert info.value.retryable is True


def test_claude_extractor_requires_api_key() -> None:
    with pytest.raises(ExtractionError):
        ClaudeExtractor(Settings(anthropic_api_key=None))


# --- Gemini ---------------------------------------------------------------------------------


class FakeGeminiModels:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, Any] = {}

    async def generate_content(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def gemini_with(models: FakeGeminiModels) -> GeminiExtractor:
    extractor = GeminiExtractor(Settings(gemini_api_key=SecretStr("gemini-test")))
    extractor.client = SimpleNamespace(aio=SimpleNamespace(models=models))  # type: ignore[assignment]
    return extractor


def gemini_response(
    text: str | None, finish: genai_types.FinishReason = genai_types.FinishReason.STOP
) -> genai_types.GenerateContentResponse:
    parts = [genai_types.Part(text=text)] if text is not None else []
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(role="model", parts=parts), finish_reason=finish
            )
        ],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=900, candidates_token_count=250, thoughts_token_count=50
        ),
        model_version="gemini-3.8-flash",
    )


async def test_gemini_extractor_sends_file_with_json_schema() -> None:
    data = ExtractedData(document_type=DocumentType.INVOICE, summary="ok", confidence=0.8, total=42)
    models = FakeGeminiModels(gemini_response(data.model_dump_json()))
    result = await gemini_with(models).extract(b"%PDF-1.4", "application/pdf", "inv.pdf")

    assert result.data.total == 42
    assert result.model == "gemini-3.8-flash"
    assert (result.input_tokens, result.output_tokens) == (900, 300)
    part = models.kwargs["contents"][0]
    assert part.inline_data.mime_type == "application/pdf"
    config = models.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert "default" not in json.dumps(config.response_json_schema)


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (genai_errors.ServerError(503, {"error": {"message": "overloaded"}}), True),
        (genai_errors.ClientError(429, {"error": {"message": "quota"}}), True),
        (genai_errors.ClientError(400, {"error": {"message": "bad file"}}), False),
    ],
)
async def test_gemini_errors_map_to_retryable(error: Exception, retryable: bool) -> None:
    with pytest.raises(ExtractionError) as info:
        await gemini_with(FakeGeminiModels(error=error)).extract(b"x", "image/png", "a.png")
    assert info.value.retryable is retryable


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (gemini_response("{}", genai_types.FinishReason.MAX_TOKENS), "incomplete"),
        (gemini_response(None, genai_types.FinishReason.SAFETY), "declined"),
        (gemini_response('{"summary": "missing confidence"}'), "expected format"),
        (gemini_response(None), "empty"),
    ],
)
async def test_gemini_bad_responses_fail_cleanly(response: Any, message: str) -> None:
    with pytest.raises(ExtractionError, match=message):
        await gemini_with(FakeGeminiModels(response)).extract(b"x", "image/png", "a.png")


def test_gemini_requires_credentials() -> None:
    with pytest.raises(ExtractionError, match="GEMINI_API_KEY"):
        GeminiExtractor(Settings(gemini_api_key=None))
    with pytest.raises(ExtractionError, match="GOOGLE_CLOUD_PROJECT"):
        GeminiExtractor(Settings(gemini_use_vertexai=True, google_cloud_project=None))
