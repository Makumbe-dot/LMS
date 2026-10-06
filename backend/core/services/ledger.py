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

The ledger is kept in the organisation's currency. A loan in another currency is
converted as it is posted: receivables at the loan's booked rate, as the change
in their base-currency carrying value so the postings telescope exactly; cash
and income at the spot rate of the day; the difference to 4800. A savings
account or a funding facility in another currency is converted the same way,
its liability (2000, 2100, 2110) at its own booked rate. See services/fx.py for
the rules and the revaluation run.
"""
import logging
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models.functions import Coalesce as Coalesce_

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
    "borrowings": "2100",
    "accrued_interest": "2110",
    "share_capital": "3100",
    "distributions": "3200",
    "write_off": "5000",
    "impairment": "5100",
    "savings_interest": "5200",
    "borrowing_interest": "5300",
    "facility_fees": "5310",
    "fx_differences": "4800",
    "deferred_fees": "1150",
    "interest_receivable": "1200",
}

# Deliberately NOT in CODES, which is documented as the accounts this module posts
# to. Nothing posts to 3000: balance_sheet derives it as income less expense since
# inception, and that derivation is what makes assets equal liabilities plus equity
# by construction rather than by luck.
RETAINED_EARNINGS = "3000"

DEFAULT_ACCOUNTS = [
    ("1000", "Cash and bank", AccountType.ASSET, "Where disbursements leave from and repayments land"),
    ("1100", "Loans receivable - principal", AccountType.ASSET, "Principal advanced and not yet repaid"),
    ("1150", "Deferred loan fees", AccountType.ASSET, "Contra-asset; fees deducted at disbursement and not yet taken to income under the effective interest method"),
    ("1200", "Interest receivable", AccountType.ASSET, "Interest accrued at the effective rate and not yet collected"),
    ("1300", "Penalties receivable", AccountType.ASSET, "Late-payment penalties charged and not yet collected"),
    ("1400", "Charges receivable", AccountType.ASSET, "Fees added to a loan balance and not yet collected"),
    ("1900", "Provision for credit losses", AccountType.ASSET, "Contra-asset; expected credit loss held against the book"),
    ("2000", "Client funds payable", AccountType.LIABILITY, "Amounts held on behalf of borrowers"),
    ("2100", "Funder borrowings", AccountType.LIABILITY, "Principal drawn on funding facilities and not yet repaid"),
    ("2110", "Accrued interest on borrowings", AccountType.LIABILITY, "Interest owed to funders and not yet paid"),
    ("3000", "Retained earnings", AccountType.EQUITY, "Accumulated result"),
    ("3100", "Share capital", AccountType.EQUITY, "Capital contributed by shareholders, less any returned"),
    ("3200", "Distributions to shareholders", AccountType.EQUITY, "Dividends paid out; carries a debit balance and nets off contributed capital"),
    ("4000", "Interest income", AccountType.INCOME, "Interest recognised as it is collected"),
    ("4100", "Fee income", AccountType.INCOME, "Admin and credit-life fees deducted at disbursement"),
    ("4200", "Penalty income", AccountType.INCOME, "Late-payment penalties charged"),
    ("4300", "Recoveries", AccountType.INCOME, "Amounts collected on loans already written off"),
    ("5000", "Loan write-offs", AccountType.EXPENSE, "Balances written off the book"),
    ("5100", "Impairment charge", AccountType.EXPENSE, "Movement in the expected credit loss provision"),
    ("5200", "Savings interest expense", AccountType.EXPENSE, "Interest credited to members' savings"),
    ("5300", "Interest on borrowings", AccountType.EXPENSE, "Interest expense on funding facilities, accrued monthly"),
    ("5310", "Facility fees", AccountType.EXPENSE, "Arrangement and commitment fees on funding facilities"),
    # What a lender spends and owns beyond its loan book, posted by manual journal.
    # Without these, salaries and rent had nowhere to go and the income statement
    # showed lending income as if it were profit.
    ("1500", "Other receivables and prepayments", AccountType.ASSET, "Deposits paid, prepaid rent and other amounts owed to the institution"),
    ("1600", "Property and equipment", AccountType.ASSET, "Vehicles, computers, furniture, at cost"),
    ("1690", "Accumulated depreciation", AccountType.ASSET, "Contra-asset; carries a credit balance against 1600"),
    ("2900", "Accruals and other payables", AccountType.LIABILITY, "Bills received or expenses incurred and not yet paid"),
    ("3900", "Opening balances", AccountType.EQUITY, "Balances brought forward from a previous system; clear to retained earnings once agreed"),
    ("4800", "Exchange differences", AccountType.INCOME, "Realised and unrealised gains and losses on foreign-currency loans; a loss shows as a negative balance"),
    ("4900", "Other income", AccountType.INCOME, "Income outside the loan book, and cash over at the tills"),
    ("6000", "Staff costs", AccountType.EXPENSE, "Salaries, wages, allowances and statutory contributions"),
    ("6100", "Rent and premises", AccountType.EXPENSE, "Rent, rates, utilities, security and cleaning"),
    ("6200", "Transport and travel", AccountType.EXPENSE, "Fuel, vehicle running costs and field travel"),
    ("6300", "Communication and IT", AccountType.EXPENSE, "Airtime, data, SMS gateway, software and hosting"),
    ("6400", "Professional fees", AccountType.EXPENSE, "Audit, legal, consulting and regulatory fees"),
    ("6500", "Office and administration", AccountType.EXPENSE, "Stationery, printing, postage and sundries"),
    ("6600", "Bank charges", AccountType.EXPENSE, "Bank and mobile-money transaction charges"),
    ("6700", "Depreciation", AccountType.EXPENSE, "The year's charge against property and equipment"),
    ("6800", "Cash shortages", AccountType.EXPENSE, "Cash short at the tills, written off on verification"),
    ("6900", "Other operating expenses", AccountType.EXPENSE, "Anything without a better home; review it monthly"),
]

# The accounts a sub-ledger is reconciled against (see `reconciliation`), and where
# their postings belong instead. A manual journal to any of them would open a break
# between the ledger and the book that nothing in the system could explain, so
# `journals` refuses them and names the right place.
CONTROL_ACCOUNTS = {
    "1100": "the loan itself (a disbursement, repayment, write-off or reschedule)",
    "1150": "the interest accrual run on the General ledger page",
    "1200": "the interest accrual run on the General ledger page",
    "1300": "the loan itself (penalty accrual, waiver or repayment)",
    "1400": "the loan itself (raise a charge, or take a repayment)",
    "1900": "the provision run on the Provisioning page",
    "2000": "the member's savings account (deposit, withdrawal or reversal)",
    "2100": "the facility on the Funding and capital page",
    "2110": "the facility on the Funding and capital page",
    "3100": "a capital movement on the Funding and capital page",
    "3200": "a dividend on the Funding and capital page",
}

# Where a till's counted difference goes when a supervisor verifies it.
CASH_SHORTAGES = "6800"
OTHER_INCOME = "4900"
OPENING_BALANCES = "3900"


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


# How each transaction type moves the three receivables, in the loan's currency.
# Summed over a loan's transactions in id order this is the loan's outstanding
# principal, penalties and charges after each one: that is what the ledger carries,
# converted, and why a posting is the change in the converted figure (see fx.py).
_PRINCIPAL_SIGN = {
    TxnType.DISBURSEMENT: 1, TxnType.OPENING_BALANCE: 1, TxnType.CAPITALISATION: 1,
    TxnType.REVERSAL: 1, TxnType.REPAYMENT: -1, TxnType.WRITE_OFF: -1,
}
_PENALTY_SIGN = {
    TxnType.PENALTY: 1, TxnType.OPENING_BALANCE: 1, TxnType.REVERSAL: 1,
    TxnType.REPAYMENT: -1, TxnType.WAIVER: -1, TxnType.WRITE_OFF: -1, TxnType.CAPITALISATION: -1,
}
_CHARGE_SIGN = {
    TxnType.CHARGE_ADDED: 1, TxnType.REVERSAL: 1,
    TxnType.REPAYMENT: -1, TxnType.WRITE_OFF: -1, TxnType.CAPITALISATION: -1,
}


def _movement(txn: Transaction, signs: dict, field: str) -> Decimal:
    sign = signs.get(txn.txn_type, 0)
    if not sign:
        return ZERO
    value = txn.amount if field == "amount" else getattr(txn, field)
    return q(sign * (value or ZERO))


def _receivables_after(txn: Transaction) -> tuple[Decimal, Decimal, Decimal]:
    """(principal, penalties, charges) outstanding on the loan once `txn` has
    been applied, from the transactions themselves rather than the loan's
    columns, which a service may not have refreshed yet when the hook fires."""
    from django.db.models import Case, DecimalField, F, Sum, Value, When

    money = DecimalField(max_digits=18, decimal_places=2)

    def signed(signs, field):
        return Coalesce_(Sum(Case(
            *[When(txn_type=kind, then=F(field) * Value(sign)) for kind, sign in signs.items()],
            default=Value(ZERO), output_field=money), output_field=money), Value(ZERO, output_field=money))

    totals = (Transaction.objects.filter(loan_id=txn.loan_id, id__lte=txn.id)
              .aggregate(p=signed(_PRINCIPAL_SIGN, "principal_component"),
                         pen=signed({**{k: v for k, v in _PENALTY_SIGN.items() if k != TxnType.PENALTY}},
                                    "penalty_component"),
                         pen_raised=signed({TxnType.PENALTY: 1}, "amount"),
                         chg=signed({**{k: v for k, v in _CHARGE_SIGN.items() if k != TxnType.CHARGE_ADDED}},
                                    "charge_component"),
                         chg_raised=signed({TxnType.CHARGE_ADDED: 1}, "amount")))
    return (q(totals["p"]), q(totals["pen"] + totals["pen_raised"]),
            q(totals["chg"] + totals["chg_raised"]))


def _eir_state(loan_id: int, upto_id: int | None, fees: Decimal) -> tuple[Decimal, Decimal]:
    """(interest receivable, fees deferred) on a loan once every transaction with
    id <= `upto_id` has been applied, from the transactions themselves.

    The receivable is the contractual interest accrued less the interest collected
    or capitalised, never below zero; both sums restart after a reschedule, which
    replaces the schedule. A write-off clears both. The deferred fees are what was
    deducted at disbursement less every unwind since.
    """
    from django.db.models import Max, Sum

    qs = Transaction.objects.filter(loan_id=loan_id)
    if upto_id is not None:
        qs = qs.filter(id__lte=upto_id)
    if qs.filter(txn_type=TxnType.WRITE_OFF).exists():
        return ZERO, ZERO
    # The fees are deferred by the disbursement itself: before it there is nothing
    # on 1150, and a loan brought over by opening balance never has any.
    if not qs.filter(txn_type=TxnType.DISBURSEMENT).exists():
        fees = ZERO
    base = qs.filter(txn_type=TxnType.CAPITALISATION).aggregate(m=Max("id"))["m"]
    window = qs.filter(id__gt=base) if base else qs
    if base:
        fees = ZERO

    def total(kind, field):
        return q(window.filter(txn_type=kind).aggregate(v=Sum(field))["v"] or ZERO)

    accrued = total(TxnType.ACCRUAL, "interest_component")
    settled = (total(TxnType.REPAYMENT, "interest_component")
               - total(TxnType.REVERSAL, "interest_component")
               + total(TxnType.CAPITALISATION, "interest_component"))
    receivable = max(ZERO, q(accrued - settled))
    unwound = q(total(TxnType.ACCRUAL, "amount") - accrued)
    return receivable, q(fees - unwound)


def _eir_movements(txn: Transaction) -> tuple[Decimal, Decimal]:
    """The change 1200 and 1150 take for `txn`, in the base currency, or zeros
    when interest is recognised on collection. 1150 is a credit balance, so a
    positive figure means more deferred."""
    from .eir import is_effective
    from .fx import to_base

    if not is_effective():
        return ZERO, ZERO
    loan = txn.loan
    fees = q(loan.admin_fee + loan.insurance_fee + loan.other_charges)
    rate = txn.book_rate or Decimal("1")
    after = _eir_state(loan.id, txn.id, fees)
    before = _eir_state(loan.id, txn.id - 1, fees) if txn.id else (ZERO, ZERO)
    return tuple(q(to_base(a, rate) - to_base(b, rate)) for a, b in zip(after, before))


def _converted_movements(txn: Transaction) -> tuple[Decimal, Decimal, Decimal]:
    """The change each receivable account takes for `txn`, in the base currency.

    For a base-currency loan this is simply the component, signed. For a foreign
    one it is q(after x rate) - q(before x rate) at the booked rate, so the ledger's
    carrying value is always exactly the converted outstanding figure.
    """
    from .fx import to_base

    moves = (_movement(txn, _PRINCIPAL_SIGN, "principal_component"),
             _movement(txn, _PENALTY_SIGN, "amount" if txn.txn_type == TxnType.PENALTY
                       else "penalty_component"),
             _movement(txn, _CHARGE_SIGN, "amount" if txn.txn_type == TxnType.CHARGE_ADDED
                       else "charge_component"))
    rate = txn.book_rate or Decimal("1")
    if rate == 1:
        return moves
    after = _receivables_after(txn)
    before = tuple(q(a - m) for a, m in zip(after, moves))
    return tuple(q(to_base(a, rate) - to_base(b, rate)) for a, b in zip(after, before))


def _lines_for(txn: Transaction) -> list[tuple[str, Decimal, Decimal, str]]:
    """(account code, debit, credit, description) for one transaction.

    Every branch below must balance: total debits == total credits. Amounts are in
    the base currency; `spot` converts cash and income, `d_principal`, `d_penalty`
    and `d_charge` are the receivables' converted movements.
    """
    from .fx import to_base

    loan = txn.loan
    spot = txn.fx_rate or Decimal("1")
    amount = to_base(txn.amount, spot)
    interest = to_base(txn.interest_component, spot)
    d_principal, d_penalty, d_charge = _converted_movements(txn)
    # Under the effective interest method: the change in interest receivable and in
    # the deferred fees. Both zero when interest is recognised on collection.
    d_receivable, d_deferred = _eir_movements(txn)

    def dr(code, value, text):
        return (code, value, ZERO, text) if value >= 0 else (code, ZERO, -value, text)

    def cr(code, value, text):
        return (code, ZERO, value, text) if value >= 0 else (code, -value, ZERO, text)

    lines = []
    if txn.txn_type == TxnType.DISBURSEMENT:
        # Dr the receivable with the full principal; the borrower gets the principal
        # less the upfront fees, which fall straight to income.
        fees = q(loan.admin_fee + loan.insurance_fee + loan.other_charges)
        net = to_base(q(txn.principal_component - fees), spot)
        lines = [dr(CODES["loans_receivable"], d_principal, "Principal advanced"),
                 cr(CODES["bank"], net, "Net paid to the borrower")]
        if d_deferred != 0:
            # Effective interest: the fees are deferred, not income on the day.
            lines.append(cr(CODES["deferred_fees"], d_deferred, "Fees deferred over the loan"))
        elif fees > 0:
            lines.append(cr(CODES["fee_income"], q(d_principal - net), "Admin and credit-life fees"))

    elif txn.txn_type == TxnType.ACCRUAL:
        # Income at the effective rate: the contractual interest for the period
        # becomes a receivable, the rest unwinds the deferred fees. Interest already
        # collected ahead of the accrual was income when it arrived, so the
        # receivable grows by less and the income line by as much less.
        lines = [dr(CODES["interest_receivable"], d_receivable, "Interest accrued for the period"),
                 cr(CODES["deferred_fees"], d_deferred, "Deferred fees unwound"),
                 cr(CODES["interest_income"], q(d_receivable - d_deferred),
                    "Interest at the effective rate")]

    elif txn.txn_type == TxnType.PENALTY:
        lines = [dr(CODES["penalties_receivable"], d_penalty, "Late-payment penalty charged"),
                 cr(CODES["penalty_income"], d_penalty, "Late-payment penalty charged")]

    elif txn.txn_type == TxnType.REPAYMENT:
        lines = [dr(CODES["bank"], amount, "Repayment received")]
        if d_principal != 0:
            lines.append(cr(CODES["loans_receivable"], -d_principal, "Principal repaid"))
        if d_receivable != 0:
            lines.append(cr(CODES["interest_receivable"], -d_receivable, "Accrued interest settled"))
        if q(interest + d_receivable) > 0:
            lines.append(cr(CODES["interest_income"], q(interest + d_receivable),
                            "Interest collected" if d_receivable == 0
                            else "Interest collected ahead of accrual"))
        if d_penalty != 0:
            lines.append(cr(CODES["penalties_receivable"], -d_penalty, "Penalty collected"))
        if d_charge != 0:
            lines.append(cr(CODES["charges_receivable"], -d_charge, "Charge collected"))

    elif txn.txn_type == TxnType.REVERSAL:
        # The exact mirror of the repayment being undone, at the rates it was
        # posted at (copied onto the reversal by repayments.reverse_transaction).
        lines = [cr(CODES["bank"], amount, "Repayment reversed")]
        if d_principal != 0:
            lines.append(dr(CODES["loans_receivable"], d_principal, "Principal restored"))
        if d_receivable != 0:
            lines.append(dr(CODES["interest_receivable"], d_receivable, "Accrued interest restored"))
        if q(interest - d_receivable) > 0:
            lines.append(dr(CODES["interest_income"], q(interest - d_receivable),
                            "Interest income reversed"))
        if d_penalty != 0:
            lines.append(dr(CODES["penalties_receivable"], d_penalty, "Penalty restored"))
        if d_charge != 0:
            lines.append(dr(CODES["charges_receivable"], d_charge, "Charge restored"))

    elif txn.txn_type == TxnType.WAIVER:
        # Waiving a penalty reverses income already recognised. Waiving interest
        # (the early-settlement rebate) touches nothing, because interest is only
        # recognised when it is collected.
        if d_penalty != 0:
            lines = [dr(CODES["penalty_income"], -d_penalty, "Penalty waived"),
                     cr(CODES["penalties_receivable"], -d_penalty, "Penalty waived")]

    elif txn.txn_type == TxnType.WRITE_OFF:
        # Only recognised balances leave the ledger: principal, penalties and
        # charges. Unearned interest was never income, so it is not an expense now.
        # Under the effective interest method the accrued interest goes with them,
        # and the fees still deferred come off the loss.
        recognised = q(-(d_principal + d_penalty + d_charge + d_receivable) + d_deferred)
        if recognised > 0:
            lines = [dr(CODES["write_off"], recognised, "Balance written off")]
            if d_principal != 0:
                lines.append(cr(CODES["loans_receivable"], -d_principal, "Principal written off"))
            if d_receivable != 0:
                lines.append(cr(CODES["interest_receivable"], -d_receivable,
                                "Accrued interest written off"))
            if d_deferred != 0:
                lines.append(cr(CODES["deferred_fees"], d_deferred, "Deferred fees released"))
            if d_penalty != 0:
                lines.append(cr(CODES["penalties_receivable"], -d_penalty, "Penalties written off"))
            if d_charge != 0:
                lines.append(cr(CODES["charges_receivable"], -d_charge, "Charges written off"))

    elif txn.txn_type == TxnType.CHARGE:
        # Raised and settled at the counter, unlike the fees netted off an advance.
        lines = [dr(CODES["bank"], amount, "Charge collected"),
                 cr(CODES["fee_income"], amount, "Charge income")]

    elif txn.txn_type == TxnType.CAPITALISATION:
        # Rescheduling rolls overdue interest, penalties and charges into a new
        # principal. The receivable grows by exactly what the other three shed.
        #
        # Capitalised interest IS recognised here, which is the one deliberate
        # exception to "interest is recognised when collected": once capitalised
        # it stops being interest and becomes principal the borrower owes, and
        # leaving it unrecognised would put an asset on 1100 with nothing on the
        # other side.
        lines = [dr(CODES["loans_receivable"], d_principal, "Capitalised into a new principal")]
        capitalised_interest = q(d_principal + d_penalty + d_charge)
        if d_receivable != 0:
            # Accrued already: it moves from the receivable, not from income.
            lines.append(cr(CODES["interest_receivable"], -d_receivable,
                            "Accrued interest capitalised"))
            capitalised_interest = q(capitalised_interest + d_receivable)
        if d_deferred != 0:
            lines.append(cr(CODES["deferred_fees"], d_deferred, "Deferred fees released"))
            capitalised_interest = q(capitalised_interest - d_deferred)
        if capitalised_interest > 0:
            lines.append(cr(CODES["interest_income"], capitalised_interest,
                            "Overdue interest capitalised"))
        if d_penalty != 0:
            lines.append(cr(CODES["penalties_receivable"], -d_penalty, "Penalties capitalised"))
        if d_charge != 0:
            lines.append(cr(CODES["charges_receivable"], -d_charge, "Charges capitalised"))

    elif txn.txn_type == TxnType.CHARGE_ADDED:
        # Added to the loan balance: a receivable now, cash when the borrower pays.
        lines = [dr(CODES["charges_receivable"], d_charge, "Charge added to the balance"),
                 cr(CODES["fee_income"], d_charge, "Charge income")]

    elif txn.txn_type == TxnType.RECOVERY:
        lines = [dr(CODES["bank"], amount, "Recovery received"),
                 cr(CODES["recovery_income"], amount, "Recovery on a written-off loan")]

    elif txn.txn_type == TxnType.OPENING_BALANCE:
        # A loan brought over from another system: the receivables arrive, no cash
        # moves. The other side is 3900 Opening balances, which an accountant clears
        # against retained earnings once the migrated book is agreed. Interest is
        # not brought over, because it is recognised when collected.
        lines = [dr(CODES["loans_receivable"], d_principal, "Principal brought forward")]
        if d_penalty != 0:
            lines.append(dr(CODES["penalties_receivable"], d_penalty, "Penalties brought forward"))
        lines.append(cr(OPENING_BALANCES, q(d_principal + d_penalty), "Opening balance"))

    # TxnType.FEE is informational: the fee is already inside the disbursement entry.
    lines = [(c, d, cr_, t) for c, d, cr_, t in lines if d > 0 or cr_ > 0]
    if not lines:
        return []

    # On a foreign-currency loan the cash came in at today's rate and the
    # receivable was carried at the booked one; what is left between them is a
    # realised exchange difference. On a base-currency loan the rates are 1 and
    # this is always zero.
    gap = q(sum((d - c for _, d, c, _ in lines), ZERO))
    if gap > 0:
        lines.append((CODES["fx_differences"], ZERO, gap, "Realised exchange gain"))
    elif gap < 0:
        lines.append((CODES["fx_differences"], -gap, ZERO, "Realised exchange loss"))
    return lines


def would_post(txn: Transaction) -> bool:
    """Whether this transaction raises a journal entry at all.

    Several types legitimately raise none: a FEE is informational because the fee
    is already inside the disbursement entry, an interest waiver touches nothing
    because interest is recognised on collection, and a write-off or capitalisation
    of nothing has nothing to post. Anything that wants to audit "every posting has
    an entry" must ask here rather than restate those rules, or the two drift.
    """
    return any(d > 0 or c > 0 for _, d, c, _ in _lines_for(txn))


@db_transaction.atomic
def post_transaction(txn: Transaction) -> JournalEntry | None:
    """Raise the journal entry for a loan transaction. Idempotent per transaction."""
    existing = JournalEntry.objects.filter(transaction=txn).first()
    if existing:
        return existing

    return _raise_entry(
        _lines_for(txn),
        entry_date=txn.txn_date,
        narration=txn.narration or f"{txn.get_txn_type_display()} on {txn.loan.loan_no}",
        source=txn.txn_type,
        what=f"transaction {txn.id}",
        loan=txn.loan, branch_id=txn.loan.branch_id, posted_by=txn.posted_by,
        transaction=txn,
    )


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


def _raise_entry(lines, *, entry_date, narration: str, source: str, what: str,
                 loan=None, branch_id=None, posted_by=None, **fk) -> JournalEntry | None:
    """The mechanics every posting source shares.

    Drops zero lines, refuses an entry whose accounts are missing or whose debits
    do not equal its credits, then writes the entry and its lines. Each source
    supplies only its own posting rules and which FK points back at it.

    Returns None rather than raising, because these run inside a post_save hook: a
    source with nothing to post (an informational fee, a waiver of interest) is
    normal, and a chart of accounts that does not exist yet is recoverable through
    backfill. `post_manual_entry` is the strict counterpart for callers that would
    rather fail loudly.
    """
    rows = [(c, q(d), q(cr), t) for c, d, cr, t in lines if q(d) > 0 or q(cr) > 0]
    if not rows:
        return None

    acc = _accounts_by_code([code for code, *_ in rows])
    missing = sorted({code for code, *_ in rows if code not in acc})
    if missing:
        log.warning("Ledger accounts %s are missing; skipping the entry for %s", missing, what)
        return None

    debits = sum((d for _, d, _, _ in rows), ZERO)
    credits = sum((c for _, _, c, _ in rows), ZERO)
    if debits != credits:
        # Refusing a lopsided entry is the whole point of double entry.
        log.error("Refusing an unbalanced entry for %s: Dr %s vs Cr %s", what, debits, credits)
        return None

    entry = JournalEntry.objects.create(
        entry_no=_next_entry_no(), entry_date=entry_date, narration=narration, source=source,
        loan=loan, branch_id=branch_id, posted_by=posted_by, **fk,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=acc[code], debit=d, credit=c, description=text)
        for code, d, c, text in rows
    ])
    return entry


def _signed(code: str, value: Decimal, text: str, *, debit: bool):
    """One line on its natural side, or on the other when `value` is negative."""
    if (value >= 0) == debit:
        return (code, abs(value), ZERO, text)
    return (code, ZERO, abs(value), text)


def _with_fx_difference(lines) -> list[tuple[str, Decimal, Decimal, str]]:
    """Drop zero lines and book what a foreign-currency entry leaves between its
    legs to 4800: the liability moved at the booked rate, the cash or the income
    and expense at the day's. Always nothing in the base currency."""
    lines = [(c, q(d), q(cr), t) for c, d, cr, t in lines if q(d) > 0 or q(cr) > 0]
    if not lines:
        return []
    gap = q(sum((d - c for _, d, c, _ in lines), ZERO))
    if gap > 0:
        lines.append((CODES["fx_differences"], ZERO, gap, "Realised exchange gain"))
    elif gap < 0:
        lines.append((CODES["fx_differences"], -gap, ZERO, "Realised exchange loss"))
    return lines


