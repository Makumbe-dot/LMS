"""Double-entry general ledger.

Every money movement in the loan book raises exactly one balanced journal entry,
so the ledger and the loan book cannot drift apart. Posting is driven off the
Transaction row rather than off each caller, which means a new way of moving
money is automatically accounted for as soon as it writes a transaction.

Interest is recognised **when it is collected**, not as it accrues. That keeps
the ledger in step with a book whose interest is recognised instalment by
instalment, and is the usual treatment in small lenders. Penalties, in contrast,
are recognised when they are charged, because they are raised as a receivable on
the instalment.

Account codes are held in CODES below and seeded by `manage.py seed`; if an
account is missing the entry is skipped rather than blocking the posting, so a
half-configured chart of accounts can never stop a teller taking money.
"""
import logging
from decimal import Decimal

from django.db import transaction as db_transaction

from ..models import (
    AccountType,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Transaction,
    TxnType,
)
from .amortisation import q

log = logging.getLogger(__name__)
ZERO = Decimal("0")

# The chart of accounts this module posts to.
CODES = {
    "bank": "1000",
    "loans_receivable": "1100",
    "penalties_receivable": "1300",
    "charges_receivable": "1400",
    "provision": "1900",
    "client_funds": "2000",
    "interest_income": "4000",
    "fee_income": "4100",
    "penalty_income": "4200",
    "recovery_income": "4300",
    "write_off": "5000",
    "impairment": "5100",
    "savings_interest": "5200",
}

DEFAULT_ACCOUNTS = [
    ("1000", "Cash and bank", AccountType.ASSET, "Where disbursements leave from and repayments land"),
    ("1100", "Loans receivable - principal", AccountType.ASSET, "Principal advanced and not yet repaid"),
    ("1300", "Penalties receivable", AccountType.ASSET, "Late-payment penalties charged and not yet collected"),
    ("1400", "Charges receivable", AccountType.ASSET, "Fees added to a loan balance and not yet collected"),
    ("1900", "Provision for credit losses", AccountType.ASSET, "Contra-asset; expected credit loss held against the book"),
    ("2000", "Client funds payable", AccountType.LIABILITY, "Amounts held on behalf of borrowers"),
    ("3000", "Retained earnings", AccountType.EQUITY, "Accumulated result"),
    ("4000", "Interest income", AccountType.INCOME, "Interest recognised as it is collected"),
    ("4100", "Fee income", AccountType.INCOME, "Admin and credit-life fees deducted at disbursement"),
    ("4200", "Penalty income", AccountType.INCOME, "Late-payment penalties charged"),
    ("4300", "Recoveries", AccountType.INCOME, "Amounts collected on loans already written off"),
    ("5000", "Loan write-offs", AccountType.EXPENSE, "Balances written off the book"),
    ("5100", "Impairment charge", AccountType.EXPENSE, "Movement in the expected credit loss provision"),
    ("5200", "Savings interest expense", AccountType.EXPENSE, "Interest credited to members' savings"),
]


def ensure_chart_of_accounts() -> int:
    """Create any missing default account. Safe to call repeatedly."""
    created = 0
    for code, name, kind, description in DEFAULT_ACCOUNTS:
        _, was_created = LedgerAccount.objects.get_or_create(
            code=code, defaults={"name": name, "type": kind, "description": description})
        created += 1 if was_created else 0
    return created


def _accounts() -> dict[str, LedgerAccount]:
    wanted = set(CODES.values())
    return {a.code: a for a in LedgerAccount.objects.filter(code__in=wanted)}


def _next_entry_no() -> str:
    from .loans import next_number

    return next_number("JE", width=8)


