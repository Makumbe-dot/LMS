"""Period close: freeze a month so nothing can be posted into it afterwards.

A month is closed by writing an AccountingPeriod row for it. A month with no row
is open, which is the decision that lets this ship onto an existing book with
fourteen months of back-dated history: nothing changes until someone closes
something.

Months close in order and only forwards, so the closed months are always a
contiguous prefix of time ending at one date. `closed_through()` is that date and
the guard is a single comparison against it. Out-of-order close was considered and
rejected: allowing August to close while July is open makes "the earliest date you
may still post to" unanswerable, and that answer is what the React date pickers
and the bulk importer need.

WHAT THIS DOES NOT DO. The guard refuses INSERTs of dated postings. It does not
make a closed month immutable:

  * `loans.reschedule` deletes and rebuilds a loan's instalments, including rows
    dated inside closed months. It posts its capitalisation dated today, so the
    ledger stays balanced, but the schedule as at a past date changes.
  * Deleting a Loan cascades to its Transactions and their JournalEntries, which
    may sit in closed months.
  * A raw `.update(txn_date=...)` can move a posting into a closed month.
  * `bulk_create` bypasses pre_save entirely. No guarded model is bulk_created
    anywhere today (JournalLine and Instalment are, and neither carries a posting
    date of its own). Any future code that bulk_creates a Transaction,
    SavingsTransaction or JournalEntry silently defeats this guard.

Point-in-time agreement between the ledger and the sub-ledgers was never
guaranteed by this codebase and this module does not add it; what it guarantees is
that no new dated posting lands in a month someone has signed off.
"""
import contextvars
import json
import logging
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound, PeriodClosedError
from ..models import (
    AccountingPeriod,
    JournalEntry,
    Loan,
    LoanStatus,
    PeriodState,
    SavingsAccount,
    Transaction,
    User,
)

log = logging.getLogger(__name__)

ZERO = Decimal("0")

# Set while a deliberate, audited repair is running. The only escape hatch.
_override = contextvars.ContextVar("period_override", default=None)


# ---------------------------------------------------------------- reading
def month_bounds(year: int, month: int) -> tuple[date, date]:
    if not 1 <= month <= 12:
        raise BusinessRuleError(f"{month} is not a month")
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def closed_through() -> date | None:
    """The last date that is closed, or None when nothing has been closed.

    One aggregate over a table with at most a few hundred rows. Correct in the
    face of another worker having just closed a month, which a cached value would
    not be, and that is the trade this control is for.
    """
    return (AccountingPeriod.objects.filter(state=PeriodState.CLOSED)
            .aggregate(through=Max("end_date"))["through"])


def state_on(on: date) -> str:
    return PeriodState.CLOSED if is_closed(on) else PeriodState.OPEN


def is_closed(on: date | None) -> bool:
    if on is None:
        return False
    through = closed_through()
    return through is not None and on <= through


def earliest_postable_date() -> date | None:
    """The first date a posting may still carry, or None when everything is open."""
    through = closed_through()
    return through + timedelta(days=1) if through else None


def period_for(on: date) -> AccountingPeriod | None:
    return AccountingPeriod.objects.filter(year=on.year, month=on.month).first()


def assert_open(on: date | None, what: str = "This posting") -> None:
    """Refuse a posting dated into a closed month."""
    if on is None or _override.get() is not None:
        return
    through = closed_through()
    if through is None or on > through:
        return
    raise PeriodClosedError(
        f"{what} is dated {on.isoformat()}, which falls in a closed accounting period. "
        f"The books are closed through {through.isoformat()}, so the earliest date you "
        f"can post to is {(through + timedelta(days=1)).isoformat()}. An administrator "
        f"can reopen the period from the Period close page.")


