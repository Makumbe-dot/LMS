"""Loan lifecycle: quote, apply, approve, reject, disburse, balances, arrears,
write-off and reschedule.

Services mutate model instances in memory and persist them through
refresh_balances(), which is the single place that writes the schedule and the
loan's running balances back to SQL Server. Callers wrap their work in
transaction.atomic().
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Prefetch

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    Instalment,
    InstalmentStatus,
    Loan,
    LoanProduct,
    LoanStatus,
    OrganisationSetting,
    Role,
    Sequence,
    Transaction,
    TxnType,
    User,
)
from . import charges
from .amortisation import add_months, build_schedule, q, set_day, total_interest
from .scoring import score_application, store_on_loan

ZERO = Decimal("0")

# Instalment columns that the services mutate in memory.
INSTALMENT_WRITE_FIELDS = [
    "principal_due", "interest_due", "penalty_due", "charge_due",
    "principal_paid", "interest_paid", "penalty_paid", "charge_paid",
    "status", "paid_date", "last_penalty_date",
]

# Loan columns that refresh_balances owns.
LOAN_BALANCE_FIELDS = [
    "principal_outstanding", "interest_outstanding", "penalties_outstanding",
    "charges_outstanding", "total_paid", "status", "closed_at",
]


def sched(loan: Loan) -> list[Instalment]:
    """The loan's instalments as one stable in-memory list.

    Repeated calls hand back the same objects, so a service can mutate an
    instalment and a later call sees the change - the behaviour the lifecycle
    logic relies on. Uses a prefetch when the caller set one up.
    """
    cached = getattr(loan, "_sched_cache", None)
    if cached is not None:
        return cached
    prefetched = getattr(loan, "_prefetched_objects_cache", {})
    if "instalments" in prefetched:
        loan._sched_cache = list(loan.instalments.all())
    else:
        loan._sched_cache = list(loan.instalments.order_by("number"))
    return loan._sched_cache


def set_sched(loan: Loan, rows: list[Instalment]) -> None:
    loan._sched_cache = rows


def _create_schedule(loan: Loan, rows) -> list[Instalment]:
    """Insert an amortisation schedule and re-read it so every row carries its id.

    SQL Server does not hand back identity values from a multi-row INSERT, so the
    objects bulk_create() returns have no primary key and could not later be
    bulk_updated. Reading the schedule back is one cheap query and keeps the
    in-memory list authoritative.
    """
    Instalment.objects.bulk_create([
        Instalment(
            loan=loan, number=r.number, due_date=r.due_date, opening_balance=r.opening_balance,
            principal_due=r.principal_due, interest_due=r.interest_due,
            closing_balance=r.closing_balance,
        ) for r in rows
    ])
    fresh = list(Instalment.objects.filter(loan_id=loan.pk).order_by("number"))
    set_sched(loan, fresh)
    return fresh


def next_number(prefix: str, width: int = 6) -> str:
    """Next human-readable number for a prefix, e.g. LN-000045.

    The counter row is locked for the duration of the surrounding transaction
    (SQL Server takes an UPDLOCK), so two officers capturing applications at the
    same moment cannot be handed the same number.
    """
    with transaction.atomic():
        seq = Sequence.objects.select_for_update().filter(pk=prefix).first()
        if seq is None:
            try:
                with transaction.atomic():
                    Sequence.objects.create(prefix=prefix, value=0)
            except IntegrityError:
                pass  # another transaction created it first
            seq = Sequence.objects.select_for_update().get(pk=prefix)
        seq.value += 1
        seq.save(update_fields=["value"])
        return f"{prefix}-{seq.value:0{width}d}"


def _fees(product: LoanProduct, principal: Decimal) -> tuple[Decimal, Decimal]:
    admin = q(principal * product.admin_fee_pct / 100)
    ins = q(principal * product.insurance_fee_pct / 100)
    return admin, ins


def default_first_due(disb: date, payday: int | None) -> date:
    """First instalment falls on the borrower's next payday at least ~2 weeks after disbursement."""
    if payday:
        candidate = set_day(disb, payday)
        if (candidate - disb).days < 14:
            candidate = set_day(add_months(disb, 1), payday)
        return candidate
    return add_months(disb, 1)


