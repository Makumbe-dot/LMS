"""Portfolio reporting: dashboard, portfolio at risk, collections due, loan book,
transaction listing and the per-loan statement.

Every function returns plain dicts/lists, which the report views hand to the
JSON renderer or to the CSV writer unchanged.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Prefetch, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth

from ..models import (
    Borrower,
    ECLStage,
    Instalment,
    Loan,
    LoanStatus,
    OrganisationSetting,
    Transaction,
    TxnType,
)
from . import arrears as arrears_svc
from .amortisation import add_months, q
from .fx import to_base
from .arrears import BUCKETS, bucket_for  # noqa: F401 - re-exported; callers import from here
from .loans import arrears

ZERO = Decimal("0")

_money = DecimalField(max_digits=18, decimal_places=2)


def active_loans(branch_id=None, *, with_schedule: bool = True) -> list[Loan]:
    """Active loans as model instances.

    `with_schedule=False` drops the instalment prefetch, for callers that get their
    arrears from `services.arrears` instead of by walking the schedule. The prefetch
    is what made a ten-thousand-loan report a hundred-thousand-row fetch.
    """
    qs = (Loan.objects
          .filter(status=LoanStatus.ACTIVE)
          .select_related("borrower", "product", "officer", "branch"))
    if with_schedule:
        qs = qs.prefetch_related(
            Prefetch("instalments", queryset=Instalment.objects.order_by("number")))
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    return list(qs)


def _sum(queryset, expression) -> Decimal:
    value = queryset.aggregate(total=Coalesce(Sum(expression, output_field=_money),
                                              Value(ZERO, output_field=_money)))["total"]
    return Decimal(value or 0)


def dashboard(as_of: date | None = None, branch_id=None) -> dict:
    as_of = as_of or date.today()
    # One statement over narrow rows, rather than every active loan with its whole
    # schedule prefetched and walked in Python.
    book = arrears_svc.totals(as_of, branch_id=branch_id)
    outstanding = book["total_outstanding"]
    principal_out = book["principal_outstanding"]

    month_start = as_of.replace(day=1)
    next_month = add_months(month_start, 1)

    txn_scope = Transaction.objects.all()
    inst_scope = Instalment.objects.all()
    loan_scope = Loan.objects.all()
    borrower_scope = Borrower.objects.all()
    if branch_id:
        txn_scope = txn_scope.filter(loan__branch_id=branch_id)
        inst_scope = inst_scope.filter(loan__branch_id=branch_id)
        loan_scope = loan_scope.filter(branch_id=branch_id)
        borrower_scope = borrower_scope.filter(branch_id=branch_id)

    disb = _sum(
        txn_scope.filter(txn_type=TxnType.DISBURSEMENT,
                         txn_date__gte=month_start, txn_date__lt=next_month),
        "principal_component")
    coll = _sum(
        txn_scope.filter(txn_type=TxnType.REPAYMENT, reversed=False,
                         txn_date__gte=month_start, txn_date__lt=next_month),
        "amount")
    due = _sum(
        inst_scope.filter(loan__status__in=[LoanStatus.ACTIVE, LoanStatus.CLOSED],
                          due_date__gte=month_start, due_date__lt=next_month),
        F("principal_due") + F("interest_due"))

    status_counts = {s.value: 0 for s in LoanStatus}
    for row in loan_scope.values("status").annotate(n=Count("id")):
        status_counts[row["status"]] = row["n"]

    return {
        "as_of": as_of,
        "borrowers": borrower_scope.count(),
        "active_loans": book["loans"],
        "pending_applications": status_counts.get("pending", 0),
        "portfolio_outstanding": outstanding,
        "principal_outstanding": principal_out,
        "par_30_amount": book["par_amount"],
        "par_30_pct": book["par_pct"],
        "disbursed_this_month": q(disb),
        "collected_this_month": q(coll),
        "due_this_month": q(due),
        "collection_rate_pct": q(coll / due * 100) if due else ZERO,
        "arrears_buckets": book["buckets"],
        "status_counts": status_counts,
        "monthly_series": monthly_series(as_of, branch_id=branch_id),
    }


def monthly_series(as_of: date, months: int = 12, branch_id=None) -> list[dict]:
    """Disbursed and collected per calendar month, for the dashboard bars."""
    start = add_months(as_of.replace(day=1), -(months - 1))
    scope = Transaction.objects.filter(loan__branch_id=branch_id) if branch_id else Transaction.objects.all()
    rows = (scope
            .filter(txn_date__gte=start, reversed=False,
                    txn_type__in=[TxnType.DISBURSEMENT, TxnType.REPAYMENT])
            .annotate(month=TruncMonth("txn_date"))
            .values("txn_type", "month")
            .annotate(total=Sum("amount", output_field=_money))
            .order_by("month"))

    series: dict[str, dict] = {}
    for i in range(months):
        m = add_months(start, i)
        key = m.strftime("%Y-%m")
        series[key] = {"month": key, "disbursed": ZERO, "collected": ZERO}
    for row in rows:
        key = row["month"].strftime("%Y-%m")
        if key in series:
            field = "disbursed" if row["txn_type"] == TxnType.DISBURSEMENT else "collected"
            series[key][field] = q(Decimal(row["total"] or 0))
    return list(series.values())


def portfolio_at_risk(as_of: date | None = None, branch_id=None) -> list[dict]:
    """Every active loan in arrears, worst first.

    Filters in SQL and fetches only the loans that are actually overdue, instead of
    loading the whole active book with its schedules and discarding the performing
    ones in Python.
    """
    as_of = as_of or date.today()
    qs = (Loan.objects
          .filter(status=LoanStatus.ACTIVE)
          .filter(arrears_svc.is_overdue(as_of))
          .select_related("borrower", "product", "officer", "branch"))
    if branch_id:
        qs = qs.filter(branch_id=branch_id)

    out = []
    for l in arrears_svc.with_arrears(qs, as_of).order_by(*arrears_svc.PAR_ORDER):
        amt = q(Decimal(l.arrears_amount or 0))
        days = arrears_svc.days_from(l.oldest_arrears_due, as_of)
        out.append({
            "loan_no": l.loan_no, "loan_id": l.id, "borrower": l.borrower.full_name,
            "phone": l.borrower.phone, "employer": l.borrower.employer,
            "officer": l.officer.full_name if l.officer else "-",
            "branch": l.branch.name if l.branch else "-",
            "product": l.product.name, "currency": l.currency or "",
            "principal_outstanding": l.principal_outstanding,
            "total_outstanding": l.total_outstanding, "arrears_amount": amt,
            "days_in_arrears": days, "bucket": bucket_for(days),
            "penalties_outstanding": l.penalties_outstanding,
        })
    return out


def arrears_ageing(as_of: date | None = None, branch_id=None) -> list[dict]:
    """The ageing table as rows, for CSV and for analysts.

    The same single pass the dashboard chart reads, so the two cannot disagree.
    """
    book = arrears_svc.totals(as_of, branch_id=branch_id)
    return [{
        "bucket": name,
        "loans": book["bucket_loans"][name],
        "principal_outstanding": book["buckets"][name],
        "arrears_amount": book["bucket_arrears"][name],
    } for name in arrears_svc.BUCKET_NAMES]


def collections_due(start: date, end: date) -> list[dict]:
    rows = (Instalment.objects
            .filter(loan__status=LoanStatus.ACTIVE, due_date__gte=start, due_date__lte=end)
            .select_related("loan", "loan__borrower")
            .order_by("due_date"))
    return [{
        "loan_no": i.loan.loan_no, "loan_id": i.loan_id, "borrower": i.loan.borrower.full_name,
        "employer": i.loan.borrower.employer, "instalment_no": i.number, "due_date": i.due_date,
        "amount_due": i.total_due, "paid": i.total_paid, "balance": i.balance, "status": i.status,
    } for i in rows]


def loan_book(as_of: date | None = None) -> list[dict]:
    as_of = as_of or date.today()
    qs = (Loan.objects
          .filter(status__in=[LoanStatus.ACTIVE, LoanStatus.CLOSED, LoanStatus.WRITTEN_OFF])
          .select_related("borrower", "product")
          .order_by("id"))
    out = []
    for l in arrears_svc.with_arrears(qs, as_of):
        amt = q(Decimal(l.arrears_amount or 0))
        days = arrears_svc.days_from(l.oldest_arrears_due, as_of)
        out.append({
            "loan_no": l.loan_no, "loan_id": l.id, "borrower": l.borrower.full_name,
            "product": l.product.name, "status": l.status, "currency": l.currency or "",
            "disbursement_date": l.disbursement_date, "maturity_date": l.maturity_date,
            "principal": l.principal, "rate_pct": l.interest_rate_pct, "term": l.term_months,
            "instalment": l.instalment_amount,
            "principal_outstanding": l.principal_outstanding,
            "interest_outstanding": l.interest_outstanding,
            "penalties_outstanding": l.penalties_outstanding,
            "charges_outstanding": l.charges_outstanding,
            "total_outstanding": l.total_outstanding, "total_paid": l.total_paid,
            "arrears_amount": amt, "days_in_arrears": days,
        })
    return out


def transactions_report(start: date, end: date, txn_type: str | None = None) -> list[dict]:
    qy = (Transaction.objects
          .filter(txn_date__gte=start, txn_date__lte=end)
          .select_related("loan", "loan__borrower"))
    if txn_type:
        qy = qy.filter(txn_type=txn_type)
    return [{
        "id": t.id, "date": t.txn_date, "loan_id": t.loan_id, "loan_no": t.loan.loan_no,
        "borrower": t.loan.borrower.full_name, "type": t.txn_type, "amount": t.amount,
        "principal": t.principal_component, "interest": t.interest_component,
        "penalty": t.penalty_component, "method": t.method or None, "reference": t.reference,
        "reversed": t.reversed, "narration": t.narration,
    } for t in qy.order_by("txn_date", "id")]


# ---------------------------------------------------------------- IFRS 9
def ecl_stage(days_past_due: int, cfg: OrganisationSetting) -> str:
    if days_past_due > cfg.ecl_stage3_days:
        return ECLStage.STAGE_3
    if days_past_due > cfg.ecl_stage2_days:
        return ECLStage.STAGE_2
    return ECLStage.STAGE_1


def ecl_report(as_of: date | None = None, branch_id=None) -> dict:
    """IFRS 9 staging and expected credit loss.

    Loans are staged on days past due, exposure is the total amount outstanding,
    and the provision is exposure x the stage rate held in organisation settings.
    """
    as_of = as_of or date.today()
    cfg = OrganisationSetting.load()
    rates = {
        ECLStage.STAGE_1: cfg.ecl_stage1_pct,
        ECLStage.STAGE_2: cfg.ecl_stage2_pct,
        ECLStage.STAGE_3: cfg.ecl_stage3_pct,
    }

    from .provisioning import booked_provision, carrying_amount, last_run, ledger_provision

    rows = []
    summary = {stage: {"stage": stage, "label": ECLStage(stage).label, "loans": 0,
                       "exposure": ZERO, "carrying_amount": ZERO, "rate_pct": rates[stage],
                       "provision": ZERO, "provision_required": ZERO}
               for stage in (ECLStage.STAGE_1, ECLStage.STAGE_2, ECLStage.STAGE_3)}

    # No instalment prefetch: staging needs days past due and carrying_amount reads
    # only the loan's own columns, so the schedule never has to leave the database.
    ecl_qs = Loan.objects.filter(status=LoanStatus.ACTIVE).select_related(
        "borrower", "product", "branch")
    if branch_id:
        ecl_qs = ecl_qs.filter(branch_id=branch_id)

    for loan in arrears_svc.with_arrears(ecl_qs, as_of):
        amount = q(Decimal(loan.arrears_amount or 0))
        days = arrears_svc.days_from(loan.oldest_arrears_due, as_of)
        stage = ecl_stage(days, cfg)
        exposure = loan.total_outstanding
        carrying = carrying_amount(loan)
        rate = rates[stage]
        provision = q(exposure * rate / 100)
        required = q(carrying * rate / 100)
        booked = q(loan.provision_held or ZERO)
        rows.append({
            "loan_no": loan.loan_no, "loan_id": loan.id, "borrower": loan.borrower.full_name,
            "product": loan.product.name,
            "branch": loan.branch.name if loan.branch else "-",
            "days_past_due": days, "stage": stage, "stage_label": ECLStage(stage).label,
            "exposure": exposure, "carrying_amount": carrying, "arrears_amount": amount,
            "provision_rate_pct": rate, "provision": provision,
            "provision_required": required, "provision_booked": booked,
            "provision_movement": q(required - booked),
            "net_exposure": q(exposure - provision),
        })
        bucket = summary[stage]
        bucket["loans"] += 1
        bucket["exposure"] += exposure
        bucket["carrying_amount"] += carrying
        bucket["provision"] += provision
        bucket["provision_required"] += required

    rows.sort(key=lambda r: (-int(r["stage"]), -r["days_past_due"]))
    for bucket in summary.values():
        bucket["exposure"] = q(bucket["exposure"])
        bucket["carrying_amount"] = q(bucket["carrying_amount"])
        bucket["provision"] = q(bucket["provision"])
        bucket["provision_required"] = q(bucket["provision_required"])

    total_exposure = q(sum((b["exposure"] for b in summary.values()), ZERO))
    total_provision = q(sum((b["provision"] for b in summary.values()), ZERO))
    total_required = q(sum((b["provision_required"] for b in summary.values()), ZERO))

    # The booked figure and the ledger check must be over the same population, or
    # the page asserts agreement on a number it is not showing. Both are
    # institution-wide, so a branch slice reports them as null rather than lying.
    whole_book = branch_id is None
    booked_all = booked_provision() if whole_book else None
    in_ledger = ledger_provision() if whole_book else None

    return {
        "as_of": as_of,
        "rows": rows,
        "summary": list(summary.values()),
        "total_exposure": total_exposure,
        "total_provision": total_provision,
        "total_carrying_amount": q(sum((b["carrying_amount"] for b in summary.values()), ZERO)),
        "total_provision_required": total_required,
        "total_provision_booked": booked_all,
        "total_provision_movement": q(total_required - booked_all) if whole_book else None,
        "ledger_provision": in_ledger,
        "ledger_agrees": (in_ledger == booked_all) if whole_book else None,
        "last_run": last_run() if whole_book else None,
        "coverage_pct": q(total_provision / total_exposure * 100) if total_exposure else ZERO,
        "rates": {"stage_1": cfg.ecl_stage1_pct, "stage_2": cfg.ecl_stage2_pct,
                  "stage_3": cfg.ecl_stage3_pct,
                  "stage_2_days": cfg.ecl_stage2_days, "stage_3_days": cfg.ecl_stage3_days},
    }


# ---------------------------------------------------------------- performance
def performance_loans(branch_id=None, as_of: date | None = None) -> list[Loan]:
    """Active loans with their arrears annotated and no schedule fetched."""
    qs = (Loan.objects
          .filter(status=LoanStatus.ACTIVE)
          .select_related("officer", "product", "branch"))
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    return list(arrears_svc.with_arrears(qs, as_of))


def _performance_rows(loans, key_fn, label_fn, as_of: date | None = None) -> list[dict]:
    """Group loans and sum their arrears.

    `loans` must be annotated by `arrears.with_arrears` — the callers all use
    `performance_loans()`, which does. Reading it off the annotation is what keeps
    this from walking every schedule three times over for three reports.
    """
    as_of = as_of or date.today()
    groups: dict = {}
    for loan in loans:
        key = key_fn(loan)
        row = groups.setdefault(key, {
            "key": key, "name": label_fn(loan), "active_loans": 0, "principal_outstanding": ZERO,
            "total_outstanding": ZERO, "arrears_amount": ZERO, "loans_in_arrears": 0,
            "par_30_amount": ZERO,
        })
        # In the organisation's currency, at each loan's booked rate.
        rate = loan.fx_rate or 1
        amount = to_base(loan.arrears_amount or 0, rate)
        days = arrears_svc.days_from(loan.oldest_arrears_due, as_of)
        row["active_loans"] += 1
        row["principal_outstanding"] += to_base(loan.principal_outstanding, rate)
        row["total_outstanding"] += to_base(loan.total_outstanding, rate)
        row["arrears_amount"] += amount
        if amount > 0:
            row["loans_in_arrears"] += 1
        if days > 30:
            row["par_30_amount"] += to_base(loan.principal_outstanding, rate)

    out = []
    for row in groups.values():
        principal = q(row["principal_outstanding"])
        out.append({
            **row,
            "principal_outstanding": principal,
            "total_outstanding": q(row["total_outstanding"]),
            "arrears_amount": q(row["arrears_amount"]),
            "par_30_amount": q(row["par_30_amount"]),
            "par_30_pct": q(row["par_30_amount"] / principal * 100) if principal else ZERO,
        })
    out.sort(key=lambda r: -r["total_outstanding"])
    return out


def officer_performance(branch_id=None) -> list[dict]:
    """Portfolio and arrears by the officer who originated the loan."""
    rows = _performance_rows(
        performance_loans(branch_id),
        key_fn=lambda l: l.officer_id,
        label_fn=lambda l: l.officer.full_name if l.officer else "Unassigned",
    )
    # Originations count the whole book, not only what is still running.
    originated = {r["officer_id"]: r["n"]
                  for r in Loan.objects.values("officer_id").annotate(n=Count("id"))}
    disbursed = {
        r["officer_id"]: r["total"]
        for r in Loan.objects.filter(disbursement_date__isnull=False)
        .values("officer_id")
        .annotate(total=Coalesce(Sum("principal", output_field=_money),
                                 Value(ZERO, output_field=_money)))
    }
    for row in rows:
        officer_id = row["key"]
        row["loans_originated"] = originated.get(officer_id, 0)
        row["total_disbursed"] = q(Decimal(disbursed.get(officer_id, 0) or 0))
    return rows


def product_performance(branch_id=None) -> list[dict]:
    """Portfolio and arrears by loan product."""
    loans = performance_loans(branch_id)
    rows = _performance_rows(loans, key_fn=lambda l: l.product_id,
                             label_fn=lambda l: l.product.name)
    products = {l.product_id: l.product for l in loans}
    for row in rows:
        product = products.get(row["key"])
        row["rate_pct"] = product.interest_rate_pct if product else None
        row["rate_method"] = product.rate_method if product else None
    return rows


def branch_performance() -> list[dict]:
    loans = performance_loans()
    return _performance_rows(
        loans,
        key_fn=lambda l: l.branch_id,
        label_fn=lambda l: l.branch.name if l.branch else "Unassigned",
    )


# ---------------------------------------------------------------- payroll
def payroll_deduction(start: date, end: date, employer: str | None = None,
                      branch_id=None) -> list[dict]:
    """The deduction schedule to send an employer for a pay period.

    One line per instalment falling due in the window, for borrowers of that
    employer, with the employee number the payroll office keys on.
    """
    qs = (Instalment.objects
          .filter(loan__status=LoanStatus.ACTIVE, due_date__gte=start, due_date__lte=end)
          .select_related("loan", "loan__borrower", "loan__branch")
          .order_by("loan__borrower__employer", "loan__borrower__last_name", "due_date"))
    if employer:
        qs = qs.filter(loan__borrower__employer__iexact=employer)
    if branch_id:
        qs = qs.filter(loan__branch_id=branch_id)

    return [{
        "employer": i.loan.borrower.employer or "-",
        "employee_no": i.loan.borrower.employee_no or "-",
        "borrower_no": i.loan.borrower.borrower_no,
        "borrower": i.loan.borrower.full_name,
        "national_id": i.loan.borrower.national_id,
        "loan_no": i.loan.loan_no,
        "loan_id": i.loan_id,
        "instalment_no": i.number,
        "due_date": i.due_date,
        "amount_due": i.total_due,
        "already_paid": i.total_paid,
        "deduct": i.balance,
        "branch": i.loan.branch.name if i.loan.branch else "-",
    } for i in qs if i.balance > 0]


def employers_with_active_loans(branch_id=None) -> list[dict]:
    """The employer list the payroll screen offers, with how much is due."""
    qs = (Loan.objects
          .filter(status=LoanStatus.ACTIVE, borrower__employer__isnull=False)
          .exclude(borrower__employer=""))
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    # charges_outstanding is included here on purpose: Loan.total_outstanding
    # includes it and payroll_deduction's `deduct` is the instalment balance, which
    # already carries charge_due. Leaving it out made the employer total the one
    # figure that disagreed with the deduction schedule the employer reconciles
    # against.
    rows = (qs.values("borrower__employer")
            .annotate(loans=Count("id"),
                      outstanding=Coalesce(Sum((F("principal_outstanding") + F("interest_outstanding")
                                                + F("penalties_outstanding")
                                                + F("charges_outstanding")) * F("fx_rate"),
                                               output_field=_money),
                                           Value(ZERO, output_field=_money)))
            .order_by("borrower__employer"))
    return [{"employer": r["borrower__employer"], "active_loans": r["loans"],
             "total_outstanding": q(Decimal(r["outstanding"] or 0))} for r in rows]


def loan_statement(loan: Loan, start: date | None = None, end: date | None = None) -> dict:
    """The per-loan statement. Lives in services/statements.py with the savings one."""
    from .statements import loan_statement as build

    return build(loan, start, end)

