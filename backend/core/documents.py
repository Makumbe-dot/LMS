"""Statements as files: Excel workbooks and PDFs, from services/statements.py data.

The PDF is what goes to a borrower, so it carries the organisation's name and
contact details from Settings (the same ones the loan agreement prints), the logo
STATEMENT_LOGO names (the Zinmad Capital monogram unless that is changed or
blanked), and every page says which statement it belongs to and how many pages
there are. The Excel workbook is for staff: the same lines as numbers, plus the
repayment schedule on a second sheet.
"""
import base64
import io
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse

from .exports import Sheet, xlsx_response
from .models import OrganisationSetting

# The brand's colours on paper: the logo's black as ink, its orange as the one
# accent (the rule under the letterhead), and a wash of that orange behind table
# headings. The same values as frontend/src/styles.css.
INK = "#16171A"
MUTED = "#5F636B"
LINE = "#E5E6E9"
HEADER_FILL = "#FDEADB"
ACCENT = "#F86A00"
ZEBRA = "#F7F7F8"


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
    """The logo STATEMENT_LOGO names. A relative path is taken from the backend
    directory, so the one that ships with the code is found wherever the server
    was started from. Blank, or a path that is not a file, leaves the header
    without a logo rather than failing the statement."""
    configured = getattr(settings, "STATEMENT_LOGO", "") or ""
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return path if path.is_file() else None


def logo_data_uri() -> str | None:
    """The same logo as a data: URI, for the printable agreement: a page of HTML
    that is fetched once and printed, so it has to carry its own picture."""
    path = logo_path()
    if not path:
        return None
    kind = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{kind};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


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
        f"Balance {data.get('currency') or org.currency} {_money(data['balance'])}, available "
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
# A statement is read top to bottom in the order a borrower asks its questions:
# who sent this and to whom; what do I owe right now and when is the next
# payment; how far through the loan am I; what are the terms; what happened in
# the period, line by line; how do I read it. reportlab is imported inside the
# functions, because the Excel path and the JSON screen should not pay for it.
MARGIN = 36
PAGE_W = 595.28                     # A4 portrait
CONTENT_W = PAGE_W - 2 * MARGIN     # 523.28
GAP = 10                            # between side-by-side panels
PANEL_W = (CONTENT_W - GAP) / 2

ORANGE = "#F87000"                  # the logo's own, for the gradient rule and bars
FLAME = "#F04A00"
RED = "#C8140A"
TRACK = "#EEEEF0"                   # the unfilled part of a progress bar
BAND = "#3A3D45"                    # the headline band: a charcoal, not the logo's black, which printed too heavy
PANEL_LINE = "#52565F"              # dividers inside the band
ON_DARK = "#FFFFFF"
ON_DARK_SOFT = "#C9CCD3"
ALERT_ON_DARK = "#FFA372"           # an overdue figure on the band


def _long(value) -> str:
    """6 October 2026, for the places a person reads rather than scans."""
    if not value:
        return "-"
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value.day} {value:%B %Y}"


def _mid(value) -> str:
    """6 Oct 2026, where a long month name would wrap."""
    if not value:
        return "-"
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f"{value.day} {value:%b %Y}"


def _pct(value) -> str:
    """4.000 -> "4%"; 7.5 -> "7.5%"; 12.25 -> "12.25%". The rate as a person says it."""
    if value is None or value == "":
        return "-"
    text = f"{Decimal(str(value)):.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _esc(value) -> str:
    from xml.sax.saxutils import escape

    return escape(str(value if value not in (None, "") else "-"))


