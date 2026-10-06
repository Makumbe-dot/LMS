"""Excel workbooks for every listing and for the spreadsheets people actually keep.

Every report that already answered `?fmt=csv` answers `?fmt=xlsx` too, through
`views.helpers.table_response`; the multi-sheet workbooks (the member register,
the portfolio workbook) are built here from the same row dicts.

A sheet is laid out the way someone opening it in Excel expects: a title and an
"as at" line, a header row that stays put when scrolling, a filter on every
column, money as numbers formatted to two places (so it sums), dates as dates,
and a SUBTOTAL row under the money columns that follows the filter.
"""
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.http import HttpResponse

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MONEY_FORMAT = "#,##0.00"
DATE_FORMAT = "yyyy-mm-dd"
HEADER_FILL = "FDEADB"   # the brand orange, as a wash
HEADER_INK = "16171A"    # the logo's black
TITLE_INK = "16171A"
NOTE_INK = "5F636B"
MAX_WIDTH = 48

# Columns that hold an identifier rather than a quantity: never totalled.
_NOT_TOTALLED = ("_id", "id", "_no", "term", "days", "number", "count", "loans", "accounts",
                 "members", "year", "month", "rate")


@dataclass
class Sheet:
    title: str
    rows: list[dict]
    heading: str | None = None
    notes: list[str] = field(default_factory=list)
    totals: bool = True
    # Money columns that must not be summed: a running balance, a rate.
    skip_totals: tuple = ()


def humanise(key: str) -> str:
    """"principal_outstanding" -> "Principal outstanding"."""
    text = str(key).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _cell_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        return float(value)  # Excel has one number type; two places survive as a format
    if isinstance(value, (datetime, date)):
        # Excel stores no timezone; a datetime is shown as the local wall-clock time.
        return value.replace(tzinfo=None) if isinstance(value, datetime) else value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def _totalled(key: str, values: list) -> bool:
    lowered = key.lower()
    if lowered == "id" or any(lowered.endswith(s) or lowered == s for s in _NOT_TOTALLED):
        return False
    numbers = [v for v in values if v is not None]
    return bool(numbers) and all(isinstance(v, Decimal) for v in numbers)


def _write_sheet(ws, sheet: Sheet) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    row = 1
    if sheet.heading:
        ws.cell(row=row, column=1, value=sheet.heading).font = Font(
            bold=True, size=14, color=TITLE_INK)
        row += 1
    for note in sheet.notes:
        ws.cell(row=row, column=1, value=note).font = Font(italic=True, color=NOTE_INK)
        row += 1
    if sheet.heading or sheet.notes:
        row += 1

    if not sheet.rows:
        ws.cell(row=row, column=1, value="Nothing to show").font = Font(italic=True)
        return

    keys = list(sheet.rows[0].keys())
    header_row = row
    header_font = Font(bold=True, color=HEADER_INK)
    header_fill = PatternFill("solid", fgColor=HEADER_FILL)
    for col, key in enumerate(keys, start=1):
        cell = ws.cell(row=header_row, column=col, value=humanise(key))
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="bottom", wrap_text=True)

    widths = [len(humanise(k)) for k in keys]
    columns = {k: [r.get(k) for r in sheet.rows] for k in keys}
    for offset, record in enumerate(sheet.rows, start=1):
        for col, key in enumerate(keys, start=1):
            raw = record.get(key)
            cell = ws.cell(row=header_row + offset, column=col, value=_cell_value(raw))
            if isinstance(raw, Decimal):
                cell.number_format = MONEY_FORMAT
            elif isinstance(raw, (date, datetime)):
                cell.number_format = DATE_FORMAT if not isinstance(raw, datetime) \
                    else "yyyy-mm-dd hh:mm"
            shown = f"{raw:,.2f}" if isinstance(raw, Decimal) else ("" if raw is None else str(raw))
            widths[col - 1] = max(widths[col - 1], min(len(shown), MAX_WIDTH))

    last_row = header_row + len(sheet.rows)
    if sheet.totals:
        total_row = last_row + 1
        labelled = False
        for col, key in enumerate(keys, start=1):
            if key not in sheet.skip_totals and _totalled(key, columns[key]):
                letter = get_column_letter(col)
                cell = ws.cell(row=total_row, column=col,
                               value=f"=SUBTOTAL(9,{letter}{header_row + 1}:{letter}{last_row})")
                cell.number_format = MONEY_FORMAT
                cell.font = Font(bold=True)
            elif not labelled and col == 1:
                ws.cell(row=total_row, column=col, value="Total").font = Font(bold=True)
                labelled = True

    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = max(8, min(width + 2, MAX_WIDTH + 2))
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(keys))}{last_row}"


def workbook_bytes(sheets: list[Sheet]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)
    used = set()
    for sheet in sheets:
        # Excel limits a sheet name to 31 characters and refuses a few symbols.
        title = "".join(ch for ch in sheet.title if ch not in '[]:*?/\\')[:31] or "Sheet"
        while title in used:
            title = title[:28] + f" {len(used)}"
        used.add(title)
        _write_sheet(book.create_sheet(title), sheet)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def xlsx_response(sheets: list[Sheet], name: str) -> HttpResponse:
    response = HttpResponse(workbook_bytes(sheets), content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
    return response


def single_sheet(rows: list[dict], name: str, heading: str | None = None,
                 notes: list[str] | None = None) -> HttpResponse:
    """One listing as one sheet, named after the report."""
    return xlsx_response([Sheet(humanise(name), rows, heading or humanise(name),
                                notes or [f"Generated {datetime.now():%Y-%m-%d %H:%M}"])], name)
