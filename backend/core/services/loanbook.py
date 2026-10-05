"""Bring an existing loan book over from another system.

A lender going live with loans already running cannot capture them as new
applications: they were disbursed months ago, part of them is repaid, some of
them are behind, and the cash left the bank long before this system existed. This
imports each one as it stands at the cut-over date.

One CSV row is one running loan. Expected columns, case-insensitive, any order:

    national_id, product_code, principal, term, disbursement_date, amount_paid

are required. Optional:

    first_name, last_name, phone          needed only when the borrower is new
    employer, employee_no, net_salary, payday, email, address, branch_code
    first_instalment_date                 else worked out as for a new loan
    interest_rate_pct                     else the product's rate
    penalties_outstanding                 unpaid penalties carried over
    external_ref                          the loan's number in the old system
    purpose

For each row the original schedule is rebuilt from the contract, and
`amount_paid` - everything the borrower has paid so far - is laid over it oldest
instalment first, interest before principal, which is what the repayment
waterfall would have done. Arrears then fall out of the schedule exactly as for
any other loan. The ledger receives one OPENING_BALANCE posting per loan: the
principal and penalties outstanding, against 3900 Opening balances, and no cash.

Two decisions worth knowing:

  * Penalties are brought over as a figure and never re-accrued for the past.
    Every instalment due before the cut-over is marked as penalised to that date,
    so the penalty job charges only what falls late from then on. Without that,
    the first nightly run would charge every migrated arrear its whole history a
    second time.

  * Re-running a file is safe when it carries external_ref: a loan already
    imported under that reference is reported, not duplicated.
"""
import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from django.db import transaction

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    Branch,
    InstalmentStatus,
    Loan,
    LoanProduct,
    LoanStatus,
    Transaction,
    TxnType,
    User,
)
from . import periods, workdays
from .amortisation import annual_percentage_rate, build_schedule, q, total_interest
from .loans import _create_schedule, default_first_due, next_number, refresh_balances, sched

ZERO = Decimal("0")
REQUIRED = {"national_id", "product_code", "principal", "term", "disbursement_date",
            "amount_paid"}
OPTIONAL = {"first_name", "last_name", "phone", "employer", "employee_no", "net_salary",
            "payday", "email", "address", "branch_code", "first_instalment_date",
            "interest_rate_pct", "penalties_outstanding", "external_ref", "purpose"}
KNOWN = REQUIRED | OPTIONAL
MAX_ROWS = 5000

TEMPLATE = (
    "national_id,first_name,last_name,phone,employer,net_salary,payday,branch_code,"
    "product_code,principal,term,disbursement_date,first_instalment_date,amount_paid,"
    "penalties_outstanding,external_ref\r\n"
    "63-123456A63,Tatenda,Moyo,0771234567,City of Harare,900,25,HQ,"
    "SAL-TERM,2000,12,2026-01-10,2026-01-25,1200,0,OLD-10045\r\n"
)


def parse(file_bytes: bytes) -> list[dict]:
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BusinessRuleError("The file is empty.")
    headers = {(name or "").strip().lower(): name for name in reader.fieldnames}
    missing = REQUIRED - set(headers)
    if missing:
        raise BusinessRuleError(
            f"Missing column(s): {', '.join(sorted(missing))}. Download the template for the "
            f"expected headers.")
    rows = [{"line": index, **{key: (raw.get(source) or "").strip()
                               for key, source in headers.items() if key in KNOWN}}
            for index, raw in enumerate(reader, start=2)]
    if not rows:
        raise BusinessRuleError("The file has a header row but no data.")
    if len(rows) > MAX_ROWS:
        raise BusinessRuleError(f"The file has more than {MAX_ROWS} rows; split it.")
    return rows


def _decimal(value: str, label: str, *, required: bool = True, minimum=ZERO) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise ValueError(f"{label} is blank")
        return None
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"{label} '{value}' is not a number")
    if number < minimum:
        raise ValueError(f"{label} cannot be below {minimum}")
    return number


def _date(value: str, label: str, *, required: bool = True) -> date | None:
    if not value:
        if required:
            raise ValueError(f"{label} is blank")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} '{value}' is not an ISO date (YYYY-MM-DD)")