def validate_terms(product: LoanProduct, principal: Decimal, term: int) -> None:
    if not product.is_active:
        raise BusinessRuleError("Product is not active")
    if not (product.min_amount <= principal <= product.max_amount):
        raise BusinessRuleError(
            f"Principal must be between {product.min_amount} and {product.max_amount}")
    if not (product.min_term_months <= term <= product.max_term_months):
        raise BusinessRuleError(
            f"Term must be between {product.min_term_months} and {product.max_term_months} months")


def quote(product: LoanProduct, principal: Decimal, term: int,
          disbursement_date: date | None = None, borrower: Borrower | None = None) -> dict:
    """Indicative pricing and schedule, plus the affordability check."""
    validate_terms(product, principal, term)
    disb = disbursement_date or date.today()
    first_due = default_first_due(disb, borrower.payday if borrower else None)
    rows = build_schedule(principal, product.interest_rate_pct, term, first_due,
                          product.rate_method)
    admin, ins = _fees(product, principal)
    catalogue = charges.quote_for(product, principal)
    other = q(sum((row["amount"] for row in catalogue), ZERO))
    ti = total_interest(rows)
    aff_pct = affordable = None
    if borrower and borrower.net_salary and borrower.net_salary > 0:
        aff_pct = q(rows[0].instalment / borrower.net_salary * 100)
        affordable = aff_pct <= product.max_instalment_to_salary_pct
    return {
        "principal": q(principal),
        "term_months": term,
        "interest_rate_pct": product.interest_rate_pct,
        "rate_method": product.rate_method,
        "instalment_amount": rows[0].instalment,
        "total_interest": ti,
        "total_repayable": q(principal + ti),
        "admin_fee": admin,
        "insurance_fee": ins,
        "other_charges": other,
        "charges": catalogue,
        "net_disbursed": q(principal - admin - ins - other),
        "affordability_pct": aff_pct,
        "affordable": affordable,
        "schedule": [vars(r) for r in rows],
        "scorecard": (score_application(borrower, product, principal, term, rows[0].instalment)
                      if borrower else None),
    }


def apply(borrower: Borrower, product: LoanProduct, principal: Decimal, term: int,
          purpose: str | None, officer: User, application_date: date | None = None,
          refinanced_from: Loan | None = None, group=None) -> Loan:
    from .groups import check_can_borrow

    validate_terms(product, principal, term)
    if borrower.is_blacklisted:
        raise BusinessRuleError("Borrower is blacklisted")
    if not borrower.kyc_verified:
        raise BusinessRuleError("Borrower KYC is not verified")

    open_loans = borrower.loans.filter(
        status__in=[LoanStatus.PENDING, LoanStatus.APPROVED, LoanStatus.ACTIVE])
    if refinanced_from is not None:
        # A top-up is allowed to sit beside the loan it will settle, and nothing else.
        open_loans = open_loans.exclude(pk=refinanced_from.pk)
    open_loan = open_loans.first()
    if open_loan:
        raise BusinessRuleError(f"Borrower already has an open loan ({open_loan.loan_no})")

    # Joint liability: the group's standing is a fact about this application.
    check_can_borrow(borrower, application_date)
    qt = quote(product, principal, term, application_date, borrower)
    if qt["affordable"] is False:
        raise BusinessRuleError(
            f"Instalment is {qt['affordability_pct']}% of net salary; "
            f"product limit is {product.max_instalment_to_salary_pct}%")
    loan = Loan.objects.create(
        loan_no=next_number("LN"), borrower=borrower, product=product, officer=officer,
        branch_id=borrower.branch_id or getattr(officer, "branch_id", None),
        principal=q(principal), interest_rate_pct=product.interest_rate_pct,
        rate_method=product.rate_method, term_months=term,
        purpose=purpose, admin_fee=qt["admin_fee"], insurance_fee=qt["insurance_fee"],
        other_charges=qt["other_charges"],
        instalment_amount=qt["instalment_amount"], total_interest=qt["total_interest"],
        status=LoanStatus.PENDING, application_date=application_date or date.today(),
        refinanced_from=refinanced_from, group=group,
    )
    set_sched(loan, [])
    if qt.get("scorecard"):
        store_on_loan(loan, qt["scorecard"])
    return loan


