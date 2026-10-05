"""Credit-life claims.

Every loan carries a credit-life fee, and this is what it buys: when a borrower
dies, is permanently disabled or is retrenched, the balance is claimed from the
insurer instead of chased from the family.

  lodge   an officer records the event. From then until the claim is decided the
          loan accrues no penalties and its borrower is sent no reminders: nobody
          should be texting a widow about arrears.
  pay     the insurer's payout is posted to the loan as an ordinary repayment, so
          the ledger needs no special case. Whatever it does not cover can be
          written off in the same step, or left on the loan to collect.
  reject  the claim is closed and the loan is back to normal. Penalties resume
          from where they stopped, so the days the claim was open are charged:
          the instalments were overdue all along.
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from ..exceptions import BusinessRuleError
from ..models import (
    ClaimStatus,
    InsuranceClaim,
    Loan,
    LoanStatus,
    PaymentMethod,
    User,
)
from .amortisation import q
from .loans import next_number, refresh_balances, write_off
from .repayments import post_repayment

ZERO = Decimal("0")


def lodge(loan: Loan, user: User, cause: str, event_date: date,
          insurer_reference: str | None = None, notes: str | None = None) -> InsuranceClaim:
    if loan.status != LoanStatus.ACTIVE:
        raise BusinessRuleError(f"Loan is {loan.status}; only an active loan can be claimed on")
    if event_date > date.today():
        raise BusinessRuleError("The event cannot be in the future")
    if loan.disbursement_date and event_date < loan.disbursement_date:
        raise BusinessRuleError("The event is before the loan was disbursed, so it was not covered")
    if loan.claims.filter(status=ClaimStatus.LODGED).exists():
        raise BusinessRuleError("This loan already has a claim open")
    refresh_balances(loan)
    return InsuranceClaim.objects.create(
        claim_no=next_number("CLM"), loan=loan, cause=cause, event_date=event_date,
        lodged_on=date.today(), lodged_by=user, amount_claimed=loan.total_outstanding,
        insurer_reference=insurer_reference or None, notes=notes or None)


def _decided(claim: InsuranceClaim, user: User, note: str | None) -> None:
    claim.decided_by = user
    claim.decided_at = datetime.now(timezone.utc)
    claim.decision_note = (note or "").strip() or None


def pay(claim: InsuranceClaim, user: User, amount: Decimal, paid_on: date | None,
        reference: str | None, write_off_remainder: bool, note: str | None = None
        ) -> InsuranceClaim:
    """Post the insurer's payout, and write off what it leaves if asked to."""
    if claim.status != ClaimStatus.LODGED:
        raise BusinessRuleError(f"The claim is already {claim.status}")
    loan = claim.loan
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("The payout must be more than zero")
    txn = post_repayment(
        loan, user, amount, paid_on, PaymentMethod.BANK_TRANSFER,
        reference or claim.insurer_reference,
        f"Credit-life claim {claim.claim_no} paid by the insurer")

    remainder = q(loan.total_outstanding) if loan.status == LoanStatus.ACTIVE else ZERO
    if remainder > 0 and write_off_remainder:
        write_off(loan, user, f"Balance left after credit-life claim {claim.claim_no}")
        claim.remainder_written_off = remainder

    claim.status = ClaimStatus.PAID
    claim.amount_paid = amount
    claim.paid_on = txn.txn_date
    claim.transaction = txn
    if reference:
        claim.insurer_reference = reference
    _decided(claim, user, note)
    claim.save()
    return claim


def reject(claim: InsuranceClaim, user: User, note: str) -> InsuranceClaim:
    if claim.status != ClaimStatus.LODGED:
        raise BusinessRuleError(f"The claim is already {claim.status}")
    if not (note or "").strip():
        raise BusinessRuleError("Say why the insurer rejected the claim")
    claim.status = ClaimStatus.REJECTED
    _decided(claim, user, note)
    claim.save()
    return claim