def _lay_payments(rows, paid: Decimal) -> Decimal:
    """Spread what was paid over the schedule, oldest first, interest then principal.

    Mutates the in-memory schedule. Returns whatever could not be placed, which is
    non-zero only when the payments exceed the whole contract.
    """
    remaining = paid
    for ins in rows:
        for due_field, paid_field in (("interest_due", "interest_paid"),
                                      ("principal_due", "principal_paid")):
            if remaining <= 0:
                return ZERO
            take = min(getattr(ins, due_field) - getattr(ins, paid_field), remaining)
            if take > 0:
                setattr(ins, paid_field, getattr(ins, paid_field) + take)
                remaining -= take
    return remaining


class _Planned:
    """A schedule row with the paid columns the real Instalment carries."""

    def __init__(self, row):
        self.row = row
        self.due_date = row.due_date
        self.principal_due = row.principal_due
        self.interest_due = row.interest_due
        self.principal_paid = ZERO
        self.interest_paid = ZERO


def _plan(row: dict, product: LoanProduct, borrower_payday: int | None,
          cutover: date, calendar: workdays.WorkingCalendar | None = None) -> dict:
    """Everything about one row that can be worked out without writing anything."""
    principal = q(_decimal(row.get("principal"), "principal", minimum=Decimal("0.01")))
    term_raw = _decimal(row.get("term"), "term", minimum=Decimal(1))
    if term_raw != term_raw.to_integral_value():
        raise ValueError(f"term '{row.get('term')}' must be a whole number of instalments")
    term = int(term_raw)
    disbursed = _date(row.get("disbursement_date"), "disbursement_date")
    if disbursed > cutover:
        raise ValueError(f"disbursed {disbursed.isoformat()}, after the cut-over date "
                         f"{cutover.isoformat()}; capture it as a new loan instead")
    first_due = _date(row.get("first_instalment_date"), "first_instalment_date", required=False)
    first_due = first_due or default_first_due(disbursed, borrower_payday,
                                               product.repayment_frequency)
    if first_due <= disbursed:
        raise ValueError("first_instalment_date must be after the disbursement date")
    rate = _decimal(row.get("interest_rate_pct"), "interest_rate_pct", required=False)
    rate = product.interest_rate_pct if rate is None else rate
    paid = q(_decimal(row.get("amount_paid"), "amount_paid"))
    penalties = q(_decimal(row.get("penalties_outstanding"), "penalties_outstanding",
                           required=False) or ZERO)

    schedule = build_schedule(principal, rate, term, first_due, product.rate_method,
                              product.repayment_frequency, calendar)
    contract = q(sum((r.instalment for r in schedule), ZERO))
    if paid >= contract:
        raise ValueError(f"amount_paid {paid} covers the whole contract of {contract}; "
                         f"there is nothing outstanding to bring over")
    planned = [_Planned(r) for r in schedule]
    _lay_payments(planned, paid)
    principal_out = q(sum((p.principal_due - p.principal_paid for p in planned), ZERO))
    overdue = q(sum((p.principal_due + p.interest_due - p.principal_paid - p.interest_paid
                     for p in planned if p.due_date < cutover), ZERO))
    return {
        "principal": principal, "term": term, "rate": rate, "disbursed": disbursed,
        "first_due": first_due, "paid": paid, "penalties": penalties, "schedule": schedule,
        "planned": planned, "principal_outstanding": principal_out,
        "interest_outstanding": q(sum((p.interest_due - p.interest_paid for p in planned),
                                      ZERO)),
        "arrears": overdue,
    }