def approve(loan: Loan, user: User) -> Loan:
    if loan.status != LoanStatus.PENDING:
        raise BusinessRuleError(f"Loan is {loan.status}, cannot approve")
    if user.id == loan.officer_id and user.role != Role.ADMIN:
        raise BusinessRuleError("The originating officer cannot approve their own loan")
    limit = OrganisationSetting.load().officer_approval_limit
    if user.role != Role.ADMIN and loan.principal > limit:
        raise BusinessRuleError(
            f"{loan.principal} is above the {limit} a loan officer may approve; "
            f"this one needs an administrator")
    loan.status = LoanStatus.APPROVED
    loan.approved_at = datetime.now(timezone.utc)
    loan.approved_by = user
    loan.save(update_fields=["status", "approved_at", "approved_by"])
    return loan


def reject(loan: Loan, user: User, reason: str | None) -> Loan:
    if loan.status not in (LoanStatus.PENDING, LoanStatus.APPROVED):
        raise BusinessRuleError(f"Loan is {loan.status}, cannot reject")
    loan.status = LoanStatus.REJECTED
    loan.rejection_reason = reason or "Rejected"
    loan.save(update_fields=["status", "rejection_reason"])
    return loan


def disburse(loan: Loan, user: User, disbursement_date: date | None,
             first_instalment_date: date | None, method: str, reference: str | None) -> Loan:
    if loan.status != LoanStatus.APPROVED:
        raise BusinessRuleError(f"Loan is {loan.status}, must be approved before disbursement")
    disb = disbursement_date or date.today()
    first_due = first_instalment_date or default_first_due(disb, loan.borrower.payday)
    if first_due <= disb:
        raise BusinessRuleError("First instalment date must be after the disbursement date")
    rows = build_schedule(loan.principal, loan.interest_rate_pct, loan.term_months, first_due,
                          loan.rate_method)
    _create_schedule(loan, rows)

    loan.status = LoanStatus.ACTIVE
    loan.disbursement_date = disb
    loan.first_instalment_date = first_due
    loan.maturity_date = rows[-1].due_date
    loan.instalment_amount = rows[0].instalment
    loan.total_interest = total_interest(rows)
    loan.principal_outstanding = loan.principal
    loan.interest_outstanding = loan.total_interest
    loan.penalties_outstanding = ZERO
    loan.charges_outstanding = ZERO
    loan.total_paid = ZERO
    loan.save(update_fields=[
        "status", "disbursement_date", "first_instalment_date", "maturity_date",
        "instalment_amount", "total_interest", "principal_outstanding",
        "interest_outstanding", "penalties_outstanding", "charges_outstanding", "total_paid",
    ])

    fees = q(loan.admin_fee + loan.insurance_fee + loan.other_charges)
    db_txn = Transaction.objects.create(
        loan=loan, txn_type=TxnType.DISBURSEMENT, txn_date=disb, amount=q(loan.principal - fees),
        principal_component=loan.principal, method=method, reference=reference, posted_by=user,
        narration=f"Disbursement of {loan.principal} less fees {fees}",
    )
    if fees > 0:
        Transaction.objects.create(
            loan=loan, txn_type=TxnType.FEE, txn_date=disb, amount=fees, posted_by=user,
            narration="Upfront fees and charges deducted at disbursement",
        )
        charges.raise_at_disbursement(loan, user, disb, db_txn)

    # A top-up settles the loan it replaces out of its own proceeds, so the
    # borrower only ever receives the difference.
    if loan.refinanced_from_id:
        old = (Loan.objects
               .select_related("borrower", "product")
               .prefetch_related(Prefetch("instalments",
                                          queryset=Instalment.objects.order_by("number")))
               .get(pk=loan.refinanced_from_id))
        if old.status == LoanStatus.ACTIVE:
            settle_early(old, user, None, disb, method, loan.loan_no,
                         f"Settled from the proceeds of {loan.loan_no}")
    return loan


