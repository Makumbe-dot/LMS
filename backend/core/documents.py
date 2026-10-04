"""Statements as files: Excel workbooks and PDFs, from services/statements.py data.

The PDF is what goes to a borrower, so it carries the organisation's name and
contact details from Settings (the same ones the loan agreement prints), a logo
only if STATEMENT_LOGO names one, and every page says which statement it belongs
to and how many pages there are. The Excel workbook is for staff: the same lines as numbers,
plus the repayment schedule on a second sheet.
"""
import io
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse

from .exports import Sheet, xlsx_response
from .models import OrganisationSetting

NAVY = "#0F1F3D"
MUTED = "#5B6B82"
LINE = "#E3E6EC"
HEADER_FILL = "#E2F2FD"
ACCENT = "#4FB3F0"
ZEBRA = "#F7F8FA"


# ---------------------------------------------------------------- shared
def _money(value) -> str:
    if value is None or value == "":
        return ""
    return f"{Decimal(value):,.2f}"


def _date(value) -> str:
    if not value:
        return "-"
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def logo_path() -> Path | None:
    """The organisation's own logo, only when STATEMENT_LOGO names one. Statements carry
    no logo otherwise, and a path that is not a file leaves the header without one."""
    configured = getattr(settings, "STATEMENT_LOGO", "") or ""
    path = Path(configured) if configured else None
    return path if path and path.is_file() else None


def _org() -> OrganisationSetting:
    return OrganisationSetting.load()


def _period(data) -> str:
    if data.get("period_start"):
        return f"{_date(data['period_start'])} to {_date(data['period_end'])}"
    return f"to {_date(data['period_end'])}"


# ---------------------------------------------------------------- Excel
def loan_statement_xlsx(data: dict, loan) -> HttpResponse:
    org = _org()
    currency = org.currency
    notes = [
        f"{org.name} · {data['borrower']} ({data['borrower_no']}, ID {data['national_id']})",
        f"{data['product']}: {currency} {_money(data['principal'])} at {data['rate_pct']}% a month "
        f"({data['rate_method'].lower()}) over {data['term']} {data['term_unit']}; "
        f"disbursed {_date(data['disbursement_date'])}, maturity {_date(data['maturity_date'])}",
        f"Outstanding: {currency} {_money(data['total_outstanding'])} "
        f"(principal {_money(data['principal_outstanding'])}, interest still to fall due "
        f"{_money(data['interest_outstanding'])}, penalties {_money(data['penalties_outstanding'])}, "
        f"charges {_money(data['charges_outstanding'])}); in arrears "
        f"{_money(data['arrears_amount'])} for {data['days_in_arrears']} days",
        f"Period {_period(data)}. The balance is principal, penalties and charges owed; interest is "
        f"charged on each instalment as it falls due.",
    ]
    rows = []
    if data.get("period_start"):
        rows.append(_loan_row(None, "Opening balance", None, None, None, None, None, None, None,
                              data["opening_balance"]))
    for line in data["lines"]:
        rows.append(_loan_row(line["date"], line["description"], line["reference"],
                              line["narration"], line["debit"], line["credit"], line["principal"],
                              line["interest"], line["penalty_and_charges"], line["balance"]))
    schedule = [{
        "No": i.number, "Due date": i.due_date, "Principal": i.principal_due,
        "Interest": i.interest_due, "Penalty": i.penalty_due, "Charges": i.charge_due,
        "Total due": i.total_due, "Paid": i.total_paid, "Balance": i.balance,
        "Status": i.get_status_display(), "Paid on": i.paid_date,
    } for i in loan.instalments.order_by("number")]
    return xlsx_response([
        Sheet("Statement", rows, f"Loan statement {data['loan_no']}", notes,
              skip_totals=("Balance",)),
        Sheet("Schedule", schedule, f"Repayment schedule {data['loan_no']}",
              [f"{data['borrower']} · {data['product']}"], skip_totals=("Balance",)),
    ], f"statement_{data['loan_no']}")


def _loan_row(on, description, reference, narration, charged, paid, principal, interest,
              penalty, balance) -> dict:
    return {"Date": on, "Description": description, "Reference": reference,
            "Narration": narration, "Charged": charged, "Paid": paid, "Principal": principal,
            "Interest": interest, "Penalties and charges": penalty, "Balance": balance}


def savings_statement_xlsx(data: dict) -> HttpResponse:
    org = _org()
    notes = [
        f"{org.name} · {data['borrower']} ({data['borrower_no']}, ID {data['national_id']})",
        f"{data['product']} at {data['interest_rate_pct_pa']}% a year · opened "
        f"{_date(data['opened_on'])} · {data['status']}",
        f"Balance {org.currency} {_money(data['balance'])}, available "
        f"{_money(data['available_balance'])} · period {_period(data)}",
    ]
    rows = []
    if data.get("period_start"):
        rows.append({"Date": None, "Description": "Opening balance", "Reference": None,
                     "Narration": None, "Money in": None, "Money out": None,
                     "Balance": data["opening_balance"]})
    for line in data["lines"]:
        rows.append({"Date": line["date"], "Description": line["description"],
                     "Reference": line["reference"], "Narration": line["narration"],
                     "Money in": line["money_in"], "Money out": line["money_out"],
                     "Balance": line["balance"]})
    return xlsx_response([Sheet("Statement", rows, f"Savings statement {data['account_no']}", notes,
                                skip_totals=("Balance",))], f"statement_{data['account_no']}")