# ---------------------------------------------------------------- the escape hatch
class allow_closed_posting:
    """Permit postings into closed months for the length of a block.

    Used by `ledger.backfill`, which re-raises journal entries for transactions
    that already exist and whose dates are therefore already history. The audit row
    is written on EXIT and only when something actually posted into a closed month,
    because a row saying "a repair ran" on every rebuild tells an auditor nothing.
    """

    def __init__(self, reason: str, user: User | None = None):
        self.reason = reason
        self.user = user
        self._token = None
        self._before = 0

    def __enter__(self):
        through = closed_through()
        self._before = self._entries_in_closed_months(through)
        self._token = _override.set(self.reason)
        return self

    def __exit__(self, *exc):
        _override.reset(self._token)
        through = closed_through()
        after = self._entries_in_closed_months(through)
        if after > self._before:
            audit(self.user, "post_into_closed_period", "journal_entry", None,
                  f"{after - self._before} entries posted into months closed through "
                  f"{through.isoformat()}: {self.reason}")
        return False

    @staticmethod
    def _entries_in_closed_months(through: date | None) -> int:
        if through is None:
            return 0
        return JournalEntry.objects.filter(entry_date__lte=through).count()


# ---------------------------------------------------------------- preflight
def _trial_balance_for(start: date, end: date) -> dict:
    from . import ledger as gl

    return gl.trial_balance(start, end)


def preflight(year: int, month: int) -> dict:
    """The named checks a close is judged on, each with what it actually found.

    Every check here can be true on a real book. Checks that are structurally
    impossible to satisfy are worse than no checks at all, because they train an
    operator to tick 'close anyway' every month.

    `blocking` checks refuse the close; `force` overrides only those marked
    `overridable`. Advisory checks never refuse and exist to be read.
    """
    start, end = month_bounds(year, month)
    today = date.today()
    period = period_for(start)
    through = closed_through()
    balance = _trial_balance_for(start, end)

    checks = []

    # 1. Structural: is this month already closed?
    checks.append({
        "key": "not_already_closed",
        "label": "The month is not already closed",
        "passed": period is None or period.is_open,
        "blocking": True,
        "overridable": False,
        "detail": ("Already closed" if period and not period.is_open
                   else "Open"),
    })

    # 2. Structural: has the month ended? You cannot freeze a month still running.
    checks.append({
        "key": "month_has_ended",
        "label": "The month has ended",
        "passed": end < today,
        "blocking": True,
        "overridable": False,
        "detail": (f"Ends {end.isoformat()}, today is {today.isoformat()}"
                   if end >= today else f"Ended {end.isoformat()}"),
    })

    # 3. Structural: close forwards only, never back into signed-off ground. This
    #    is what keeps the closed months one contiguous run, which is what makes
    #    earliest_postable_date a real answer, so force must never override it.
    #    Closing a month absorbs every month before it: closing September when the
    #    books are closed through March signs off April to September in one action.
    checks.append({
        "key": "closes_forwards",
        "label": "Closes forwards, not back into a month already signed off",
        "passed": through is None or start > through,
        "blocking": True,
        "overridable": False,
        "detail": (
            f"The books are already closed through {through.isoformat()}"
            if through is not None and start <= through
            else f"Closes everything from {'the start of the book' if through is None else (through + timedelta(days=1)).isoformat()} "
                 f"up to {end.isoformat()}"),
    })

    # 4. Integrity: the entries dated in this month balance among themselves.
    checks.append({
        "key": "double_entry_balances",
        "label": "Debits equal credits for the month",
        "passed": bool(balance["balanced"]),
        "blocking": True,
        "overridable": True,
        "detail": f"Dr {balance['total_debit']} / Cr {balance['total_credit']}",
    })

    # 5. Integrity: every posting that should have raised an entry did.
    unposted = _unposted_transactions(start, end)
    checks.append({
        "key": "every_posting_has_an_entry",
        "label": "Every transaction that should post has a journal entry",
        "passed": not unposted,
        "blocking": True,
        "overridable": True,
        "detail": ("All posted" if not unposted else
                   f"{len(unposted)} without an entry: "
                   + ", ".join(t.txn_type for t in unposted[:5])
                   + " — run Rebuild on the General ledger page"),
    })

    # 6. Advisory: postings dated after month end already exist, which is normal in
    #    the first weeks of the next month and means the snapshot is not the whole
    #    book as it stands now.
    later = Transaction.objects.filter(txn_date__gt=end).count()
    checks.append({
        "key": "postings_after_month_end",
        "label": "No postings dated after the month end",
        # `passed` says whether there is anything to report, not whether the close
        # may proceed — a check that always reads Pass while printing a count is
        # reassurance dressed over a fact.
        "passed": later == 0,
        "blocking": False,
        "overridable": False,
        "detail": (f"{later} transactions are dated after {end.isoformat()}, so the frozen "
                   f"trial balance is not the book as it stands now" if later else "None"),
    })

    # 7. Advisory: penalties should be accrued to month end before the month is
    #    signed off, because accrue_penalties dates its PENALTY transaction as_of.
    stale = _instalments_awaiting_penalties(end)
    checks.append({
        "key": "penalties_accrued_to_month_end",
        "label": "Penalties accrued to the month end",
        "passed": stale == 0,
        "blocking": False,
        "overridable": False,
        "detail": ("Accrued" if stale == 0 else
                   f"{stale} overdue instalments not penalised to {end.isoformat()} — "
                   f"run: manage.py run_penalties --as-of {end.isoformat()}"),
    })

    # 8. Advisory: the ledger agrees with the loan and savings books AS THEY STAND
    #    NOW, not as at the month end. It is reported because it is worth seeing,
    #    and never blocking because a book that has moved on since the month end
    #    cannot be expected to match a date-restricted trial balance.
    agreement = reconciliation_now()
    checks.append({
        "key": "ledger_agrees_with_the_book",
        "label": "The ledger agrees with the loan and savings books (as at today)",
        "passed": agreement["agrees"],
        "blocking": False,
        "overridable": False,
        "detail": ("Agrees" if agreement["agrees"] else
                   "; ".join(f"{r['code']} ledger {r['ledger']} vs book {r['book']}"
                             for r in agreement["rows"] if not r["agrees"])),
    })

    # 9. Advisory: journals dated in the month still awaiting approval. Closing over
    #    them means they can never post on their own date, which is usually not what
    #    whoever prepared them intended.
    from .journals import awaiting_approval

    drafts = awaiting_approval(start, end)
    checks.append({
        "key": "no_journals_awaiting_approval",
        "label": "No manual journals dated in the month are awaiting approval",
        "passed": drafts == 0,
        "blocking": False,
        "overridable": False,
        "detail": ("None" if drafts == 0 else
                   f"{drafts} awaiting approval — post or reject them on the Journals page, or "
                   f"they will have to be re-dated after the close"),
    })

    blocking_failures = [c for c in checks if c["blocking"] and not c["passed"]]
    hard_failures = [c for c in blocking_failures if not c["overridable"]]
    return {
        "year": year,
        "month": month,
        "label": f"{start:%B %Y}",
        "start_date": start,
        "end_date": end,
        "state": period.state if period else PeriodState.OPEN,
        "checks": checks,
        "can_close": not blocking_failures,
        "can_force": not hard_failures,
        "trial_balance": balance,
        "entries": JournalEntry.objects.filter(entry_date__range=(start, end)).count(),
    }


