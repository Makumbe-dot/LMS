"""Booking the IFRS 9 expected credit loss provision to the general ledger.

`reports.ecl_report()` has always staged the book and computed a provision.
Nothing booked it: accounts 1900 (Provision for credit losses) and 5100
(Impairment charge) existed and carried no lines, so the ledger overstated net
assets by the whole expected loss.

This module books it, and books only the MOVEMENT. The provision carried is
tracked per loan in `Loan.provision_held`, which gives a fifth reconciliation
identity in the same shape as the existing four:

    the credit balance on account 1900 == SUM(loans.provision_held)

over **every** loan, not only active ones — a closed loan keeps its provision
until a run sweeps it.

Two deliberate decisions:

* **The provision is booked on the recognised carrying amount** (principal +
  penalties + charges outstanding — exactly what accounts 1100, 1300 and 1400
  hold), not on total outstanding. Interest is recognised when collected and
  there is no interest receivable, so provisioning gross exposure would put a
  provision against an asset the ledger does not carry. The ECL report keeps
  its gross figures for disclosure and gains the bookable ones beside them.

* **A run reads balances as they stand when it executes.** `period_end` is the
  label the run is filed under and the key that makes it idempotent; it is not a
  point-in-time restatement of the book. A run behind the latest posted one is
  refused, because its movement would be computed against today's carried
  provision and would mean nothing.
"""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    ECLStage,
    JournalEntry,
    Loan,
    LoanStatus,
    OrganisationSetting,
    ProvisionRun,
    ProvisionRunLine,
    ProvisionRunStatus,
    User,
)
from . import arrears as arrears_svc
from . import ledger, periods
from .amortisation import month_end, q
from .loans import arrears, next_number
from .reports import ecl_stage

ZERO = Decimal("0")

PROVISION = ledger.CODES["provision"]      # 1900, a contra-asset carrying a credit balance
IMPAIRMENT = ledger.CODES["impairment"]    # 5100, the P&L charge


# ---------------------------------------------------------------- measurement
def carrying_amount(loan: Loan) -> Decimal:
    """The recognised asset behind a loan: what 1100 + 1300 + 1400 actually hold."""
    return q((loan.principal_outstanding or ZERO)
             + (loan.penalties_outstanding or ZERO)
             + (loan.charges_outstanding or ZERO))


def _rate_for(stage: str, cfg: OrganisationSetting) -> Decimal:
    return {
        ECLStage.STAGE_1: cfg.ecl_stage1_pct,
        ECLStage.STAGE_2: cfg.ecl_stage2_pct,
        ECLStage.STAGE_3: cfg.ecl_stage3_pct,
    }[stage]


def assess(loan: Loan, cfg: OrganisationSetting, as_of: date,
           days: int | None = None) -> dict:
    """Stage one active loan and work out what it should carry.

    `days` is passed in by the run, which reads it off a set-based annotation for
    the whole book at once. Left out, it falls back to walking this loan's schedule.
    """
    if days is None:
        _amount, days = arrears(loan, as_of)
    stage = ecl_stage(days, cfg)
    rate = _rate_for(stage, cfg)
    carrying = carrying_amount(loan)
    required = q(carrying * rate / 100)
    before = q(loan.provision_held or ZERO)
    return {
        "loan_status": loan.status,
        "stage": stage,
        "days_past_due": days,
        "exposure": q(loan.total_outstanding),
        "carrying_amount": carrying,
        "rate_pct": rate,
        "provision_required": required,
        "provision_before": before,
        "provision_after": required,
        "movement": q(required - before),
    }


def release_line(loan: Loan) -> dict:
    """A loan that has left the active book but still carries a provision.

    Exposure and carrying amount are reported as zero rather than as the loan's
    live balances: a written-off loan keeps non-zero balances (write_off does
    not zero them), and counting those in a run's totals would silently include
    loans that are off the book.
    """
    before = q(loan.provision_held or ZERO)
    return {
        "loan_status": loan.status,
        "stage": None,
        "days_past_due": 0,
        "exposure": ZERO,
        "carrying_amount": ZERO,
        "rate_pct": ZERO,
        "provision_required": ZERO,
        "provision_before": before,
        "provision_after": ZERO,
        "movement": q(-before),
    }


