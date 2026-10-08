"""Currencies, exchange rates and the revaluation of foreign-currency balances.

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

Savings and funding follow the same model from the other side of the balance
sheet. A savings product, and so its accounts, may take deposits in another
currency, and a funding facility may be drawn in one. The liabilities - 2000 for
a savings balance, 2100 and 2110 for a facility's principal and accrued interest -
are carried at the account's or the facility's booked rate (`fx_rate` on each:
the spot rate on the day it was opened, moved by each revaluation), as the change
in their base-currency value, so 2000 equals the sum of q(balance x rate) to the
cent. Cash, savings interest, fees and borrowing costs go in at the spot rate of
the day (`fx_rate` on the movement), and the difference is realised on 4800 as it
is for a loan.

A **revaluation run** (`revalue()`) restates every open foreign-currency loan,
savings account and facility at the closing rate for a date: each balance moves to
q(balance x new rate), the difference goes to 4800 as an unrealised gain or loss,
and the booked rate becomes the new rate. A receivable that rises in base value is
a gain; a liability that rises is a loss, so its sign is turned over. Running it
at every month end is what IFRS expects of a monetary item in a foreign currency;
running it twice at the same rate posts nothing. A run is a `RevaluationRun` with
one line per loan, account or facility, so a Rebuild can re-post it, like a
provision run.

Rates are "units of the base currency per ONE unit of the foreign currency": with
a USD base, a rate of 0.0028 for ZWG means one ZWG is worth 0.0028 USD. The rate
for a date is the latest one on or before it. Cash is counted in a till drawer of
its own currency (services/tills.py); account 1000 holds it at the rate it came in
at.
"""
from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    ExchangeRate,
    FundingFacility,
    Loan,
    LoanStatus,
    OrganisationSetting,
    RevaluationLine,
    RevaluationRun,
    SavingsAccount,
    SavingsStatus,
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


def carried_change(after, before, rate) -> Decimal:
    """The change in a balance's base-currency carrying value at `rate`: what a
    posting to a control account carried at a booked rate is, so the postings
    telescope to exactly q(balance x rate) however the conversions round."""
    return q(to_base(after, rate) - to_base(before, rate))


def currencies_in_use() -> list[str]:
    """The base first, then every other currency a product, a facility or a rate
    names."""
    from ..models import LoanProduct, SavingsProduct

    base = base_currency()
    others = set(normalise(c) for c in LoanProduct.objects.values_list("currency", flat=True))
    others |= set(normalise(c) for c in SavingsProduct.objects.values_list("currency", flat=True))
    others |= set(normalise(c) for c in FundingFacility.objects.values_list("currency", flat=True))
    others |= set(ExchangeRate.objects.values_list("code", flat=True))
    others.discard(base)
    others.discard("")
    return [base, *sorted(others)]


def is_foreign(holder) -> bool:
    """Whether a loan, savings account, facility or till is in another currency."""
    return normalise(holder.currency) not in ("", base_currency())


def stored_code(code: str | None) -> str:
    """A currency as a product, facility or till stores it: blank for the base."""
    code = normalise(code)
    return "" if code == base_currency() else code


def validate_code(code: str | None) -> str:
    """A currency chosen on a form, checked and stored: blank for the base, and
    any other code only once the Currencies page has a rate for it."""
    code = stored_code(code)
    if not code:
        return ""
    if len(code) > 8 or not code.isalpha():
        raise BusinessRuleError("A currency is a short code such as ZWG or ZAR")
    if rate_on(code, strict=False) is None:
        raise BusinessRuleError(
            f"No exchange rate for {code} yet. Add one on the Currencies page first.")
    return code


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


# The fields every preview line carries, so a loan, an account and a facility
# read alike on the page and in the serializer; each kind fills its own.
_BLANK_LINE = {
    "loan_id": None, "loan_no": None, "borrower": None,
    "savings_account_id": None, "facility_id": None,
    "principal_outstanding": ZERO, "penalties_outstanding": ZERO,
    "charges_outstanding": ZERO, "interest_receivable": ZERO, "fees_deferred": ZERO,
    "principal_movement": ZERO, "penalties_movement": ZERO, "charges_movement": ZERO,
    "interest_movement": ZERO, "fees_movement": ZERO,
    "savings_balance": ZERO, "savings_movement": ZERO,
    "borrowings": ZERO, "borrowings_movement": ZERO,
    "borrowing_interest": ZERO, "borrowing_interest_movement": ZERO,
}