def _unposted_transactions(start: date, end: date) -> list[Transaction]:
    """Transactions in the range that should have raised an entry but did not.

    Asks the ledger what it WOULD post rather than restating its rules here, so
    this check cannot drift out of step with `_lines_for`. Several transaction
    types legitimately raise no entry at all: a FEE is informational because the
    fee is already inside the disbursement entry, an interest waiver touches
    nothing because interest is recognised on collection, and a write-off or a
    capitalisation of nothing has nothing to post.
    """
    from . import ledger as gl

    candidates = (Transaction.objects
                  .filter(txn_date__range=(start, end), journal_entry__isnull=True)
                  .select_related("loan"))
    return [txn for txn in candidates if gl.would_post(txn)]


def _instalments_awaiting_penalties(end: date) -> int:
    from ..models import Instalment, InstalmentStatus

    return (Instalment.objects
            .filter(due_date__lte=end, loan__status=LoanStatus.ACTIVE)
            .exclude(status=InstalmentStatus.PAID)
            .filter(Q(last_penalty_date__isnull=True) | Q(last_penalty_date__lt=end))
            .count())


def reconciliation_now() -> dict:
    """The four sub-ledger identities as they stand, for the close screen."""
    from . import ledger as gl

    by_code = {row["code"]: row["balance"] for row in gl.trial_balance()["rows"]}
    active = Loan.objects.filter(status=LoanStatus.ACTIVE)
    book = {
        "1100": active.aggregate(v=Sum("principal_outstanding"))["v"] or ZERO,
        "1300": active.aggregate(v=Sum("penalties_outstanding"))["v"] or ZERO,
        "1400": active.aggregate(v=Sum("charges_outstanding"))["v"] or ZERO,
        "2000": SavingsAccount.objects.aggregate(v=Sum("balance"))["v"] or ZERO,
    }
    names = {
        "1100": "Loans receivable", "1300": "Penalties receivable",
        "1400": "Charges receivable", "2000": "Client savings",
    }
    rows = []
    for code, expected in book.items():
        ledger_value = by_code.get(code, ZERO)
        rows.append({
            "code": code, "name": names[code], "ledger": ledger_value,
            "book": expected, "difference": ledger_value - expected,
            "agrees": ledger_value == expected,
        })
    return {"rows": rows, "agrees": all(r["agrees"] for r in rows)}


