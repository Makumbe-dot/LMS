"""Credit scorecard.

A transparent, points-based assessment run at application. Every factor returns
its own points and a sentence saying why, so an officer can explain a decision
to a borrower and a supervisor can argue with the weighting rather than with a
black box.

The score is **advisory**. It never blocks an application on its own; the hard
rules (affordability, KYC, blacklist, one open loan at a time) live in
services/loans.py and are enforced there.
"""
from datetime import date
from decimal import Decimal

from ..models import CreditGrade, Loan, LoanStatus

ZERO = Decimal("0")

# Grade boundaries, highest first.
GRADES = [
    (80, CreditGrade.A),
    (65, CreditGrade.B),
    (50, CreditGrade.C),
    (35, CreditGrade.D),
    (0, CreditGrade.E),
]


def grade_for(score: int) -> str:
    for threshold, grade in GRADES:
        if score >= threshold:
            return grade
    return CreditGrade.E


def _affordability_points(instalment: Decimal, salary: Decimal, cap: Decimal,
                          basis: str = "net salary") -> tuple[int, str]:
    """25 points. How much of the borrower's monthly income the repayments take.

    `instalment` is the MONTHLY equivalent (amortisation.monthly_equivalent), so a
    weekly loan is measured against a month's income like any other. `basis` names
    the income: a salary, or what a business leaves.
    """
    if not salary or salary <= 0:
        return 0, f"No {basis} on file, so affordability cannot be assessed"
    ratio = instalment / salary * 100
    share = f"Repayments come to {ratio:.1f}% of {basis} a month"
    if ratio <= cap / 3:
        return 25, f"{share}, well inside the {cap}% limit"
    if ratio <= cap / 2:
        return 20, f"{share}, comfortably inside the {cap}% limit"
    if ratio <= cap * Decimal("0.75"):
        return 14, share
    if ratio <= cap:
        return 7, f"{share}, close to the {cap}% limit"
    return 0, f"{share}, above the {cap}% limit"


def _history_points(borrower, weight: int = 30) -> tuple[int, str]:
    """What the borrower did with the money last time, out of `weight`."""
    previous = list(borrower.loans.filter(
        status__in=[LoanStatus.CLOSED, LoanStatus.WRITTEN_OFF]).only("id", "status"))
    scale = Decimal(weight) / 30
    if not previous:
        return round(12 * scale), "No completed loans yet, so there is no repayment record to go on"

    written_off = sum(1 for l in previous if l.status == LoanStatus.WRITTEN_OFF)
    settled = len(previous) - written_off
    if written_off:
        return 0, f"{written_off} previous loan(s) were written off"
    if settled >= 3:
        return weight, f"{settled} previous loans settled in full"
    if settled == 2:
        return round(25 * scale), "Two previous loans settled in full"
    return round(20 * scale), "One previous loan settled in full"


def _bureau_points(enquiry, weight: int = 15) -> tuple[int, str]:
    """What the borrower did with everyone else's money, out of `weight`.

    A default on record scores nothing: it is the one fact a bureau exists to
    tell us. Otherwise arrears elsewhere take most of the points, and the bureau's
    own score fills in the rest.
    """
    label = f"Bureau report of {enquiry.enquired_at:%Y-%m-%d}"
    if enquiry.defaults:
        return 0, f"{label}: {enquiry.defaults} default(s) with other lenders"
    if enquiry.worst_days_in_arrears > 30:
        return round(weight * 0.2), (f"{label}: {enquiry.worst_days_in_arrears} days in arrears "
                                     f"with another lender")
    if enquiry.accounts_in_arrears:
        return round(weight * 0.5), (f"{label}: {enquiry.accounts_in_arrears} account(s) "
                                     f"elsewhere in arrears")
    if enquiry.score is not None and enquiry.score_max:
        share = Decimal(enquiry.score) / Decimal(enquiry.score_max)
        points = round(weight * (Decimal("0.6") + Decimal("0.4") * share))
        return min(points, weight), (f"{label}: score {enquiry.score}/{enquiry.score_max}, "
                                     f"{enquiry.open_accounts} account(s) elsewhere all current")
    return round(weight * 0.8), f"{label}: {enquiry.open_accounts} account(s) elsewhere, all current"


def _arrears_points(borrower, as_of: date) -> tuple[int, str]:
    """20 points. How the borrower is handling what they already owe."""
    from .loans import arrears

    running = list(borrower.loans.filter(status=LoanStatus.ACTIVE).prefetch_related("instalments"))
    if not running:
        return 20, "No loan currently running"
    worst = 0
    for loan in running:
        _amount, days = arrears(loan, as_of)
        worst = max(worst, days)
    if worst == 0:
        return 18, "Current on every running loan"
    if worst <= 7:
        return 10, f"Up to {worst} days late on a running loan"
    if worst <= 30:
        return 4, f"Up to {worst} days late on a running loan"
    return 0, f"{worst} days in arrears on a running loan"