# ---------------------------------------------------------------- PDF
def _styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "org": ParagraphStyle("org", parent=base["Normal"], fontName="Helvetica-Bold",
                              fontSize=13, leading=16, textColor=HexColor(NAVY)),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8.5, leading=11,
                                textColor=HexColor(MUTED)),
        "title": ParagraphStyle("title", parent=base["Normal"], fontName="Helvetica-Bold",
                                fontSize=16, leading=20, textColor=HexColor(NAVY),
                                spaceBefore=4, spaceAfter=2),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9, leading=12,
                               textColor=HexColor(NAVY)),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontSize=8, leading=10,
                               textColor=HexColor(NAVY)),
        "note": ParagraphStyle("note", parent=base["Normal"], fontSize=7.5, leading=10,
                               textColor=HexColor(MUTED), spaceBefore=6),
    }


def _numbered_canvas(footer: str):
    """A canvas that writes "Page n of N" once it knows N."""
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                width, _ = self._pagesize
                self.setStrokeColor(HexColor(LINE))
                self.line(36, 34, width - 36, 34)
                self.setFont("Helvetica", 7.5)
                self.setFillColor(HexColor(MUTED))
                self.drawString(36, 22, footer)
                self.drawRightString(width - 36, 22, f"Page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    return NumberedCanvas


def _header(org, title: str, subtitle: str, styles):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Image, Paragraph, Table, TableStyle

    contact = " · ".join(part for part in [org.address, org.phone, org.email] if part)
    left = [Paragraph(org.name, styles["org"])]
    if contact:
        left.append(Paragraph(contact, styles["small"]))
    logo = logo_path()
    if logo:
        from reportlab.lib.utils import ImageReader

        width, height = ImageReader(str(logo)).getSize()
        image = Image(str(logo), width=34 * width / height, height=34)
        head = Table([[image, left]], colWidths=[34 * width / height + 10, None])
    else:
        head = Table([[left]])
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, HexColor(ACCENT)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [head, Paragraph(title, styles["title"]), Paragraph(subtitle, styles["small"])]


def _facts(pairs_left, pairs_right, styles):
    """Two columns of label / value facts."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    def column(pairs):
        return [[Paragraph(f"<font color='{MUTED}'>{label}</font>", styles["body"]),
                 Paragraph(str(value if value not in (None, "") else "-"), styles["body"])]
                for label, value in pairs]

    left = Table(column(pairs_left), colWidths=[108, 152])
    right = Table(column(pairs_right), colWidths=[110, 150])
    for table in (left, right):
        table.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1.5),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    outer = Table([[left, right]], colWidths=[270, 262])
    outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("BOX", (0, 0), (-1, -1), 0.6, HexColor(LINE)),
                               ("TOPPADDING", (0, 0), (-1, -1), 8),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                               ("LEFTPADDING", (0, 0), (0, 0), 10)]))
    return outer


def _lines_table(header: list[str], rows: list[list], widths: list[float], numeric: set[int],
                 styles, totals: list | None = None):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    body = [[Paragraph(str(cell), styles["cell"]) if isinstance(cell, str) and i not in numeric
             else cell for i, cell in enumerate(row)] for row in rows]
    data = [header] + body + ([totals] if totals else [])
    table = Table(data, colWidths=widths, repeatRows=1)
    commands = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor(NAVY)),
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(HEADER_FILL)),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 1), (-1, -1), HexColor(NAVY)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, HexColor(LINE)),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for col in numeric:
        commands.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    for index in range(1, len(body) + 1):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-1, index), HexColor(ZEBRA)))
    if totals:
        commands += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8),
                     ("LINEABOVE", (0, -1), (-1, -1), 0.8, HexColor(NAVY))]
    table.setStyle(TableStyle(commands))
    return table


def _pdf_response(story, name: str, footer: str, landscape_page: bool = False) -> HttpResponse:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate

    buffer = io.BytesIO()
    page = landscape(A4) if landscape_page else A4
    doc = SimpleDocTemplate(buffer, pagesize=page, leftMargin=36, rightMargin=36, topMargin=34,
                            bottomMargin=48, title=name, author=_org().name)
    doc.build(story, canvasmaker=_numbered_canvas(footer))
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
    return response


def loan_statement_pdf(data: dict) -> HttpResponse:
    from reportlab.platypus import Paragraph, Spacer

    org = _org()
    cur = org.currency
    styles = _styles()
    story = _header(org, "Loan statement",
                    f"{data['loan_no']} · period {_period(data)} · issued {date.today().isoformat()}",
                    styles)
    story.append(Spacer(1, 8))
    story.append(_facts(
        [("Borrower", data["borrower"]), ("Member no.", data["borrower_no"]),
         ("National ID", data["national_id"]), ("Phone", data["phone"]),
         ("Employer", data["employer"]), ("Branch", data["branch"])],
        [("Product", data["product"]),
         ("Principal", f"{cur} {_money(data['principal'])}"),
         ("Rate", f"{data['rate_pct']}% a month, {data['rate_method'].lower()}"),
         ("Term", f"{data['term']} {data['term_unit']}"),
         ("Instalment", f"{cur} {_money(data['instalment_amount'])}"),
         ("APR, fees included", f"{data['apr_pct']}% a year" if data["apr_pct"] is not None else "-"),
         ("Disbursed / maturity", f"{_date(data['disbursement_date'])} / {_date(data['maturity_date'])}")],
        styles))
    story.append(Spacer(1, 8))
    story.append(_facts(
        [("Principal outstanding", f"{cur} {_money(data['principal_outstanding'])}"),
         ("Interest to fall due", f"{cur} {_money(data['interest_outstanding'])}"),
         ("Penalties", f"{cur} {_money(data['penalties_outstanding'])}"),
         ("Charges", f"{cur} {_money(data['charges_outstanding'])}")],
        [("Total outstanding", f"<b>{cur} {_money(data['total_outstanding'])}</b>"),
         ("In arrears", f"{cur} {_money(data['arrears_amount'])} ({data['days_in_arrears']} days)"),
         ("Next instalment", f"{cur} {_money(data['next_due_amount'])} on {_date(data['next_due_date'])}"
          if data["next_due_date"] else "-"),
         ("Status", str(data["status"]).replace("_", " "))],
        styles))
    story.append(Spacer(1, 10))

    rows = []
    if data.get("period_start"):
        rows.append(["", "Opening balance", "", "", "", "", _money(data["opening_balance"])])
    for line in data["lines"]:
        rows.append([_date(line["date"]), line["description"], line["reference"] or "",
                     _money(line["debit"]) if line["debit"] else "",
                     _money(line["credit"]) if line["credit"] else "",
                     _money(line["interest"]) if line["interest"] else "",
                     _money(line["balance"])])
    interest_paid = sum((Decimal(str(line["interest"] or 0)) for line in data["lines"]), Decimal("0"))
    story.append(_lines_table(
        ["Date", "Description", "Reference", "Charged", "Paid", "Interest", "Balance"],
        rows, [56, 168, 70, 62, 62, 62, 62], {3, 4, 5, 6}, styles,
        totals=["", "Totals for the period", "", _money(data["total_charged"]),
                _money(data["total_paid_in_period"]), _money(interest_paid),
                _money(data["closing_balance"])]))
    story.append(Paragraph(
        "The balance is the principal, penalties and charges owed. Interest is charged on each "
        "instalment as it falls due and is shown in its own column when paid; the interest still "
        "to fall due is in the total outstanding above. Payments are applied to penalties, then "
        "charges, then interest, then principal, oldest instalment first.", styles["note"]))
    return _pdf_response(story, f"statement_{data['loan_no']}",
                         f"{org.name} · loan statement {data['loan_no']} · {data['borrower']}")


def savings_statement_pdf(data: dict) -> HttpResponse:
    from reportlab.platypus import Paragraph, Spacer

    org = _org()
    cur = org.currency
    styles = _styles()
    story = _header(org, "Savings statement",
                    f"{data['account_no']} · period {_period(data)} · issued {date.today().isoformat()}",
                    styles)
    story.append(Spacer(1, 8))
    story.append(_facts(
        [("Member", data["borrower"]), ("Member no.", data["borrower_no"]),
         ("National ID", data["national_id"]), ("Phone", data["phone"])],
        [("Product", data["product"]),
         ("Interest", f"{data['interest_rate_pct_pa']}% a year"),
         ("Opened", _date(data["opened_on"])), ("Branch", data["branch"]),
         ("Balance", f"<b>{cur} {_money(data['balance'])}</b>"),
         ("Available", f"{cur} {_money(data['available_balance'])}")],
        styles))
    story.append(Spacer(1, 10))
    rows = []
    if data.get("period_start"):
        rows.append(["", "Opening balance", "", "", "", _money(data["opening_balance"])])
    for line in data["lines"]:
        rows.append([_date(line["date"]), line["description"], line["reference"] or "",
                     _money(line["money_in"]) if line["money_in"] else "",
                     _money(line["money_out"]) if line["money_out"] else "",
                     _money(line["balance"])])
    story.append(_lines_table(
        ["Date", "Description", "Reference", "Money in", "Money out", "Balance"],
        rows, [58, 214, 80, 66, 66, 58], {3, 4, 5}, styles,
        totals=["", "Totals for the period", "", _money(data["total_in"]),
                _money(data["total_out"]), _money(data["closing_balance"])]))
    story.append(Paragraph(f"Generated {datetime.now():%Y-%m-%d %H:%M}.", styles["note"]))
    return _pdf_response(story, f"statement_{data['account_no']}",
                         f"{org.name} · savings statement {data['account_no']} · {data['borrower']}")