# ---------------------------------------------------------------- closing
@db_transaction.atomic
def close_period(year: int, month: int, user: User | None, note: str | None = None,
                 force: bool = False) -> AccountingPeriod:
    """Freeze a month and snapshot the trial balance it is signed off on."""
    checks = preflight(year, month)
    failures = [c for c in checks["checks"] if c["blocking"] and not c["passed"]]
    hard = [c for c in failures if not c["overridable"]]
    if hard:
        raise BusinessRuleError(
            f"{checks['label']} cannot be closed: "
            + "; ".join(f"{c['label']} ({c['detail']})" for c in hard))
    if failures and not force:
        raise BusinessRuleError(
            f"{checks['label']} failed {len(failures)} pre-close check(s): "
            + "; ".join(f"{c['label']} ({c['detail']})" for c in failures)
            + ". Fix them, or close anyway to record the exception.")

    start, end = checks["start_date"], checks["end_date"]
    balance = checks["trial_balance"]
    overridden = [c["label"] for c in failures]
    if overridden:
        note = ((note + " | ") if note else "") + "Closed over: " + "; ".join(overridden)

    period, _ = AccountingPeriod.objects.get_or_create(
        year=year, month=month, defaults={"state": PeriodState.CLOSED})
    period.state = PeriodState.CLOSED
    period.closed_at = timezone.now()
    period.closed_by = user if (user and user.is_authenticated) else None
    period.note = note
    period.snapshot_debits = balance["total_debit"]
    period.snapshot_credits = balance["total_credit"]
    period.snapshot_entries = checks["entries"]
    period.snapshot_principal_outstanding = (
        Loan.objects.filter(status=LoanStatus.ACTIVE)
        .aggregate(v=Sum("principal_outstanding"))["v"] or ZERO)
    period.snapshot_savings_balance = (
        SavingsAccount.objects.aggregate(v=Sum("balance"))["v"] or ZERO)
    period.snapshot_json = json.dumps({
        "closed_at": timezone.now().isoformat(),
        "range": [start.isoformat(), end.isoformat()],
        "trial_balance": balance["rows"],
        "reconciliation": checks["checks"][-1]["detail"],
        "checks": [{k: c[k] for k in ("key", "passed", "detail")} for c in checks["checks"]],
    }, default=str)
    period.save()

    audit(user, "close_period", "accounting_period", period.id,
          f"{period.label} closed: Dr {period.snapshot_debits} / Cr "
          f"{period.snapshot_credits} over {period.snapshot_entries} entries"
          + (f" (forced over: {'; '.join(overridden)})" if overridden else ""))
    return period