def booked_provision() -> Decimal:
    """The provision carried across every loan — the sub-ledger behind 1900."""
    total = Loan.objects.aggregate(total=Sum("provision_held"))["total"]
    return q(total or ZERO)


def ledger_provision(as_of: date | None = None) -> Decimal:
    """The credit balance standing on 1900, as a POSITIVE number.

    1900 is typed ASSET (it is a contra-asset), so every generic ledger helper
    reports it as debit-minus-credit, i.e. negative. The flip is explicit here.
    """
    debit, credit = ledger.account_movement(PROVISION, end=as_of)
    return q(credit - debit)


# ---------------------------------------------------------------- the run
def _gather(cfg: OrganisationSetting, as_of: date) -> tuple[list, int, int]:
    """(rows, assessed, released) for every loan the run touches."""
    rows = []
    assessed = released = 0

    # Days past due for the whole active book in one statement, rather than every
    # loan's schedule fetched and walked. Staging and carrying_amount read only the
    # loan's own columns, so nothing else needs the instalments.
    active = arrears_svc.with_arrears(
        Loan.objects.filter(status=LoanStatus.ACTIVE)
        .select_related("borrower", "product", "branch"), as_of)
    for loan in active:
        days = arrears_svc.days_from(loan.oldest_arrears_due, as_of)
        rows.append((loan, assess(loan, cfg, as_of, days=days)))
        assessed += 1

    stale = (Loan.objects
             .exclude(status=LoanStatus.ACTIVE)
             .filter(provision_held__gt=0))
    for loan in stale:
        rows.append((loan, release_line(loan)))
        released += 1

    return rows, assessed, released


def preview(as_of: date | None = None) -> dict:
    """What a run for this period would post. Posts nothing."""
    cfg = OrganisationSetting.load()
    as_of = as_of or date.today()
    period = month_end(as_of)
    rows, assessed, released = _gather(cfg, as_of)

    required = q(sum((a["provision_required"] for _l, a in rows), ZERO))
    before = booked_provision()
    existing = ProvisionRun.objects.filter(period_end=period,
                                           status=ProvisionRunStatus.POSTED).first()
    in_ledger = ledger_provision()
    return {
        "period_end": period,
        "entry_date": min(period, date.today()),
        "loans_assessed": assessed,
        "loans_released": released,
        "total_exposure": q(sum((a["exposure"] for _l, a in rows), ZERO)),
        "total_carrying_amount": q(sum((a["carrying_amount"] for _l, a in rows), ZERO)),
        "provision_required": required,
        "provision_booked": before,
        "movement": q(required - before),
        "ledger_provision": in_ledger,
        "ledger_agrees": in_ledger == before,
        "already_posted": existing is not None,
        "existing_run_no": existing.run_no if existing else None,
        "rates": {
            "stage_1": cfg.ecl_stage1_pct, "stage_2": cfg.ecl_stage2_pct,
            "stage_3": cfg.ecl_stage3_pct, "stage_2_days": cfg.ecl_stage2_days,
            "stage_3_days": cfg.ecl_stage3_days,
        },
    }


