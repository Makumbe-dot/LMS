"""Bank and mobile-money reconciliation: the statement against the ledger.

A repayment typed against the wrong loan, a deposit nobody posted, a bank charge
nobody booked, a transfer that never arrived: none of these breaks a trial
balance, because the books agree with themselves. They only show up against the
bank's own record, which is what this compares.

Each statement line is matched to one journal entry that moved the same amount
through account 1000. Entries rather than transactions, because every way money
moves ends in exactly one, so one rule covers repayments, savings, funding,
capital and manual journals alike.

Which entries a statement can match:

  * never a cash movement - cash goes through a till, not a bank account;
  * never a till difference - that is cash too;
  * a bank statement takes bank transfers and payroll deductions (employers
    remit by transfer); a mobile-money statement takes mobile money;
  * anything without a method (a manual journal, a counter charge) can match
    either, because only the person who posted it knew which account it hit.

Auto-match pairs a line with an entry of exactly the same amount dated within
three days, and only when there is a single candidate - or a single one whose
reference agrees. Anything ambiguous is left for a person. A match is never made
on a different amount: a difference is something to book (a bank charge, a
short remittance), not something to absorb.
"""
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.db.models import CharField, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    BankStatement,
    JournalEntry,
    PaymentMethod,
    StatementLine,
    StatementLineStatus,
    User,
)
from . import ledger
from .amortisation import q
from .loans import next_number

ZERO = Decimal("0")
AUTO_WINDOW_DAYS = 3
MANUAL_WINDOW_DAYS = 14
ELIGIBLE = {
    PaymentMethod.BANK_TRANSFER: {PaymentMethod.BANK_TRANSFER, PaymentMethod.SALARY_DEDUCTION},
    PaymentMethod.MOBILE_MONEY: {PaymentMethod.MOBILE_MONEY},
}
NOT_BANK_SOURCES = {"till_variance"}

_ALIASES = {
    "date": "date", "txn_date": "date", "transaction date": "date", "value date": "date",
    "posting date": "date",
    "description": "description", "narration": "description", "details": "description",
    "particulars": "description",
    "reference": "reference", "ref": "reference", "transaction id": "reference",
    "amount": "amount",
    "money_in": "money_in", "money in": "money_in", "credit": "money_in", "deposit": "money_in",
    "money_out": "money_out", "money out": "money_out", "debit": "money_out",
    "withdrawal": "money_out",
}


# ---------------------------------------------------------------- reading a file
def _parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{raw}' is not a date this importer reads (YYYY-MM-DD or DD/MM/YYYY)")


def _parse_amount(raw: str) -> Decimal | None:
    raw = (raw or "").strip().replace(",", "").replace(" ", "")
    if not raw:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    try:
        value = Decimal(raw.strip("()"))
    except InvalidOperation:
        raise ValueError(f"'{raw}' is not an amount")
    return -value if negative else value


def parse(file_bytes: bytes) -> list[dict]:
    """A bank or wallet export as rows of (date, description, reference, signed amount).

    Takes either one signed `amount` column or a money-in / money-out pair
    (credit / debit from the bank's side). Dates are ISO or day-first, as banks in
    the region export them.
    """
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BusinessRuleError("The file is empty.")
    headers = {}
    for name in reader.fieldnames:
        key = _ALIASES.get((name or "").strip().lower())
        if key and key not in headers:
            headers[key] = name
    if "date" not in headers or not ({"amount"} <= set(headers)
                                     or {"money_in", "money_out"} & set(headers)):
        raise BusinessRuleError(
            "The file needs a date column and either an amount column or money in / money out "
            "columns.")

    rows = []
    for index, raw in enumerate(reader, start=2):
        if not any((value or "").strip() for value in raw.values()):
            continue  # trailing blank rows are common in bank exports
        try:
            on = _parse_date(raw.get(headers["date"]) or "")
            if "amount" in headers:
                amount = _parse_amount(raw.get(headers["amount"]))
            else:
                money_in = _parse_amount(raw.get(headers.get("money_in", ""), "")) or ZERO
                money_out = _parse_amount(raw.get(headers.get("money_out", ""), "")) or ZERO
                amount = money_in - abs(money_out)
            if not amount:
                raise ValueError("no amount")
        except ValueError as exc:
            raise BusinessRuleError(f"Row {index}: {exc}")
        rows.append({
            "line_no": index,
            "date": on,
            "description": (raw.get(headers.get("description", ""), "") or "").strip()[:255] or None,
            "reference": (raw.get(headers.get("reference", ""), "") or "").strip()[:120] or None,
            "amount": q(amount),
        })
    if not rows:
        raise BusinessRuleError("The file has a header row but no transactions.")
    if len(rows) > 10000:
        raise BusinessRuleError("The file has more than 10,000 rows; split it by month.")
    return rows


