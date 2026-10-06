"""Payroll returns: what an employer deducted, against what it was asked to.

The deduction schedule (reports.payroll_deduction) goes to the employer's payroll
office. What comes back is a list of what was actually taken from each salary.
Checking it builds a run: one line per loan on the schedule, set against what the
file says was deducted for that loan, plus a line for anything in the file that
was not on the schedule. Nothing is posted until someone posts the run; then each
deduction becomes a salary-deduction repayment dated the day the money arrived,
and the shortfalls are what is left to chase.

A file row finds its loan by loan number when it has one, then by employee number
within the employer, then by national ID.
"""
import csv
import io
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from ..audit import audit
from ..exceptions import BusinessRuleError
from ..models import (
    Loan,
    LoanStatus,
    PaymentMethod,
    PayrollLineStatus,
    PayrollRun,
    PayrollRunLine,
    PayrollRunStatus,
    User,
)
from .amortisation import q
from .notifications import queue_receipt
from .reports import payroll_deduction
from .repayments import post_repayment

ZERO = Decimal("0")

COLUMNS = {
    "loan_no": ("loan_no", "loan", "loan_number"),
    "employee_no": ("employee_no", "employee", "emp_no", "staff_no", "payroll_no",
                    "employee_number"),
    "national_id": ("national_id", "id_no", "id_number", "nationalid"),
    "amount": ("amount", "deducted", "deduction", "amount_deducted"),
    "name": ("name", "employee_name", "full_name", "borrower"),
}


def _key(heading: str) -> str:
    return re.sub(r"[^a-z_]", "", heading.strip().lower().replace(" ", "_"))


def parse(file_bytes: bytes) -> list[dict]:
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BusinessRuleError("The file has no heading row")
    headings = {_key(h): h for h in reader.fieldnames if h}
    mapping = {}
    for field, names in COLUMNS.items():
        for name in names:
            if name in headings:
                mapping[field] = headings[name]
                break
    if "amount" not in mapping:
        raise BusinessRuleError("The file needs an amount column")
    if not {"loan_no", "employee_no", "national_id"} & set(mapping):
        raise BusinessRuleError("The file needs a loan number, employee number or national ID "
                                "column")
    rows = []
    for line, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        values = {f: (row.get(h) or "").strip() for f, h in mapping.items()}
        try:
            amount = q(Decimal(values["amount"].replace(",", "") or "0"))
        except InvalidOperation:
            raise BusinessRuleError(f"Line {line}: '{values['amount']}' is not an amount")
        if amount < 0:
            raise BusinessRuleError(f"Line {line}: a deduction cannot be negative")
        rows.append({**values, "line": line, "amount": amount})
    return rows


def _schedule(employer: str, start: date, end: date) -> dict[int, dict]:
    """What was asked of this employer, per loan."""
    expected: dict[int, dict] = {}
    for row in payroll_deduction(start, end, employer):
        entry = expected.setdefault(row["loan_id"], {
            "employee_no": row["employee_no"] if row["employee_no"] != "-" else None,
            "name": row["borrower"], "expected": ZERO})
        entry["expected"] += row["deduct"]
    return expected


def _find(row: dict, employer: str, scheduled: set[int]) -> tuple[Loan | None, str | None]:
    """The loan a file row is for; the loans on this schedule come first."""
    if row.get("loan_no"):
        loan = Loan.objects.filter(loan_no__iexact=row["loan_no"]).first()
        return (loan, None) if loan else (None, f"No loan {row['loan_no']}")
    filters = []
    if row.get("employee_no"):
        filters.append({"borrower__employee_no__iexact": row["employee_no"],
                        "borrower__employer__iexact": employer})
    if row.get("national_id"):
        filters.append({"borrower__national_id__iexact": row["national_id"]})
    for criteria in filters:
        loans = list(Loan.objects.filter(status=LoanStatus.ACTIVE, **criteria))
        on_schedule = [loan for loan in loans if loan.id in scheduled]
        if len(on_schedule) == 1:
            return on_schedule[0], None
        if len(loans) == 1:
            return loans[0], None
        if len(loans) > 1:
            return None, "More than one active loan; give the loan number"
    who = row.get("employee_no") or row.get("national_id")
    return None, f"No active loan for {who}"