@db_transaction.atomic
def run_provision(user: User | None = None, as_of: date | None = None, *,
                  narration: str | None = None, force: bool = False) -> ProvisionRun:
    """Book the month's movement in the provision.

    Sets a transient `created_now` attribute on the returned run: False when an
    existing posted run for the period was handed back untouched.
    """
    cfg = OrganisationSetting.load()
    as_of = as_of or date.today()
    period = month_end(as_of)

    existing = ProvisionRun.objects.filter(period_end=period,
                                           status=ProvisionRunStatus.POSTED).first()
    if existing and not force:
        existing.created_now = False
        return existing

    latest = (ProvisionRun.objects
              .filter(status=ProvisionRunStatus.POSTED)
              .exclude(period_end=period)
              .order_by("-period_end").first())
    if latest and period < latest.period_end:
        raise BusinessRuleError(
            f"{period} is behind the latest posted run ({latest.run_no} for "
            f"{latest.period_end}). A run reads today's balances, so booking it into an "
            f"earlier period would post a figure that means nothing.")

    # The entry is dated at the period end, or today when the period has not ended
    # yet; refuse up front rather than let the JournalEntry guard fire half way
    # through the run.
    periods.assert_open(min(period, date.today()), "This provision run")

    # Read the ledger BEFORE anything is posted, or the field whose whole purpose
    # is to reveal drift would always agree with itself.
    ledger_before = ledger_provision()

    if existing and force:
        reverse_run(existing, user, f"Reversed by a forced re-run of {period}")

    rows, assessed, released = _gather(cfg, as_of)
    required = q(sum((a["provision_required"] for _l, a in rows), ZERO))
    before = booked_provision()
    movement = q(required - before)
    entry_date = min(period, date.today())

    entry = None
    if movement != 0:
        if movement > 0:
            lines = [
                (IMPAIRMENT, movement, ZERO, "Increase in the expected credit loss provision"),
                (PROVISION, ZERO, movement, "Provision raised"),
            ]
        else:
            amount = q(-movement)
            lines = [
                (PROVISION, amount, ZERO, "Provision released"),
                (IMPAIRMENT, ZERO, amount, "Release of the expected credit loss provision"),
            ]
        entry = ledger.post_manual_entry(
            lines, entry_date,
            narration or f"Expected credit loss provision for {period}",
            source="provision", posted_by=user, strict=True)

    try:
        with db_transaction.atomic():
            run = ProvisionRun.objects.create(
                run_no=next_number("PRV"), period_end=period,
                status=ProvisionRunStatus.POSTED,
                loans_assessed=assessed, loans_released=released,
                total_exposure=q(sum((a["exposure"] for _l, a in rows), ZERO)),
                total_carrying_amount=q(sum((a["carrying_amount"] for _l, a in rows), ZERO)),
                provision_required=required, provision_before=before, movement=movement,
                ledger_provision_before=ledger_before,
                stage1_pct=cfg.ecl_stage1_pct, stage2_pct=cfg.ecl_stage2_pct,
                stage3_pct=cfg.ecl_stage3_pct, stage2_days=cfg.ecl_stage2_days,
                stage3_days=cfg.ecl_stage3_days,
                journal_entry=entry, narration=narration, run_by=user,
            )
    except IntegrityError:
        raise BusinessRuleError(
            f"{period} was provisioned by another operator a moment ago. Refresh and look "
            f"at the run that landed before booking again.")

    ProvisionRunLine.objects.bulk_create([
        ProvisionRunLine(
            run=run, loan=loan, loan_status=a["loan_status"], stage=a["stage"],
            days_past_due=a["days_past_due"], exposure=a["exposure"],
            carrying_amount=a["carrying_amount"], rate_pct=a["rate_pct"],
            provision_required=a["provision_required"], provision_before=a["provision_before"],
            provision_after=a["provision_after"], movement=a["movement"],
        ) for loan, a in rows
    ])

    changed = [loan for loan, a in rows if q(loan.provision_held or ZERO) != a["provision_after"]]
    for loan, a in rows:
        loan.provision_held = a["provision_after"]
    if changed:
        Loan.objects.bulk_update(changed, ["provision_held"], batch_size=100)

    run.created_now = True
    return run


@db_transaction.atomic
def reverse_run(run: ProvisionRun, user: User | None, narration: str) -> ProvisionRun:
    """Undo a run: mirror its entry and put every provision_held back."""
    if run.status != ProvisionRunStatus.POSTED:
        raise BusinessRuleError(f"{run.run_no} is already {run.status}")

    later = (ProvisionRun.objects
             .filter(status=ProvisionRunStatus.POSTED, period_end__gt=run.period_end)
             .order_by("period_end").first())
    if later:
        raise BusinessRuleError(
            f"{later.run_no} for {later.period_end} was booked after this one. Reverse that "
            f"first, or the provision carried would no longer match the ledger.")

    lines = list(run.lines.select_related("loan"))
    for line in lines:
        current = q(line.loan.provision_held or ZERO)
        if current != line.provision_after:
            raise BusinessRuleError(
                f"{line.loan.loan_no} now carries {current}, not the {line.provision_after} this "
                f"run left it with. Something has moved since; reversing would break the tie "
                f"between account 1900 and the loan book.")

    mirror = None
    if run.journal_entry_id:
        original = run.journal_entry
        # The mirror carries the original's date, so a closed month refuses the
        # reversal outright. That is the correct answer: a provision signed off in
        # a closed month cannot be quietly undone.
        periods.assert_open(
            original.entry_date,
            f"Reversing {run.run_no}, whose entry is dated {original.entry_date.isoformat()},")
        # Date the mirror on the original, so a run and its reversal net to zero
        # inside the same income-statement window.
        mirror = ledger.post_manual_entry(
            [(l.account.code, l.credit, l.debit, f"Reversal: {l.description or ''}".strip())
             for l in original.lines.select_related("account")],
            original.entry_date,
            f"Reversal of {run.run_no}: {narration}",
            source="provision_rev", posted_by=user, strict=True)

    for line in lines:
        line.loan.provision_held = line.provision_before
    Loan.objects.bulk_update([line.loan for line in lines], ["provision_held"], batch_size=100)

    run.status = ProvisionRunStatus.REVERSED
    run.reversal_entry = mirror
    run.reversed_at = timezone.now()
    run.reversed_by = user
    run.narration = f"{run.narration or ''}\nReversed: {narration}".strip()
    run.save(update_fields=["status", "reversal_entry", "reversed_at", "reversed_by",
                            "narration"])
    return run