def _savings_lines(stxn) -> list[tuple[str, Decimal, Decimal, str]]:
    """Members' savings are the institution's liability, not its income.

    The 2000 leg is the change in the balance's base-currency value at the
    account's booked rate, from the balance the movement left; the other leg -
    the cash, the interest expense, the fee income - is the amount at the day's
    spot rate; a reversal puts that leg back at the rate it went in at, which the
    reversal carries. In the base currency both rates are 1 and the two legs are
    the amount; in another, whatever lies between goes to 4800.
    """
    from ..models import SavingsTxnType
    from .fx import ONE, carried_change, to_base

    kind = stxn.txn_type
    if kind == SavingsTxnType.REVERSAL and stxn.reversal_of_id:
        kind = stxn.reversal_of.txn_type
        mirror = True
    else:
        mirror = False

    # (the other leg's account, whether it is a debit, how the balance moves, and
    # the text of each leg)
    rules = {
        SavingsTxnType.DEPOSIT: (CODES["bank"], True, 1,
                                 "Savings deposit", "Owed to the member"),
        SavingsTxnType.WITHDRAWAL: (CODES["bank"], False, -1,
                                    "Savings withdrawal", "Paid out to the member"),
        SavingsTxnType.INTEREST: (CODES["savings_interest"], True, 1,
                                  "Interest credited to savings", "Owed to the member"),
        SavingsTxnType.FEE: (CODES["fee_income"], False, -1,
                             "Savings account fee", "Account fee taken from the balance"),
    }
    if kind not in rules:
        return []
    code, other_is_debit, sign, text, owed_text = rules[kind]
    if mirror:
        other_is_debit, sign = not other_is_debit, -sign
        text, owed_text = f"Reversal: {text}", f"Reversal: {owed_text}"

    # Credit 2000 with the rise in what is owed; a fall comes out as a debit.
    after = stxn.balance_after
    owed = carried_change(after, q(after - sign * stxn.amount), stxn.book_rate or ONE)
    return _with_fx_difference([
        _signed(code, to_base(stxn.amount, stxn.fx_rate or ONE), text, debit=other_is_debit),
        _signed(CODES["client_funds"], owed, owed_text, debit=False),
    ])