@transaction.atomic
def check(employer: str, start: date, end: date, received_on: date, file_bytes: bytes,
          user: User, file_name: str | None = None, reference: str | None = None) -> PayrollRun:
    """Build a draft run from the employer's file. Posts nothing."""
    employer = (employer or "").strip()
    if not employer:
        raise BusinessRuleError("Choose the employer the return is from")
    if end < start:
        raise BusinessRuleError("The period ends before it starts")
    rows = parse(file_bytes)
    expected = _schedule(employer, start, end)
    if not expected and not rows:
        raise BusinessRuleError(f"Nothing was due from {employer} in that period, and the "
                                f"file is empty")

    deducted: dict[int, Decimal] = defaultdict(lambda: ZERO)
    file_lines: dict[int, int] = {}
    unknown = []
    for row in rows:
        loan, problem = _find(row, employer, set(expected))
        if loan is None:
            unknown.append((row, problem))
            continue
        deducted[loan.id] += row["amount"]
        file_lines.setdefault(loan.id, row["line"])
        if loan.id not in expected:
            expected[loan.id] = {"employee_no": row.get("employee_no") or None,
                                 "name": row.get("name") or None, "expected": ZERO}

    run = PayrollRun.objects.create(employer=employer, period_start=start, period_end=end,
                                    received_on=received_on, reference=reference,
                                    file_name=(file_name or "")[:200] or None, created_by=user)
    loans = Loan.objects.in_bulk(list(expected))
    lines = []
    for loan_id, entry in expected.items():
        loan = loans[loan_id]
        want, got = q(entry["expected"]), q(deducted.get(loan_id, ZERO))
        owed = loan.total_outstanding if loan.status == LoanStatus.ACTIVE else ZERO
        if got > owed:
            state, note = PayrollLineStatus.OVER, f"Owes {owed}; {got - owed} to refund"
        elif got == 0:
            state, note = PayrollLineStatus.MISSED, None
        elif got < want:
            state, note = PayrollLineStatus.SHORT, f"{want - got} short"
        else:
            state, note = PayrollLineStatus.FULL, None
        lines.append(PayrollRunLine(run=run, loan=loan, employee_no=entry["employee_no"],
                                    name=entry["name"] or loan.borrower.full_name,
                                    file_line=file_lines.get(loan_id), expected=want,
                                    deducted=got, status=state, note=note))
    for row, problem in unknown:
        lines.append(PayrollRunLine(run=run, employee_no=row.get("employee_no") or None,
                                    name=row.get("name") or row.get("national_id") or None,
                                    file_line=row["line"], deducted=row["amount"],
                                    status=PayrollLineStatus.UNKNOWN, note=problem))
    PayrollRunLine.objects.bulk_create(lines)
    run.expected_total = q(sum((line.expected for line in lines), ZERO))
    run.deducted_total = q(sum((line.deducted for line in lines), ZERO))
    run.save(update_fields=["expected_total", "deducted_total"])
    audit(user, "check_payroll_return", "system", run.id,
          f"{employer} {start}..{end}: expected {run.expected_total}, "
          f"deducted {run.deducted_total}")
    return run


@transaction.atomic
def post(run_id: int, user: User) -> PayrollRun:
    """Post every deduction on a checked run as a repayment, all or nothing. A
    deduction above what the loan owes posts what it owes; the rest is for refund."""
    run = PayrollRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None:
        raise BusinessRuleError("Payroll run not found")
    if run.status != PayrollRunStatus.DRAFT:
        raise BusinessRuleError("This return has already been posted")
    total = ZERO
    for line in run.lines.select_related("loan").exclude(status=PayrollLineStatus.UNKNOWN):
        if line.deducted <= 0:
            continue
        loan = Loan.objects.select_for_update().get(pk=line.loan_id)
        amount = min(line.deducted, loan.total_outstanding)
        if amount <= 0:
            continue
        try:
            txn = post_repayment(loan, user, amount, run.received_on,
                                 PaymentMethod.SALARY_DEDUCTION,
                                 (run.reference or f"PAYROLL-{run.id}")[:100],
                                 f"{run.employer} payroll {run.period_start:%b %Y}")
        except BusinessRuleError as exc:
            raise BusinessRuleError(f"{loan.loan_no} (line {line.file_line or '-'}): {exc}")
        line.transaction = txn
        line.save(update_fields=["transaction"])
        total += txn.amount
        queue_receipt(loan, txn.amount, txn.id, txn.txn_date)
    run.status = PayrollRunStatus.POSTED
    run.posted_total = q(total)
    run.posted_by = user
    run.posted_at = timezone.now()
    run.save(update_fields=["status", "posted_total", "posted_by", "posted_at"])
    audit(user, "post_payroll_return", "system", run.id,
          f"{run.employer} {run.period_start:%Y-%m}: {run.posted_total} posted")
    return run


@transaction.atomic
def discard(run_id: int, user: User) -> None:
    run = PayrollRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None:
        raise BusinessRuleError("Payroll run not found")
    if run.status != PayrollRunStatus.DRAFT:
        raise BusinessRuleError("A posted return stays; reverse its repayments instead")
    audit(user, "discard_payroll_return", "system", run.id, f"{run.employer}")
    run.delete()


def summary(run: PayrollRun) -> dict:
    counts = defaultdict(int)
    shortfall = ZERO
    for line in run.lines.all():
        counts[line.status] += 1
        if line.status in (PayrollLineStatus.SHORT, PayrollLineStatus.MISSED):
            shortfall += line.shortfall
    # A string, as the serializers send money: JSON would otherwise make it a float.
    return {"counts": dict(counts), "shortfall": str(q(shortfall))}