@db_transaction.atomic
def reopen_period(year: int, month: int, user: User | None, reason: str) -> AccountingPeriod:
    """Reopen the most recently closed month.

    Only the latest closed month can be reopened, because the closed months are a
    contiguous prefix and reopening one from the middle would put a hole in it.
    Reopening August when September is also closed means reopening September
    first, which is the honest order anyway: you cannot revisit August's numbers
    while September's are signed off on top of them.
    """
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise BusinessRuleError(
            "Reopening a closed period needs a reason of at least 10 characters, "
            "recorded in the audit trail.")

    period = AccountingPeriod.objects.filter(year=year, month=month).first()
    if period is None or period.is_open:
        raise BusinessRuleError(f"{year}-{month:02d} is not closed.")

    latest = (AccountingPeriod.objects.filter(state=PeriodState.CLOSED)
              .order_by("-end_date").first())
    if latest and latest.id != period.id:
        raise PeriodClosedError(
            f"{latest.label} is closed after {period.label}. Reopen {latest.label} "
            f"first — the closed months have to stay contiguous for "
            f"'the earliest date you can post to' to mean anything.")

    snapshot = f"Dr {period.snapshot_debits} / Cr {period.snapshot_credits}"
    period.state = PeriodState.OPEN
    period.reopened_at = timezone.now()
    period.reopened_by = user if (user and user.is_authenticated) else None
    period.reopen_reason = reason
    period.reopen_count += 1
    period.save()

    audit(user, "reopen_period", "accounting_period", period.id,
          f"{period.label} reopened (reopen #{period.reopen_count}) — {reason}. "
          f"Trial balance at the close being superseded: {snapshot}")
    return period


# ---------------------------------------------------------------- the register
def register(year: int | None = None) -> dict:
    """Twelve months of one year, with every closed one's snapshot.

    Months with no row are reported as open, or as closed-by-implication when they
    fall before the close-through date: closing August closes July whether or not
    July has a row of its own, and the register has to say so rather than show a
    green Open tag over a month nothing can post to.
    """
    today = date.today()
    year = year or today.year
    through = closed_through()
    rows_by_month = {p.month: p for p in AccountingPeriod.objects.filter(year=year)}

    months = []
    for month in range(1, 13):
        start, end = month_bounds(year, month)
        period = rows_by_month.get(month)
        if period and not period.is_open:
            state, implied = PeriodState.CLOSED, False
        elif through is not None and end <= through:
            state, implied = PeriodState.CLOSED, True
        else:
            state, implied = PeriodState.OPEN, False
        months.append({
            "year": year, "month": month, "label": f"{start:%B %Y}",
            "start_date": start, "end_date": end, "state": state,
            "closed_by_implication": implied,
            "has_ended": end < today,
            # Any ended month ahead of the close-through date may be closed, and
            # doing so absorbs the months between. Mirrors the closes_forwards
            # check, so the button is offered exactly where the close would pass.
            "closable": (state == PeriodState.OPEN and end < today
                         and (through is None or start > through)),
            "period": period,
            "transactions": Transaction.objects.filter(txn_date__range=(start, end)).count(),
        })

    closed_rows = AccountingPeriod.objects.filter(state=PeriodState.CLOSED).order_by("-end_date")
    last_closed = closed_rows.first()
    return {
        "year": year,
        "months": months,
        "open_months": sum(1 for m in months if m["state"] == PeriodState.OPEN),
        "closed_through": through,
        "earliest_postable_date": earliest_postable_date(),
        "last_closed": last_closed,
        "next_to_close": _next_to_close(through, today),
        "reopened_periods": AccountingPeriod.objects.filter(reopen_count__gt=0).count(),
    }


def _next_to_close(through: date | None, today: date) -> dict | None:
    """The earliest month that has ended and can still be closed.

    Suggested rather than required: any later ended month is equally legal and
    absorbs the ones between. This is the smallest step, which is the right default
    for a book that is being closed month by month.
    """
    if through is not None:
        candidate = through + timedelta(days=1)
    else:
        first = Transaction.objects.order_by("txn_date").values_list("txn_date", flat=True).first()
        if first is None:
            return None
        candidate = first.replace(day=1)
    start, end = month_bounds(candidate.year, candidate.month)
    if end >= today:
        return None
    return {"year": start.year, "month": start.month, "label": f"{start:%B %Y}",
            "end_date": end}


def status_for(on: date | None = None) -> dict:
    """What the React date fields and banners need for one date."""
    on = on or date.today()
    through = closed_through()
    return {
        "on": on,
        "state": state_on(on),
        "is_open": not is_closed(on),
        "closed_through": through,
        "earliest_postable_date": earliest_postable_date(),
    }


def get_period(year: int, month: int) -> AccountingPeriod:
    month_bounds(year, month)  # validates
    period = AccountingPeriod.objects.filter(year=year, month=month).first()
    if period is None:
        raise NotFound(f"{year}-{month:02d} has never been closed.")
    return period
