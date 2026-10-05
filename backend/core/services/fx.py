"""Currencies, exchange rates and the revaluation of foreign-currency loans.

The ledger is kept in ONE currency, the organisation's (`OrganisationSetting.
currency`, the base). A product may be priced in another currency, and a loan sold
under it is then kept, instalment by instalment, in that currency. Every ledger
line for such a loan is converted when it is posted:

  * The **receivables** (1100, 1300, 1400) are carried at the loan's **booked
    rate** (`Loan.fx_rate`): the spot rate on the day it was disbursed, moved to
    the closing rate by each revaluation run since. A posting to a receivable is
    the CHANGE in its base-currency carrying value, q(after x rate) - q(before x
    rate), so the sum of the postings telescopes to exactly q(outstanding x rate)
    however many cents the individual conversions rounded. That exactness is what
    keeps the reconciliation page honest: 1100 equals the sum over loans of
    principal outstanding times the booked rate, to the cent, at all times.
  * **Cash and income** (1000, 4000, 4100, 4300) are converted at the **spot rate**
    on the transaction date (`Transaction.fx_rate`), because that is what the money
    was worth when it arrived.
  * Whatever the two leave between them - a repayment that relieves the receivable
    at the booked rate and brings in cash at today's rate - is a realised exchange
    difference, and goes to 4800.

A **revaluation run** (`revalue()`) restates every open foreign-currency loan at
the closing rate for a date: the receivables move to q(outstanding x new rate),
the difference goes to 4800 as an unrealised gain or loss, and the loan's booked
rate becomes the new rate. Running it at every month end is what IFRS expects of a
monetary asset in a foreign currency; running it twice at the same rate posts
nothing. A run is a `RevaluationRun` with one line per loan, so a Rebuild can
re-post it, like a provision run.

Rates are "units of the base currency per ONE unit of the foreign currency": with
a USD base, a rate of 0.0028 for ZWG means one ZWG is worth 0.0028 USD. The rate
for a date is the latest one on or before it. Savings, funding and the tills stay
in the base currency: a foreign-currency repayment taken in cash is counted in
the drawer at the spot rate.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    ExchangeRate,
    Loan,
    LoanStatus,
    OrganisationSetting,
    RevaluationLine,
    RevaluationRun,
    User,
)
from . import periods
from .amortisation import q

ZERO = Decimal("0")
ONE = Decimal("1")
RATE_PLACES = Decimal("0.000001")

FX_DIFFERENCES = "4800"   # realised and unrealised exchange differences; either sign


def base_currency() -> str:
    return (OrganisationSetting.load().currency or "USD").upper()


def normalise(code: str | None) -> str:
    return (code or "").strip().upper()


def rate_on(code: str | None, on: date | None = None, *, strict: bool = True) -> Decimal:
    """Base units per one unit of `code` on a date: 1 for the base currency itself.

    With strict=True a missing rate is a BusinessRuleError that names the currency
    and the date, because quoting, disbursing or repaying a foreign loan without a
    rate would post a guess. With strict=False it is None, for a listing.
    """
    code = normalise(code)
    if not code or code == base_currency():
        return ONE
    on = on or date.today()
    row = (ExchangeRate.objects.filter(code=code, rate_date__lte=on)
           .order_by("-rate_date", "-id").first())
    if row is None:
        if not strict:
            return None
        raise BusinessRuleError(
            f"No exchange rate for {code} on or before {on}. Add one on the Currencies page.")
    return row.rate


def to_base(amount, rate) -> Decimal:
    """A loan-currency amount in the base currency, to the cent."""
    return q(Decimal(amount or 0) * Decimal(rate or ONE))


def currencies_in_use() -> list[str]:
    """The base first, then every other currency a product or a rate names."""
    from ..models import LoanProduct

    base = base_currency()
    others = set(normalise(c) for c in LoanProduct.objects.values_list("currency", flat=True))
    others |= set(ExchangeRate.objects.values_list("code", flat=True))
    others.discard(base)
    others.discard("")
    return [base, *sorted(others)]


def is_foreign(loan: Loan) -> bool:
    return normalise(loan.currency) not in ("", base_currency())


# ---------------------------------------------------------------- rates
@db_transaction.atomic
def set_rate(code: str, rate_date: date, rate: Decimal, user: User | None,
             note: str | None = None) -> ExchangeRate:
    code = normalise(code)
    if not code or len(code) > 8 or not code.isalpha():
        raise BusinessRuleError("A currency is a short alphabetic code, such as ZWG or ZAR")
    if code == base_currency():
        raise BusinessRuleError(f"{code} is the base currency; its rate is always 1")
    rate = Decimal(rate).quantize(RATE_PLACES)
    if rate <= 0:
        raise BusinessRuleError("A rate must be greater than zero")
    row, _created = ExchangeRate.objects.update_or_create(
        code=code, rate_date=rate_date,
        defaults={"rate": rate, "note": note or None, "set_by": user})
    return row


# ---------------------------------------------------------------- revaluation
def _carrying(loan: Loan, rate, position) -> tuple[Decimal, ...]:
    receivable, deferred = position
    return (to_base(loan.principal_outstanding, rate),
            to_base(loan.penalties_outstanding, rate),
            to_base(loan.charges_outstanding, rate),
            to_base(receivable, rate),
            to_base(deferred, rate))


def preview(as_of: date | None = None) -> dict:
    """What a run on this date would move, loan by loan, without posting."""
    from .eir import book_positions, is_effective

    as_of = as_of or date.today()
    base = base_currency()
    lines = []
    missing = set()
    positions = book_positions() if is_effective() else {}
    for loan in (Loan.objects.filter(status=LoanStatus.ACTIVE)
                 .exclude(currency__in=["", base]).select_related("borrower").order_by("id")):
        new_rate = rate_on(loan.currency, as_of, strict=False)
        if new_rate is None:
            missing.add(normalise(loan.currency))
            continue
        old_rate = loan.fx_rate or ONE
        position = positions.get(loan.id, (ZERO, ZERO, ONE))[:2]
        before = _carrying(loan, old_rate, position)
        after = _carrying(loan, new_rate, position)
        # The deferred fees are a credit: a rise in their base value is a loss, so
        # the net movement takes them away.
        movement = [q(a - b) for a, b in zip(after, before)]
        net = q(movement[0] + movement[1] + movement[2] + movement[3] - movement[4])
        lines.append({
            "loan_id": loan.id, "loan_no": loan.loan_no, "borrower": loan.borrower.full_name,
            "currency": normalise(loan.currency),
            "principal_outstanding": q(loan.principal_outstanding),
            "penalties_outstanding": q(loan.penalties_outstanding),
            "charges_outstanding": q(loan.charges_outstanding),
            "interest_receivable": position[0], "fees_deferred": position[1],
            "old_rate": old_rate, "new_rate": new_rate,
            "carrying_before": q(sum(before[:4], ZERO) - before[4]),
            "carrying_after": q(sum(after[:4], ZERO) - after[4]),
            "principal_movement": movement[0], "penalties_movement": movement[1],
            "charges_movement": movement[2], "interest_movement": movement[3],
            "fees_movement": movement[4], "movement": net,
        })
    return {
        "as_of": as_of, "base_currency": base, "lines": lines,
        "loans": len(lines),
        "movement": q(sum((l["movement"] for l in lines), ZERO)),
        "missing_rates": sorted(missing),
    }


@db_transaction.atomic
def revalue(as_of: date | None, user: User | None, narration: str | None = None) -> RevaluationRun:
    """Restate every open foreign-currency loan at the closing rate for `as_of`."""
    from ..audit import audit
    from . import ledger
    from .loans import next_number

    as_of = as_of or date.today()
    periods.assert_open(as_of, "This revaluation")
    plan = preview(as_of)
    if plan["missing_rates"]:
        raise BusinessRuleError(
            f"No exchange rate on or before {as_of} for {', '.join(plan['missing_rates'])}. "
            f"Add the closing rates first.")
    if not plan["lines"]:
        raise BusinessRuleError("There are no open foreign-currency loans to revalue")

    run = RevaluationRun.objects.create(
        run_no=next_number("FXR"), as_of=as_of, base_currency=plan["base_currency"],
        loans_revalued=len(plan["lines"]), movement=plan["movement"],
        narration=narration or f"Revaluation of foreign-currency loans at {as_of}", run_by=user)
    RevaluationLine.objects.bulk_create([
        RevaluationLine(run=run, loan_id=l["loan_id"], currency=l["currency"],
                        old_rate=l["old_rate"], new_rate=l["new_rate"],
                        principal_outstanding=l["principal_outstanding"],
                        penalties_outstanding=l["penalties_outstanding"],
                        charges_outstanding=l["charges_outstanding"],
                        interest_receivable=l["interest_receivable"],
                        fees_deferred=l["fees_deferred"],
                        principal_movement=l["principal_movement"],
                        penalties_movement=l["penalties_movement"],
                        charges_movement=l["charges_movement"],
                        interest_movement=l["interest_movement"],
                        fees_movement=l["fees_movement"], movement=l["movement"])
        for l in plan["lines"]
    ])
    # The booked rate moves with the ledger, so the next posting on each loan
    # telescopes from the restated carrying value.
    for line in plan["lines"]:
        Loan.objects.filter(pk=line["loan_id"]).update(fx_rate=line["new_rate"])

    entry = ledger.post_manual_entry(_run_lines(run), as_of, run.narration,
                                     source="fx_revaluation", posted_by=user)
    if entry:
        run.journal_entry = entry
        run.save(update_fields=["journal_entry"])
    audit(user, "fx_revaluation", "revaluation_run", run.id,
          f"{run.run_no}: {run.loans_revalued} loans, movement {run.movement}")
    return run


def _run_lines(run: RevaluationRun) -> list[tuple[str, Decimal, Decimal, str]]:
    """One line per receivable account for the whole run, and 4800 for the rest.

    A rise in the base value of a receivable is a debit to it and an unrealised
    gain; a fall is the reverse. The 4800 line is whatever balances, which is the
    sum of the three movements by construction.
    """
    from . import ledger

    totals = run.lines.aggregate(p=Sum("principal_movement"), pen=Sum("penalties_movement"),
                                 c=Sum("charges_movement"), i=Sum("interest_movement"),
                                 f=Sum("fees_movement"))
    lines = []
    for code, amount, text in [
        (ledger.CODES["loans_receivable"], q(totals["p"] or ZERO), "Loans receivable restated"),
        (ledger.CODES["penalties_receivable"], q(totals["pen"] or ZERO),
         "Penalties receivable restated"),
        (ledger.CODES["charges_receivable"], q(totals["c"] or ZERO), "Charges receivable restated"),
        (ledger.CODES["interest_receivable"], q(totals["i"] or ZERO),
         "Interest receivable restated"),
        # a credit balance: a rise is a credit to it
        (ledger.CODES["deferred_fees"], -q(totals["f"] or ZERO), "Deferred fees restated"),
    ]:
        if amount > 0:
            lines.append((code, amount, ZERO, text))
        elif amount < 0:
            lines.append((code, ZERO, -amount, text))
    net = q(sum((d - c for _, d, c, _ in lines), ZERO))
    if net > 0:
        lines.append((FX_DIFFERENCES, ZERO, net, "Unrealised exchange gain"))
    elif net < 0:
        lines.append((FX_DIFFERENCES, -net, ZERO, "Unrealised exchange loss"))
    return lines


def repost_runs() -> dict:
    """Re-raise the entries of runs that have lost them, for `ledger.backfill`."""
    with periods.allow_closed_posting("revaluation run repost"):
        from . import ledger

        reposted = 0
        for run in RevaluationRun.objects.filter(journal_entry__isnull=True).exclude(movement=0):
            entry = ledger.post_manual_entry(
                _run_lines(run), min(run.as_of, date.today()),
                f"Re-posted: {run.narration}", source="fx_revaluation",
                posted_by=run.run_by, strict=False)
            if entry:
                run.journal_entry = entry
                run.save(update_fields=["journal_entry"])
                reposted += 1
        return {"reposted": reposted}


def unrevalued_loans(as_of: date) -> int:
    """Open foreign-currency loans whose booked rate is not the closing rate for
    `as_of`: what a month end still has to restate."""
    count = 0
    for loan in (Loan.objects.filter(status=LoanStatus.ACTIVE)
                 .exclude(currency__in=["", base_currency()]).only("currency", "fx_rate")):
        closing = rate_on(loan.currency, as_of, strict=False)
        if closing is None or closing != loan.fx_rate:
            count += 1
    return count


def describe() -> dict:
    """The currencies in use and the latest rate for each, for the Currencies page."""
    base = base_currency()
    out = []
    for code in currencies_in_use():
        if code == base:
            continue
        latest = ExchangeRate.objects.filter(code=code).order_by("-rate_date", "-id").first()
        out.append({
            "code": code,
            "rate": latest.rate if latest else None,
            "rate_date": latest.rate_date if latest else None,
            "open_loans": Loan.objects.filter(status=LoanStatus.ACTIVE, currency=code).count(),
        })
    return {"base_currency": base, "currencies": out, "as_of": timezone.localdate()}
