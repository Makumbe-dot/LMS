"""Arrears, computed by the database instead of by iterating schedules in Python.

`loans.arrears(loan, as_of)` walks a loan's instalments in memory. That is the
right shape for the services that MUTATE instalments — a repayment allocates
against the objects `refresh_balances()` later persists — but it was also what
every report used, so the dashboard, PAR, the loan book, the ECL report, the three
performance reports and group standing each loaded every active loan with its whole
schedule prefetched. On a ten-thousand-loan book that is a hundred thousand-odd
instalment rows and ten thousand model instantiations per page.

This module holds the same definition as ORM expressions, so a report is one
statement over narrow rows.

TWO DEFINITIONS NOW EXIST, and they must agree. `Instalment.balance` (the Python
property), `OVERDUE_BALANCE` below, and `dbo.vw_loan_book` in
sql/04_reporting_views_v2.sql all name the same eight columns. `test_arrears.py`
asserts the first two agree loan-for-loan on a book exercised through the whole
lifecycle, and asserts the report path issues a bounded number of queries so a
quiet regression to the per-loan path fails the suite rather than just getting slow.

READS COMMITTED STATE. Never call anything here between a service mutating
Instalment objects and `refresh_balances()` persisting them — it would read the
old figures with no error. `groups.standing()` and `scoring._arrears_points()` are
both reached from inside `loans.apply()`'s open transaction, and are safe only
because `apply()` mutates nothing before calling them. A future caller that
mutates first has to persist first.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import (
    DecimalField,
    Exists,
    F,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from ..models import Instalment, Loan, LoanStatus
from .amortisation import q

ZERO = Decimal("0")
_money = DecimalField(max_digits=18, decimal_places=2)

# What an instalment still owes. The same eight columns as Instalment.total_due
# minus Instalment.total_paid; keep the three definitions in step.
OVERDUE_BALANCE = (
    F("principal_due") + F("interest_due") + F("penalty_due") + F("charge_due")
    - F("principal_paid") - F("interest_paid") - F("penalty_paid") - F("charge_paid")
)


def _overdue(as_of: date):
    """Instalments of the outer loan that are past due and still owe something.

    The balance filter is per instalment, not a per-loan HAVING, which is what keeps
    this equal to the Python sum if a negative instalment balance ever becomes
    possible. It cannot today — the waterfall caps each take at (due - paid) — but
    the two would diverge silently if it did.
    """
    return (Instalment.objects
            .filter(loan=OuterRef("pk"), due_date__lt=as_of)
            .annotate(bal=OVERDUE_BALANCE)
            .filter(bal__gt=0))


def annotations(as_of: date | None = None) -> dict:
    """`arrears_amount` and `oldest_arrears_due` for a Loan queryset.

    Two correlated subqueries per loan row, not three: days in arrears is
    `(as_of - oldest_arrears_due).days`, which `days_from()` below computes for
    free from the date already fetched. A third subquery to render a number that
    subtraction gives is not worth the scan.

    `as_of` is bound as a parameter rather than left to the server's clock, so a
    report for a date does not quietly move if the run straddles midnight.
    """
    as_of = as_of or date.today()
    overdue = _overdue(as_of)
    return {
        "arrears_amount": Coalesce(
            Subquery(overdue.values("loan").annotate(t=Sum("bal", output_field=_money))
                     .values("t")[:1], output_field=_money),
            Value(ZERO, output_field=_money)),
        "oldest_arrears_due": Subquery(
            overdue.order_by("due_date").values("due_date")[:1]),
    }


def with_arrears(queryset, as_of: date | None = None):
    """The queryset, annotated. `.values()` after this stays narrow."""
    return queryset.annotate(**annotations(as_of))


def is_overdue(as_of: date | None = None) -> Exists:
    """An Exists for filtering, cheaper than annotating a sum and comparing it."""
    return Exists(_overdue(as_of or date.today()))


def days_from(oldest_due, as_of: date | None = None) -> int:
    """Days in arrears from the oldest overdue due date. 0 when nothing is overdue."""
    if oldest_due is None:
        return 0
    return max(0, ((as_of or date.today()) - oldest_due).days)


# The ageing buckets, in order. These names reach the dashboard chart, the ageing
# CSV and the frontend's BUCKET_LABEL map, so they are an interface: renaming one
# breaks a label silently.
BUCKETS = [("current", 0, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90),
           ("91-180", 91, 180), ("180+", 181, 10 ** 6)]
BUCKET_NAMES = [name for name, _, _ in BUCKETS]


def bucket_for(days: int) -> str:
    """The ageing bucket a number of days falls in.

    One definition, used by the dashboard, the ageing report and the CSV, so a
    bucket cannot mean one thing on a chart and another in a spreadsheet.
    """
    for name, low, high in BUCKETS:
        if low <= days <= high:
            return name
    return "180+"


def rows(as_of: date | None = None, *, branch_id=None, statuses=(LoanStatus.ACTIVE,),
         loan_ids=None) -> list[dict]:
    """One narrow dict per loan: id, the four balances, arrears and days.

    Everything a report needs about arrears without instantiating a Loan or
    touching an Instalment row in Python.
    """
    as_of = as_of or date.today()
    qs = Loan.objects.filter(status__in=statuses)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    if loan_ids is not None:
        loan_ids = list(loan_ids)
        if not loan_ids:
            return []
        # SQL Server caps a statement at 2100 parameters, so a long id list has to
        # be chunked. Prefer a branch or status filter where the caller can.
        if len(loan_ids) > 1000:
            out = []
            for start in range(0, len(loan_ids), 1000):
                out += rows(as_of, branch_id=branch_id, statuses=statuses,
                            loan_ids=loan_ids[start:start + 1000])
            return out
        qs = qs.filter(pk__in=loan_ids)

    fetched = (with_arrears(qs, as_of)
               # No trailing order_by('number') from Instalment.Meta can reach the
               # GROUP BY of the subquery (Django forces no ordering when grouping),
               # but an explicit one would, so never add one.
               .values("id", "principal_outstanding", "interest_outstanding",
                       "penalties_outstanding", "charges_outstanding",
                       "arrears_amount", "oldest_arrears_due"))
    return [{
        **row,
        "arrears_amount": Decimal(row["arrears_amount"] or 0),
        "days_in_arrears": days_from(row["oldest_arrears_due"], as_of),
    } for row in fetched]


def by_loan(as_of: date | None = None, **kwargs) -> dict[int, dict]:
    """The same rows keyed by loan id, for joining onto something else."""
    return {row["id"]: row for row in rows(as_of, **kwargs)}


def totals(as_of: date | None = None, *, branch_id=None, par_days: int = 30) -> dict:
    """Ageing buckets, portfolio at risk and the book totals, in one pass.

    `buckets` holds PRINCIPAL OUTSTANDING per bucket, not the overdue amount —
    that is what the dashboard chart has always shown and what PAR means, and
    changing it here would silently restate a headline figure. `bucket_arrears`
    carries the overdue amount alongside for the ageing report, which wants both.

    One pass rather than several, because the same loan classified twice by two
    passes is how a dashboard ends up disagreeing with itself.
    """
    as_of = as_of or date.today()
    cutoff = as_of - timedelta(days=par_days)

    amounts = {b: ZERO for b in BUCKET_NAMES}
    overdue = {b: ZERO for b in BUCKET_NAMES}
    counts = {b: 0 for b in BUCKET_NAMES}
    principal_total = outstanding_total = arrears_total = ZERO
    par_amount = ZERO
    par_loans = loans_in_arrears = 0
    loans = 0

    for row in rows(as_of, branch_id=branch_id):
        loans += 1
        principal = Decimal(row["principal_outstanding"] or 0)
        principal_total += principal
        outstanding_total += (principal
                              + Decimal(row["interest_outstanding"] or 0)
                              + Decimal(row["penalties_outstanding"] or 0)
                              + Decimal(row["charges_outstanding"] or 0))
        bucket = bucket_for(row["days_in_arrears"])
        counts[bucket] += 1
        amounts[bucket] += principal
        overdue[bucket] += row["arrears_amount"]
        if row["arrears_amount"] > 0:
            arrears_total += row["arrears_amount"]
            loans_in_arrears += 1
        if row["oldest_arrears_due"] is not None and row["oldest_arrears_due"] < cutoff:
            par_amount += principal
            par_loans += 1

    return {
        "as_of": as_of,
        "loans": loans,
        "buckets": {b: q(amounts[b]) for b in BUCKET_NAMES},
        "bucket_arrears": {b: q(overdue[b]) for b in BUCKET_NAMES},
        "bucket_loans": counts,
        "principal_outstanding": q(principal_total),
        "total_outstanding": q(outstanding_total),
        "arrears_total": q(arrears_total),
        "loans_in_arrears": loans_in_arrears,
        "par_amount": q(par_amount),
        "par_loans": par_loans,
        "par_pct": q(par_amount / principal_total * 100) if principal_total > 0 else ZERO,
        "par_days": par_days,
    }


# Ordering a paginated report on a value computed in Python is impossible, so PAR
# sorts on `oldest_arrears_due` — the oldest due date and the most days in arrears
# are the same ordering. The `-id` tiebreak is load-bearing: SQL Server's
# OFFSET/FETCH can return a tied row on two pages, or on neither, without it.
PAR_ORDER = ("oldest_arrears_due", "-id")