@db_transaction.atomic
def post_savings_transaction(stxn) -> JournalEntry | None:
    """Raise the journal entry for a savings movement. Idempotent per transaction."""
    existing = JournalEntry.objects.filter(savings_transaction=stxn).first()
    if existing:
        return existing

    return _raise_entry(
        _savings_lines(stxn),
        entry_date=stxn.txn_date,
        narration=stxn.narration or f"{stxn.get_txn_type_display()} on {stxn.account.account_no}",
        source=f"savings_{stxn.txn_type}",
        what=f"savings transaction {stxn.id}",
        branch_id=stxn.account.branch_id, posted_by=stxn.posted_by,
        savings_transaction=stxn,
    )


# ---------------------------------------------------------------- funding
def _mirror(lines) -> list[tuple[str, Decimal, Decimal, str]]:
    """Swap every debit and credit, so a reversal undoes exactly what it reverses."""
    return [(code, credit, debit, f"Reversal: {text}")
            for code, debit, credit, text in lines]


def _facility_lines(ftxn) -> list[tuple[str, Decimal, Decimal, str]]:
    """Borrowing from a funder is a liability, and its interest is an expense.

    Unlike loan interest — recognised when collected, because there is no interest
    receivable — borrowing interest is accrued monthly to 2110. The asymmetry is
    deliberate: an unrecognised asset is prudent, an unrecognised liability is not.
    """
    from ..models import FacilityTxnType
    from .fx import ONE, carried_change, to_base

    kind = ftxn.txn_type
    mirror = False
    if kind == FacilityTxnType.REVERSAL and ftxn.reversal_of_id:
        kind = ftxn.reversal_of.txn_type
        mirror = True

    # In the facility's currency: the cash, the interest expense and the fee at the
    # day's spot rate (a reversal carries the rate the original went in at), and
    # the change in 2100 or 2110 at the facility's booked rate, from the balance
    # the movement left, so the two accounts telescope to q(balance x rate)
    # exactly. In the base currency both are the amount and 4800 never appears.
    amount = to_base(ftxn.amount, ftxn.fx_rate or ONE)
    if kind == FacilityTxnType.FEE:
        lines = [(CODES["facility_fees"], amount, ZERO, "Facility fee"),
                 (CODES["bank"], ZERO, amount, "Paid to the funder")]
        return _with_fx_difference(_mirror(lines) if mirror else lines)

    # (the other leg's account, whether it is a debit, its text; the liability,
    # the balance it is read from, how that balance moves, its text)
    rules = {
        FacilityTxnType.DRAWDOWN: (CODES["bank"], True, "Received from the funder",
                                   CODES["borrowings"], "principal_after", 1,
                                   "Drawn on the facility"),
        FacilityTxnType.REPAYMENT: (CODES["bank"], False, "Paid to the funder",
                                    CODES["borrowings"], "principal_after", -1,
                                    "Principal repaid to the funder"),
        FacilityTxnType.INTEREST_ACCRUAL: (CODES["borrowing_interest"], True,
                                           "Interest accrued on borrowings",
                                           CODES["accrued_interest"], "accrued_after", 1,
                                           "Owed to the funder"),
        # Never split between 2110 and 5300. funding.pay_interest refuses more than
        # has accrued and tells the caller to run the accrual first, so an interest
        # payment only ever settles a liability already on the books. Expensing the
        # unaccrued remainder here is what double-counts it when the month-end
        # accrual then posts the same period again. On a foreign facility what lies
        # between the booked rate and the day's is an exchange difference, not
        # interest.
        FacilityTxnType.INTEREST_PAYMENT: (CODES["bank"], False, "Interest paid to the funder",
                                           CODES["accrued_interest"], "accrued_after", -1,
                                           "Accrued interest settled"),
    }
    if kind not in rules:
        return []
    code, other_is_debit, text, liability, field, sign, owed_text = rules[kind]
    if mirror:
        other_is_debit, sign = not other_is_debit, -sign
        text, owed_text = f"Reversal: {text}", f"Reversal: {owed_text}"

    # Credit the liability with the rise in what is owed; a fall is a debit.
    after = getattr(ftxn, field)
    owed = carried_change(after, q(after - sign * ftxn.amount), ftxn.book_rate or ONE)
    return _with_fx_difference([
        _signed(code, amount, text, debit=other_is_debit),
        _signed(liability, owed, owed_text, debit=False),
    ])


