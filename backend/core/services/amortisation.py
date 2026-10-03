"""Amortisation with level instalments, in two flavours.

* **Reducing balance** (annuity): interest is charged on the outstanding balance,
  so the interest share of each instalment falls as the principal is repaid.
* **Flat rate**: interest is charged on the original principal for the whole
  term and spread evenly, which is simpler to explain and always dearer.

Instalments fall monthly, fortnightly or weekly. Products quote a MONTHLY rate
whatever the frequency, and the rate per instalment is that monthly rate scaled
by 12 / periods-a-year: a year of weekly instalments carries the same nominal
interest as a year of monthly ones, so changing the frequency changes when the
borrower pays, not what the product costs.

All arithmetic is in Decimal, rounded to cents. The final instalment absorbs
rounding so the schedule closes to exactly zero.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def q(x) -> Decimal:
    """Quantise to cents, half up."""
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def add_months(d: date, months: int) -> date:
    """Add calendar months, clamping the day to month end (31 Jan + 1 month = 28/29 Feb)."""
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    day = min(d.day, monthrange(y, m)[1])
    return date(y, m, day)


def set_day(d: date, day: int) -> date:
    return date(d.year, d.month, min(day, monthrange(d.year, d.month)[1]))


def month_end(d: date) -> date:
    """The last day of the calendar month d falls in."""
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


REDUCING = "reducing"
FLAT = "flat"

MONTHLY = "monthly"
FORTNIGHTLY = "fortnightly"
WEEKLY = "weekly"
PERIODS_PER_YEAR = {MONTHLY: 12, FORTNIGHTLY: 26, WEEKLY: 52}


def period_rate(monthly_rate_pct, frequency: str = MONTHLY) -> Decimal:
    """The interest rate per instalment period, as a fraction (0.05, not 5)."""
    return (Decimal(monthly_rate_pct) / Decimal(100)
            * Decimal(12) / Decimal(PERIODS_PER_YEAR[frequency]))


def monthly_equivalent(instalment, frequency: str = MONTHLY) -> Decimal:
    """What an instalment comes to over an average month. Salaries are monthly, so
    affordability is always measured against this, never the raw weekly figure."""
    return q(Decimal(instalment) * PERIODS_PER_YEAR[frequency] / 12)


def nth_due_date(first_due: date, n: int, frequency: str = MONTHLY) -> date:
    """The due date n instalments after the first (n = 0 is the first)."""
    if frequency == WEEKLY:
        return first_due + timedelta(weeks=n)
    if frequency == FORTNIGHTLY:
        return first_due + timedelta(weeks=2 * n)
    return add_months(first_due, n)


def instalment_amount(principal, monthly_rate_pct, term: int, method: str = REDUCING,
                      frequency: str = MONTHLY) -> Decimal:
    """The level instalment over `term` instalments at the product's monthly rate."""
    P = Decimal(principal)
    r = period_rate(monthly_rate_pct, frequency)
    if method == FLAT:
        return q((P + P * r * term) / term)
    if r == 0:
        return q(P / term)
    factor = (1 + r) ** term
    return q(P * r * factor / (factor - 1))


def monthly_instalment(principal, monthly_rate_pct, term: int, method: str = REDUCING) -> Decimal:
    return instalment_amount(principal, monthly_rate_pct, term, method, MONTHLY)


@dataclass
class Row:
    number: int
    due_date: date
    opening_balance: Decimal
    principal_due: Decimal
    interest_due: Decimal
    instalment: Decimal
    closing_balance: Decimal


def build_schedule(principal, monthly_rate_pct, term: int, first_due: date,
                   method: str = REDUCING, frequency: str = MONTHLY) -> list[Row]:
    """`term` instalments of the given frequency, the first falling on `first_due`."""
    if method == FLAT:
        return _flat_schedule(principal, monthly_rate_pct, term, first_due, frequency)
    return _reducing_schedule(principal, monthly_rate_pct, term, first_due, frequency)