def _lines_for(txn: Transaction) -> list[tuple[str, Decimal, Decimal, str]]:
    """(account code, debit, credit, description) for one transaction.

    Every branch below must balance: total debits == total credits.
    """
    loan = txn.loan
    amount = q(txn.amount)
    principal = q(txn.principal_component)
    interest = q(txn.interest_component)
    penalty = q(txn.penalty_component)
    charge = q(txn.charge_component)

    if txn.txn_type == TxnType.DISBURSEMENT:
        # Dr the receivable with the full principal; the borrower gets the principal
        # less the upfront fees, which fall straight to income.
        fees = q(loan.admin_fee + loan.insurance_fee + loan.other_charges)
        lines = [
            (CODES["loans_receivable"], principal, ZERO, "Principal advanced"),
            (CODES["bank"], ZERO, q(principal - fees), "Net paid to the borrower"),
        ]
        if fees > 0:
            lines.append((CODES["fee_income"], ZERO, fees, "Admin and credit-life fees"))
        return lines

    if txn.txn_type == TxnType.PENALTY:
        return [
            (CODES["penalties_receivable"], amount, ZERO, "Late-payment penalty charged"),
            (CODES["penalty_income"], ZERO, amount, "Late-payment penalty charged"),
        ]

    if txn.txn_type == TxnType.REPAYMENT:
        lines = [(CODES["bank"], amount, ZERO, "Repayment received")]
        if principal > 0:
            lines.append((CODES["loans_receivable"], ZERO, principal, "Principal repaid"))
        if interest > 0:
            lines.append((CODES["interest_income"], ZERO, interest, "Interest collected"))
        if penalty > 0:
            lines.append((CODES["penalties_receivable"], ZERO, penalty, "Penalty collected"))
        if charge > 0:
            lines.append((CODES["charges_receivable"], ZERO, charge, "Charge collected"))
        return lines

    if txn.txn_type == TxnType.REVERSAL:
        # The exact mirror of the repayment being undone.
        lines = [(CODES["bank"], ZERO, amount, "Repayment reversed")]
        if principal > 0:
            lines.append((CODES["loans_receivable"], principal, ZERO, "Principal restored"))
        if interest > 0:
            lines.append((CODES["interest_income"], interest, ZERO, "Interest income reversed"))
        if penalty > 0:
            lines.append((CODES["penalties_receivable"], penalty, ZERO, "Penalty restored"))
        if charge > 0:
            lines.append((CODES["charges_receivable"], charge, ZERO, "Charge restored"))
        return lines

    if txn.txn_type == TxnType.WAIVER:
        # Waiving a penalty reverses income already recognised. Waiving interest
        # (the early-settlement rebate) touches nothing, because interest is only
        # recognised when it is collected.
        if penalty > 0:
            return [
                (CODES["penalty_income"], penalty, ZERO, "Penalty waived"),
                (CODES["penalties_receivable"], ZERO, penalty, "Penalty waived"),
            ]
        return []

    if txn.txn_type == TxnType.WRITE_OFF:
        # Only recognised balances leave the ledger: principal and penalties.
        # Unearned interest was never income, so it is not an expense now.
        recognised = q(principal + penalty + charge)
        if recognised <= 0:
            return []
        lines = [(CODES["write_off"], recognised, ZERO, "Balance written off")]
        if principal > 0:
            lines.append((CODES["loans_receivable"], ZERO, principal, "Principal written off"))
        if penalty > 0:
            lines.append((CODES["penalties_receivable"], ZERO, penalty, "Penalties written off"))
        if charge > 0:
            lines.append((CODES["charges_receivable"], ZERO, charge, "Charges written off"))
        return lines

    if txn.txn_type == TxnType.CHARGE:
        # Raised and settled at the counter, unlike the fees netted off an advance.
        return [
            (CODES["bank"], amount, ZERO, "Charge collected"),
            (CODES["fee_income"], ZERO, amount, "Charge income"),
        ]

    if txn.txn_type == TxnType.CAPITALISATION:
        # Rescheduling rolls overdue interest, penalties and charges into a new
        # principal. The receivable grows by exactly what the other three shed.
        #
        # Capitalised interest IS recognised here, which is the one deliberate
        # exception to "interest is recognised when collected": once capitalised
        # it stops being interest and becomes principal the borrower owes, and
        # leaving it unrecognised would put an asset on 1100 with nothing on the
        # other side.
        lines = [(CODES["loans_receivable"], principal, ZERO, "Capitalised into a new principal")]
        if interest > 0:
            lines.append((CODES["interest_income"], ZERO, interest, "Overdue interest capitalised"))
        if penalty > 0:
            lines.append((CODES["penalties_receivable"], ZERO, penalty, "Penalties capitalised"))
        if charge > 0:
            lines.append((CODES["charges_receivable"], ZERO, charge, "Charges capitalised"))
        return lines

    if txn.txn_type == TxnType.CHARGE_ADDED:
        # Added to the loan balance: a receivable now, cash when the borrower pays.
        return [
            (CODES["charges_receivable"], amount, ZERO, "Charge added to the balance"),
            (CODES["fee_income"], ZERO, amount, "Charge income"),
        ]

    if txn.txn_type == TxnType.RECOVERY:
        return [
            (CODES["bank"], amount, ZERO, "Recovery received"),
            (CODES["recovery_income"], ZERO, amount, "Recovery on a written-off loan"),
        ]

    # TxnType.FEE is informational: the fee is already inside the disbursement entry.
    return []