def _capital_lines(ctxn) -> list[tuple[str, Decimal, Decimal, str]]:
    """Shareholders' money is equity, never income."""
    from ..models import CapitalTxnType

    amount = q(ctxn.amount)
    kind = ctxn.txn_type
    mirror = False
    if kind == CapitalTxnType.REVERSAL and ctxn.reversal_of_id:
        kind = ctxn.reversal_of.txn_type
        mirror = True

    if kind == CapitalTxnType.INJECTION:
        lines = [(CODES["bank"], amount, ZERO, "Capital received"),
                 (CODES["share_capital"], ZERO, amount, "Capital contributed")]
    elif kind == CapitalTxnType.RETURN_OF_CAPITAL:
        lines = [(CODES["share_capital"], amount, ZERO, "Capital returned"),
                 (CODES["bank"], ZERO, amount, "Paid to the shareholder")]
    elif kind == CapitalTxnType.DIVIDEND:
        # 3200 is typed EQUITY and carries a debit balance, so a trial balance
        # reports it negative and it nets off contributed capital by itself.
        lines = [(CODES["distributions"], amount, ZERO, "Dividend declared and paid"),
                 (CODES["bank"], ZERO, amount, "Paid to the shareholder")]
    else:
        return []

    return _mirror(lines) if mirror else lines


