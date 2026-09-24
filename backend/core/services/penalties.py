"""Daily penalty accrual on overdue instalments.

Penalty = overdue instalment balance (principal + interest unpaid) x product daily rate x days,
starting after the product's grace period. Idempotent: each instalment records the last date
penalties were accrued to, so running the job twice on the same day charges nothing extra."""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Prefetch

from ..models import Instalment, Loan, LoanStatus, Transaction, TxnType
from .amortisation import q
from .loans import refresh_balances, sched

ZERO = Decimal("0")


def active_loans_for_accrual():
    return (Loan.objects
            .filter(status=LoanStatus.ACTIVE)
            .select_related("product", "borrower")
            .prefetch_related(Prefetch("instalments", queryset=Instalment.objects.order_by("number"))))


def accrue_penalties(as_of: date | None = None, loan: Loan | None = None) -> dict:
    as_of = as_of or date.today()
    loans = [loan] if loan is not None else list(active_loans_for_accrual())
    total = ZERO
    touched = 0
    for ln in loans:
        product = ln.product
        rate = Decimal(product.penalty_rate_pct_per_day) / 100
        loan_pen = ZERO
        for ins in sched(ln):
            unpaid = ((ins.principal_due - ins.principal_paid)
                      + (ins.interest_due - ins.interest_paid))
            if unpaid <= 0:
                continue
            start = ins.last_penalty_date or (ins.due_date + timedelta(days=product.grace_days))
            if as_of <= start:
                continue
            days = (as_of - start).days
            pen = q(unpaid * rate * days)
            if pen > 0:
                ins.penalty_due += pen
                loan_pen += pen
            ins.last_penalty_date = as_of
        if loan_pen > 0:
            Transaction.objects.create(
                loan=ln, txn_type=TxnType.PENALTY, txn_date=as_of, amount=loan_pen,
                penalty_component=loan_pen,
                narration=f"Late payment penalty accrued to {as_of.isoformat()}",
            )
            total += loan_pen
            touched += 1
        refresh_balances(ln, as_of)
    return {"as_of": as_of.isoformat(), "loans_penalised": touched, "total_penalties": q(total)}
