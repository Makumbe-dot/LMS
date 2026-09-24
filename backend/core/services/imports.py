"""Bulk repayment import.

Payroll bureaux and banks return deduction schedules as CSV. This parses such a
file, validates every line against the loan book, and either reports what would
happen (a dry run) or posts the batch.

Expected columns, case-insensitive, in any order:

    loan_no, amount, date, method, reference, narration

Only loan_no and amount are required. `date` defaults to today, `method` to
salary_deduction.
"""
import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Prefetch

from ..exceptions import BusinessRuleError
from ..models import Instalment, Loan, LoanStatus, PaymentMethod, User
from .amortisation import q
from .repayments import post_repayment

REQUIRED = {"loan_no", "amount"}
KNOWN = REQUIRED | {"date", "method", "reference", "narration"}
METHODS = {m.value for m in PaymentMethod}


def _loan_for(loan_no: str) -> Loan | None:
    return (Loan.objects
            .filter(loan_no__iexact=loan_no)
            .select_related("borrower", "product")
            .prefetch_related(Prefetch("instalments", queryset=Instalment.objects.order_by("number")))
            .first())


def parse(file_bytes: bytes) -> list[dict]:
    """Decode and normalise the CSV into a list of raw row dicts."""
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = file_bytes.decode("latin-1")
        except UnicodeDecodeError:
            raise BusinessRuleError("The file is not readable as text. Save it as CSV (UTF-8).")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BusinessRuleError("The file is empty.")

    headers = {(name or "").strip().lower(): name for name in reader.fieldnames}
    missing = REQUIRED - set(headers)
    if missing:
        raise BusinessRuleError(
            f"Missing column(s): {', '.join(sorted(missing))}. "
            f"Expected headers: {', '.join(sorted(KNOWN))}.")

    rows = []
    for index, raw in enumerate(reader, start=2):  # row 1 is the header
        rows.append({
            "line": index,
            **{key: (raw.get(source) or "").strip() for key, source in headers.items()
               if key in KNOWN},
        })
    if not rows:
        raise BusinessRuleError("The file has a header row but no data.")
    if len(rows) > 5000:
        raise BusinessRuleError("The file has more than 5000 rows; split it into smaller batches.")
    return rows


def validate(rows: list[dict]) -> dict:
    """Check every row against the loan book without changing anything."""
    checked = []
    seen_total = Decimal("0")
    valid = 0
    # Running tally per loan, so two lines against one loan cannot jointly overpay it.
    committed_per_loan: dict[int, Decimal] = {}

    for row in rows:
        result = {
            "line": row["line"],
            "loan_no": row.get("loan_no", ""),
            "amount": row.get("amount", ""),
            "date": row.get("date", "") or date.today().isoformat(),
            "method": (row.get("method") or PaymentMethod.SALARY_DEDUCTION).lower(),
            "reference": row.get("reference") or None,
            "narration": row.get("narration") or "Bulk import",
            "borrower": "",
            "ok": False,
            "error": None,
        }

        loan = _loan_for(result["loan_no"]) if result["loan_no"] else None
        try:
            amount = q(Decimal(result["amount"].replace(",", "")))
        except (InvalidOperation, AttributeError, ValueError):
            amount = None

        try:
            parsed_date = date.fromisoformat(result["date"])
        except ValueError:
            parsed_date = None

        if not result["loan_no"]:
            result["error"] = "loan_no is blank"
        elif loan is None:
            result["error"] = f"No loan numbered {result['loan_no']}"
        elif amount is None or amount <= 0:
            result["error"] = f"Amount '{result['amount']}' is not a positive number"
        elif parsed_date is None:
            result["error"] = f"Date '{result['date']}' is not an ISO date (YYYY-MM-DD)"
        elif result["method"] not in METHODS:
            result["error"] = (f"Method '{result['method']}' is not one of "
                               f"{', '.join(sorted(METHODS))}")
        elif loan.status != LoanStatus.ACTIVE:
            result["error"] = f"Loan {loan.loan_no} is {loan.status}, not active"
        else:
            already = committed_per_loan.get(loan.id, Decimal("0"))
            headroom = q(loan.total_outstanding - already)
            if amount > headroom:
                result["error"] = (f"Amount {amount} exceeds the {headroom} still outstanding on "
                                   f"{loan.loan_no}"
                                   + (" after earlier rows in this file" if already else ""))
            else:
                committed_per_loan[loan.id] = already + amount
                result["ok"] = True
                result["loan_id"] = loan.id
                result["amount"] = str(amount)
                result["date"] = parsed_date.isoformat()
                result["borrower"] = loan.borrower.full_name
                valid += 1
                seen_total += amount

        if loan is not None and not result["borrower"]:
            result["borrower"] = loan.borrower.full_name
        checked.append(result)

    return {
        "rows": checked,
        "total_rows": len(checked),
        "valid_rows": valid,
        "invalid_rows": len(checked) - valid,
        "total_amount": q(seen_total),
    }


def commit(validation: dict, user: User, allow_partial: bool = False) -> dict:
    """Post the validated batch. All or nothing unless allow_partial is set."""
    if validation["valid_rows"] == 0:
        raise BusinessRuleError("Nothing to post: no row in the file passed validation.")
    if validation["invalid_rows"] and not allow_partial:
        raise BusinessRuleError(
            f"{validation['invalid_rows']} row(s) failed validation. Fix the file, or re-submit "
            f"with 'post the valid rows only' selected.")

    posted = []
    total = Decimal("0")
    with transaction.atomic():
        for row in validation["rows"]:
            if not row["ok"]:
                continue
            loan = _loan_for(row["loan_no"])
            txn = post_repayment(
                loan, user, Decimal(row["amount"]), date.fromisoformat(row["date"]),
                row["method"], row["reference"], row["narration"],
            )
            total += txn.amount
            posted.append({"line": row["line"], "loan_no": loan.loan_no,
                           "transaction_id": txn.id, "amount": str(txn.amount)})

    return {
        "posted_rows": len(posted),
        "skipped_rows": validation["invalid_rows"],
        "total_amount": q(total),
        "postings": posted,
    }