@db_transaction.atomic
def post_transaction(txn: Transaction) -> JournalEntry | None:
    """Raise the journal entry for a transaction. Idempotent per transaction."""
    existing = JournalEntry.objects.filter(transaction=txn).first()
    if existing:
        return existing

    acc = _accounts()
    if not acc:
        log.warning("No chart of accounts; skipping the ledger entry for transaction %s", txn.id)
        return None

    raw = _lines_for(txn)
    lines = [(code, d, c, text) for code, d, c, text in raw if d > 0 or c > 0]
    if not lines:
        return None

    missing = [code for code, *_ in lines if code not in acc]
    if missing:
        log.warning("Ledger accounts %s are missing; skipping entry for transaction %s",
                    missing, txn.id)
        return None

    debits = sum((d for _, d, _, _ in lines), ZERO)
    credits = sum((c for _, _, c, _ in lines), ZERO)
    if debits != credits:
        # Refusing to post a lopsided entry is the whole point of double entry.
        log.error("Refusing an unbalanced entry for transaction %s: Dr %s vs Cr %s",
                  txn.id, debits, credits)
        return None

    entry = JournalEntry.objects.create(
        entry_no=_next_entry_no(), entry_date=txn.txn_date,
        narration=txn.narration or f"{txn.get_txn_type_display()} on {txn.loan.loan_no}",
        source=txn.txn_type, transaction=txn, loan=txn.loan,
        branch_id=txn.loan.branch_id, posted_by=txn.posted_by,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=acc[code], debit=d, credit=c, description=text)
        for code, d, c, text in lines
    ])
    return entry


def account_movement(code: str, start=None, end=None, branch_id=None) -> tuple[Decimal, Decimal]:
    """(total debits, total credits) posted to one account over a window.

    Returned as a pair rather than a balance, so the caller decides which side is
    natural for that account. A contra-asset such as 1900 stands on the credit
    side even though it is typed ASSET.
    """
    from django.db.models import DecimalField, Q, Sum, Value
    from django.db.models.functions import Coalesce

    money = DecimalField(max_digits=18, decimal_places=2)
    where = Q(account__code=code)
    if start:
        where &= Q(entry__entry_date__gte=start)
    if end:
        where &= Q(entry__entry_date__lte=end)
    if branch_id:
        where &= Q(entry__branch_id=branch_id)

    totals = JournalLine.objects.filter(where).aggregate(
        debit=Coalesce(Sum("debit", output_field=money), Value(ZERO, output_field=money)),
        credit=Coalesce(Sum("credit", output_field=money), Value(ZERO, output_field=money)),
    )
    return q(totals["debit"]), q(totals["credit"])


