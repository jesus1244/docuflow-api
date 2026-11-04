import csv
import io

import pytest
from httpx import AsyncClient

from docuflow.core.config import get_settings
from tests.conftest import InlineQueue, make_pdf, register_and_login

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def upload(
    client: AsyncClient, headers: dict[str, str], content: bytes, name: str = "invoice.pdf"
) -> dict:  # type: ignore[type-arg]
    response = await client.post(
        "/documents", headers=headers, files={"file": (name, content, "application/pdf")}
    )
    assert response.status_code == 202, response.text
    body: dict = response.json()  # type: ignore[type-arg]
    return body


async def test_upload_processes_document_end_to_end(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes, inline_queue: InlineQueue
) -> None:
    created = await upload(client, auth_headers, invoice_pdf)
    assert created["status"] == "pending"
    assert inline_queue.names()[0] == "process_document"

    detail = (await client.get(f"/documents/{created['id']}", headers=auth_headers)).json()
    assert detail["status"] == "completed"
    assert detail["document_type"] == "invoice"
    data = detail["extracted_data"]
    assert data["document_number"] == "INV-2026-001"
    assert data["issue_date"] == "2026-10-01"
    assert data["total"] == 138.05
    assert data["currency"] == "USD"
    assert data["issuer"]["name"] == "Acme Supplies LLC"
    assert len(data["line_items"]) == 2


async def test_content_type_is_detected_from_bytes_not_client(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await upload(client, auth_headers, PNG_BYTES, name="scan.pdf")
    assert created["content_type"] == "image/png"


async def test_rejects_unsupported_file(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("evil.pdf", b"MZ\x90\x00", "application/pdf")},
    )
    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_file"


async def test_rejects_file_over_limit(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    response = await client.post(
        "/documents",
        headers=auth_headers,
        files={"file": ("big.pdf", make_pdf(["x"]), "application/pdf")},
    )
    assert response.status_code == 413


async def test_list_filters_and_paginates(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    for i in range(3):
        await upload(client, auth_headers, invoice_pdf, name=f"invoice-{i}.pdf")
    await upload(client, auth_headers, make_pdf(["Service agreement", "Contract"]), name="nda.pdf")

    page = (await client.get("/documents?limit=2", headers=auth_headers)).json()
    assert page["total"] == 4
    assert len(page["items"]) == 2

    contracts = (await client.get("/documents?document_type=contract", headers=auth_headers)).json()
    assert [d["filename"] for d in contracts["items"]] == ["nda.pdf"]

    search = (await client.get("/documents?search=invoice-1", headers=auth_headers)).json()
    assert search["total"] == 1


async def test_documents_are_isolated_between_users(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    created = await upload(client, auth_headers, invoice_pdf)
    other = await register_and_login(client, email="eve@example.com")

    assert (await client.get(f"/documents/{created['id']}", headers=other)).status_code == 404
    assert (await client.delete(f"/documents/{created['id']}", headers=other)).status_code == 404
    assert (await client.get("/documents", headers=other)).json()["total"] == 0


async def test_review_updates_data_and_marks_reviewed(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    created = await upload(client, auth_headers, invoice_pdf)
    detail = (await client.get(f"/documents/{created['id']}", headers=auth_headers)).json()
    corrected = detail["extracted_data"] | {"total": 140.0, "confidence": 1.0}

    response = await client.put(
        f"/documents/{created['id']}/extracted-data",
        json={"extracted_data": corrected},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_reviewed"] is True
    assert response.json()["extracted_data"]["total"] == 140.0


async def test_reprocess_and_delete(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes, inline_queue: InlineQueue
) -> None:
    created = await upload(client, auth_headers, invoice_pdf)
    response = await client.post(f"/documents/{created['id']}/reprocess", headers=auth_headers)
    assert response.status_code == 202
    assert inline_queue.names().count("process_document") == 2

    assert (
        await client.delete(f"/documents/{created['id']}", headers=auth_headers)
    ).status_code == 204
    assert (
        await client.get(f"/documents/{created['id']}", headers=auth_headers)
    ).status_code == 404


async def test_download_original_file(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    created = await upload(client, auth_headers, invoice_pdf)
    response = await client.get(f"/documents/{created['id']}/file", headers=auth_headers)
    assert response.status_code == 200
    assert response.content == invoice_pdf
    assert response.headers["content-type"] == "application/pdf"


async def test_stats_aggregate_in_sql(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    await upload(client, auth_headers, invoice_pdf)
    await upload(client, auth_headers, invoice_pdf)
    stats = (await client.get("/documents/stats", headers=auth_headers)).json()

    assert stats["total_documents"] == 2
    assert stats["by_status"] == [{"status": "completed", "count": 2}]
    assert stats["totals_by_currency"] == [{"currency": "USD", "total": 276.1}]
    assert len(stats["last_30_days"]) == 30
    assert stats["last_30_days"][-1]["count"] == 2


async def test_csv_export(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    await upload(client, auth_headers, invoice_pdf)
    response = await client.get("/documents/export.csv", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["document_number"] == "INV-2026-001"
    assert rows[0]["total"] == "138.05"


async def test_sheets_export_requires_configuration(
    client: AsyncClient, auth_headers: dict[str, str], invoice_pdf: bytes
) -> None:
    created = await upload(client, auth_headers, invoice_pdf)
    response = await client.post(
        f"/documents/{created['id']}/export/sheets",
        json={"spreadsheet_id": "1AbCdEfGhIjKlMnOp"},
        headers=auth_headers,
    )
    assert response.status_code == 503
