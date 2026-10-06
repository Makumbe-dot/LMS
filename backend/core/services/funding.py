"""Where the money to lend comes from: shareholders' capital and funder borrowings.

Without this, account 1000 goes negative the moment disbursements exceed
collections, and no balance sheet can be drawn: the assets were real but the
liabilities and equity that paid for them were nowhere in the system.

Two asymmetries with the loan book are deliberate:

  * Borrowing interest IS accrued monthly to account 2110, while loan interest is
    recognised only when collected. An unrecognised asset is prudent; an
    unrecognised liability is not. A board pack that understates what is owed to a
    funder is the failure this exists to prevent.

  * An interest payment never splits between the liability and the expense.
    `pay_interest` refuses more than has accrued and says to run the accrual first.
    Expensing an unaccrued remainder is what double-counts a period: pay on the
    1st with nothing accrued, and the month-end accrual then charges the same
    period again.

Cash is guarded. Every outflow here — a principal repayment, an interest payment,
a fee, a return of capital, a dividend — refuses to drive account 1000 below zero,
because a slice that exists to stop cash going negative must not be the thing that
puts it there.

A facility may be in another currency. Its limit and every movement are then in
that currency; 2100 and 2110 carry it at the facility's booked rate, cash and
borrowing costs go in at the day's rate, and the difference is realised on 4800,
as for a foreign loan (services/fx.py). The cash guard measures a foreign payment
at the day's rate, which is what it takes out of the bank.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum

from ..exceptions import BusinessRuleError
from ..models import (
    CapitalTransaction,
    CapitalTxnType,
    FacilityTransaction,
    FacilityTxnType,
    FundingFacility,
    User,
)
from . import fx, periods
from .amortisation import add_months, q
from .loans import next_number

ZERO = Decimal("0")

# The columns _post owns, mirroring loans.LOAN_BALANCE_FIELDS.
FACILITY_BALANCE_FIELDS = ["principal_outstanding", "interest_accrued"]


# ---------------------------------------------------------------- cash
def cash_balance(as_of: date | None = None) -> Decimal:
    """What account 1000 holds, optionally as at a date.

    Read from the ledger rather than tracked separately: the ledger already is the
    cash book, and a second running total would be a second thing to reconcile.
    """
    from . import ledger as gl

    debit, credit = gl.account_movement(gl.CODES["bank"], end=as_of)
    return q(debit - credit)


def assert_cash(amount: Decimal, what: str, on: date | None = None) -> None:
    """Refuse a payment the bank cannot fund.

    Measured as at the posting date, not as at today: a payment back-dated into a
    month when the bank was empty is refused even if there is money now, which is
    the only reading under which the guard means anything for a back-dated entry.
    """
    available = cash_balance(on)
    if q(amount) > available:
        when = f" as at {on.isoformat()}" if on else ""
        raise BusinessRuleError(
            f"{what} of {q(amount)} exceeds the {available} in the bank{when}. Record the "
            f"capital or the drawdown that funds it first.")


# ---------------------------------------------------------------- facilities
@db_transaction.atomic
def open_facility(user: User | None, *, funder_name: str, name: str, facility_limit: Decimal,
                  interest_rate_pct_pa: Decimal = ZERO, start_date: date | None = None,
                  maturity_date: date | None = None, is_revolving: bool = False,
                  repayment_terms: str | None = None, branch_id: int | None = None,
                  notes: str | None = None, currency: str | None = None) -> FundingFacility:
    # Blank for the organisation's currency. Another needs a rate, and the limit
    # and every movement are then in it; the liability is carried on 2100 and
    # 2110 at the spot rate of the start date until a revaluation moves it on.
    currency = fx.validate_code(currency)
    facility_limit = q(Decimal(facility_limit))
    if facility_limit <= 0:
        raise BusinessRuleError("A facility limit must be greater than zero")
    if Decimal(interest_rate_pct_pa) < 0:
        raise BusinessRuleError("An interest rate cannot be negative")
    start_date = start_date or date.today()
    if maturity_date and maturity_date <= start_date:
        raise BusinessRuleError("The maturity date must be after the start date")
    if not (funder_name or "").strip():
        raise BusinessRuleError("Name the funder")

    return FundingFacility.objects.create(
        facility_no=next_number("FAC"), funder_name=funder_name.strip(), name=name,
        facility_limit=facility_limit, interest_rate_pct_pa=Decimal(interest_rate_pct_pa),
        is_revolving=is_revolving, start_date=start_date, maturity_date=maturity_date,
        repayment_terms=repayment_terms, branch_id=branch_id, notes=notes, created_by=user,
        currency=currency, fx_rate=fx.rate_on(currency, start_date),
    )


def _in_base(facility: FundingFacility, amount: Decimal, on: date) -> Decimal:
    """A facility-currency payment in the organisation's currency at the day's
    rate, which is what it takes out of account 1000."""
    return fx.to_base(amount, fx.rate_on(facility.currency, on))


def _post(facility: FundingFacility, user: User | None, kind: str, amount: Decimal,
          txn_date: date | None, method: str | None, reference: str | None,
          narration: str | None, *, principal_delta: Decimal = ZERO,
          accrued_delta: Decimal = ZERO,
          reversal_of: FacilityTransaction | None = None,
          fx_rate: Decimal | None = None) -> FacilityTransaction:
    """Move the facility's balances and write the movement, atomically.

    `reversal_of` is passed here rather than set afterwards, because the ledger hook
    fires on post_save and works out which legs to mirror from what the reversal
    points back to. Assigning it on a later save leaves the reversal with no journal
    entry at all.
    """
    txn_date = txn_date or date.today()
    periods.assert_open(txn_date, f"This facility {kind.replace('_', ' ')}")

    facility.principal_outstanding = q((facility.principal_outstanding or ZERO) + principal_delta)
    facility.interest_accrued = q((facility.interest_accrued or ZERO) + accrued_delta)
    facility.save(update_fields=FACILITY_BALANCE_FIELDS)

    return FacilityTransaction.objects.create(
        facility=facility, txn_type=kind, txn_date=txn_date, amount=q(amount),
        principal_after=facility.principal_outstanding,
        accrued_after=facility.interest_accrued,
        method=method, reference=reference, narration=narration, posted_by=user,
        reversal_of=reversal_of, fx_rate=fx_rate,
    )


def _assert_open_facility(facility: FundingFacility) -> None:
    if facility.closed_on:
        raise BusinessRuleError(
            f"{facility.facility_no} was closed on {facility.closed_on.isoformat()}")


@db_transaction.atomic
def drawdown(facility: FundingFacility, user: User | None, amount: Decimal,
             txn_date: date | None = None, method: str | None = None,
             reference: str | None = None, narration: str | None = None) -> FacilityTransaction:
    """Draw on the facility. Cash in, liability up."""
    _assert_open_facility(facility)
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A drawdown must be greater than zero")
    txn_date = txn_date or date.today()
    if txn_date < facility.start_date:
        raise BusinessRuleError(
            f"A drawdown cannot be dated before the facility started on "
            f"{facility.start_date.isoformat()}")

    available = facility.available
    if amount > available:
        kind = "revolving" if facility.is_revolving else "term"
        raise BusinessRuleError(
            f"{amount} exceeds the {available} still available on {facility.facility_no} "
            f"(a {kind} facility with a limit of {facility.facility_limit})")

    return _post(facility, user, FacilityTxnType.DRAWDOWN, amount, txn_date, method, reference,
                 narration or "Drawdown", principal_delta=amount)


@db_transaction.atomic
def repay(facility: FundingFacility, user: User | None, amount: Decimal,
          txn_date: date | None = None, method: str | None = None, reference: str | None = None,
          narration: str | None = None) -> FacilityTransaction:
    """Repay principal to the funder."""
    _assert_open_facility(facility)
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A repayment must be greater than zero")
    if amount > facility.principal_outstanding:
        raise BusinessRuleError(
            f"{amount} exceeds the {facility.principal_outstanding} principal outstanding on "
            f"{facility.facility_no}")
    txn_date = txn_date or date.today()
    assert_cash(_in_base(facility, amount, txn_date), "This repayment to the funder", txn_date)

    return _post(facility, user, FacilityTxnType.REPAYMENT, amount, txn_date, method, reference,
                 narration or "Principal repaid to the funder", principal_delta=-amount)


@db_transaction.atomic
def pay_interest(facility: FundingFacility, user: User | None, amount: Decimal,
                 txn_date: date | None = None, method: str | None = None,
                 reference: str | None = None,
                 narration: str | None = None) -> FacilityTransaction:
    """Settle accrued interest. Refuses more than has accrued.

    Deliberately not "pay whatever the funder invoiced and expense the difference":
    that path charges 5300 now and again at the month end for the same period. If
    the funder has billed more than the books show, the accrual is behind — run it.
    """
    _assert_open_facility(facility)
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("An interest payment must be greater than zero")
    txn_date = txn_date or date.today()
    accrued = q(facility.interest_accrued or ZERO)
    if amount > accrued:
        raise BusinessRuleError(
            f"Only {accrued} of interest has accrued on {facility.facility_no}. Run the interest "
            f"accrual to {txn_date.isoformat()} first, then pay it — paying ahead of the accrual "
            f"charges the same period to 5300 twice.")
    assert_cash(_in_base(facility, amount, txn_date), "This interest payment", txn_date)

    return _post(facility, user, FacilityTxnType.INTEREST_PAYMENT, amount, txn_date, method,
                 reference, narration or "Interest paid to the funder", accrued_delta=-amount)


@db_transaction.atomic
def charge_fee(facility: FundingFacility, user: User | None, amount: Decimal,
               txn_date: date | None = None, method: str | None = None,
               reference: str | None = None,
               narration: str | None = None) -> FacilityTransaction:
    """An arrangement, commitment or non-utilisation fee, paid to the funder.

    A separate movement rather than a component netted off a drawdown. The ledger,
    the cash and the reversals come out identical either way, and this way there is
    no fee column on the facility, no "first drawdown only" rule, and no reversal
    that has to carry a copy of the component.
    """
    _assert_open_facility(facility)
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A fee must be greater than zero")
    txn_date = txn_date or date.today()
    assert_cash(_in_base(facility, amount, txn_date), "This facility fee", txn_date)

    return _post(facility, user, FacilityTxnType.FEE, amount, txn_date, method, reference,
                 narration or "Facility fee")


def accrue_interest(as_of: date | None = None, facility: FundingFacility | None = None) -> dict:
    """Accrue interest on drawn balances, catching up any months missed.

    A month at a time in a loop, not one month per run: if the scheduler was down
    for three months, a single accrual would silently lose two months of a real
    debt, and the 2110-versus-facilities check would still read OK because both
    sides would be wrong together.

    A month that falls in a closed accounting period is SKIPPED and NAMED in the
    result rather than posted or silently dropped. Refusing the whole run would make
    a book with any closed period un-accruable forever; posting anyway would defeat
    the close. Naming them is the only answer that neither loses the interest
    quietly nor breaks the close — reopen the period to pick it up.

    `last_accrual_date` is stamped whether or not anything was posted, so a
    zero-rate facility is not rescanned from its start date every night.
    """
    as_of = as_of or date.today()
    facilities = ([facility] if facility is not None
                  else list(FundingFacility.objects.filter(closed_on__isnull=True)))

    accrued_total = ZERO
    touched = months_posted = 0
    skipped: list[str] = []
    for fac in facilities:
        rate = Decimal(fac.interest_rate_pct_pa or 0)
        posted_here = ZERO
        # The first accrual runs from the facility's start date.
        cursor = fac.last_accrual_date or fac.start_date
        with db_transaction.atomic():
            while add_months(cursor, 1) <= as_of:
                cursor = add_months(cursor, 1)
                if rate <= 0 or fac.principal_outstanding <= 0:
                    continue
                amount = q(fac.principal_outstanding * rate / 100 / 12)
                if amount <= 0:
                    continue
                if periods.is_closed(cursor):
                    skipped.append(f"{fac.facility_no} {cursor.isoformat()} ({amount})")
                    continue
                _post(fac, None, FacilityTxnType.INTEREST_ACCRUAL, amount, cursor,
                      None, None, f"Interest at {rate}% a year", accrued_delta=amount)
                posted_here += amount
                months_posted += 1

            if cursor != fac.last_accrual_date:
                fac.last_accrual_date = cursor
                fac.save(update_fields=["last_accrual_date"])

        if posted_here > 0:
            accrued_total += posted_here
            touched += 1

    return {
        "as_of": as_of.isoformat(),
        "facilities_accrued": touched,
        "months_posted": months_posted,
        "interest_accrued": q(accrued_total),
        "months_skipped": len(skipped),
        "skipped": skipped,
    }


@db_transaction.atomic
def reverse_facility_transaction(facility: FundingFacility, ftxn: FacilityTransaction,
                                 user: User | None, narration: str) -> FacilityTransaction:
    if ftxn.reversed:
        raise BusinessRuleError("That movement has already been reversed")
    if ftxn.txn_type == FacilityTxnType.REVERSAL:
        raise BusinessRuleError("A reversal cannot itself be reversed")
    if ftxn.txn_type == FacilityTxnType.INTEREST_ACCRUAL:
        raise BusinessRuleError(
            "An accrual cannot be reversed. It is not a payment and reversing it would leave "
            "the facility's last accrual date claiming a month that is no longer on the books.")
    if ftxn.facility_id != facility.id:
        raise BusinessRuleError("That movement belongs to another facility")

    # Undo whatever the original did to the two balances.
    deltas = {
        FacilityTxnType.DRAWDOWN: {"principal_delta": -ftxn.amount},
        FacilityTxnType.REPAYMENT: {"principal_delta": ftxn.amount},
        FacilityTxnType.INTEREST_PAYMENT: {"accrued_delta": ftxn.amount},
        FacilityTxnType.FEE: {},
    }[ftxn.txn_type]

    if ftxn.txn_type == FacilityTxnType.DRAWDOWN:
        if facility.principal_outstanding < ftxn.amount:
            raise BusinessRuleError(
                f"Reversing this drawdown would take the principal below zero: only "
                f"{facility.principal_outstanding} is outstanding and the drawdown was "
                f"{ftxn.amount}. Reverse the repayments first.")
        # the cash goes back at the rate it came in at
        assert_cash(fx.to_base(ftxn.amount, ftxn.fx_rate),
                    "Reversing this drawdown returns cash, and that")
    # A reversed repayment legitimately pushes principal back above a lowered
    # limit, so the availability check is deliberately not applied here.

    ftxn.reversed = True
    ftxn.save(update_fields=["reversed"])
    # The reversal carries the original's spot rate, so the cash and expense legs
    # mirror exactly; the liability is restated at today's booked rate, as every
    # posting is, and whatever lies between is an exchange difference.
    return _post(facility, user, FacilityTxnType.REVERSAL, ftxn.amount, date.today(),
                 ftxn.method, ftxn.reference, narration, reversal_of=ftxn,
                 fx_rate=ftxn.fx_rate, **deltas)


@db_transaction.atomic
def close_facility(facility: FundingFacility, user: User | None,
                   narration: str | None = None) -> FundingFacility:
    """Close a facility that owes nothing. A date, not a status."""
    if facility.closed_on:
        raise BusinessRuleError(f"{facility.facility_no} is already closed")
    if facility.principal_outstanding > 0 or facility.interest_accrued > 0:
        raise BusinessRuleError(
            f"{facility.facility_no} still owes {facility.principal_outstanding} of principal and "
            f"{facility.interest_accrued} of accrued interest. Repay it before closing.")
    facility.closed_on = date.today()
    if narration:
        facility.notes = f"{facility.notes}\n{narration}".strip() if facility.notes else narration
    facility.save(update_fields=["closed_on", "notes"])
    return facility


# ---------------------------------------------------------------- capital
def _capital(kind: str, user: User | None, amount: Decimal, contributor: str,
             txn_date: date | None = None, method: str | None = None,
             reference: str | None = None, narration: str | None = None,
             branch_id: int | None = None,
             reversal_of: CapitalTransaction | None = None) -> CapitalTransaction:
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A capital movement must be greater than zero")
    if not (contributor or "").strip():
        raise BusinessRuleError("Name the contributor")
    txn_date = txn_date or date.today()
    periods.assert_open(txn_date, f"This {kind.replace('_', ' ')}")

    return CapitalTransaction.objects.create(
        txn_type=kind, txn_date=txn_date, amount=amount, contributor=contributor.strip(),
        method=method, reference=reference, narration=narration, branch_id=branch_id,
        posted_by=user, reversal_of=reversal_of,
    )


def inject_capital(user, amount, contributor, txn_date=None, method=None, reference=None,
                   narration=None, branch_id=None) -> CapitalTransaction:
    return _capital(CapitalTxnType.INJECTION, user, amount, contributor, txn_date, method,
                    reference, narration or "Capital injection", branch_id)


@db_transaction.atomic
def return_capital(user, amount, contributor, txn_date=None, method=None, reference=None,
                   narration=None, branch_id=None) -> CapitalTransaction:
    summary = capital_summary()
    if q(Decimal(amount)) > summary["net_capital"]:
        raise BusinessRuleError(
            f"{q(Decimal(amount))} exceeds the {summary['net_capital']} of capital contributed "
            f"and not yet returned")
    txn_date = txn_date or date.today()
    assert_cash(amount, "This return of capital", txn_date)
    return _capital(CapitalTxnType.RETURN_OF_CAPITAL, user, amount, contributor, txn_date, method,
                    reference, narration or "Return of capital", branch_id)


@db_transaction.atomic
def pay_dividend(user, amount, contributor, txn_date=None, method=None, reference=None,
                 narration=None, branch_id=None) -> CapitalTransaction:
    """Pay out a dividend. Refused when there is not the equity or the cash for it."""
    from . import ledger as gl

    amount = q(Decimal(amount))
    txn_date = txn_date or date.today()
    equity = gl.balance_sheet(txn_date)["total_equity"]
    if amount > equity:
        raise BusinessRuleError(
            f"A dividend of {amount} exceeds the {equity} of equity. Paying it would leave the "
            f"institution with negative equity.")
    assert_cash(amount, "This dividend", txn_date)
    return _capital(CapitalTxnType.DIVIDEND, user, amount, contributor, txn_date, method,
                    reference, narration or "Dividend", branch_id)


@db_transaction.atomic
def reverse_capital_transaction(ctxn: CapitalTransaction, user: User | None,
                                narration: str) -> CapitalTransaction:
    if ctxn.reversed:
        raise BusinessRuleError("That movement has already been reversed")
    if ctxn.txn_type == CapitalTxnType.REVERSAL:
        raise BusinessRuleError("A reversal cannot itself be reversed")

    if ctxn.txn_type == CapitalTxnType.INJECTION:
        assert_cash(ctxn.amount, "Reversing this injection returns cash, and that")
        # Inject 1000, return 1000, then reverse the injection, and account 3100
        # would sit at -1000: negative share capital. The reconciliation would still
        # agree, because the sub-ledger computes the same nonsense.
        remaining = q(capital_summary()["net_capital"] - ctxn.amount)
        if remaining < 0:
            raise BusinessRuleError(
                f"Reversing this {ctxn.amount} injection would leave share capital at "
                f"{remaining}. Reverse the returns of capital that drew on it first.")

    ctxn.reversed = True
    ctxn.save(update_fields=["reversed"])
    return _capital(CapitalTxnType.REVERSAL, user, ctxn.amount, ctxn.contributor, date.today(),
                    ctxn.method, ctxn.reference, narration, ctxn.branch_id, reversal_of=ctxn)


def _live_capital():
    """Movements that still count: a reversed one and its reversal both net out."""
    return CapitalTransaction.objects.filter(reversed=False).exclude(
        txn_type=CapitalTxnType.REVERSAL)


def capital_summary() -> dict:
    live = _live_capital()

    def total(kind):
        return q(live.filter(txn_type=kind).aggregate(v=Sum("amount"))["v"] or ZERO)

    injected = total(CapitalTxnType.INJECTION)
    returned = total(CapitalTxnType.RETURN_OF_CAPITAL)
    dividends = total(CapitalTxnType.DIVIDEND)
    return {
        "injected": injected,
        "returned": returned,
        "dividends": dividends,
        # Contributed capital, which is exactly what account 3100 carries. Dividends
        # sit on 3200 and are NOT netted off here, or the 3100 identity would break.
        "net_capital": q(injected - returned),
    }


# ---------------------------------------------------------------- reporting
def facility_totals(facility: FundingFacility) -> dict:
    """The sums that are not stored on the facility, computed when asked."""
    live = facility.transactions.filter(reversed=False).exclude(
        txn_type=FacilityTxnType.REVERSAL)

    def total(kind):
        return q(live.filter(txn_type=kind).aggregate(v=Sum("amount"))["v"] or ZERO)

    return {
        "total_drawn": total(FacilityTxnType.DRAWDOWN),
        "total_repaid": total(FacilityTxnType.REPAYMENT),
        "total_interest_accrued": total(FacilityTxnType.INTEREST_ACCRUAL),
        "total_interest_paid": total(FacilityTxnType.INTEREST_PAYMENT),
        "total_fees": total(FacilityTxnType.FEE),
    }


def funding_summary(as_of: date | None = None) -> dict:
    """The funding book at a glance, for the KPI strip."""
    as_of = as_of or date.today()
    facilities = list(FundingFacility.objects.all())
    live = [f for f in facilities if f.closed_on is None]

    # In the organisation's currency, each facility at its booked rate, so
    # "drawn" and "accrued" are what 2100 and 2110 carry.
    def total(value):
        return q(sum((fx.to_base(value(f), f.fx_rate) for f in live), ZERO))

    limit = total(lambda f: f.facility_limit)
    drawn = total(lambda f: f.principal_outstanding)
    accrued = total(lambda f: f.interest_accrued)
    available = total(lambda f: f.available)
    capital = capital_summary()

    return {
        "as_of": as_of,
        "facilities": len(facilities),
        "open_facilities": len(live),
        "total_limit": limit,
        "drawn": drawn,
        "available": available,
        "accrued_interest": accrued,
        # Guarded: an institution with no facility has no utilisation, not a
        # division by zero.
        "utilisation_pct": q(drawn / limit * 100) if limit > 0 else ZERO,
        "capital": capital,
        "cash": cash_balance(),
        "maturing_soon": [
            {"facility_no": f.facility_no, "funder_name": f.funder_name,
             "maturity_date": f.maturity_date,
             "currency": f.currency or fx.base_currency(),
             "principal_outstanding": f.principal_outstanding,
             "days": (f.maturity_date - as_of).days}
            for f in live
            if f.maturity_date and 0 <= (f.maturity_date - as_of).days <= 90
        ],
    }