@db_transaction.atomic
def post_manual_entry(lines, entry_date, narration: str, source: str, *, loan=None,
                      branch_id=None, posted_by=None, strict: bool = True) -> JournalEntry | None:
    """Raise one balanced entry that no Transaction stands behind.

    `lines` is a list of (account code, debit, credit, description). Used by the
    provision run and anything else that moves value without moving cash. With
    strict=True a missing account raises rather than silently skipping, because
    an accounting run that half-posts is worse than one that refuses.
    """
    from ..exceptions import BusinessRuleError

    rows = [(code, q(d), q(c), text) for code, d, c, text in lines if q(d) > 0 or q(c) > 0]
    if not rows:
        return None

    acc = _accounts_by_code([code for code, *_ in rows])
    missing = sorted({code for code, *_ in rows if code not in acc})
    if missing:
        message = (f"Ledger account(s) {', '.join(missing)} are missing from the chart of "
                   f"accounts. Use the Rebuild button on the General ledger page.")
        if strict:
            raise BusinessRuleError(message)
        log.warning(message)
        return None

    debits = sum((d for _, d, _, _ in rows), ZERO)
    credits = sum((c for _, _, c, _ in rows), ZERO)
    if debits != credits:
        raise BusinessRuleError(
            f"Refusing an unbalanced entry for {source}: Dr {debits} vs Cr {credits}")

    entry = JournalEntry.objects.create(
        entry_no=_next_entry_no(), entry_date=entry_date, narration=narration, source=source,
        loan=loan, branch_id=branch_id, posted_by=posted_by,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=acc[code], debit=d, credit=c, description=text)
        for code, d, c, text in rows
    ])
    return entry


def _accounts_by_code(codes) -> dict[str, LedgerAccount]:
    return {a.code: a for a in LedgerAccount.objects.filter(code__in=set(codes))}


def _savings_lines(stxn) -> list[tuple[str, Decimal, Decimal, str]]:
    """Members' savings are the institution's liability, not its income."""
    from ..models import SavingsTxnType

    amount = q(stxn.amount)
    kind = stxn.txn_type
    if kind == SavingsTxnType.REVERSAL and stxn.reversal_of_id:
        kind = stxn.reversal_of.txn_type
        mirror = True
    else:
        mirror = False

    if kind == SavingsTxnType.DEPOSIT:
        lines = [(CODES["bank"], amount, ZERO, "Savings deposit"),
                 (CODES["client_funds"], ZERO, amount, "Owed to the member")]
    elif kind == SavingsTxnType.WITHDRAWAL:
        lines = [(CODES["client_funds"], amount, ZERO, "Paid out to the member"),
                 (CODES["bank"], ZERO, amount, "Savings withdrawal")]
    elif kind == SavingsTxnType.INTEREST:
        lines = [(CODES["savings_interest"], amount, ZERO, "Interest credited to savings"),
                 (CODES["client_funds"], ZERO, amount, "Owed to the member")]
    elif kind == SavingsTxnType.FEE:
        lines = [(CODES["client_funds"], amount, ZERO, "Account fee taken from the balance"),
                 (CODES["fee_income"], ZERO, amount, "Savings account fee")]
    else:
        return []

    if mirror:
        return [(code, credit, debit, f"Reversal: {text}") for code, debit, credit, text in lines]
    return lines


@db_transaction.atomic
def post_savings_transaction(stxn) -> JournalEntry | None:
    """Raise the journal entry for a savings movement. Idempotent per transaction."""
    existing = JournalEntry.objects.filter(savings_transaction=stxn).first()
    if existing:
        return existing

    acc = _accounts()
    lines = [(c, d, cr, t) for c, d, cr, t in _savings_lines(stxn) if d > 0 or cr > 0]
    if not lines:
        return None
    missing = [code for code, *_ in lines if code not in acc]
    if missing:
        log.warning("Ledger accounts %s are missing; skipping savings entry %s", missing, stxn.id)
        return None

    debits = sum((d for _, d, _, _ in lines), ZERO)
    credits = sum((c for _, _, c, _ in lines), ZERO)
    if debits != credits:
        log.error("Refusing an unbalanced savings entry %s: Dr %s vs Cr %s",
                  stxn.id, debits, credits)
        return None

    entry = JournalEntry.objects.create(
        entry_no=_next_entry_no(), entry_date=stxn.txn_date,
        narration=stxn.narration or f"{stxn.get_txn_type_display()} on {stxn.account.account_no}",
        source=f"savings_{stxn.txn_type}", savings_transaction=stxn,
        branch_id=stxn.account.branch_id, posted_by=stxn.posted_by,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=acc[code], debit=d, credit=c, description=text)
        for code, d, c, text in lines
    ])
    return entry


