"""Repayment posting with waterfall allocation, oldest instalment first:

    penalties -> charges -> interest -> principal

Costs the borrower has already incurred come off first, so a partial payment
never leaves a fee quietly accruing behind the principal. Overpayment is refused
outright, so a payment can never leave a negative balance."""
from datetime import date
from decimal import Decimal

from ..exceptions import BusinessRuleError
from ..models import Loan, LoanStatus, Transaction, TxnType, User
from . import periods
from .amortisation import q
from .loans import refresh_balances, sched

ZERO = Decimal("0")


def post_repayment(loan: Loan, user: User, amount: Decimal, txn_date: date | None,
                   method: str, reference: str | None, narration: str | None) -> Transaction:
    if loan.status != LoanStatus.ACTIVE:
        raise BusinessRuleError(
            f"Loan is {loan.status}; repayments can only be posted to active loans")
    amount = q(Decimal(amount))
    txn_date = txn_date or date.today()
    # Before the waterfall mutates any in-memory instalment. The pre_save guard
    # would catch it anyway, but this says which posting was refused.
    periods.assert_open(txn_date, "This repayment")
    if amount > loan.total_outstanding:
        raise BusinessRuleError(
            f"Amount {amount} exceeds total outstanding {loan.total_outstanding}")

    remaining = amount
    p_alloc = i_alloc = pen_alloc = chg_alloc = ZERO
    for ins in sched(loan):  # ordered by instalment number
        if remaining <= 0:
            break
        # penalties
        due = ins.penalty_due - ins.penalty_paid
        if due > 0:
            take = min(due, remaining)
            ins.penalty_paid += take
            pen_alloc += take
            remaining -= take
        # charges added to the balance
        due = ins.charge_due - ins.charge_paid
        if due > 0 and remaining > 0:
            take = min(due, remaining)
            ins.charge_paid += take
            chg_alloc += take
            remaining -= take
        # interest
        due = ins.interest_due - ins.interest_paid
        if due > 0 and remaining > 0:
            take = min(due, remaining)
            ins.interest_paid += take
            i_alloc += take
            remaining -= take
        # principal
        due = ins.principal_due - ins.principal_paid
        if due > 0 and remaining > 0:
            take = min(due, remaining)
            ins.principal_paid += take
            p_alloc += take
            remaining -= take
        if ins.balance <= 0 and ins.paid_date is None:
            ins.paid_date = txn_date

    txn = Transaction.objects.create(
        loan=loan, txn_type=TxnType.REPAYMENT, txn_date=txn_date, amount=amount,
        principal_component=q(p_alloc), interest_component=q(i_alloc),
        penalty_component=q(pen_alloc), charge_component=q(chg_alloc),
        method=method, reference=reference, narration=narration, posted_by=user,
    )
    refresh_balances(loan)
    return txn


def reverse_transaction(loan: Loan, txn: Transaction, user: User, narration: str) -> Transaction:
    if txn.txn_type != TxnType.REPAYMENT:
        raise BusinessRuleError("Only repayments can be reversed")
    if txn.reversed:
        raise BusinessRuleError("Transaction already reversed")
    # Un-allocate from the LATEST instalments backwards, mirroring the waterfall in reverse.
    p, i, pen = txn.principal_component, txn.interest_component, txn.penalty_component
    chg = txn.charge_component
    for ins in reversed(sched(loan)):
        take = min(p, ins.principal_paid)
        ins.principal_paid -= take
        p -= take
        take = min(i, ins.interest_paid)
        ins.interest_paid -= take
        i -= take
        take = min(chg, ins.charge_paid)
        ins.charge_paid -= take
        chg -= take
        take = min(pen, ins.penalty_paid)
        ins.penalty_paid -= take
        pen -= take
        if ins.balance > 0:
            ins.paid_date = None

    txn.reversed = True
    txn.save(update_fields=["reversed"])
    rev = Transaction.objects.create(
        loan=loan, txn_type=TxnType.REVERSAL, txn_date=date.today(), amount=txn.amount,
        principal_component=txn.principal_component, interest_component=txn.interest_component,
        penalty_component=txn.penalty_component, charge_component=txn.charge_component,
        # The cash goes back at the rate it came in at, so the bank leg mirrors
        # exactly; the receivable is restored at today's booked rate, as every
        # posting is, and whatever lies between is an exchange difference.
        fx_rate=txn.fx_rate,
        reversal_of=txn, narration=narration, posted_by=user,
    )
    if loan.status == LoanStatus.CLOSED:
        loan.status = LoanStatus.ACTIVE
        loan.closed_at = None
    refresh_balances(loan)
    return rev


def waive_penalties(loan: Loan, user: User, amount: Decimal, narration: str) -> Transaction:
    amount = q(Decimal(amount))
    if amount > loan.penalties_outstanding:
        raise BusinessRuleError(
            f"Waiver {amount} exceeds penalties outstanding {loan.penalties_outstanding}")
    remaining = amount
    for ins in sched(loan):
        due = ins.penalty_due - ins.penalty_paid
        if due > 0 and remaining > 0:
            take = min(due, remaining)
            ins.penalty_due -= take
            remaining -= take
    txn = Transaction.objects.create(
        loan=loan, txn_type=TxnType.WAIVER, txn_date=date.today(), amount=amount,
        penalty_component=amount, narration=narration, posted_by=user,
    )
    refresh_balances(loan)
    return txn
