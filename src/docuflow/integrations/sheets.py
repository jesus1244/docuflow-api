"""Google Sheets export via a service account.

Setup: create a service account in Google Cloud, enable the Sheets API, download its JSON key,
set GOOGLE_SERVICE_ACCOUNT_FILE, and share the target spreadsheet with the service account email.
"""

import asyncio
from typing import Any

import gspread

from docuflow.core.config import get_settings
from docuflow.core.exceptions import AppError, ServiceUnavailableError


class SheetsExportError(AppError):
    status_code = 502
    code = "sheets_export_failed"
    message = "Could not write to Google Sheets"


def _append_rows_sync(
    spreadsheet_id: str, worksheet: str, header: list[str], rows: list[list[Any]]
) -> int:
    settings = get_settings()
    client = gspread.service_account(filename=str(settings.google_service_account_file))
    spreadsheet = client.open_by_key(spreadsheet_id)
    try:
        sheet = spreadsheet.worksheet(worksheet)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=worksheet, rows=1000, cols=len(header))
        sheet.append_row(header)
    sheet.append_rows(rows, value_input_option=gspread.utils.ValueInputOption.user_entered)
    return len(rows)


async def append_rows(
    spreadsheet_id: str, worksheet: str, header: list[str], rows: list[list[Any]]
) -> int:
    if not get_settings().google_service_account_file:
        raise ServiceUnavailableError("Google Sheets integration is not configured")
    try:
        # gspread is synchronous: run it in a worker thread so the event loop stays free.
        return await asyncio.to_thread(_append_rows_sync, spreadsheet_id, worksheet, header, rows)
    except gspread.exceptions.APIError as exc:
        raise SheetsExportError(str(exc)) from exc
    except gspread.SpreadsheetNotFound as exc:
        raise SheetsExportError(
            "Spreadsheet not found. Share it with the service account email."
        ) from exc