def _styles():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()["Normal"]

    def style(name, **kw):
        return ParagraphStyle(name, parent=base, **kw)

    return {
        "org": style("org", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=HexColor(INK)),
        "small": style("small", fontSize=8.5, leading=11.5, textColor=HexColor(MUTED)),
        "small-right": style("small-right", fontSize=8.5, leading=11.5, textColor=HexColor(MUTED),
                             alignment=TA_RIGHT),
        "kind": style("kind", fontName="Helvetica-Bold", fontSize=8, leading=10,
                      textColor=HexColor(ACCENT), alignment=TA_RIGHT),
        "docno": style("docno", fontName="Helvetica-Bold", fontSize=15, leading=18,
                       textColor=HexColor(INK), alignment=TA_RIGHT),
        "eyebrow": style("eyebrow", fontName="Helvetica-Bold", fontSize=7, leading=9,
                         textColor=HexColor(MUTED), spaceAfter=3),
        "name": style("name", fontName="Helvetica-Bold", fontSize=11.5, leading=14,
                      textColor=HexColor(INK), spaceAfter=1),
        "body": style("body", fontSize=9, leading=12.5, textColor=HexColor(INK)),
        "label": style("label", fontSize=8.5, leading=12, textColor=HexColor(MUTED)),
        "value": style("value", fontSize=8.5, leading=12, textColor=HexColor(INK), alignment=TA_RIGHT),
        "section": style("section", fontName="Helvetica-Bold", fontSize=7.5, leading=10,
                         textColor=HexColor(MUTED)),
        "section-right": style("section-right", fontSize=8, leading=10, textColor=HexColor(MUTED),
                               alignment=TA_RIGHT),
        "cell": style("cell", fontSize=8, leading=10.5, textColor=HexColor(INK)),
        "cell-muted": style("cell-muted", fontSize=8, leading=10.5, textColor=HexColor(MUTED)),
        "head": style("head", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=HexColor(INK)),
        "head-right": style("head-right", fontName="Helvetica-Bold", fontSize=7, leading=9,
                            textColor=HexColor(INK), alignment=TA_RIGHT),
        "note": style("note", fontSize=7.5, leading=10.5, textColor=HexColor(MUTED)),
        "note-head": style("note-head", fontName="Helvetica-Bold", fontSize=7.5, leading=10.5,
                           textColor=HexColor(INK), spaceAfter=2),
        "hero-label": style("hero-label", fontName="Helvetica-Bold", fontSize=6.8, leading=9,
                            textColor=HexColor(ON_DARK_SOFT)),
        "hero-value": style("hero-value", fontName="Helvetica-Bold", fontSize=13, leading=16,
                            textColor=HexColor(ON_DARK)),
        "hero-value-lg": style("hero-value-lg", fontName="Helvetica-Bold", fontSize=20, leading=23,
                               textColor=HexColor(ON_DARK)),
        "hero-sub": style("hero-sub", fontSize=7.5, leading=10, textColor=HexColor(ON_DARK_SOFT)),
    }


def _flowables():
    """The two things drawn by hand: the gradient rule and the progress bar."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus.flowables import Flowable

    def gradient(canvas, x, y, width, height):
        """The brand gradient inside a rounded box: a clip, then a shading."""
        canvas.saveState()
        path = canvas.beginPath()
        path.roundRect(x, y, width, height, height / 2)
        canvas.clipPath(path, stroke=0, fill=0)
        canvas.linearGradient(x, y, x + width, y, (HexColor(ORANGE), HexColor(FLAME), HexColor(RED)),
                              extend=True)
        canvas.restoreState()

    class BrandRule(Flowable):
        """A hairline in the logo's orange-to-red, under the letterhead."""

        def __init__(self, width, height=2.4, space_after=10):
            super().__init__()
            self.width, self.height, self.space_after = width, height, space_after

        def wrap(self, *_):
            return self.width, self.height + self.space_after

        def draw(self):
            gradient(self.canv, 0, self.space_after, self.width, self.height)

    class Progress(Flowable):
        """How far along: a caption each side and a bar filled to `fraction`."""

        def __init__(self, width, fraction, left, right):
            super().__init__()
            self.width, self.fraction = width, max(0.0, min(1.0, float(fraction)))
            self.left, self.right = left, right
            self.height = 30

        def wrap(self, *_):
            return self.width, self.height

        def draw(self):
            c = self.canv
            c.setFont("Helvetica-Bold", 8.5)
            c.setFillColor(HexColor(INK))
            c.drawString(0, 18, self.left)
            c.setFont("Helvetica", 8)
            c.setFillColor(HexColor(MUTED))
            c.drawRightString(self.width, 18, self.right)
            c.setFillColor(HexColor(TRACK))
            c.roundRect(0, 4, self.width, 7, 3.5, stroke=0, fill=1)
            if self.fraction > 0:
                gradient(c, 0, 4, max(7, self.width * self.fraction), 7)

    return BrandRule, Progress