@db_transaction.atomic
def post_facility_transaction(ftxn) -> JournalEntry | None:
    """Raise the journal entry for a facility movement. Idempotent per transaction."""
    existing = JournalEntry.objects.filter(facility_transaction=ftxn).first()
    if existing:
        return existing

    return _raise_entry(
        _facility_lines(ftxn),
        entry_date=ftxn.txn_date,
        narration=(ftxn.narration
                   or f"{ftxn.get_txn_type_display()} on {ftxn.facility.facility_no}"),
        source=f"facility_{ftxn.txn_type}",
        what=f"facility transaction {ftxn.id}",
        branch_id=ftxn.facility.branch_id, posted_by=ftxn.posted_by,
        facility_transaction=ftxn,
    )


@db_transaction.atomic
def post_capital_transaction(ctxn) -> JournalEntry | None:
    """Raise the journal entry for a capital movement. Idempotent per transaction."""
    existing = JournalEntry.objects.filter(capital_transaction=ctxn).first()
    if existing:
        return existing

    return _raise_entry(
        _capital_lines(ctxn),
        entry_date=ctxn.txn_date,
        narration=ctxn.narration or f"{ctxn.get_txn_type_display()} — {ctxn.contributor}",
        source=f"capital_{ctxn.txn_type}",
        what=f"capital transaction {ctxn.id}",
        branch_id=ctxn.branch_id, posted_by=ctxn.posted_by,
        capital_transaction=ctxn,
    )