def validate(rows: list[dict], cutover: date) -> dict:
    """Check every row against the book without writing anything."""
    periods.assert_open(cutover, "The cut-over date")
    products = {p.code.lower(): p for p in LoanProduct.objects.all()}
    calendar = workdays.load()
    branches = {b.code.lower(): b for b in Branch.objects.all()}
    ids = [r.get("national_id") for r in rows if r.get("national_id")]
    borrowers = {b.national_id: b for b in Borrower.objects.filter(national_id__in=ids)}
    open_ids = set(Loan.objects.filter(
        borrower__national_id__in=ids,
        status__in=[LoanStatus.PENDING, LoanStatus.APPROVED, LoanStatus.ACTIVE],
    ).values_list("borrower__national_id", flat=True))
    refs = [r.get("external_ref") for r in rows if r.get("external_ref")]
    imported = set(Loan.objects.filter(external_ref__in=refs).values_list("external_ref",
                                                                          flat=True))

    checked = []
    seen_ids: set[str] = set()
    seen_refs: set[str] = set()
    totals = {"principal_outstanding": ZERO, "penalties": ZERO, "arrears": ZERO}
    for row in rows:
        national_id = row.get("national_id", "")
        result = {"line": row["line"], "national_id": national_id,
                  "external_ref": row.get("external_ref") or None,
                  "product_code": row.get("product_code", ""), "borrower": "",
                  "new_borrower": False, "ok": False, "error": None}
        try:
            if not national_id:
                raise ValueError("national_id is blank")
            if national_id in seen_ids:
                raise ValueError(f"{national_id} appears twice in the file; a borrower has one "
                                 f"open loan at a time")
            seen_ids.add(national_id)
            ref = row.get("external_ref")
            if ref and ref in imported:
                raise ValueError(f"{ref} has already been imported")
            if ref and ref in seen_refs:
                raise ValueError(f"{ref} appears twice in the file")
            if ref:
                seen_refs.add(ref)
            product = products.get((row.get("product_code") or "").lower())
            if product is None:
                raise ValueError(f"No product with code '{row.get('product_code')}'")

            borrower = borrowers.get(national_id)
            if borrower is None:
                missing = [f for f in ("first_name", "last_name", "phone") if not row.get(f)]
                if missing:
                    raise ValueError(f"{national_id} is not on the register, so "
                                     f"{', '.join(missing)} are needed to create them")
                code = (row.get("branch_code") or "").lower()
                if code and code not in branches:
                    raise ValueError(f"No branch with code '{row.get('branch_code')}'")
                payday = int(_decimal(row.get("payday"), "payday", required=False,
                                      minimum=Decimal(1)) or 25)
                if payday > 31:
                    raise ValueError("payday must be a day of the month, 1 to 31")
                _decimal(row.get("net_salary"), "net_salary", required=False)
                result["borrower"] = f"{row['first_name']} {row['last_name']}"
                result["new_borrower"] = True
            else:
                if national_id in open_ids:
                    raise ValueError(f"{borrower.full_name} already has an open loan here")
                payday = borrower.payday
                result["borrower"] = borrower.full_name

            plan = _plan(row, product, payday, cutover, calendar)
        except ValueError as exc:
            result["error"] = str(exc)
            checked.append(result)
            continue

        result.update({
            "ok": True,
            "principal": str(plan["principal"]),
            "term": plan["term"],
            "repayment_frequency": product.repayment_frequency,
            "disbursement_date": plan["disbursed"].isoformat(),
            "amount_paid": str(plan["paid"]),
            "principal_outstanding": str(plan["principal_outstanding"]),
            "penalties_outstanding": str(plan["penalties"]),
            "arrears": str(plan["arrears"]),
        })
        totals["principal_outstanding"] += plan["principal_outstanding"]
        totals["penalties"] += plan["penalties"]
        totals["arrears"] += plan["arrears"]
        checked.append(result)

    valid = sum(1 for r in checked if r["ok"])
    return {
        "cutover_date": cutover.isoformat(),
        "rows": checked,
        "total_rows": len(checked),
        "valid_rows": valid,
        "invalid_rows": len(checked) - valid,
        "new_borrowers": sum(1 for r in checked if r["ok"] and r["new_borrower"]),
        "principal_outstanding": q(totals["principal_outstanding"]),
        "penalties_outstanding": q(totals["penalties"]),
        "arrears": q(totals["arrears"]),
    }


