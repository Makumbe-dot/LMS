"""Loan and savings statements: the lines, the running balance, the summary.

The same data feeds the statement on screen, the Excel download and the PDF, so
the three cannot disagree.

**The loan balance.** Interest is recognised as each instalment falls due and is
collected (see services/ledger.py), so the running balance on a loan statement
is what the borrower owes that has been charged: principal, plus penalties and
charges added to the loan. A repayment reduces it by the principal, penalty and
charge it paid; the interest it paid has its own column. The closing balance of
an active loan therefore equals principal + penalties + charges outstanding, and
the summary adds the interest still to fall due to give the total outstanding.

The previous statement took the whole repayment, interest included, off a balance
that never had the interest on it, so its closing figure matched nothing the
borrower owed; and it ignored opening balances and reschedules altogether.

Every line is shown, reversed ones included, with the reversal beside it, so the
statement accounts for every receipt the borrower may hold.
"""
from datetime import date
from decimal import Decimal

from ..models import (
    Loan,
    SavingsAccount,
    SavingsTxnType,
    TxnType,
)
from .amortisation import q
from .loans import TERM_UNITS, sched

ZERO = Decimal("0")

LOAN_LABELS = {
    TxnType.DISBURSEMENT: "Loan advanced",
    TxnType.FEE: "Fees deducted from the advance",
    TxnType.REPAYMENT: "Repayment",
    TxnType.PENALTY: "Late-payment penalty",
    TxnType.CHARGE: "Charge paid at the counter",
    TxnType.CHARGE_ADDED: "Charge added to the loan",
    TxnType.CAPITALISATION: "Arrears capitalised on reschedule",
    TxnType.WAIVER: "Waiver",
    TxnType.WRITE_OFF: "Written off",
    TxnType.RECOVERY: "Recovered after write-off",
    TxnType.REVERSAL: "Repayment reversed",
    TxnType.OPENING_BALANCE: "Balance brought forward",
    TxnType.ACCRUAL: "Interest accrued (ledger only)",
}


def _loan_effect(t) -> tuple[Decimal, Decimal, Decimal, str]:
    """(charged, paid, change to the balance, label) for one loan transaction."""
    p, i = t.principal_component, t.interest_component
    pen, chg = t.penalty_component, t.charge_component
    label = LOAN_LABELS.get(t.txn_type, t.get_txn_type_display())
    kind = t.txn_type
    if kind == TxnType.DISBURSEMENT:
        return p, ZERO, p, label
    if kind in (TxnType.PENALTY, TxnType.CHARGE_ADDED):
        return t.amount, ZERO, t.amount, label
    if kind == TxnType.REPAYMENT:
        return ZERO, t.amount, -(p + pen + chg), label + (" (reversed below)" if t.reversed else "")
    if kind == TxnType.REVERSAL:
        return t.amount, ZERO, p + pen + chg, label
    if kind == TxnType.OPENING_BALANCE:
        return p + pen, ZERO, p + pen, label
    if kind == TxnType.CAPITALISATION:
        # The receivable grows by the overdue interest rolled into the new principal;
        # the penalties and charges capitalised were already on the balance.
        grows = p - pen - chg
        return grows, ZERO, grows, label
    if kind == TxnType.WAIVER:
        if pen > 0:
            return ZERO, pen, -pen, "Penalties waived"
        return ZERO, ZERO, ZERO, f"Unearned interest of {q(i)} rebated (never charged)"
    if kind == TxnType.WRITE_OFF:
        recognised = p + pen + chg
        return ZERO, recognised, -recognised, label
    if kind == TxnType.CHARGE:
        return t.amount, t.amount, ZERO, label
    if kind == TxnType.RECOVERY:
        return ZERO, t.amount, ZERO, label
    if kind == TxnType.FEE:
        return ZERO, ZERO, ZERO, f"{label} ({q(t.amount)}; the full principal is repayable)"
    return ZERO, ZERO, ZERO, label