def refresh_balances(loan: Loan, as_of: date | None = None, save: bool = True) -> Loan:
    """Recompute the loan's running balances and instalment statuses from the schedule,
    then write the schedule and the loan back to the database."""
    as_of = as_of or date.today()
    p = i = pen = chg = paid = ZERO
    rows = sched(loan)
    for ins in rows:
        p += ins.principal_due - ins.principal_paid
        i += ins.interest_due - ins.interest_paid
        pen += ins.penalty_due - ins.penalty_paid
        chg += ins.charge_due - ins.charge_paid
        paid += ins.total_paid
        if ins.balance <= 0:
            ins.status = InstalmentStatus.PAID
        elif ins.total_paid > 0:
            ins.status = (InstalmentStatus.OVERDUE if ins.due_date < as_of
                          else InstalmentStatus.PARTIAL)
        else:
            ins.status = (InstalmentStatus.OVERDUE if ins.due_date < as_of
                          else InstalmentStatus.PENDING)
    loan.principal_outstanding = q(p)
    loan.interest_outstanding = q(i)
    loan.penalties_outstanding = q(pen)
    loan.charges_outstanding = q(chg)
    loan.total_paid = q(paid)
    if loan.status == LoanStatus.ACTIVE and loan.total_outstanding <= 0 and rows:
        loan.status = LoanStatus.CLOSED
        last_paid = max((r.paid_date for r in rows if r.paid_date), default=None)
        loan.closed_at = (datetime.combine(last_paid, datetime.min.time(), tzinfo=timezone.utc)
                          if last_paid else datetime.now(timezone.utc))
    if save:
        if rows:
            Instalment.objects.bulk_update(rows, INSTALMENT_WRITE_FIELDS)
        loan.save(update_fields=LOAN_BALANCE_FIELDS)
    return loan


def arrears(loan: Loan, as_of: date | None = None) -> tuple[Decimal, int]:
    """(amount overdue, days since the oldest unpaid due date)."""
    as_of = as_of or date.today()
    amt = ZERO
    days = 0
    for ins in sched(loan):
        if ins.due_date < as_of and ins.balance > 0:
            amt += ins.balance
            days = max(days, (as_of - ins.due_date).days)
    return q(amt), days


def settlement_quote(loan: Loan, as_of: date | None = None) -> dict:
    """What it costs to close this loan today.

    Interest that has already fallen due is payable; interest on instalments not
    yet due has not been earned, so it is rebated rather than collected. The
    figure therefore sits below the raw total outstanding.
    """
    if loan.status != LoanStatus.ACTIVE:
        raise BusinessRuleError(f"Loan is {loan.status}; only active loans can be settled early")
    as_of = as_of or date.today()
    principal_due = interest_earned = interest_unearned = penalties = charges = ZERO
    for ins in sched(loan):
        principal_due += ins.principal_due - ins.principal_paid
        penalties += ins.penalty_due - ins.penalty_paid
        charges += ins.charge_due - ins.charge_paid
        unpaid_interest = ins.interest_due - ins.interest_paid
        if ins.due_date <= as_of:
            interest_earned += unpaid_interest
        else:
            interest_unearned += unpaid_interest
    payoff = q(principal_due + interest_earned + penalties + charges)
    return {
        "as_of": as_of,
        "loan_no": loan.loan_no,
        "principal_outstanding": q(principal_due),
        "interest_accrued": q(interest_earned),
        "penalties_outstanding": q(penalties),
        "charges_outstanding": q(charges),
        "interest_rebate": q(interest_unearned),
        "settlement_amount": payoff,
        "total_outstanding": q(loan.total_outstanding),
        "saving_vs_running_to_term": q(interest_unearned),
    }


