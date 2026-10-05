"""Interest by the effective interest method (IFRS 9), as an organisation setting.

The book recognises interest in one of two ways, chosen once on the Settings page:

  * **collected** (the default, and what the ledger has always done): interest is
    income when it is collected, and the fees deducted at disbursement are income
    on the day. Simple, prudent, and what a small lender's management accounts
    usually show.
  * **effective**: the fees are integral to the loan, so they are deferred at
    disbursement (Cr 1150) and the loan is carried at amortised cost. Each
    instalment period, income is the amortised cost times the **effective rate**
    (the per-period rate at which the instalments discount to the net amount
    advanced). Of that income, the contractual interest for the period becomes a
    receivable (Dr 1200 Interest receivable) and the rest unwinds the deferred
    fees (Dr 1150). Over the life of the loan the income is exactly the
    contractual interest plus the fees, spread at a constant yield.

The choice cannot be changed while any loan is running: half a book on one basis
and half on the other would reconcile to nothing.

Mechanics. `rows(loan)` works the effective-rate schedule out of the loan's own
instalments and fees, so it is the same every time it is asked. `accrue_interest`
is the month-end run: for every instalment that has fallen due and not yet been
accrued it raises one ACCRUAL transaction per loan (amount = income at the
effective rate, interest_component = the contractual interest), which the ledger
posts like any other transaction. A repayment then settles the receivable; one
that arrives before the accrual is recognised as collected, and the accrual that
follows recognises only what is left. When a loan closes, is settled early or is
rescheduled, the fees still deferred are released to income; a write-off takes
them against the loss. Loans brought over from another system accrue only the
instalments that fall due after the cut-over: the old system recognised the rest.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Prefetch

from ..exceptions import BusinessRuleError
from ..models import (
    Instalment,
    InterestMethod,
    Loan,
    LoanStatus,
    OrganisationSetting,
    Transaction,
    TxnType,
    User,
)
from . import periods
from .amortisation import q
from .loans import sched

ZERO = Decimal("0")


def method() -> str:
    return OrganisationSetting.load().interest_method


def is_effective() -> bool:
    return method() == InterestMethod.EFFECTIVE


def assert_can_change(new_method: str) -> None:
    """A change of basis needs an empty active book."""
    if new_method == method():
        return
    running = Loan.objects.filter(status=LoanStatus.ACTIVE).count()
    if running:
        raise BusinessRuleError(
            f"The interest method cannot change while {running} loan(s) are running: the "
            f"book would be half on one basis and half on the other. Wait until the active "
            f"book has run off, or agree a cut-over with the auditor and migrate the book.")


# ---------------------------------------------------------------- the schedule
def fees_to_spread(loan: Loan) -> Decimal:
    """The fees integral to the loan, spread over its life. Zero once the loan
    has been rescheduled: the old instrument is derecognised and its fees released."""
    if loan.transactions.filter(txn_type=TxnType.CAPITALISATION).exists():
        return ZERO
    return q(loan.admin_fee + loan.insurance_fee + loan.other_charges)


def effective_rate(net_advanced, instalments) -> Decimal:
    """The per-period rate r with net = sum(instalment_k / (1 + r) ** k).

    Bisection in Decimal; the present value falls as the rate rises so it cannot
    miss. Zero when nothing was advanced or the instalments sum to no more than it.
    """
    from decimal import localcontext

    net = Decimal(net_advanced)
    flows = [Decimal(x) for x in instalments]
    if net <= 0 or sum(flows) <= net:
        return ZERO
    with localcontext() as ctx:
        ctx.prec = 18

        def excess(rate):
            return sum((a / (1 + rate) ** k for k, a in enumerate(flows, start=1)), Decimal(0)) - net

        low, high = Decimal(0), Decimal(1)
        while excess(high) > 0:
            high *= 2
        for _ in range(60):
            mid = (low + high) / 2
            if excess(mid) > 0:
                low = mid
            else:
                high = mid
        return (low + high) / 2


def rows(loan: Loan) -> list[dict]:
    """Per instalment: the income at the effective rate, the contractual
    interest, and the fee unwind between them. Sums to contractual interest plus
    the fees exactly; the last row takes the rounding."""
    schedule = sched(loan)
    if not schedule:
        return []
    fees = fees_to_spread(loan)
    contractual = [q(i.principal_due + i.interest_due) for i in schedule]
    net = q(sum(contractual, ZERO) - sum((q(i.interest_due) for i in schedule), ZERO) - fees)
    out = []
    if fees == 0:
        for ins in schedule:
            out.append({"number": ins.number, "due_date": ins.due_date,
                        "eir_interest": q(ins.interest_due), "contractual": q(ins.interest_due),
                        "fee_unwind": ZERO})
        return out
    rate = effective_rate(net, contractual)
    carrying = net
    for ins in schedule[:-1]:
        income = q(carrying * rate)
        out.append({"number": ins.number, "due_date": ins.due_date, "eir_interest": income,
                    "contractual": q(ins.interest_due), "fee_unwind": q(income - ins.interest_due)})
        carrying = q(carrying + income - (ins.principal_due + ins.interest_due))
    last = schedule[-1]
    unwound = sum((r["fee_unwind"] for r in out), ZERO)
    final_unwind = q(fees - unwound)
    out.append({"number": last.number, "due_date": last.due_date,
                "eir_interest": q(last.interest_due + final_unwind),
                "contractual": q(last.interest_due), "fee_unwind": final_unwind})
    return out


# ---------------------------------------------------------------- the run
def _opening_date(loan: Loan) -> date | None:
    opening = loan.transactions.filter(txn_type=TxnType.OPENING_BALANCE).only("txn_date").first()
    return opening.txn_date if opening else None


def active_loans():
    return (Loan.objects.filter(status=LoanStatus.ACTIVE)
            .prefetch_related(Prefetch("instalments", queryset=Instalment.objects.order_by("number"))))


@db_transaction.atomic
def accrue_interest(as_of: date | None = None, loan: Loan | None = None,
                    user: User | None = None) -> dict:
    """Recognise the income of every instalment period that has ended by `as_of`.

    Idempotent: an instalment records the date it was accrued on, so a second run
    for the same month raises nothing.
    """
    as_of = as_of or date.today()
    if not is_effective():
        raise BusinessRuleError(
            "Interest is recognised when collected; there is nothing to accrue. Choose the "
            "effective interest method on the Settings page to accrue it instead.")
    periods.assert_open(as_of, "Interest accrual")
    loans = [loan] if loan is not None else list(active_loans())
    income_total = ZERO
    touched = 0
    for ln in loans:
        cutover = _opening_date(ln)
        plan = {r["number"]: r for r in rows(ln)}
        income = contractual = unwind = ZERO
        accrued = []
        for ins in sched(ln):
            if ins.accrued_on is not None or ins.due_date > as_of:
                continue
            if cutover and ins.due_date <= cutover:
                continue
            row = plan.get(ins.number)
            if row is None:
                continue
            income += row["eir_interest"]
            contractual += row["contractual"]
            unwind += row["fee_unwind"]
            ins.accrued_on = as_of
            accrued.append(ins)
        if not accrued:
            continue
        Transaction.objects.create(
            loan=ln, txn_type=TxnType.ACCRUAL, txn_date=as_of, amount=q(income),
            interest_component=q(contractual), posted_by=user,
            narration=(f"Interest at the effective rate for instalment(s) "
                       f"{', '.join(str(i.number) for i in accrued)}, to {as_of.isoformat()}"),
        )
        Instalment.objects.bulk_update(accrued, ["accrued_on"])
        ln.interest_accrued = q(ln.interest_accrued + contractual)
        ln.fees_deferred = q(ln.fees_deferred - unwind)
        ln.save(update_fields=["interest_accrued", "fees_deferred"])
        income_total += income
        touched += 1
    return {"as_of": as_of.isoformat(), "loans_accrued": touched,
            "interest_income": q(income_total)}


def release_deferred_fees(loan: Loan, on: date, user: User | None, why: str) -> Decimal:
    """Take whatever fees are still deferred to income: the loan has closed, been
    settled, or been rescheduled, so there is no life left to spread them over."""
    if not is_effective():
        return ZERO
    remaining = q(loan.fees_deferred or ZERO)
    if remaining == 0:
        return ZERO
    Transaction.objects.create(
        loan=loan, txn_type=TxnType.ACCRUAL, txn_date=on, amount=remaining,
        interest_component=ZERO, posted_by=user,
        narration=f"Deferred fees of {remaining} released: {why}",
    )
    loan.fees_deferred = ZERO
    loan.save(update_fields=["fees_deferred"])
    return remaining


def unaccrued_instalments(end: date) -> int:
    """Instalments due by `end` on running loans that no accrual has reached."""
    if not is_effective():
        return 0
    return (Instalment.objects
            .filter(loan__status=LoanStatus.ACTIVE, due_date__lte=end, accrued_on__isnull=True)
            .count())


def book_positions() -> dict[int, tuple[Decimal, Decimal]]:
    """Per active loan: (interest receivable, fees deferred) in the loan's currency,
    from the loan's own columns and its schedule. What 1200 and 1150 should hold."""
    from django.db.models import Sum

    paid = {row["loan_id"]: q(row["v"] or 0) for row in
            Instalment.objects.filter(loan__status=LoanStatus.ACTIVE)
            .values("loan_id").annotate(v=Sum("interest_paid"))}
    out = {}
    for loan in Loan.objects.filter(status=LoanStatus.ACTIVE).only(
            "id", "interest_accrued", "fees_deferred", "fx_rate"):
        receivable = max(ZERO, q(loan.interest_accrued - paid.get(loan.id, ZERO)))
        out[loan.id] = (receivable, q(loan.fees_deferred), loan.fx_rate)
    return out