def _numbered_canvas(footer: str):
    """A canvas that writes the footer and "Page n of N" once it knows N."""
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    stamp = f"Generated {datetime.now():%d %b %Y %H:%M}"

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
                self.setLineWidth(0.6)
                self.line(MARGIN, 36, width - MARGIN, 36)
                self.setFont("Helvetica", 7.5)
                self.setFillColor(HexColor(MUTED))
                self.drawString(MARGIN, 24, footer)
                self.drawRightString(width - MARGIN, 24, f"Page {self._pageNumber} of {total}")
                self.setFont("Helvetica", 6.5)
                self.drawString(MARGIN, 14, stamp)
                super().showPage()
            super().save()

    return NumberedCanvas


def _letterhead(org, kind: str, number: str, styles):
    """Logo and organisation on the left, what the document is on the right,
    and the brand rule under both."""
    from reportlab.platypus import Image, Paragraph, Table, TableStyle

    BrandRule, _ = _flowables()
    contact = " · ".join(_esc(part) for part in [org.address, org.phone, org.email] if part)
    left = [Paragraph(_esc(org.name), styles["org"])]
    if contact:
        left.append(Paragraph(contact, styles["small"]))
    if getattr(org, "registration", ""):
        left.append(Paragraph(_esc(org.registration), styles["small"]))
    right = [Paragraph(kind.upper(), styles["kind"]), Paragraph(_esc(number), styles["docno"]),
             Paragraph(f"Issued {_long(date.today())}", styles["small-right"])]

    logo = logo_path()
    cells, widths = [left, right], [CONTENT_W - 170, 170]
    if logo:
        from reportlab.lib.utils import ImageReader

        w, h = ImageReader(str(logo)).getSize()
        logo_w = 38 * w / h
        cells = [Image(str(logo), width=logo_w, height=38), left, right]
        widths = [logo_w + 12, CONTENT_W - 170 - logo_w - 12, 170]
    head = Table([cells], colWidths=widths)
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [head, BrandRule(CONTENT_W)]


def _addressee(eyebrow: str, lines: list[str], facts: list[tuple[str, str]], styles):
    """Who the statement is for, and the handful of facts that identify it."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    left = [Paragraph(eyebrow.upper(), styles["eyebrow"]), Paragraph(_esc(lines[0]), styles["name"])]
    left += [Paragraph(_esc(line), styles["small"]) for line in lines[1:] if line]
    right = Table([[Paragraph(_esc(k), styles["label"]), Paragraph(v, styles["value"])]
                   for k, v in facts], colWidths=[86, 134])
    right.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, HexColor(LINE)),
    ]))
    block = Table([[left, right]], colWidths=[CONTENT_W - 220, 220])
    block.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return block


def _hero(cells: list[tuple], styles):
    """The dark band: the three or four figures the reader came for.

    Each cell is (label, value, sub, tone); the first is set larger. `tone`
    "alert" colours a figure that needs attention, such as an overdue amount.
    """
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    labels, values, subs = [], [], []
    for index, (label, value, sub, tone) in enumerate(cells):
        colour = ALERT_ON_DARK if tone == "alert" else ON_DARK
        labels.append(Paragraph(label.upper(), styles["hero-label"]))
        values.append(Paragraph(f'<font color="{colour}">{_esc(value)}</font>',
                                styles["hero-value-lg" if index == 0 else "hero-value"]))
        subs.append(Paragraph(_esc(sub) if sub else "", styles["hero-sub"]))
    first = 178
    rest = (CONTENT_W - first) / max(len(cells) - 1, 1)
    table = Table([labels, values, subs], colWidths=[first] + [rest] * (len(cells) - 1))
    commands = [
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(BAND)),
        ("ROUNDEDCORNERS", [10, 10, 10, 10]),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 13), ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 1), ("BOTTOMPADDING", (0, 1), (-1, 1), 1),
        ("TOPPADDING", (0, 2), (-1, 2), 1), ("BOTTOMPADDING", (0, 2), (-1, 2), 13),
    ]
    for col in range(1, len(cells)):
        commands.append(("LINEBEFORE", (col, 0), (col, -1), 0.6, HexColor(PANEL_LINE)))
    table.setStyle(TableStyle(commands))
    return table


def _panel(title: str, pairs: list[tuple[str, str]], styles, total: tuple[str, str] | None = None):
    """A bordered card of label / value facts, with an optional ruled total."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    rows = [[Paragraph(title.upper(), styles["section"]), ""]]
    rows += [[Paragraph(_esc(k), styles["label"]), Paragraph(v, styles["value"])] for k, v in pairs]
    if total:
        rows.append([Paragraph(f"<b>{_esc(total[0])}</b>", styles["label"]),
                     Paragraph(f"<b>{total[1]}</b>", styles["value"])])
    table = Table(rows, colWidths=[PANEL_W * 0.46 - 12, PANEL_W * 0.54 - 12])
    commands = [
        ("BOX", (0, 0), (-1, -1), 0.6, HexColor(LINE)),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("SPAN", (0, 0), (1, 0)),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("TOPPADDING", (0, 1), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 1), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, HexColor(ACCENT)),
    ]
    if total:
        commands += [("LINEABOVE", (0, -1), (-1, -1), 0.8, HexColor(INK)),
                     ("TOPPADDING", (0, -1), (-1, -1), 5)]
    table.setStyle(TableStyle(commands))
    return table