def settle_early(loan: Loan, user: User, amount: Decimal | None, txn_date: date | None,
                 method: str, reference: str | None, narration: str) -> dict:
    """Rebate the unearned interest, then take the payoff figure as one repayment."""
    from .repayments import post_repayment  # local import: repayments imports this module

    as_of = txn_date or date.today()
    quote_now = settlement_quote(loan, as_of)
    expected = quote_now["settlement_amount"]
    if amount is not None and q(Decimal(amount)) != expected:
        raise BusinessRuleError(
            f"Settlement amount must be exactly {expected}; the quote changes as interest accrues")

    rebate = quote_now["interest_rebate"]
    if rebate > 0:
        for ins in sched(loan):
            if ins.due_date > as_of:
                ins.interest_due = ins.interest_paid
        Transaction.objects.create(
            loan=loan, txn_type=TxnType.WAIVER, txn_date=as_of, amount=rebate,
            interest_component=rebate, posted_by=user,
            narration=f"Early settlement: unearned interest of {rebate} rebated",
        )
        refresh_balances(loan, as_of)

    txn = post_repayment(loan, user, expected, as_of, method, reference,
                         narration or "Early settlement")
    return {"quote": quote_now, "transaction_id": txn.id, "status": loan.status}


def write_off(loan: Loan, user: User, narration: str) -> Loan:
    if loan.status != LoanStatus.ACTIVE:
        raise BusinessRuleError("Only active loans can be written off")
    today = date.today()
    amt = loan.total_outstanding
    Transaction.objects.create(
        loan=loan, txn_type=TxnType.WRITE_OFF, txn_date=today, amount=amt,
        principal_component=loan.principal_outstanding,
        interest_component=loan.interest_outstanding,
        penalty_component=loan.penalties_outstanding,
        charge_component=loan.charges_outstanding, narration=narration, posted_by=user,
    )
    loan.status = LoanStatus.WRITTEN_OFF
    loan.closed_at = datetime.now(timezone.utc)
    loan.save(update_fields=["status", "closed_at"])

    # The month that carries the write-off expense carries the offsetting release
    # of the provision held against this loan.
    from .provisioning import release_on_write_off  # local: provisioning imports this module

    release_on_write_off(loan, user, today)
    return loan


def top_up_quote(old_loan: Loan, product: LoanProduct, principal: Decimal, term: int,
                 as_of: date | None = None) -> dict:
    """What a top-up would look like: settle the old loan, pay the borrower the rest."""
    if old_loan.status != LoanStatus.ACTIVE:
        raise BusinessRuleError(f"Loan {old_loan.loan_no} is {old_loan.status}; only an active "
                                f"loan can be topped up")
    as_of = as_of or date.today()
    settlement = settlement_quote(old_loan, as_of)
    new_quote = quote(product, principal, term, as_of, old_loan.borrower)
    cash_out = q(new_quote["net_disbursed"] - settlement["settlement_amount"])
    return {
        "existing_loan_no": old_loan.loan_no,
        "existing_loan_id": old_loan.id,
        "settlement_amount": settlement["settlement_amount"],
        "interest_rebate": settlement["interest_rebate"],
        "new_principal": q(principal),
        "new_term_months": term,
        "new_instalment": new_quote["instalment_amount"],
        "fees": q(new_quote["admin_fee"] + new_quote["insurance_fee"]
                  + new_quote["other_charges"]),
        "net_disbursed": new_quote["net_disbursed"],
        "cash_to_borrower": cash_out,
        "sufficient": cash_out >= 0,
        "quote": new_quote,
    }