def backfill(limit: int | None = None) -> dict:
    """Post ledger entries for transactions that do not have one.

    Used after the chart of accounts is set up on a book that already has
    history, and by the seed command.
    """
    ensure_chart_of_accounts()
    pending = (Transaction.objects
               .filter(journal_entry__isnull=True)
               .select_related("loan")
               .order_by("id"))
    if limit:
        pending = pending[:limit]

    posted = skipped = 0
    for txn in pending:
        if post_transaction(txn):
            posted += 1
        else:
            skipped += 1

    from ..models import SavingsTransaction

    savings_pending = (SavingsTransaction.objects
                       .filter(journal_entry__isnull=True)
                       .select_related("account", "reversal_of")
                       .order_by("id"))
    if limit:
        savings_pending = savings_pending[:limit]
    for stxn in savings_pending:
        if post_savings_transaction(stxn):
            posted += 1
        else:
            skipped += 1

    # Provision entries stand behind no Transaction, so the two sweeps above
    # cannot find them. Without this, "Rebuild" after a JournalEntry wipe would
    # leave account 1900 at zero while the loans still carry a provision, and
    # the fifth reconciliation identity would be unrecoverable.
    from .provisioning import repost_runs

    reposted = repost_runs()
    posted += reposted["reposted"]

    return {"posted": posted, "skipped": skipped, "provision_runs_reposted": reposted["reposted"]}


def trial_balance(start=None, end=None, branch_id=None) -> dict:
    """Every account with its debits, credits and closing balance."""
    from django.db.models import DecimalField, Q, Sum, Value
    from django.db.models.functions import Coalesce

    money = DecimalField(max_digits=18, decimal_places=2)
    where = Q()
    if start:
        where &= Q(lines__entry__entry_date__gte=start)
    if end:
        where &= Q(lines__entry__entry_date__lte=end)
    if branch_id:
        where &= Q(lines__entry__branch_id=branch_id)

    rows = []
    total_debit = total_credit = ZERO
    accounts = (LedgerAccount.objects
                .annotate(
                    debit=Coalesce(Sum("lines__debit", filter=where, output_field=money),
                                   Value(ZERO, output_field=money)),
                    credit=Coalesce(Sum("lines__credit", filter=where, output_field=money),
                                    Value(ZERO, output_field=money)))
                .order_by("code"))

    for account in accounts:
        debit = q(account.debit)
        credit = q(account.credit)
        if debit == 0 and credit == 0:
            continue
        balance = q(debit - credit) if account.is_debit_balance else q(credit - debit)
        rows.append({
            "code": account.code, "name": account.name, "type": account.type,
            "debit": debit, "credit": credit, "balance": balance,
            "side": "debit" if account.is_debit_balance else "credit",
        })
        total_debit += debit
        total_credit += credit

    return {
        "rows": rows,
        "total_debit": q(total_debit),
        "total_credit": q(total_credit),
        "balanced": q(total_debit) == q(total_credit),
        "start": start,
        "end": end,
    }


def income_statement(start=None, end=None, branch_id=None) -> dict:
    """Income less expense over a period, from the same ledger."""
    balance = trial_balance(start, end, branch_id)
    income = [r for r in balance["rows"] if r["type"] == AccountType.INCOME]
    expense = [r for r in balance["rows"] if r["type"] == AccountType.EXPENSE]
    total_income = q(sum((r["balance"] for r in income), ZERO))
    total_expense = q(sum((r["balance"] for r in expense), ZERO))
    return {
        "income": income,
        "expense": expense,
        "total_income": total_income,
        "total_expense": total_expense,
        "surplus": q(total_income - total_expense),
        "start": start,
        "end": end,
    }