def backfill(limit: int | None = None) -> dict:
    """Post ledger entries for transactions that do not have one.

    Used after the chart of accounts is set up on a book that already has
    history, and by the seed command.

    Runs inside allow_closed_posting: every transaction it touches already exists,
    so its date is already history, and refusing to account for it because its
    month is closed would leave the ledger permanently short with no way to fix it.
    """
    from . import periods

    with periods.allow_closed_posting("ledger backfill"):
        return _backfill(limit)


def _sources():
    """Every model that raises a journal entry, and how to sweep it.

    A table rather than one block per source, because each new posting source used
    to mean another near-identical paragraph here — and a source someone forgot to
    add would leave its account silently short after a Rebuild.
    """
    from ..models import CapitalTransaction, FacilityTransaction, SavingsTransaction

    return [
        ("loan", Transaction.objects.select_related("loan"), post_transaction),
        ("savings", SavingsTransaction.objects.select_related("account", "reversal_of"),
         post_savings_transaction),
        ("facility", FacilityTransaction.objects.select_related("facility", "reversal_of"),
         post_facility_transaction),
        ("capital", CapitalTransaction.objects.select_related("reversal_of"),
         post_capital_transaction),
    ]


def _backfill(limit: int | None = None) -> dict:
    ensure_chart_of_accounts()

    posted = skipped = 0
    per_source = {}
    for name, queryset, post in _sources():
        pending = queryset.filter(journal_entry__isnull=True).order_by("id")
        if limit:
            pending = pending[:limit]
        raised = 0
        for row in pending:
            if post(row):
                raised += 1
                posted += 1
            else:
                skipped += 1
        per_source[name] = raised

    # Provision entries stand behind no transaction of any kind, so the sweeps
    # above cannot find them. Without this, "Rebuild" after a JournalEntry wipe
    # would leave account 1900 at zero while the loans still carry a provision, and
    # that reconciliation identity would be unrecoverable.
    from .provisioning import repost_runs

    reposted = repost_runs()
    posted += reposted["reposted"]

    # Manual journals likewise stand behind no transaction.
    from .journals import repost_journals

    journals = repost_journals()
    posted += journals["reposted"]

    # And so do the differences found when a till is verified.
    from .tills import repost_variances

    tills = repost_variances()
    posted += tills["reposted"]

    # And the restatement of foreign-currency loans at a closing rate.
    from .fx import repost_runs as repost_revaluations

    revaluations = repost_revaluations()
    posted += revaluations["reposted"]

    return {"posted": posted, "skipped": skipped, "by_source": per_source,
            "provision_runs_reposted": reposted["reposted"],
            "manual_journals_reposted": journals["reposted"],
            "till_variances_reposted": tills["reposted"],
            "revaluation_runs_reposted": revaluations["reposted"]}


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