def _employment_points(borrower) -> tuple[int, str]:
    """15 points. How verifiable the income is."""
    points = 0
    reasons = []
    if borrower.employer:
        points += 8
        reasons.append(f"employed by {borrower.employer}")
    else:
        reasons.append("no employer recorded")
    if borrower.employee_no:
        points += 4
        reasons.append("employee number on file for payroll deduction")
    if borrower.job_title:
        points += 3
        reasons.append("job title recorded")
    return points, "Employment: " + ", ".join(reasons)


def _business_points(borrower, as_of: date) -> tuple[int, str]:
    """15 points, for a borrower who lives off a business: how established and how
    well evidenced it is, in place of the employment points a payslip earns."""
    points = 0
    reasons = [borrower.business_name or "business not named"]
    years = borrower.years_trading(as_of)
    if years is None:
        reasons.append("trading since unknown")
    elif years >= 2:
        points += 6
        reasons.append(f"trading {years} years")
    elif years >= 1:
        points += 3
        reasons.append(f"trading {years} years")
    else:
        reasons.append("trading under a year")
    if borrower.business_registration_no:
        points += 3
        reasons.append("registered")
    if (borrower.monthly_sales or ZERO) > 0:
        points += 3
        reasons.append("cash flow assessed")
    else:
        reasons.append("no cash-flow worksheet")
    if borrower.guarantors.exists():
        points += 3
        reasons.append("guarantor on file")
    return points, "Business: " + ", ".join(reasons)


def _kyc_points(borrower) -> tuple[int, str]:
    """10 points. Identity and paperwork."""
    points = 6 if borrower.kyc_verified else 0
    documents = borrower.documents.count()
    if documents >= 2:
        points += 4
    elif documents == 1:
        points += 2
    detail = "KYC verified" if borrower.kyc_verified else "KYC not verified"
    return points, f"{detail}, {documents} document(s) on file"


def score_application(borrower, product, principal: Decimal, term: int,
                      instalment: Decimal, as_of: date | None = None) -> dict:
    """Score one proposed loan. Returns the score, the grade and the breakdown."""
    from .bureau import latest as latest_bureau_report
    from .loans import income_cap

    as_of = as_of or date.today()
    factors = []

    # With a fresh bureau report, the 30 points for repayment history are shared
    # between this book and everyone else's; without one, this book carries them
    # all and the card says a bureau was not consulted.
    report = latest_bureau_report(borrower, as_of)
    if report:
        history = [("Repayment history", 15, _history_points(borrower, 15)),
                   ("Credit bureau", 15, _bureau_points(report, 15))]
    else:
        points, reason = _history_points(borrower)
        history = [("Repayment history", 30, (points, f"{reason} (no bureau report)"))]

    for name, weight, (points, reason) in [
        *history,
        ("Affordability", 25, _affordability_points(
            instalment, borrower.net_salary, income_cap(product, borrower),
            "net salary" if borrower.is_employed else "net business income")),
        ("Current arrears", 20, _arrears_points(borrower, as_of)),
        (("Employment", 15, _employment_points(borrower)) if borrower.is_employed
         else ("Business", 15, _business_points(borrower, as_of))),
        ("KYC and documents", 10, _kyc_points(borrower)),
    ]:
        factors.append({"factor": name, "points": points, "max": weight, "reason": reason})

    score = sum(f["points"] for f in factors)

    # Hard negatives sit outside the weighting: they cap the score outright.
    if borrower.is_blacklisted:
        score = 0
        factors.append({"factor": "Blacklist", "points": 0, "max": 0,
                        "reason": "Borrower is blacklisted; the application cannot proceed"})

    grade = grade_for(score)
    return {
        "score": score,
        "grade": grade,
        "grade_label": CreditGrade(grade).label,
        "factors": factors,
        "summary": _summary(score, grade),
    }


def _summary(score: int, grade: str) -> str:
    if grade == CreditGrade.A:
        return f"Score {score}/100. A strong application on every measure."
    if grade == CreditGrade.B:
        return f"Score {score}/100. A sound application; approve on the usual terms."
    if grade == CreditGrade.C:
        return f"Score {score}/100. Acceptable, but worth a second look at the weakest factor."
    if grade == CreditGrade.D:
        return (f"Score {score}/100. Marginal. Consider a smaller amount, a longer term, "
                f"or security before approving.")
    return f"Score {score}/100. Weak. Decline unless there is a good reason on file."


def store_on_loan(loan: Loan, result: dict) -> Loan:
    """Persist the scorecard against the loan it was run for."""
    import json

    loan.credit_score = result["score"]
    loan.credit_grade = result["grade"]
    loan.score_detail = json.dumps(result["factors"])
    loan.save(update_fields=["credit_score", "credit_grade", "score_detail"])
    return loan


def read_from_loan(loan: Loan) -> dict | None:
    """The stored breakdown, back as a dict."""
    if loan.credit_score is None:
        return None
    import json

    try:
        factors = json.loads(loan.score_detail) if loan.score_detail else []
    except (ValueError, TypeError):
        factors = []
    return {
        "score": loan.credit_score,
        "grade": loan.credit_grade,
        "grade_label": CreditGrade(loan.credit_grade).label if loan.credit_grade else "",
        "factors": factors,
        "summary": _summary(loan.credit_score, loan.credit_grade),
    }
