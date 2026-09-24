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

from ..models import Charge, ChargeTiming, Loan, LoanCharge, LoanProduct, Transaction, TxnType, User
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
                 on: date | None = None) -> LoanCharge:
    """A one-off charge against a loan, outside the catalogue timing."""
    from ..exceptions import BusinessRuleError

    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A charge must be greater than zero")
    on = on or date.today()

    # A manual charge is settled at the counter, so it is its own movement of
    # money rather than a deduction from an advance.
    txn = Transaction.objects.create(
        loan=loan, txn_type=TxnType.CHARGE, txn_date=on, amount=amount, posted_by=user,
        narration=f"Charge collected: {name}",
    )
    return LoanCharge.objects.create(loan=loan, charge=charge, name=name, amount=amount,
                                     applied_on=on, transaction=txn)