def balance_sheet(as_of=None, branch_id=None) -> dict:
    """Assets, liabilities and equity as at one date.

    Retained earnings is DERIVED as income less expense since inception, plus
    anything a manual posting has put on 3000. Nothing in this system posts a
    year-end closing entry to 3000, so without the derivation the surplus would
    simply be missing from equity and the sheet would not add up.

    `balanced` is therefore close to a tautology: given a set of entries where every
    debit has a credit, assets minus liabilities always equals the derived equity.
    It is reported because a non-zero difference means something has corrupted the
    journal, not because agreement is an achievement. The checks that can genuinely
    fail are the sub-ledger identities in `reconciliation()`, and those are what a
    board pack should be read against.
    """
    from datetime import date as _date

    as_of = as_of or _date.today()
    balance = trial_balance(None, as_of, branch_id)
    rows = balance["rows"]

    assets = [r for r in rows if r["type"] == AccountType.ASSET]
    liabilities = [r for r in rows if r["type"] == AccountType.LIABILITY]
    # 3000 is excluded from the posted equity rows and folded into the derived
    # figure instead, so a manual posting to it can never be counted twice.
    posted_equity = [r for r in rows
                     if r["type"] == AccountType.EQUITY and r["code"] != RETAINED_EARNINGS]
    posted_retained = next((r["balance"] for r in rows if r["code"] == RETAINED_EARNINGS), ZERO)

    income = q(sum((r["balance"] for r in rows if r["type"] == AccountType.INCOME), ZERO))
    expense = q(sum((r["balance"] for r in rows if r["type"] == AccountType.EXPENSE), ZERO))
    retained = q(income - expense + posted_retained)

    # trial_balance drops accounts with no movement, so in the normal case there is
    # no 3000 row to take a name from.
    name = (LedgerAccount.objects.filter(code=RETAINED_EARNINGS)
            .values_list("name", flat=True).first() or "Retained earnings")
    retained_row = {
        "code": RETAINED_EARNINGS, "name": name, "type": AccountType.EQUITY,
        # Presented on its natural side, so the keys match every other row: a
        # csv_response builds its DictWriter from the first row's keys and an extra
        # or missing key on a later row raises.
        "debit": ZERO if retained >= 0 else q(-retained),
        "credit": retained if retained >= 0 else ZERO,
        "balance": retained, "side": "credit",
    }
    equity = posted_equity + [retained_row]

    total_assets = q(sum((r["balance"] for r in assets), ZERO))
    total_liabilities = q(sum((r["balance"] for r in liabilities), ZERO))
    total_equity = q(sum((r["balance"] for r in equity), ZERO))

    return {
        "as_of": as_of,
        "assets": assets,
        "liabilities": liabilities,
        "equity": equity,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "total_liabilities_and_equity": q(total_liabilities + total_equity),
        "retained_earnings": retained,
        "income_to_date": income,
        "expense_to_date": expense,
        "difference": q(total_assets - total_liabilities - total_equity),
        "balanced": q(total_assets - total_liabilities - total_equity) == ZERO,
        "branch_id": branch_id,
    }


