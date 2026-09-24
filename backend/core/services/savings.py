"""Savings and deposit accounts.

Members' balances are the institution's liability, not its income, and the
ledger reflects that: a deposit debits bank and credits client funds payable.

Every movement writes its own SavingsTransaction carrying the balance after it,
so a statement can be read straight off the table without replaying history.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    SavingsAccount,
    SavingsProduct,
    SavingsStatus,
    SavingsTransaction,
    SavingsTxnType,
    User,
)
from .amortisation import add_months, q
from .loans import next_number

ZERO = Decimal("0")


def open_account(borrower: Borrower, product: SavingsProduct, user: User,
                 opening_deposit: Decimal | None = None, opened_on: date | None = None,
                 method: str | None = None, reference: str | None = None) -> SavingsAccount:
    if not product.is_active:
        raise BusinessRuleError("That savings product is not active")
    if borrower.is_blacklisted:
        raise BusinessRuleError("Borrower is blacklisted")
    existing = borrower.savings_accounts.filter(product=product).exclude(
        status=SavingsStatus.CLOSED).first()
    if existing:
        raise BusinessRuleError(
            f"{borrower.full_name} already has a {product.name} account ({existing.account_no})")

    account = SavingsAccount.objects.create(
        account_no=next_number("SAV"), borrower=borrower, product=product,
        branch_id=borrower.branch_id, opened_on=opened_on or date.today(),
    )
    if opening_deposit and Decimal(opening_deposit) > 0:
        deposit(account, user, opening_deposit, opened_on, method, reference, "Opening deposit")
    return account


def _post(account: SavingsAccount, user: User, kind: str, amount: Decimal,
          txn_date: date | None, method: str | None, reference: str | None,
          narration: str | None, signed: Decimal) -> SavingsTransaction:
    """Write one movement and the resulting balance, atomically."""
    amount = q(Decimal(amount))
    account.balance = q((account.balance or ZERO) + signed)
    account.save(update_fields=["balance"])
    return SavingsTransaction.objects.create(
        account=account, txn_type=kind, txn_date=txn_date or date.today(), amount=amount,
        balance_after=account.balance, method=method, reference=reference,
        narration=narration, posted_by=user,
    )


@db_transaction.atomic
def deposit(account: SavingsAccount, user: User, amount: Decimal, txn_date: date | None = None,
            method: str | None = None, reference: str | None = None,
            narration: str | None = None) -> SavingsTransaction:
    if account.status == SavingsStatus.CLOSED:
        raise BusinessRuleError(f"Account {account.account_no} is closed")
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A deposit must be greater than zero")
    if account.status == SavingsStatus.DORMANT:
        account.status = SavingsStatus.ACTIVE  # a deposit wakes a dormant account
        account.save(update_fields=["status"])
    return _post(account, user, SavingsTxnType.DEPOSIT, amount, txn_date, method, reference,
                 narration or "Deposit", amount)


@db_transaction.atomic
def withdraw(account: SavingsAccount, user: User, amount: Decimal, txn_date: date | None = None,
             method: str | None = None, reference: str | None = None,
             narration: str | None = None) -> SavingsTransaction:
    if account.status != SavingsStatus.ACTIVE:
        raise BusinessRuleError(f"Account {account.account_no} is {account.status}")
    if not account.product.allow_withdrawals:
        raise BusinessRuleError(f"{account.product.name} does not allow withdrawals")
    amount = q(Decimal(amount))
    if amount <= 0:
        raise BusinessRuleError("A withdrawal must be greater than zero")
    if amount > account.available_balance:
        raise BusinessRuleError(
            f"{amount} exceeds the {account.available_balance} available "
            f"(balance {account.balance}, minimum balance {account.product.min_balance})")
    return _post(account, user, SavingsTxnType.WITHDRAWAL, amount, txn_date, method, reference,
                 narration or "Withdrawal", -amount)


@db_transaction.atomic
def reverse(account: SavingsAccount, stxn: SavingsTransaction, user: User,
            narration: str) -> SavingsTransaction:
    if stxn.reversed:
        raise BusinessRuleError("That movement has already been reversed")
    if stxn.txn_type not in (SavingsTxnType.DEPOSIT, SavingsTxnType.WITHDRAWAL):
        raise BusinessRuleError("Only deposits and withdrawals can be reversed")

    signed = -stxn.amount if stxn.txn_type == SavingsTxnType.DEPOSIT else stxn.amount
    if account.balance + signed < 0:
        raise BusinessRuleError(
            "Reversing this deposit would take the balance below zero; the money has been drawn")

    stxn.reversed = True
    stxn.save(update_fields=["reversed"])
    reversal = _post(account, user, SavingsTxnType.REVERSAL, stxn.amount, date.today(),
                     stxn.method, stxn.reference, narration, signed)
    reversal.reversal_of = stxn
    reversal.save(update_fields=["reversal_of"])
    return reversal


@db_transaction.atomic
def close_account(account: SavingsAccount, user: User, narration: str | None = None) -> SavingsAccount:
    """Pay out whatever is left, then close."""
    if account.status == SavingsStatus.CLOSED:
        raise BusinessRuleError("That account is already closed")
    if account.balance < 0:
        raise BusinessRuleError("The account is overdrawn and cannot be closed")
    if account.balance > 0:
        _post(account, user, SavingsTxnType.WITHDRAWAL, account.balance, date.today(), None, None,
              narration or "Closing withdrawal", -account.balance)
    account.status = SavingsStatus.CLOSED
    account.closed_on = date.today()
    account.save(update_fields=["status", "closed_on"])
    return account


def accrue_interest(as_of: date | None = None, account: SavingsAccount | None = None) -> dict:
    """Credit a month of interest and take the monthly fee.

    Interest is simple, on the balance standing at the run, at one twelfth of the
    product's annual rate. Idempotent within a month: each account records the
    date it was last credited.
    """
    as_of = as_of or date.today()
    accounts = ([account] if account is not None
                else list(SavingsAccount.objects.filter(status=SavingsStatus.ACTIVE)
                          .select_related("product", "borrower")))

    credited = charged = 0
    interest_total = fee_total = ZERO
    for acc in accounts:
        product = acc.product
        # One credit per calendar month.
        if acc.last_interest_date and add_months(acc.last_interest_date, 1) > as_of:
            continue

        with db_transaction.atomic():
            if product.interest_rate_pct_pa > 0 and acc.balance > 0:
                interest = q(acc.balance * product.interest_rate_pct_pa / 100 / 12)
                if interest > 0:
                    _post(acc, None, SavingsTxnType.INTEREST, interest, as_of, None, None,
                          f"Interest at {product.interest_rate_pct_pa}% a year", interest)
                    interest_total += interest
                    credited += 1

            if product.monthly_fee > 0 and acc.balance > 0:
                fee = min(q(product.monthly_fee), acc.balance)
                if fee > 0:
                    _post(acc, None, SavingsTxnType.FEE, fee, as_of, None, None,
                          "Monthly account fee", -fee)
                    fee_total += fee
                    charged += 1

            acc.last_interest_date = as_of
            acc.save(update_fields=["last_interest_date"])

    return {
        "as_of": as_of.isoformat(),
        "accounts_credited": credited,
        "interest_credited": q(interest_total),
        "accounts_charged": charged,
        "fees_taken": q(fee_total),
    }


def mark_dormant(months: int = 6, as_of: date | None = None) -> dict:
    """Flag accounts with no member activity for a while."""
    as_of = as_of or date.today()
    cutoff = add_months(as_of, -months)
    touched = 0
    for acc in SavingsAccount.objects.filter(status=SavingsStatus.ACTIVE):
        last = (acc.transactions
                .filter(txn_type__in=[SavingsTxnType.DEPOSIT, SavingsTxnType.WITHDRAWAL])
                .order_by("-txn_date").first())
        last_date = last.txn_date if last else acc.opened_on
        if last_date < cutoff:
            acc.status = SavingsStatus.DORMANT
            acc.save(update_fields=["status"])
            touched += 1
    return {"as_of": as_of.isoformat(), "marked_dormant": touched}


def portfolio(branch_id=None) -> dict:
    """The savings book at a glance."""
    qs = SavingsAccount.objects.select_related("product", "borrower", "branch")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    accounts = list(qs)
    active = [a for a in accounts if a.status == SavingsStatus.ACTIVE]
    return {
        "accounts": len(accounts),
        "active_accounts": len(active),
        "dormant_accounts": sum(1 for a in accounts if a.status == SavingsStatus.DORMANT),
        "closed_accounts": sum(1 for a in accounts if a.status == SavingsStatus.CLOSED),
        "total_balance": q(sum((a.balance for a in accounts), ZERO)),
        "by_product": [
            {
                "product": product.name,
                "accounts": sum(1 for a in accounts if a.product_id == product.id),
                "balance": q(sum((a.balance for a in accounts if a.product_id == product.id), ZERO)),
            }
            for product in SavingsProduct.objects.all()
        ],
    }