def commit(rows: list[dict], validation: dict, user: User, cutover: date) -> dict:
    """Write every valid row, all or nothing.

    Refuses a file with any invalid row: a half-migrated book is harder to agree
    than one that has not started.
    """
    if validation["invalid_rows"]:
        raise BusinessRuleError(
            f"{validation['invalid_rows']} row(s) have problems. A migration is all or "
            f"nothing; fix the file and check it again.")
    if not validation["valid_rows"]:
        raise BusinessRuleError("Nothing to import.")

    products = {p.code.lower(): p for p in LoanProduct.objects.all()}
    calendar = workdays.load()
    branches = {b.code.lower(): b for b in Branch.objects.all()}
    imported = []
    with transaction.atomic():
        for row in rows:
            national_id = row["national_id"]
            borrower = Borrower.objects.filter(national_id=national_id).first()
            if borrower is None:
                branch = branches.get((row.get("branch_code") or "").lower())
                borrower = Borrower.objects.create(
                    borrower_no=next_number("BRW"), national_id=national_id,
                    first_name=row["first_name"], last_name=row["last_name"],
                    phone=row["phone"], email=row.get("email") or None,
                    address=row.get("address") or None, employer=row.get("employer") or None,
                    employee_no=row.get("employee_no") or None,
                    net_salary=q(_decimal(row.get("net_salary"), "net_salary",
                                          required=False) or ZERO),
                    payday=int(_decimal(row.get("payday"), "payday", required=False) or 25),
                    # Already lent to under the old system's checks.
                    kyc_verified=True,
                    branch=branch or getattr(user, "branch", None),
                    notes=f"Brought over from the previous system on {cutover.isoformat()}",
                )
            product = products[row["product_code"].lower()]
            plan = _plan(row, product, borrower.payday, cutover, calendar)
            imported.append(_bring_over(borrower, product, plan, row, user, cutover))

    return {
        "imported_rows": len(imported),
        "principal_outstanding": q(sum((Decimal(i["principal_outstanding"])
                                        for i in imported), ZERO)),
        "loans": imported,
    }


def _bring_over(borrower: Borrower, product: LoanProduct, plan: dict, row: dict, user: User,
                cutover: date) -> dict:
    disbursed = plan["disbursed"]
    schedule = plan["schedule"]
    loan = Loan.objects.create(
        loan_no=next_number("LN"), external_ref=row.get("external_ref") or None,
        borrower=borrower, product=product, officer=user,
        branch_id=borrower.branch_id or getattr(user, "branch_id", None),
        principal=plan["principal"], interest_rate_pct=plan["rate"],
        rate_method=product.rate_method, repayment_frequency=product.repayment_frequency,
        term_months=plan["term"],
        purpose=(row.get("purpose") or "Brought over at migration")[:200],
        instalment_amount=schedule[0].instalment, total_interest=total_interest(schedule),
        apr_pct=annual_percentage_rate(plan["principal"],
                                       [(r.due_date, r.instalment) for r in schedule],
                                       disbursed),
        status=LoanStatus.ACTIVE, application_date=disbursed,
        approved_at=datetime.combine(disbursed, datetime.min.time(), tzinfo=timezone.utc),
        approved_by=user, disbursement_date=disbursed, first_instalment_date=plan["first_due"],
        maturity_date=schedule[-1].due_date,
    )
    rows = _create_schedule(loan, schedule)
    for ins, planned in zip(rows, plan["planned"]):
        ins.principal_paid = planned.principal_paid
        ins.interest_paid = planned.interest_paid
        if ins.principal_paid + ins.interest_paid >= ins.principal_due + ins.interest_due:
            # The old system's payment date is not in the file; the due date is the
            # nearest honest stand-in, and says "on time" rather than inventing lateness.
            ins.paid_date = ins.due_date
        if ins.due_date < cutover:
            # Penalised up to the cut-over by the old system, whose figure arrives
            # below. See the module docstring.
            ins.last_penalty_date = cutover

    if plan["penalties"] > 0:
        target = next((i for i in rows if i.due_date < cutover
                       and i.principal_due + i.interest_due > i.principal_paid + i.interest_paid),
                      None) or next((i for i in rows if i.paid_date is None), rows[-1])
        target.penalty_due += plan["penalties"]
    refresh_balances(loan, cutover)

    Transaction.objects.create(
        loan=loan, txn_type=TxnType.OPENING_BALANCE, txn_date=cutover,
        amount=q(loan.principal_outstanding + loan.penalties_outstanding),
        principal_component=loan.principal_outstanding,
        penalty_component=loan.penalties_outstanding, posted_by=user,
        reference=row.get("external_ref") or None,
        narration=(f"Opening balance brought forward"
                   + (f" from {row['external_ref']}" if row.get("external_ref") else "")
                   + f": {plan['paid']} paid of the original {plan['principal']}"),
    )
    return {"line": row["line"], "loan_id": loan.id, "loan_no": loan.loan_no,
            "external_ref": loan.external_ref, "borrower": borrower.full_name,
            "principal_outstanding": str(loan.principal_outstanding),
            "status": "overdue" if any(i.status == InstalmentStatus.OVERDUE for i in sched(loan))
            else "current"}