def reconciliation(as_of=None) -> dict:
    """Every account whose balance is claimed to equal a sub-ledger, checked.

    These are the checks that can actually fail, unlike the balance sheet's own
    `balanced` flag. Each row names the account, what the ledger says, what the
    sub-ledger says, and the difference. Whole-book only: facility and capital
    entries carry no branch, so a branch slice of any of these is meaningless.
    """
    from django.db.models import Sum

    from ..models import (
        CapitalTransaction,
        CapitalTxnType,
        FundingFacility,
        Loan,
        LoanStatus,
        SavingsAccount,
    )

    def total(queryset, field):
        return queryset.aggregate(v=Sum(field))["v"] or ZERO

    def converted(queryset, field):
        # Each loan, savings account or facility at its booked rate, rounded per
        # row: exactly what the ledger carries (see services/fx.py), so a foreign
        # book reconciles to the cent.
        from .fx import to_base

        return q(sum((to_base(value, rate) for value, rate
                      in queryset.values_list(field, "fx_rate")), ZERO))

    by_code = {row["code"]: row["balance"] for row in trial_balance(None, as_of)["rows"]}
    active = Loan.objects.filter(status=LoanStatus.ACTIVE)
    facilities = FundingFacility.objects.all()
    # A reversed movement and the reversal itself both stay on the register; the
    # pair nets to zero in the ledger, so the sub-ledger total must exclude both.
    capital = CapitalTransaction.objects.filter(reversed=False).exclude(
        txn_type=CapitalTxnType.REVERSAL)

    claims = [
        ("1100", "Loans receivable", converted(active, "principal_outstanding"),
         "principal outstanding on active loans, at the booked rates"),
        ("1300", "Penalties receivable", converted(active, "penalties_outstanding"),
         "penalties outstanding on active loans, at the booked rates"),
        ("1400", "Charges receivable", converted(active, "charges_outstanding"),
         "charges outstanding on active loans, at the booked rates"),
        ("1900", "Provision for credit losses", -total(Loan.objects.all(), "provision_held"),
         "provision held across every loan"),
        ("2000", "Client funds payable", converted(SavingsAccount.objects.all(), "balance"),
         "savings balances, at the booked rates"),
        ("2100", "Funder borrowings", converted(facilities, "principal_outstanding"),
         "principal outstanding on funding facilities, at the booked rates"),
        ("2110", "Accrued interest on borrowings", converted(facilities, "interest_accrued"),
         "interest accrued and unpaid on funding facilities, at the booked rates"),
        ("3100", "Share capital",
         q(total(capital.filter(txn_type=CapitalTxnType.INJECTION), "amount")
           - total(capital.filter(txn_type=CapitalTxnType.RETURN_OF_CAPITAL), "amount")),
         "capital injected less capital returned"),
        ("3200", "Distributions to shareholders",
         -total(capital.filter(txn_type=CapitalTxnType.DIVIDEND), "amount"),
         "dividends paid"),
    ]

    rows = []
    for code, name, book, what in claims:
        ledger_value = q(by_code.get(code, ZERO))
        book = q(book)
        rows.append({
            "code": code, "name": name, "ledger": ledger_value, "book": book,
            "difference": q(ledger_value - book), "agrees": ledger_value == book,
            "sub_ledger": what,
        })

    # Two more accounts stand against the book under the effective interest method,
    # and read zero against zero otherwise; shown either way, so every control
    # account the journals refuse is on this page.
    from .eir import book_positions, is_effective
    from .fx import to_base

    positions = list(book_positions().values()) if is_effective() else []
    for code, name, book, what in [
        ("1200", "Interest receivable",
         q(sum((to_base(r, rate) for r, _d, rate in positions), ZERO)),
         "interest accrued and not yet collected on active loans"),
        ("1150", "Deferred loan fees",
         -q(sum((to_base(d, rate) for _r, d, rate in positions), ZERO)),
         "fees deducted at disbursement and not yet taken to income"),
    ]:
        ledger_value = q(by_code.get(code, ZERO))
        rows.append({
            "code": code, "name": name, "ledger": ledger_value, "book": q(book),
            "difference": q(ledger_value - q(book)), "agrees": ledger_value == q(book),
            "sub_ledger": what,
        })
    rows.sort(key=lambda r: r["code"])

    sheet = balance_sheet(as_of)
    return {
        "as_of": sheet["as_of"],
        "rows": rows,
        "agrees": all(r["agrees"] for r in rows),
        "breaks": [r for r in rows if not r["agrees"]],
        "trial_balance_balanced": trial_balance(None, as_of)["balanced"],
        "balance_sheet_balanced": sheet["balanced"],
    }