@db_transaction.atomic
def release_on_write_off(loan: Loan, user: User | None = None,
                         txn_date: date | None = None) -> Decimal:
    """Release the provision carried against a loan leaving the book.

    Raised inline from loans.write_off, so the month that carries the 5000
    expense also carries the offsetting 5100 credit, and so it can never be
    forgotten or run twice.
    """
    amount = q(loan.provision_held or ZERO)
    if amount <= 0:
        return ZERO

    ledger.post_manual_entry(
        [(PROVISION, amount, ZERO, f"Provision released on write-off of {loan.loan_no}"),
         (IMPAIRMENT, ZERO, amount, "Release of the expected credit loss provision")],
        txn_date or date.today(),
        f"Provision released on write-off of {loan.loan_no}",
        source="provision_rel", loan=loan, branch_id=loan.branch_id, posted_by=user,
        strict=False)

    loan.provision_held = ZERO
    loan.save(update_fields=["provision_held"])
    return amount


def repost_runs() -> dict:
    """Re-raise the journal entries for posted runs that have lost them.

    `ledger.backfill()` sweeps Transaction and SavingsTransaction; a provision
    entry stands behind neither, so a JournalEntry wipe followed by Rebuild
    would leave 1900 at zero while the loans still carry a provision. This is
    the repost path that makes the fifth identity recoverable.

    Runs inside allow_closed_posting for the same reason backfill does: these runs
    already happened, their dates are already history, and refusing to re-account
    for them because the month is closed would leave 1900 permanently short.
    """
    with periods.allow_closed_posting("provision run repost"):
        return _repost_runs()


def _repost_runs() -> dict:
    reposted = 0
    for run in ProvisionRun.objects.filter(status=ProvisionRunStatus.POSTED,
                                           journal_entry__isnull=True).exclude(movement=0):
        movement = q(run.movement)
        if movement > 0:
            lines = [(IMPAIRMENT, movement, ZERO, "Increase in the expected credit loss provision"),
                     (PROVISION, ZERO, movement, "Provision raised")]
        else:
            amount = q(-movement)
            lines = [(PROVISION, amount, ZERO, "Provision released"),
                     (IMPAIRMENT, ZERO, amount, "Release of the expected credit loss provision")]
        entry = ledger.post_manual_entry(
            lines, min(run.period_end, date.today()),
            f"Re-posted: expected credit loss provision for {run.period_end}",
            source="provision", posted_by=run.run_by, strict=False)
        if entry:
            run.journal_entry = entry
            run.save(update_fields=["journal_entry"])
            reposted += 1

    # A write-off release also stands behind no Transaction. Any loan that is off
    # the book and still carries a provision is swept by the next run, so there is
    # nothing to repost here beyond the runs themselves.
    return {"reposted": reposted}


def last_run() -> dict | None:
    run = ProvisionRun.objects.filter(status=ProvisionRunStatus.POSTED).first()
    if run is None:
        return None
    return {
        "run_no": run.run_no, "period_end": run.period_end, "movement": run.movement,
        "provision_required": run.provision_required, "created_at": run.created_at,
    }