@db_transaction.atomic
def create_statement(user: User | None, rows: list[dict], *, account_name: str, channel: str,
                     file_name: str | None = None, opening_balance=None,
                     closing_balance=None) -> BankStatement:
    if channel not in ELIGIBLE:
        raise BusinessRuleError("A statement is from a bank account or a mobile-money wallet")
    statement = BankStatement.objects.create(
        statement_no=next_number("STM"), account_name=account_name.strip(), channel=channel,
        period_start=min(r["date"] for r in rows), period_end=max(r["date"] for r in rows),
        opening_balance=opening_balance, closing_balance=closing_balance, file_name=file_name,
        uploaded_by=user,
    )
    StatementLine.objects.bulk_create([
        StatementLine(statement=statement, line_no=r["line_no"], txn_date=r["date"],
                      description=r["description"], reference=r["reference"], amount=r["amount"])
        for r in rows
    ])
    return statement


# ---------------------------------------------------------------- the book side
def entries(start: date, end: date, channel: str, *, unmatched_only: bool = True):
    """Ledger entries that moved bank money in a window, with their cash effect,
    method and reference annotated: what a statement for `channel` could match."""
    money = DecimalField(max_digits=18, decimal_places=2)
    bank = Q(lines__account__code=ledger.CODES["bank"])
    qs = (JournalEntry.objects
          .filter(entry_date__range=(start, end))
          .exclude(source__in=NOT_BANK_SOURCES)
          .annotate(
              cash=(Coalesce(Sum("lines__debit", filter=bank, output_field=money),
                             Value(ZERO, output_field=money))
                    - Coalesce(Sum("lines__credit", filter=bank, output_field=money),
                               Value(ZERO, output_field=money))),
              method=Coalesce("transaction__method", "savings_transaction__method",
                              "facility_transaction__method", "capital_transaction__method",
                              output_field=CharField()),
              ref=Coalesce("transaction__reference", "savings_transaction__reference",
                           "facility_transaction__reference", "capital_transaction__reference",
                           "manual_journal__reference", output_field=CharField()),
          )
          .exclude(cash=0)
          .filter(Q(method__isnull=True) | Q(method__in=ELIGIBLE[channel]))
          .select_related("loan")
          .order_by("entry_date", "id"))
    if unmatched_only:
        qs = qs.filter(statement_line__isnull=True)
    return qs


def _ref_agrees(line: StatementLine, entry) -> bool:
    theirs = " ".join(filter(None, [line.reference, line.description])).lower()
    ours = (entry.ref or "").strip().lower()
    return bool(ours) and len(ours) >= 4 and ours in theirs


# ---------------------------------------------------------------- matching
@db_transaction.atomic
def auto_match(statement: BankStatement, user: User | None) -> dict:
    lines = list(statement.lines.filter(status=StatementLineStatus.UNMATCHED))
    if not lines:
        return {"matched": 0, "left": 0}
    pool = list(entries(statement.period_start - timedelta(days=AUTO_WINDOW_DAYS),
                        statement.period_end + timedelta(days=AUTO_WINDOW_DAYS),
                        statement.channel))
    taken: set[int] = set()
    matched = 0
    for line in lines:
        window = timedelta(days=AUTO_WINDOW_DAYS)
        candidates = [e for e in pool if e.id not in taken and q(e.cash) == line.amount
                      and abs(e.entry_date - line.txn_date) <= window]
        if len(candidates) > 1:
            # Narrow by reference when any agrees, else by the exact date. Still more
            # than one - two identical repayments on one day - is left for a person.
            by_ref = [e for e in candidates if _ref_agrees(line, e)]
            candidates = by_ref or [e for e in candidates if e.entry_date == line.txn_date]
        if len(candidates) != 1:
            continue
        entry = candidates[0]
        taken.add(entry.id)
        _record_match(line, entry, user, auto=True)
        matched += 1
    return {"matched": matched,
            "left": statement.lines.filter(status=StatementLineStatus.UNMATCHED).count()}


def _record_match(line: StatementLine, entry: JournalEntry, user: User | None,
                  auto: bool = False) -> None:
    line.journal_entry = entry
    line.status = StatementLineStatus.MATCHED
    line.matched_by = user
    line.matched_at = timezone.now()
    line.auto_matched = auto
    line.note = None
    line.save(update_fields=["journal_entry", "status", "matched_by", "matched_at",
                             "auto_matched", "note"])


