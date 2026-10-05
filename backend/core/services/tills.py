"""Teller tills: a float in the morning, a count at close, a second pair of eyes.

The ledger's account 1000 says how much cash the institution holds; a till says
how much of it one teller is answerable for. What the drawer should hold is the
opening float plus every cash movement the teller posted while it was open - read
from the postings themselves, never typed in, so the count is compared with what
the system recorded and with nothing anyone could adjust to match.

Which movements count:

  * loan repayments, recoveries and cash disbursements with method = cash;
  * charges collected at the counter (they carry no method; the counter is cash);
  * savings deposits and withdrawals with method = cash;
  * a reversal of any of those, against the till of whoever posts the reversal,
    because that is whose drawer the money goes back out of.

Funding and capital movements are not teller business and never count.

A counted till is verified by someone other than the teller who counted it. On
verification a difference is posted to the ledger - a shortage Dr 6800 Cash
shortages / Cr 1000, an overage Dr 1000 / Cr 4900 Other income - because until it
is, the ledger claims cash the building does not hold (or misses some it does).
"""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    OrganisationSetting,
    PaymentMethod,
    Role,
    SavingsTransaction,
    SavingsTxnType,
    TillSession,
    TillStatus,
    Transaction,
    TxnType,
    User,
)
from . import ledger, periods
from .amortisation import q
from .loans import next_number

ZERO = Decimal("0")
CASH = PaymentMethod.CASH


# ---------------------------------------------------------------- what moved
def _loan_cash(txn: Transaction) -> Decimal:
    """The signed cash effect of one loan transaction on the drawer: + in, - out.

    The drawer is counted in the organisation's currency, so a foreign-currency
    loan's cash is taken at the spot rate stamped on the transaction.
    """
    from .fx import to_base

    kind = txn.txn_type
    amount = to_base(txn.amount, txn.fx_rate)
    if kind in (TxnType.REPAYMENT, TxnType.RECOVERY):
        return amount if txn.method == CASH else ZERO
    if kind == TxnType.CHARGE:
        return amount if txn.method in (CASH, None) else ZERO
    if kind == TxnType.DISBURSEMENT:
        return -amount if txn.method == CASH else ZERO
    if kind == TxnType.REVERSAL and txn.reversal_of_id:
        original = txn.reversal_of
        return -_loan_cash(original) if original.txn_type != TxnType.REVERSAL else ZERO
    return ZERO


def _savings_cash(stxn: SavingsTransaction) -> Decimal:
    if stxn.txn_type == SavingsTxnType.REVERSAL and stxn.reversal_of_id:
        return -_savings_cash(stxn.reversal_of)
    if stxn.method != CASH:
        return ZERO
    if stxn.txn_type == SavingsTxnType.DEPOSIT:
        return stxn.amount
    if stxn.txn_type == SavingsTxnType.WITHDRAWAL:
        return -stxn.amount
    return ZERO


def movements(session: TillSession) -> list[dict]:
    """Every cash movement in the till's window, oldest first."""
    end = session.closed_at or timezone.now()
    window = {"posted_by_id": session.teller_id, "created_at__gte": session.opened_at,
              "created_at__lte": end}
    rows = []
    for txn in (Transaction.objects.filter(**window)
                .select_related("loan", "reversal_of").order_by("created_at", "id")):
        amount = _loan_cash(txn)
        if amount:
            rows.append({"at": txn.created_at, "kind": txn.get_txn_type_display(),
                         "reference": txn.loan.loan_no, "detail": txn.reference or "",
                         "amount": q(amount)})
    for stxn in (SavingsTransaction.objects.filter(**window)
                 .select_related("account", "reversal_of").order_by("created_at", "id")):
        amount = _savings_cash(stxn)
        if amount:
            rows.append({"at": stxn.created_at, "kind": f"Savings {stxn.get_txn_type_display().lower()}",
                         "reference": stxn.account.account_no, "detail": stxn.reference or "",
                         "amount": q(amount)})
    rows.sort(key=lambda r: r["at"])
    return rows


def position(session: TillSession) -> dict:
    """What the drawer should hold now (or held at the count)."""
    if session.status != TillStatus.OPEN:
        return {"cash_in": session.cash_in, "cash_out": session.cash_out,
                "expected_cash": session.expected_cash, "movements": movements(session)}
    rows = movements(session)
    cash_in = q(sum((r["amount"] for r in rows if r["amount"] > 0), ZERO))
    cash_out = q(-sum((r["amount"] for r in rows if r["amount"] < 0), ZERO))
    return {"cash_in": cash_in, "cash_out": cash_out,
            "expected_cash": q(session.opening_float + cash_in - cash_out), "movements": rows}


# ---------------------------------------------------------------- the guard
def assert_till_open(user_id: int | None, method: str | None, what: str) -> None:
    """Refuse a cash posting by someone with no open till, when the setting asks.

    System postings (no user) are never refused: nobody stands at a counter for them.
    """
    if method != CASH or not user_id:
        return
    if not OrganisationSetting.load().require_open_till:
        return
    if not TillSession.objects.filter(teller_id=user_id, status=TillStatus.OPEN).exists():
        raise BusinessRuleError(
            f"{what} is in cash, and cash postings need an open till. Open your till on the "
            f"Teller till page first.")