def _open_savings():
    return (SavingsAccount.objects.exclude(status=SavingsStatus.CLOSED)
            .exclude(currency__in=["", base_currency()]))


def _open_facilities():
    return (FundingFacility.objects.filter(closed_on__isnull=True)
            .exclude(currency__in=["", base_currency()]))


def preview(as_of: date | None = None) -> dict:
    """What a run on this date would move, line by line, without posting."""
    from .eir import book_positions, is_effective

    as_of = as_of or date.today()
    base = base_currency()
    lines = []
    missing = set()

    def closing(code):
        rate = rate_on(code, as_of, strict=False)
        if rate is None:
            missing.add(normalise(code))
        return rate

    positions = book_positions() if is_effective() else {}
    for loan in (Loan.objects.filter(status=LoanStatus.ACTIVE)
                 .exclude(currency__in=["", base]).select_related("borrower").order_by("id")):
        new_rate = closing(loan.currency)
        if new_rate is None:
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
            **_BLANK_LINE, "kind": "loan",
            "loan_id": loan.id, "loan_no": loan.loan_no, "borrower": loan.borrower.full_name,
            "reference": loan.loan_no, "holder": loan.borrower.full_name,
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

    # Members' savings: a liability, so a rise in its base value is a loss.
    for account in _open_savings().select_related("borrower").order_by("id"):
        new_rate = closing(account.currency)
        if new_rate is None:
            continue
        old_rate = account.fx_rate or ONE
        before, after = to_base(account.balance, old_rate), to_base(account.balance, new_rate)
        change = q(after - before)
        lines.append({
            **_BLANK_LINE, "kind": "savings",
            "savings_account_id": account.id, "reference": account.account_no,
            "holder": account.borrower.full_name, "currency": normalise(account.currency),
            "old_rate": old_rate, "new_rate": new_rate,
            "savings_balance": q(account.balance), "savings_movement": change,
            "carrying_before": before, "carrying_after": after, "movement": -change,
        })

    # Funder borrowings: principal and accrued interest, both liabilities.
    for facility in _open_facilities().order_by("id"):
        new_rate = closing(facility.currency)
        if new_rate is None:
            continue
        old_rate = facility.fx_rate or ONE
        principal = q(to_base(facility.principal_outstanding, new_rate)
                      - to_base(facility.principal_outstanding, old_rate))
        accrued = q(to_base(facility.interest_accrued, new_rate)
                    - to_base(facility.interest_accrued, old_rate))
        before = q(to_base(facility.principal_outstanding, old_rate)
                   + to_base(facility.interest_accrued, old_rate))
        lines.append({
            **_BLANK_LINE, "kind": "facility",
            "facility_id": facility.id, "reference": facility.facility_no,
            "holder": facility.funder_name, "currency": normalise(facility.currency),
            "old_rate": old_rate, "new_rate": new_rate,
            "borrowings": q(facility.principal_outstanding), "borrowings_movement": principal,
            "borrowing_interest": q(facility.interest_accrued),
            "borrowing_interest_movement": accrued,
            "carrying_before": before, "carrying_after": q(before + principal + accrued),
            "movement": q(-(principal + accrued)),
        })

    def count(kind):
        return sum(1 for l in lines if l["kind"] == kind)

    return {
        "as_of": as_of, "base_currency": base, "lines": lines,
        "loans": count("loan"), "savings_accounts": count("savings"),
        "facilities": count("facility"),
        "movement": q(sum((l["movement"] for l in lines), ZERO)),
        "missing_rates": sorted(missing),
    }


_LINE_FIELDS = [
    "currency", "old_rate", "new_rate", "principal_outstanding", "penalties_outstanding",
    "charges_outstanding", "interest_receivable", "fees_deferred", "principal_movement",
    "penalties_movement", "charges_movement", "interest_movement", "fees_movement",
    "savings_balance", "savings_movement", "borrowings", "borrowings_movement",
    "borrowing_interest", "borrowing_interest_movement", "movement",
]