def _side_by_side(left, right):
    from reportlab.platypus import Table, TableStyle

    pair = Table([[left, right]], colWidths=[PANEL_W, PANEL_W], spaceBefore=GAP)
    pair.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), GAP),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return pair


def _section(title: str, aside: str, styles):
    """A heading over the transactions, with the period on the right."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    row = Table([[Paragraph(title.upper(), styles["section"]), Paragraph(_esc(aside), styles["section-right"])]],
                colWidths=[CONTENT_W / 2, CONTENT_W / 2], spaceBefore=16)
    row.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LINEBELOW", (0, 0), (-1, -1), 0.6, HexColor(LINE)),
    ]))
    return row


def _describe(line, styles):
    """The description cell: what happened, with the narration under it in small
    type, and the whole thing greyed when the line was later reversed."""
    from reportlab.platypus import Paragraph

    text = _esc(line["description"])
    if line.get("narration"):
        text += f'<br/><font size="7" color="{MUTED}">{_esc(line["narration"])}</font>'
    return Paragraph(text, styles["cell-muted" if line.get("reversed") else "cell"])


def _lines_table(header: list[str], rows: list[list], widths: list[float], numeric: set[int],
                 styles, totals: list | None = None, muted_rows: set[int] = frozenset()):
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    head = [Paragraph(h.upper(), styles["head-right" if i in numeric else "head"])
            for i, h in enumerate(header)]
    body = [[Paragraph(_esc(cell) if cell else "", styles["cell"])
             if isinstance(cell, str) and i not in numeric else cell
             for i, cell in enumerate(row)] for row in rows]
    data = [head] + body + ([totals] if totals else [])
    table = Table(data, colWidths=widths, repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(HEADER_FILL)),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 1), (-1, -1), HexColor(INK)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, HexColor(LINE)),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (0, -1), 8), ("RIGHTPADDING", (-1, 0), (-1, -1), 8),
    ]
    for col in numeric:
        commands.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    for index in range(1, len(body) + 1):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-1, index), HexColor(ZEBRA)))
        if index - 1 in muted_rows:
            commands.append(("TEXTCOLOR", (0, index), (-1, index), HexColor(MUTED)))
    if totals:
        commands += [("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8),
                     ("LINEABOVE", (0, -1), (-1, -1), 0.9, HexColor(INK)),
                     ("TOPPADDING", (0, -1), (-1, -1), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]
    table.setStyle(TableStyle(commands))
    return table


def _notes(title: str, paragraphs: list[str], styles, footer: str | None = None):
    """The small print, in one soft box at the foot of the statement: numbered,
    so a line can be referred to, with an unnumbered closing line (how to reach us)."""
    from reportlab.lib.colors import HexColor
    from reportlab.platypus import Paragraph, Table, TableStyle

    rows = [[Paragraph(title, styles["note-head"]), ""]]
    rows += [[Paragraph(str(i), styles["note"]), Paragraph(p, styles["note"])]
             for i, p in enumerate(paragraphs, start=1)]
    commands = [
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(ZEBRA)),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("SPAN", (0, 0), (1, 0)),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 1), (0, -1), 0), ("LEFTPADDING", (1, 1), (1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]
    if footer:
        rows.append([Paragraph(footer, styles["note"]), ""])
        commands += [("SPAN", (0, -1), (1, -1)), ("TOPPADDING", (0, -1), (-1, -1), 6)]
    box = Table(rows, colWidths=[28, CONTENT_W - 28], spaceBefore=12)
    box.setStyle(TableStyle(commands))
    return box


def _declarations(org, kind: str, period_end) -> list[str]:
    """What the institution declares at the foot of a statement.

    Settings may carry the institution's own wording, one declaration per line,
    and when it does that is printed and nothing else. Otherwise the standard set
    below: each line states something the system actually does (a settlement
    rebate, penalties under the agreement), never a regulator or a licence, which
    only the institution can name - that belongs in the registration line.
    """
    own = [line.strip() for line in (getattr(org, "statement_declarations", "") or "").splitlines()]
    own = [_esc(line) for line in own if line]
    if own:
        return own
    name = _esc(org.name)
    until = _long(period_end)
    common_head = [
        "Please check this statement carefully and tell us within 30 days of the issue date if "
        "anything on it appears to be wrong. After that it is taken as correct.",
    ]
    common_tail = [
        f"Payments received after {until} are not shown. Only an official receipt issued by {name} "
        "is proof of payment; do not pay cash to anyone without one.",
        "This statement is confidential and is intended only for the person named on it. It is "
        "produced by computer and is valid without a signature.",
    ]
    if kind == "loan":
        return common_head + [
            "Interest, fees and penalties are charged as set out in your loan agreement and our "
            "tariff of charges, which is available at any branch.",
            "Overdue instalments attract the penalties set out in your agreement, and a loan in "
            "arrears may affect your ability to borrow again.",
            "You may settle the loan early at any time; interest on instalments not yet due is not "
            "charged. Ask any branch for a settlement figure.",
        ] + common_tail
    return common_head + [
        "Interest is credited at the rate shown, under the terms of the account. Funds held against "
        "a loan, or a minimum balance the account requires, are not available for withdrawal.",
    ] + common_tail


def _contact_line(org) -> str | None:
    ways = [f"call {_esc(org.phone)}" if org.phone else None,
            f"write to {_esc(org.email)}" if org.email else None]
    ways = [w for w in ways if w]
    return f"Questions about this statement? Please {' or '.join(ways)}." if ways else None


def _pdf_response(story, name: str, footer: str, landscape_page: bool = False) -> HttpResponse:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate

    buffer = io.BytesIO()
    page = landscape(A4) if landscape_page else A4
    doc = SimpleDocTemplate(buffer, pagesize=page, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=30, bottomMargin=52, title=name, author=_org().name)
    doc.build(story, canvasmaker=_numbered_canvas(footer))
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
    return response


def _humanise(value) -> str:
    text = str(value or "").replace("_", " ")
    return text[:1].upper() + text[1:]


def loan_statement_pdf(data: dict) -> HttpResponse:
    from reportlab.platypus import KeepTogether, Spacer

    org = _org()
    cur = org.currency
    styles = _styles()
    _, Progress = _flowables()

    def money(value) -> str:
        return f"{cur} {_money(value)}"

    arrears = Decimal(str(data["arrears_amount"] or 0))
    outstanding = Decimal(str(data["total_outstanding"] or 0))
    repayable = Decimal(str(data["total_repayable"] or 0))
    paid = Decimal(str(data["total_paid"] or 0))
    status = _humanise(data["status"])

    story = _letterhead(org, "Loan statement", data["loan_no"], styles)
    story.append(_addressee(
        "Statement for",
        [data["borrower"], f"Member {data['borrower_no']} · ID {data['national_id']}",
         data.get("phone"), data.get("address"),
         f"Employer: {data['employer']}" if data.get("employer") else None],
        [("Period", _esc(_period(data))), ("Product", _esc(data["product"])),
         ("Branch", _esc(data.get("branch"))), ("Status", _esc(status))]
        + ([("Reference", _esc(data["external_ref"]))] if data.get("external_ref") else []),
        styles))

    # What to pay next: the coming instalment; failing that, whatever is overdue;
    # and only when neither exists has nothing more fallen due.
    if data["next_due_date"]:
        next_cell = ("Next instalment", money(data["next_due_amount"]),
                     f"due {_mid(data['next_due_date'])}", None)
    elif arrears > 0:
        next_cell = ("To pay now", money(arrears), "every instalment has fallen due", "alert")
    else:
        next_cell = ("Next instalment", "-", "Nothing more falls due", None)
    if arrears > 0:
        arrears_sub = f"{data['days_in_arrears']} days overdue - please settle"
    else:
        arrears_sub = "Up to date"
    story.append(_hero([
        ("Total outstanding", money(outstanding),
         "principal, interest to fall due, penalties and charges" if outstanding > 0 else "Nothing owing",
         None),
        next_cell,
        ("In arrears", money(arrears), arrears_sub, "alert" if arrears > 0 else None),
        ("Paid to date", money(paid), f"of {money(repayable)} repayable", None),
    ], styles))

    done, total = data["instalments_paid"], data["instalments_total"]
    fraction = (paid / repayable) if repayable > 0 else Decimal("0")
    story.append(Spacer(1, 8))
    story.append(Progress(
        CONTENT_W, fraction,
        f"{done} of {total} instalment{'s' if total != 1 else ''} paid",
        f"{min(100, int(fraction * 100))}% of {money(repayable)} repaid"))

    story.append(_side_by_side(
        _panel("The loan", [
            ("Principal advanced", money(data["principal"])),
            ("Interest rate", f"{_pct(data['rate_pct'])} a month, {_esc(str(data['rate_method']).lower())}"),
            ("Term", f"{_esc(data['term'])} {_esc(data['term_unit'])}"),
            ("Instalment", money(data["instalment_amount"])),
            ("APR, fees included", f"{_pct(data['apr_pct'])} a year" if data["apr_pct"] is not None else "-"),
            ("Disbursed", _esc(_long(data["disbursement_date"]))),
            ("Maturity", _esc(_long(data["maturity_date"]))),
        ], styles),
        _panel("Where it stands", [
            ("Principal outstanding", money(data["principal_outstanding"])),
            ("Interest still to fall due", money(data["interest_outstanding"])),
            ("Penalties", money(data["penalties_outstanding"])),
            ("Charges", money(data["charges_outstanding"])),
            ("In arrears", f"{money(arrears)}" + (f" ({data['days_in_arrears']} days)" if arrears > 0 else "")),
            ("Paid to date", money(paid)),
        ], styles, total=("Total outstanding", money(outstanding))),
    ))

    rows, muted = [], set()
    if data.get("period_start"):
        rows.append(["", "Balance brought forward", "", "", "", "", _money(data["opening_balance"])])
    for line in data["lines"]:
        if line.get("reversed"):
            muted.add(len(rows))
        rows.append([_date(line["date"]), _describe(line, styles), line["reference"] or "",
                     _money(line["debit"]) if line["debit"] else "",
                     _money(line["credit"]) if line["credit"] else "",
                     _money(line["interest"]) if line["interest"] else "",
                     _money(line["balance"])])
    interest_paid = sum((Decimal(str(line["interest"] or 0)) for line in data["lines"]), Decimal("0"))
    story.append(_section("Transactions", f"{len(data['lines'])} in the period {_period(data)}", styles))
    story.append(_lines_table(
        ["Date", "Description", "Reference", "Charged", "Paid", "Interest", "Balance"],
        rows, [62, 163, 66, 58, 58, 58, 58.28], {3, 4, 5, 6}, styles,
        totals=["", "Totals for the period", "", _money(data["total_charged"]),
                _money(data["total_paid_in_period"]), _money(interest_paid),
                _money(data["closing_balance"])],
        muted_rows=muted))

    notes = [
        "<b>The balance</b> is the principal, penalties and charges you owe. Interest is charged "
        "on each instalment as it falls due and is shown in its own column when paid; the interest "
        "still to fall due is included in the total outstanding above.",
        "<b>Payments</b> are applied to penalties first, then charges, then interest, then principal, "
        "oldest instalment first. A reversed payment is shown in grey with its reversal beside it, so "
        "every receipt you hold is accounted for.",
    ] + _declarations(org, "loan", data["period_end"])
    # One block of small print: on the page if it fits, else overleaf, never split.
    story.append(KeepTogether(_notes("Notes and declarations", notes, styles, footer=_contact_line(org))))
    return _pdf_response(story, f"statement_{data['loan_no']}",
                         f"{org.name} · loan statement {data['loan_no']} · {data['borrower']}")


def savings_statement_pdf(data: dict) -> HttpResponse:
    from reportlab.platypus import KeepTogether

    org = _org()
    cur = data.get("currency") or org.currency   # the account's own currency
    styles = _styles()

    def money(value) -> str:
        return f"{cur} {_money(value)}"

    balance = Decimal(str(data["balance"] or 0))
    available = Decimal(str(data["available_balance"] or 0))
    held = balance - available
    status = _humanise(data["status"])

    story = _letterhead(org, "Savings statement", data["account_no"], styles)
    story.append(_addressee(
        "Statement for",
        [data["borrower"], f"Member {data['borrower_no']} · ID {data['national_id']}",
         data.get("phone"), data.get("address")],
        [("Period", _esc(_period(data))), ("Product", _esc(data["product"])),
         ("Branch", _esc(data.get("branch"))), ("Status", _esc(status))],
        styles))
    interest_credited = sum((Decimal(str(line["money_in"] or 0)) for line in data["lines"]
                             if line["type"] == "interest"), Decimal("0"))
    story.append(_hero([
        ("Balance", money(balance), f"as at {_mid(data['period_end'])}", None),
        ("Available", money(available),
         f"to withdraw; {money(held)} held" if held > 0 else "to withdraw, all of it", None),
        ("Interest", f"{_pct(data['interest_rate_pct_pa'])} a year", "credited to the account", None),
        ("Opened", _mid(data["opened_on"]), status, None),
    ], styles))

    story.append(_side_by_side(
        _panel("The account", [
            ("Account number", _esc(data["account_no"])),
            ("Product", _esc(data["product"])),
            ("Interest rate", f"{_pct(data['interest_rate_pct_pa'])} a year"),
            ("Opened", _esc(_long(data["opened_on"]))),
            ("Branch", _esc(data.get("branch"))),
        ], styles),
        _panel("This period", [
            ("Opening balance", money(data["opening_balance"])),
            ("Money in", money(data["total_in"])),
            ("of which interest", money(interest_credited)),
            ("Money out", money(data["total_out"])),
            ("Transactions", str(len(data["lines"]))),
        ], styles, total=("Closing balance", money(data["closing_balance"]))),
    ))

    rows, muted = [], set()
    if data.get("period_start"):
        rows.append(["", "Balance brought forward", "", "", "", _money(data["opening_balance"])])
    for line in data["lines"]:
        if line.get("reversed"):
            muted.add(len(rows))
        rows.append([_date(line["date"]), _describe(line, styles), line["reference"] or "",
                     _money(line["money_in"]) if line["money_in"] else "",
                     _money(line["money_out"]) if line["money_out"] else "",
                     _money(line["balance"])])
    story.append(_section("Transactions", f"{len(data['lines'])} in the period {_period(data)}", styles))
    story.append(_lines_table(
        ["Date", "Description", "Reference", "Money in", "Money out", "Balance"],
        rows, [62, 199, 80, 60, 60, 62.28], {3, 4, 5}, styles,
        totals=["", "Totals for the period", "", _money(data["total_in"]),
                _money(data["total_out"]), _money(data["closing_balance"])],
        muted_rows=muted))

    notes = [
        "<b>The balance</b> after each line is what the account held at the end of that day. "
        "A reversed entry is shown in grey with its reversal beside it.",
    ] + _declarations(org, "savings", data["period_end"])
    story.append(KeepTogether(_notes("Notes and declarations", notes, styles, footer=_contact_line(org))))
    return _pdf_response(story, f"statement_{data['account_no']}",
                         f"{org.name} · savings statement {data['account_no']} · {data['borrower']}")