def loan_statement(loan: Loan, start: date | None = None, end: date | None = None) -> dict:
    """A loan's statement, optionally for a window, with an opening balance for it."""
    end = end or date.today()
    opening = ZERO
    lines = []
    for t in loan.transactions.order_by("txn_date", "id"):
        if t.txn_type == TxnType.ACCRUAL:
            # Moves nothing the borrower owes or has paid; it is the ledger's affair.
            continue
        charged, paid, change, label = _loan_effect(t)
        if start and t.txn_date < start:
            opening += change
            continue
        if t.txn_date > end:
            continue
        lines.append({
            "date": t.txn_date, "type": t.txn_type, "description": label,
            "narration": t.narration, "reference": t.reference,
            "debit": q(charged), "credit": q(paid),
            "principal": q(t.principal_component), "interest": q(t.interest_component),
            "penalty_and_charges": q(t.penalty_component + t.charge_component),
            "balance": ZERO,  # filled below, once the opening figure is known
            "reversed": t.reversed,
            "_change": change,
        })
    running = opening
    for line in lines:
        running += line.pop("_change")
        line["balance"] = q(running)

    amount, days = loan_arrears(loan, end)
    schedule = sched(loan)
    next_due = next((i for i in schedule if i.balance > 0 and i.due_date >= end), None)
    borrower = loan.borrower
    return {
        "loan_no": loan.loan_no, "loan_id": loan.id, "external_ref": loan.external_ref,
        "borrower": borrower.full_name, "borrower_no": borrower.borrower_no,
        "national_id": borrower.national_id, "phone": borrower.phone,
        "address": borrower.address, "employer": borrower.employer,
        "product": loan.product.name, "principal": loan.principal,
        "rate_pct": loan.interest_rate_pct, "rate_method": loan.get_rate_method_display(),
        "repayment_frequency": loan.repayment_frequency,
        "term": loan.term_months,
        "term_unit": TERM_UNITS.get(loan.repayment_frequency, "instalments"),
        "instalment_amount": loan.instalment_amount, "apr_pct": loan.apr_pct,
        "disbursement_date": loan.disbursement_date, "maturity_date": loan.maturity_date,
        "status": loan.status, "branch": loan.branch.name if loan.branch_id else None,
        "principal_outstanding": loan.principal_outstanding,
        "interest_outstanding": loan.interest_outstanding,
        "penalties_outstanding": loan.penalties_outstanding,
        "charges_outstanding": loan.charges_outstanding,
        "total_outstanding": loan.total_outstanding, "total_paid": loan.total_paid,
        # How far through the loan the borrower is, for the statement's progress line.
        "total_repayable": q(loan.principal + loan.total_interest),
        "instalments_total": len(schedule),
        "instalments_paid": sum(1 for i in schedule if i.total_due > 0 and i.balance <= 0),
        "arrears_amount": amount, "days_in_arrears": days,
        "next_due_date": next_due.due_date if next_due else None,
        "next_due_amount": q(next_due.balance) if next_due else None,
        "period_start": start, "period_end": end,
        "opening_balance": q(opening),
        "closing_balance": q(running),
        "total_charged": q(sum((l["debit"] for l in lines), ZERO)),
        "total_paid_in_period": q(sum((l["credit"] for l in lines), ZERO)),
        "lines": lines,
    }


def loan_arrears(loan: Loan, as_of: date) -> tuple[Decimal, int]:
    amount = days = 0
    for ins in sched(loan):
        if ins.due_date < as_of and ins.balance > 0:
            amount += ins.balance
            days = max(days, (as_of - ins.due_date).days)
    return q(Decimal(amount)), days


# ---------------------------------------------------------------- savings
SAVINGS_LABELS = {
    SavingsTxnType.DEPOSIT: "Deposit",
    SavingsTxnType.WITHDRAWAL: "Withdrawal",
    SavingsTxnType.INTEREST: "Interest credited",
    SavingsTxnType.FEE: "Account fee",
    SavingsTxnType.REVERSAL: "Reversal",
}
_MONEY_IN = (SavingsTxnType.DEPOSIT, SavingsTxnType.INTEREST)


def _savings_signed(stxn) -> Decimal:
    if stxn.txn_type == SavingsTxnType.REVERSAL and stxn.reversal_of_id:
        return -_savings_signed(stxn.reversal_of)
    return stxn.amount if stxn.txn_type in _MONEY_IN else -stxn.amount


def savings_statement(account: SavingsAccount, start: date | None = None,
                      end: date | None = None) -> dict:
    """A savings account's statement: money in, money out, the balance after each."""
    end = end or date.today()
    opening = ZERO
    lines = []
    for s in account.transactions.select_related("reversal_of").order_by("txn_date", "id"):
        signed = _savings_signed(s)
        if start and s.txn_date < start:
            opening += signed
            continue
        if s.txn_date > end:
            continue
        label = SAVINGS_LABELS.get(s.txn_type, s.get_txn_type_display())
        if s.txn_type == SavingsTxnType.REVERSAL and s.reversal_of_id:
            label = f"Reversal of the {s.reversal_of.get_txn_type_display().lower()} of " \
                    f"{s.reversal_of.txn_date.isoformat()}"
        elif s.reversed:
            label += " (reversed below)"
        lines.append({
            "date": s.txn_date, "type": s.txn_type, "description": label,
            "narration": s.narration, "reference": s.reference,
            "money_in": q(signed) if signed > 0 else ZERO,
            "money_out": q(-signed) if signed < 0 else ZERO,
            "balance": ZERO, "_signed": signed,
        })
    running = opening
    for line in lines:
        running += line.pop("_signed")
        line["balance"] = q(running)

    borrower = account.borrower
    return {
        "account_no": account.account_no, "account_id": account.id,
        "borrower": borrower.full_name, "borrower_no": borrower.borrower_no,
        "national_id": borrower.national_id, "phone": borrower.phone,
        "address": borrower.address,
        "product": account.product.name,
        "interest_rate_pct_pa": account.product.interest_rate_pct_pa,
        "status": account.status, "opened_on": account.opened_on,
        "branch": account.branch.name if account.branch_id else None,
        "balance": account.balance, "available_balance": account.available_balance,
        "period_start": start, "period_end": end,
        "opening_balance": q(opening), "closing_balance": q(running),
        "total_in": q(sum((l["money_in"] for l in lines), ZERO)),
        "total_out": q(sum((l["money_out"] for l in lines), ZERO)),
        "lines": lines,
    }