# ---------------------------------------------------------------- lifecycle
def current(user: User) -> TillSession | None:
    return TillSession.objects.filter(teller=user, status=TillStatus.OPEN).first()


@db_transaction.atomic
def open_till(user: User, opening_float: Decimal) -> TillSession:
    opening_float = q(Decimal(opening_float))
    if opening_float < 0:
        raise BusinessRuleError("The opening float cannot be negative")
    existing = current(user)
    if existing:
        raise BusinessRuleError(f"You already have {existing.session_no} open since "
                                f"{timezone.localtime(existing.opened_at):%Y-%m-%d %H:%M}. "
                                f"Count and close it first.")
    try:
        with db_transaction.atomic():
            return TillSession.objects.create(
                session_no=next_number("TILL"), teller=user, branch_id=user.branch_id,
                opening_float=opening_float, business_date=date.today())
    except IntegrityError:
        # Two tabs opening at once: the filtered unique index holds the line.
        raise BusinessRuleError("You already have a till open")


@db_transaction.atomic
def count(session: TillSession, user: User, counted_cash: Decimal,
          note: str | None) -> TillSession:
    """Close the drawer at a counted figure. The teller counts their own."""
    if session.status != TillStatus.OPEN:
        raise BusinessRuleError(f"{session.session_no} is already counted")
    if session.teller_id != user.id and not user.has_role(Role.ADMIN):
        raise BusinessRuleError("Only the teller who holds a till, or an administrator, can "
                                "count it")
    counted_cash = q(Decimal(counted_cash))
    if counted_cash < 0:
        raise BusinessRuleError("A count cannot be negative")

    now = position(session)
    variance = q(counted_cash - now["expected_cash"])
    if variance != 0 and not (note or "").strip():
        raise BusinessRuleError(
            f"The count is {'over' if variance > 0 else 'short'} by {abs(variance)}. Say what "
            f"was checked before closing.")
    session.closed_at = timezone.now()
    session.cash_in = now["cash_in"]
    session.cash_out = now["cash_out"]
    session.expected_cash = now["expected_cash"]
    session.counted_cash = counted_cash
    session.variance = variance
    session.close_note = (note or "").strip() or None
    session.status = TillStatus.COUNTED
    session.save()
    return session


def _variance_lines(session: TillSession):
    amount = abs(session.variance)
    if session.variance < 0:
        return [(ledger.CASH_SHORTAGES, amount, ZERO, "Cash short at the till"),
                (ledger.CODES["bank"], ZERO, amount, "Cash short at the till")]
    return [(ledger.CODES["bank"], amount, ZERO, "Cash over at the till"),
            (ledger.OTHER_INCOME, ZERO, amount, "Cash over at the till")]


def repost_variances() -> dict:
    """Re-raise the entries of verified differences, for `ledger.backfill`: a till
    difference stands behind no transaction, so the sweeps cannot find it."""
    reposted = 0
    with periods.allow_closed_posting("till variance repost"):
        for session in TillSession.objects.filter(status=TillStatus.VERIFIED,
                                                  variance_entry__isnull=True).exclude(variance=0):
            if not session.variance:
                continue
            session.variance_entry = ledger.post_manual_entry(
                _variance_lines(session), session.business_date,
                f"{session.session_no}: till difference (re-posted)", "till_variance",
                branch_id=session.branch_id, posted_by=session.verified_by, strict=False)
            if session.variance_entry:
                session.save(update_fields=["variance_entry"])
                reposted += 1
    return {"reposted": reposted}


@db_transaction.atomic
def verify(session: TillSession, user: User, note: str | None) -> TillSession:
    """A supervisor signs the count off and the difference, if any, is booked."""
    if session.status != TillStatus.COUNTED:
        raise BusinessRuleError(f"{session.session_no} is {session.get_status_display().lower()}; "
                                f"only a counted till can be verified")
    if session.teller_id == user.id:
        raise BusinessRuleError("Someone other than the teller must verify a till")
    if not user.has_role(Role.ADMIN, Role.LOAN_OFFICER):
        raise BusinessRuleError("Only an administrator or a loan officer can verify a till")

    if session.variance:
        on = session.business_date
        moved = ""
        if periods.is_closed(on):
            on = periods.earliest_postable_date()
            moved = f" (counted {session.business_date.isoformat()}, a month since closed)"
        amount = abs(session.variance)
        session.variance_entry = ledger.post_manual_entry(
            _variance_lines(session), on,
            f"{session.session_no}: till {'short' if session.variance < 0 else 'over'} "
            f"by {amount}{moved}",
            "till_variance", branch_id=session.branch_id, posted_by=user, strict=True)

    session.status = TillStatus.VERIFIED
    session.verified_by = user
    session.verified_at = timezone.now()
    session.verify_note = (note or "").strip() or None
    session.save(update_fields=["status", "verified_by", "verified_at", "verify_note",
                                "variance_entry"])
    return session