def apply_top_up(old_loan: Loan, product: LoanProduct, principal: Decimal, term: int,
                 purpose: str | None, officer: User,
                 application_date: date | None = None) -> Loan:
    """Capture a top-up application against a running loan.

    Nothing is settled here. The old loan is paid off out of the new one's
    proceeds when the new loan is disbursed, so an application that is never
    approved leaves the borrower exactly where they were.
    """
    preview = top_up_quote(old_loan, product, principal, term, application_date)
    if not preview["sufficient"]:
        raise BusinessRuleError(
            f"{principal} is not enough to settle {old_loan.loan_no}: "
            f"{preview['net_disbursed']} would be advanced against a settlement figure of "
            f"{preview['settlement_amount']}")
    return apply(old_loan.borrower, product, principal, term,
                 purpose or f"Top-up of {old_loan.loan_no}", officer, application_date,
                 refinanced_from=old_loan, group=old_loan.group)


def record_recovery(loan: Loan, user: User, amount: Decimal, txn_date: date | None,
                    method: str, reference: str | None, narration: str | None) -> Transaction:
    """Money collected on a loan that was already written off.

    The loan's balances are not touched - they went to zero at write-off. The
    receipt is recorded against the loan for the borrower's history, and the
    ledger takes it to recovery income.
    """
    if loan.status != LoanStatus.WRITTEN_OFF:
        raise BusinessRuleError(
            f"Loan is {loan.status}; recoveries can only be recorded against a written-off loan")
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A recovery must be greater than zero")

    written_off = q(sum(
        (t.amount for t in loan.transactions.filter(txn_type=TxnType.WRITE_OFF)), ZERO))
    already = q(sum(
        (t.amount for t in loan.transactions.filter(txn_type=TxnType.RECOVERY)), ZERO))
    if already + amount > written_off:
        raise BusinessRuleError(
            f"Recoveries would total {q(already + amount)}, more than the {written_off} "
            f"written off")

    return Transaction.objects.create(
        loan=loan, txn_type=TxnType.RECOVERY, txn_date=txn_date or date.today(), amount=amount,
        method=method, reference=reference, posted_by=user,
        narration=narration or f"Recovery on written-off loan {loan.loan_no}",
    )


def reschedule(loan: Loan, user: User, new_term: int, new_rate: Decimal | None,
               first_due: date | None, narration: str) -> Loan:
    """Capitalise arrears + outstanding principal into a fresh schedule.
    Unpaid future interest is dropped."""
    if loan.status != LoanStatus.ACTIVE:
        raise BusinessRuleError("Only active loans can be rescheduled")
    refresh_balances(loan)
    today = date.today()
    overdue_interest = sum(
        (i.interest_due - i.interest_paid for i in sched(loan) if i.due_date < today), ZERO)
    new_principal = q(loan.principal_outstanding + overdue_interest
                      + loan.penalties_outstanding + loan.charges_outstanding)
    rate = new_rate if new_rate is not None else loan.interest_rate_pct
    first = first_due or default_first_due(today, loan.borrower.payday)
    prior_paid = loan.total_paid

    loan.instalments.all().delete()
    rows = build_schedule(new_principal, rate, new_term, first, loan.rate_method)
    _create_schedule(loan, rows)

    loan.principal = new_principal
    loan.interest_rate_pct = rate
    loan.term_months = new_term
    loan.first_instalment_date = first
    loan.maturity_date = rows[-1].due_date
    loan.instalment_amount = rows[0].instalment
    loan.total_interest = total_interest(rows)
    loan.principal_outstanding = new_principal
    loan.interest_outstanding = loan.total_interest
    loan.penalties_outstanding = ZERO
    loan.charges_outstanding = ZERO
    loan.total_paid = ZERO
    loan.save(update_fields=[
        "principal", "interest_rate_pct", "term_months", "first_instalment_date",
        "maturity_date", "instalment_amount", "total_interest", "principal_outstanding",
        "interest_outstanding", "penalties_outstanding", "charges_outstanding", "total_paid",
    ])
    Transaction.objects.create(
        loan=loan, txn_type=TxnType.FEE, txn_date=today, amount=ZERO, posted_by=user,
        narration=(f"{narration}: capitalised {new_principal} over {new_term} months at "
                   f"{rate}%/month (previously paid {prior_paid})"),
    )
    return loan