def candidates(line: StatementLine) -> list:
    """Entries a person could match this line to: same amount, within two weeks,
    not already matched, nearest date first."""
    window = timedelta(days=MANUAL_WINDOW_DAYS)
    found = [e for e in entries(line.txn_date - window, line.txn_date + window,
                                line.statement.channel)
             if q(e.cash) == line.amount]
    return sorted(found, key=lambda e: (abs((e.entry_date - line.txn_date).days),
                                        not _ref_agrees(line, e)))


@db_transaction.atomic
def match(line: StatementLine, entry_id: int, user: User) -> StatementLine:
    if line.status == StatementLineStatus.MATCHED:
        raise BusinessRuleError(f"Line {line.line_no} is already matched; unmatch it first")
    entry = next((e for e in entries(date.min, date.max, line.statement.channel)
                  .filter(pk=entry_id)), None)
    if entry is None:
        taken = JournalEntry.objects.filter(pk=entry_id, statement_line__isnull=False).exists()
        raise BusinessRuleError(
            "That entry is already matched to another statement line" if taken else
            "That entry did not move money through the bank on this channel")
    if q(entry.cash) != line.amount:
        raise BusinessRuleError(
            f"The entry moved {q(entry.cash)} and the statement line {line.amount}. A match must "
            f"be exact: book the difference (a bank charge, a short remittance) and match each "
            f"part.")
    _record_match(line, entry, user)
    return line


@db_transaction.atomic
def unmatch(line: StatementLine) -> StatementLine:
    line.journal_entry = None
    line.status = StatementLineStatus.UNMATCHED
    line.matched_by = None
    line.matched_at = None
    line.auto_matched = False
    line.note = None
    line.save(update_fields=["journal_entry", "status", "matched_by", "matched_at",
                             "auto_matched", "note"])
    return line


@db_transaction.atomic
def ignore(line: StatementLine, user: User, note: str) -> StatementLine:
    """Set a line aside on purpose: a transfer between the institution's own accounts,
    say, which is not a ledger movement at all. The reason stays on the line."""
    if line.status == StatementLineStatus.MATCHED:
        raise BusinessRuleError(f"Line {line.line_no} is matched; unmatch it first")
    line.status = StatementLineStatus.IGNORED
    line.note = note
    line.matched_by = user
    line.matched_at = timezone.now()
    line.save(update_fields=["status", "note", "matched_by", "matched_at"])
    return line


def journal_for(line: StatementLine, account_code: str, user: User):
    """Prepare the journal a line the books do not know about calls for.

    A bank charge of -2.50 against 6600 becomes Dr 6600 / Cr 1000 for 2.50, carrying
    the bank's reference; once an administrator posts it, auto-match pairs the two.
    """
    from . import journals

    if line.status != StatementLineStatus.UNMATCHED:
        raise BusinessRuleError(f"Line {line.line_no} is {line.get_status_display().lower()}")
    amount = abs(line.amount)
    bank = ledger.CODES["bank"]
    lines = ([{"account_code": account_code, "debit": amount}, {"account_code": bank, "credit": amount}]
             if line.amount < 0 else
             [{"account_code": bank, "debit": amount}, {"account_code": account_code, "credit": amount}])
    return journals.prepare(
        user, entry_date=line.txn_date,
        narration=f"{line.statement.account_name}: {line.description or 'statement line'}",
        lines=lines, reference=line.reference or f"{line.statement.statement_no}/{line.line_no}")


# ---------------------------------------------------------------- the picture
def summary(statement: BankStatement) -> dict:
    lines = list(statement.lines.all())
    by_status = {s: [l for l in lines if l.status == s] for s in StatementLineStatus.values}
    money_in = q(sum((l.amount for l in lines if l.amount > 0), ZERO))
    money_out = q(-sum((l.amount for l in lines if l.amount < 0), ZERO))
    adds_up = None
    if statement.opening_balance is not None and statement.closing_balance is not None:
        adds_up = q(statement.opening_balance + money_in - money_out) == q(statement.closing_balance)
    return {
        "lines": len(lines),
        "matched": len(by_status[StatementLineStatus.MATCHED]),
        "unmatched": len(by_status[StatementLineStatus.UNMATCHED]),
        "ignored": len(by_status[StatementLineStatus.IGNORED]),
        "money_in": money_in,
        "money_out": money_out,
        "unmatched_amount": q(sum((l.amount for l in by_status[StatementLineStatus.UNMATCHED]),
                                  ZERO)),
        "adds_up": adds_up,
        "reconciled": not by_status[StatementLineStatus.UNMATCHED],
    }


def entry_row(entry) -> dict:
    return {
        "id": entry.id, "entry_no": entry.entry_no, "entry_date": entry.entry_date,
        "narration": entry.narration, "source": entry.source, "amount": q(entry.cash),
        "method": entry.method, "reference": entry.ref,
        "loan_no": entry.loan.loan_no if entry.loan_id else None,
    }