def _reducing_schedule(principal, monthly_rate_pct, term: int, first_due: date,
                       frequency: str = MONTHLY) -> list[Row]:
    P = q(Decimal(principal))
    r = period_rate(monthly_rate_pct, frequency)
    inst = instalment_amount(P, monthly_rate_pct, term, REDUCING, frequency)
    rows: list[Row] = []
    bal = P
    for n in range(1, term + 1):
        interest = q(bal * r)
        if n < term:
            principal_part = q(inst - interest)
            if principal_part > bal:
                principal_part = bal
        else:
            principal_part = bal  # final instalment clears the balance
        closing = q(bal - principal_part)
        rows.append(
            Row(
                number=n,
                due_date=nth_due_date(first_due, n - 1, frequency),
                opening_balance=bal,
                principal_due=principal_part,
                interest_due=interest,
                instalment=q(principal_part + interest),
                closing_balance=closing,
            )
        )
        bal = closing
    return rows


def _flat_schedule(principal, monthly_rate_pct, term: int, first_due: date,
                   frequency: str = MONTHLY) -> list[Row]:
    """Interest on the original principal, spread evenly across the term.

    Both the principal and the interest legs are levelled; the last instalment
    takes the rounding on each leg, so principal sums to exactly the advance and
    interest to exactly P x r x n, with r the rate per instalment period.
    """
    P = q(Decimal(principal))
    r = period_rate(monthly_rate_pct, frequency)
    total_interest_amount = q(P * r * term)
    principal_each = q(P / term)
    interest_each = q(total_interest_amount / term)

    rows: list[Row] = []
    bal = P
    principal_so_far = interest_so_far = Decimal("0")
    for n in range(1, term + 1):
        if n < term:
            principal_part = principal_each
            interest = interest_each
        else:
            principal_part = q(P - principal_so_far)
            interest = q(total_interest_amount - interest_so_far)
        principal_so_far += principal_part
        interest_so_far += interest
        closing = q(bal - principal_part)
        rows.append(
            Row(
                number=n,
                due_date=nth_due_date(first_due, n - 1, frequency),
                opening_balance=bal,
                principal_due=principal_part,
                interest_due=interest,
                instalment=q(principal_part + interest),
                closing_balance=closing,
            )
        )
        bal = closing
    return rows


def total_interest(rows: list[Row]) -> Decimal:
    return q(sum((r.interest_due for r in rows), Decimal("0")))


def annual_percentage_rate(net_advanced, payments, start: date) -> Decimal | None:
    """The yearly rate that discounts the instalments back to what the borrower received.

    `payments` is a list of (due date, amount). The borrower receives `net_advanced`
    on `start` - the principal less every fee deducted from it - and the APR is the
    rate r for which

        net_advanced = sum(amount_k / (1 + r) ** (days_k / 365))

    So unlike the monthly nominal rate on the product, it prices the upfront fees in
    with the interest, and it is comparable across lenders, terms and repayment
    frequencies. It is a disclosure figure, rounded to two places; nothing posts
    from it.

    Solved by bisection in Decimal. The present value falls as the rate rises, so
    bisection cannot miss; it stops once the bracket is a ten-millionth wide, far
    tighter than the two places shown. Returns None when there is no rate to find:
    nothing advanced, or nothing to repay beyond it.

    Each discount factor is exp(-t * ln(1 + r)) with the logarithm taken once per
    trial rate, not once per instalment: a quote is recomputed as the officer types,
    and a weekly loan has fifty-odd instalments.
    """
    from decimal import localcontext

    net = Decimal(net_advanced)
    if net <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 16
        year = Decimal(365)
        flows = [(Decimal((due - start).days) / year, Decimal(amount))
                 for due, amount in payments if amount]
        if not flows or sum(a for _, a in flows) <= net:
            return None

        def excess(rate: Decimal) -> Decimal:
            # Present value of the repayments over what was advanced; falls as rate rises.
            log_base = (1 + rate).ln()
            return sum((a * (-(t * log_base)).exp() for t, a in flows), Decimal(0)) - net

        low, high = Decimal(0), Decimal(1)
        while excess(high) > 0:
            high *= 2
            if high > 10_000:  # a million percent: not a loan anyone should be quoting
                return None
        tolerance = Decimal("1e-7")
        while high - low > tolerance:
            mid = (low + high) / 2
            if excess(mid) > 0:
                low = mid
            else:
                high = mid
        return q((low + high) / 2 * 100)