@db_transaction.atomic
def revalue(as_of: date | None, user: User | None, narration: str | None = None) -> RevaluationRun:
    """Restate every open foreign-currency loan, savings account and facility at
    the closing rate for `as_of`."""
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
        raise BusinessRuleError("There are no open foreign-currency loans, savings accounts or "
                                "facilities to revalue")

    run = RevaluationRun.objects.create(
        run_no=next_number("FXR"), as_of=as_of, base_currency=plan["base_currency"],
        loans_revalued=plan["loans"], savings_revalued=plan["savings_accounts"],
        facilities_revalued=plan["facilities"], movement=plan["movement"],
        narration=narration or f"Revaluation of foreign-currency balances at {as_of}",
        run_by=user)
    RevaluationLine.objects.bulk_create([
        RevaluationLine(run=run, loan_id=l["loan_id"], savings_account_id=l["savings_account_id"],
                        facility_id=l["facility_id"], **{f: l[f] for f in _LINE_FIELDS})
        for l in plan["lines"]
    ])
    # The booked rate moves with the ledger, so the next posting on each loan,
    # account and facility telescopes from the restated carrying value.
    for line in plan["lines"]:
        if line["kind"] == "loan":
            Loan.objects.filter(pk=line["loan_id"]).update(fx_rate=line["new_rate"])
        elif line["kind"] == "savings":
            SavingsAccount.objects.filter(pk=line["savings_account_id"]).update(
                fx_rate=line["new_rate"])
        else:
            FundingFacility.objects.filter(pk=line["facility_id"]).update(
                fx_rate=line["new_rate"])

    entry = ledger.post_manual_entry(_run_lines(run), as_of, run.narration,
                                     source="fx_revaluation", posted_by=user)
    if entry:
        run.journal_entry = entry
        run.save(update_fields=["journal_entry"])
    audit(user, "fx_revaluation", "revaluation_run", run.id,
          f"{run.run_no}: {run.loans_revalued} loans, {run.savings_revalued} savings accounts, "
          f"{run.facilities_revalued} facilities, movement {run.movement}")
    return run


def _run_lines(run: RevaluationRun) -> list[tuple[str, Decimal, Decimal, str]]:
    """One line per control account for the whole run, and 4800 for the rest.

    A rise in the base value of a receivable is a debit to it and an unrealised
    gain; a fall is the reverse. A liability - savings, borrowings and the interest
    accrued on them - is the other way about: a rise is a credit to it and a loss.
    The 4800 line is whatever balances, which is the net of the movements by
    construction.
    """
    from . import ledger

    totals = run.lines.aggregate(p=Sum("principal_movement"), pen=Sum("penalties_movement"),
                                 c=Sum("charges_movement"), i=Sum("interest_movement"),
                                 f=Sum("fees_movement"), s=Sum("savings_movement"),
                                 b=Sum("borrowings_movement"),
                                 bi=Sum("borrowing_interest_movement"))
    lines = []
    for code, amount, text in [
        (ledger.CODES["loans_receivable"], q(totals["p"] or ZERO), "Loans receivable restated"),
        (ledger.CODES["penalties_receivable"], q(totals["pen"] or ZERO),
         "Penalties receivable restated"),
        (ledger.CODES["charges_receivable"], q(totals["c"] or ZERO), "Charges receivable restated"),
        (ledger.CODES["interest_receivable"], q(totals["i"] or ZERO),
         "Interest receivable restated"),
        # credit balances: a rise is a credit to them
        (ledger.CODES["deferred_fees"], -q(totals["f"] or ZERO), "Deferred fees restated"),
        (ledger.CODES["client_funds"], -q(totals["s"] or ZERO), "Client savings restated"),
        (ledger.CODES["borrowings"], -q(totals["b"] or ZERO), "Funder borrowings restated"),
        (ledger.CODES["accrued_interest"], -q(totals["bi"] or ZERO),
         "Accrued interest on borrowings restated"),
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


def unrevalued_balances(as_of: date) -> dict:
    """Loans, savings accounts and facilities in another currency still carried at
    an earlier rate than the closing one for `as_of`, counted by kind."""
    def stale(queryset):
        return sum(1 for row in queryset.only("currency", "fx_rate")
                   if rate_on(row.currency, as_of, strict=False) != row.fx_rate)

    return {"loans": unrevalued_loans(as_of), "savings_accounts": stale(_open_savings()),
            "facilities": stale(_open_facilities())}


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
            "open_savings": _open_savings().filter(currency=code).count(),
            "open_facilities": _open_facilities().filter(currency=code).count(),
        })
    return {"base_currency": base, "currencies": out, "as_of": timezone.localdate()}
