"""The charges catalogue.

The admin and credit-life fees on LoanProduct stay where they are: they are part
of the core pricing and the schedule maths. This catalogue is for everything a
lender bolts on beside them - stamp duty, a processing charge, a cash-out cost -
defined once and attached to whichever products carry them.

A charge's amount is frozen onto the loan when it is raised, so renaming or
repricing a charge never rewrites history.
"""
from datetime import date
from decimal import Decimal

from ..models import (
    Charge,
    ChargeCollection,
    ChargeTiming,
    Loan,
    LoanCharge,
    LoanProduct,
    LoanStatus,
    Transaction,
    TxnType,
    User,
)
from . import periods
from .amortisation import q

ZERO = Decimal("0")


def catalogue_for(product: LoanProduct, timing: str = ChargeTiming.DISBURSEMENT) -> list[Charge]:
    """The active charges a product carries at a given point in the life of a loan."""
    return list(Charge.objects
                .filter(product_charges__product=product, is_active=True, timing=timing)
                .order_by("code"))


def quote_for(product: LoanProduct, principal: Decimal,
              timing: str = ChargeTiming.DISBURSEMENT) -> list[dict]:
    """What the catalogue would add to this loan, itemised."""
    return [{
        "code": charge.code,
        "name": charge.name,
        "basis": charge.basis,
        "value": charge.value,
        "amount": charge.amount_for(principal),
    } for charge in catalogue_for(product, timing)]


def total_for(product: LoanProduct, principal: Decimal,
              timing: str = ChargeTiming.DISBURSEMENT) -> Decimal:
    return q(sum((row["amount"] for row in quote_for(product, principal, timing)), ZERO))


def raise_at_disbursement(loan: Loan, user: User, on: date,
                          transaction: Transaction | None = None) -> list[LoanCharge]:
    """Freeze the catalogue charges onto the loan as it is disbursed."""
    rows = []
    for charge in catalogue_for(loan.product, ChargeTiming.DISBURSEMENT):
        amount = charge.amount_for(loan.principal)
        if amount <= 0:
            continue
        rows.append(LoanCharge(loan=loan, charge=charge, name=charge.name, amount=amount,
                               applied_on=on, transaction=transaction))
    if rows:
        LoanCharge.objects.bulk_create(rows)
    return rows


def raise_manual(loan: Loan, user: User, charge: Charge | None, name: str, amount: Decimal,
                 on: date | None = None,
                 collection: str = ChargeCollection.COUNTER) -> LoanCharge:
    """A one-off charge against a loan, outside the catalogue timing.

    Two ways to recover it, and they are genuinely different transactions:

    * **counter** - the borrower pays it now. Cash in, fee income; the loan's
      balance is untouched.
    * **balance** - the charge is added to the next unpaid instalment and
      recovered with the loan. It becomes a receivable, and the repayment
      waterfall takes it after penalties and before interest.
    """
    from ..exceptions import BusinessRuleError
    from .loans import refresh_balances, sched

    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A charge must be greater than zero")
    on = on or date.today()
    periods.assert_open(on, "This charge")

    if collection == ChargeCollection.COUNTER:
        txn = Transaction.objects.create(
            loan=loan, txn_type=TxnType.CHARGE, txn_date=on, amount=amount, posted_by=user,
            narration=f"Charge collected: {name}",
        )
        return LoanCharge.objects.create(loan=loan, charge=charge, name=name, amount=amount,
                                         applied_on=on, collection=collection, transaction=txn)

    # Added to the balance: find the instalment that will carry it.
    if loan.status != LoanStatus.ACTIVE:
        raise BusinessRuleError(
            f"Loan is {loan.status}; a charge can only be added to the balance of an active loan")
    rows = sched(loan)
    target = next((i for i in rows if i.balance > 0), rows[-1] if rows else None)
    if target is None:
        raise BusinessRuleError("The loan has no schedule to add the charge to")

    target.charge_due += amount
    txn = Transaction.objects.create(
        loan=loan, txn_type=TxnType.CHARGE_ADDED, txn_date=on, amount=amount,
        charge_component=amount, posted_by=user,
        narration=f"Charge added to instalment {target.number}: {name}",
    )
    item = LoanCharge.objects.create(loan=loan, charge=charge, name=name, amount=amount,
                                     applied_on=on, collection=collection, instalment=target,
                                     transaction=txn)
    refresh_balances(loan)
    return item
