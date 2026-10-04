"""The spreadsheets people keep: members, balances, what is outstanding and overdue.

* **Member register**: one row per member (each borrower once, however many loans
  or accounts they hold), with their group, savings, loans outstanding and arrears.
* **Group membership**: one row per active member of each group, with the same
  money columns, so a group's exposure can be summed in Excel.
* **Savings balances**, **loans outstanding** and **overdue loans**: one row per
  account or loan.
* **Portfolio workbook**: all of the above, plus a summary sheet, in one file.

Every function reads the book in a fixed number of queries, whatever its size:
balances come from the running columns, arrears from the one set-based
definition in services/arrears.py, and per-member totals are added up in Python
over narrow rows rather than by a query per member.
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum

from ..exports import Sheet
from ..models import (
    Borrower,
    GroupMember,
    Loan,
    LoanStatus,
    OrganisationSetting,
    SavingsAccount,
    SavingsStatus,
    Transaction,
    TxnType,
)
from . import arrears as arrears_svc
from .amortisation import q
from .loans import TERM_UNITS
from .reports import portfolio_at_risk

ZERO = Decimal("0")


def _member_loans(as_of: date, branch_id=None) -> dict[int, dict]:
    """Per borrower: active loans and their balances and arrears, in one query."""
    qs = Loan.objects.filter(status=LoanStatus.ACTIVE)
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    totals: dict[int, dict] = defaultdict(lambda: {
        "active_loans": 0, "principal": ZERO, "interest": ZERO, "penalties_charges": ZERO,
        "overdue": ZERO, "days": 0})
    for row in (arrears_svc.with_arrears(qs, as_of)
                .values("borrower_id", "principal_outstanding", "interest_outstanding",
                        "penalties_outstanding", "charges_outstanding", "arrears_amount",
                        "oldest_arrears_due")):
        member = totals[row["borrower_id"]]
        member["active_loans"] += 1
        member["principal"] += row["principal_outstanding"]
        member["interest"] += row["interest_outstanding"]
        member["penalties_charges"] += row["penalties_outstanding"] + row["charges_outstanding"]
        member["overdue"] += Decimal(row["arrears_amount"] or 0)
        member["days"] = max(member["days"],
                             arrears_svc.days_from(row["oldest_arrears_due"], as_of))
    return totals


def _memberships() -> dict[int, GroupMember]:
    return {m.borrower_id: m for m in
            GroupMember.objects.filter(is_active=True).select_related("group")}


def member_register(branch_id=None, as_of: date | None = None) -> list[dict]:
    """One row per member, with everything they hold and owe."""
    as_of = as_of or date.today()
    borrowers = Borrower.objects.select_related("branch").order_by("borrower_no")
    if branch_id:
        borrowers = borrowers.filter(branch_id=branch_id)

    savings = {r["borrower_id"]: r for r in (
        SavingsAccount.objects.exclude(status=SavingsStatus.CLOSED)
        .values("borrower_id").annotate(accounts=Count("id"), balance=Sum("balance")))}
    loans = _member_loans(as_of, branch_id)
    taken = {r["borrower_id"]: r for r in (
        Loan.objects.exclude(status__in=[LoanStatus.PENDING, LoanStatus.REJECTED])
        .values("borrower_id").annotate(
            loans=Count("id"),
            closed=Count("id", filter=Q(status=LoanStatus.CLOSED)),
            written_off=Count("id", filter=Q(status=LoanStatus.WRITTEN_OFF))))}
    last_paid = {r["loan__borrower_id"]: r["last"] for r in (
        Transaction.objects.filter(txn_type=TxnType.REPAYMENT, reversed=False)
        .values("loan__borrower_id").annotate(last=Max("txn_date")))}
    groups = _memberships()

    out = []
    for b in borrowers:
        s = savings.get(b.id, {})
        l = loans.get(b.id)
        t = taken.get(b.id, {})
        g = groups.get(b.id)
        principal = l["principal"] if l else ZERO
        interest = l["interest"] if l else ZERO
        extra = l["penalties_charges"] if l else ZERO
        out.append({
            "member_no": b.borrower_no,
            "name": b.full_name,
            "national_id": b.national_id,
            "gender": b.gender or "",
            "phone": b.phone,
            "employer": b.employer or "",
            "employee_no": b.employee_no or "",
            "branch": b.branch.name if b.branch_id else "",
            "group": g.group.name if g else "",
            "group_role": g.get_role_display() if g else "",
            "member_since": b.created_at.date() if b.created_at else None,
            "kyc_verified": b.kyc_verified,
            "blacklisted": b.is_blacklisted,
            "savings_accounts": s.get("accounts", 0),
            "savings_balance": q(Decimal(s.get("balance") or 0)),
            "loans_taken": t.get("loans", 0),
            "loans_repaid": t.get("closed", 0),
            "loans_written_off": t.get("written_off", 0),
            "active_loans": l["active_loans"] if l else 0,
            "principal_outstanding": q(principal),
            "interest_outstanding": q(interest),
            "penalties_and_charges": q(extra),
            "total_outstanding": q(principal + interest + extra),
            "overdue_amount": q(l["overdue"]) if l else ZERO,
            "days_overdue": l["days"] if l else 0,
            "arrears_bucket": arrears_svc.bucket_for(l["days"]) if l else "",
            "last_repayment": last_paid.get(b.id),
        })
    return out


def group_membership(branch_id=None, as_of: date | None = None) -> list[dict]:
    """One row per active group member, with their money columns."""
    as_of = as_of or date.today()
    members = (GroupMember.objects.filter(is_active=True)
               .select_related("group", "group__branch", "borrower")
               .order_by("group__name", "role", "borrower__last_name"))
    if branch_id:
        members = members.filter(group__branch_id=branch_id)
    loans = _member_loans(as_of)
    savings = {r["borrower_id"]: r["balance"] for r in (
        SavingsAccount.objects.exclude(status=SavingsStatus.CLOSED)
        .values("borrower_id").annotate(balance=Sum("balance")))}
    out = []
    for m in members:
        l = loans.get(m.borrower_id)
        out.append({
            "group_no": m.group.group_no, "group": m.group.name,
            "group_status": m.group.get_status_display(),
            "branch": m.group.branch.name if m.group.branch_id else "",
            "meeting_day": m.group.meeting_day or "",
            "member_no": m.borrower.borrower_no, "member": m.borrower.full_name,
            "phone": m.borrower.phone, "role": m.get_role_display(), "joined": m.joined_on,
            "savings_balance": q(Decimal(savings.get(m.borrower_id) or 0)),
            "active_loans": l["active_loans"] if l else 0,
            "total_outstanding": q(l["principal"] + l["interest"] + l["penalties_charges"])
            if l else ZERO,
            "overdue_amount": q(l["overdue"]) if l else ZERO,
            "days_overdue": l["days"] if l else 0,
        })
    return out


def savings_balances(branch_id=None) -> list[dict]:
    qs = (SavingsAccount.objects.select_related("borrower", "product", "branch")
          .order_by("account_no"))
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    return [{
        "account_no": a.account_no, "member_no": a.borrower.borrower_no,
        "member": a.borrower.full_name, "product": a.product.name,
        "branch": a.branch.name if a.branch_id else "", "status": a.get_status_display(),
        "opened_on": a.opened_on, "balance": a.balance,
        "minimum_balance": a.product.min_balance, "available_balance": a.available_balance,
        "last_interest_date": a.last_interest_date,
    } for a in qs]


def outstanding_loans(branch_id=None, as_of: date | None = None) -> list[dict]:
    """Every active loan with its balances and arrears."""
    as_of = as_of or date.today()
    qs = (Loan.objects.filter(status=LoanStatus.ACTIVE)
          .select_related("borrower", "product", "officer", "branch").order_by("loan_no"))
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    out = []
    for l in arrears_svc.with_arrears(qs, as_of):
        days = arrears_svc.days_from(l.oldest_arrears_due, as_of)
        out.append({
            "loan_no": l.loan_no, "member_no": l.borrower.borrower_no,
            "member": l.borrower.full_name, "phone": l.borrower.phone,
            "employer": l.borrower.employer or "", "branch": l.branch.name if l.branch_id else "",
            "officer": l.officer.full_name if l.officer_id else "",
            "product": l.product.name, "repaid": l.get_repayment_frequency_display(),
            "term": f"{l.term_months} {TERM_UNITS.get(l.repayment_frequency, 'instalments')}",
            "disbursed": l.disbursement_date, "maturity": l.maturity_date,
            "principal": l.principal, "instalment": l.instalment_amount,
            "principal_outstanding": l.principal_outstanding,
            "interest_outstanding": l.interest_outstanding,
            "penalties_outstanding": l.penalties_outstanding,
            "charges_outstanding": l.charges_outstanding,
            "total_outstanding": l.total_outstanding, "total_paid": l.total_paid,
            "overdue_amount": q(Decimal(l.arrears_amount or 0)), "days_overdue": days,
            "arrears_bucket": arrears_svc.bucket_for(days),
        })
    return out


def overdue_loans(branch_id=None, as_of: date | None = None) -> list[dict]:
    """The loans in arrears, worst first: the portfolio-at-risk listing."""
    return [{k: v for k, v in row.items() if k != "loan_id"}
            for row in portfolio_at_risk(as_of, branch_id)]


def summary(branch_id=None, as_of: date | None = None) -> list[dict]:
    as_of = as_of or date.today()
    book = arrears_svc.totals(as_of, branch_id=branch_id)
    members = Borrower.objects.all()
    accounts = SavingsAccount.objects.exclude(status=SavingsStatus.CLOSED)
    if branch_id:
        members = members.filter(branch_id=branch_id)
        accounts = accounts.filter(branch_id=branch_id)
    savings_total = accounts.aggregate(v=Sum("balance"))["v"] or ZERO
    rows = [
        ("Members", members.count(), "count"),
        ("Members in a group", GroupMember.objects.filter(
            is_active=True, borrower__in=members).values("borrower").distinct().count(), "count"),
        ("Savings accounts open", accounts.count(), "count"),
        ("Savings balances", q(savings_total), "money"),
        ("Active loans", book["loans"], "count"),
        ("Principal outstanding", book["principal_outstanding"], "money"),
        ("Total outstanding", book["total_outstanding"], "money"),
        ("Overdue (in arrears)", q(sum(book["bucket_arrears"].values(), ZERO)), "money"),
        ("Portfolio at risk > 30 days", book["par_amount"], "money"),
        ("PAR > 30 days, % of principal", book["par_pct"], "percent"),
    ]
    rows += [(f"Principal in arrears bucket {name}", book["buckets"][name], "money")
             for name in arrears_svc.BUCKET_NAMES]
    return [{"measure": label, "value": value, "unit": unit} for label, value, unit in rows]


def portfolio_workbook(branch_id=None) -> list[Sheet]:
    org = OrganisationSetting.load()
    as_of = date.today()
    scope = "all branches"
    if branch_id:
        from ..models import Branch

        branch = Branch.objects.filter(pk=branch_id).first()
        scope = branch.name if branch else f"branch {branch_id}"
    note = [f"{org.name} · {scope} · as at {as_of.isoformat()} · "
            f"generated {datetime.now():%Y-%m-%d %H:%M} · amounts in {org.currency}"]
    return [
        Sheet("Summary", summary(branch_id, as_of), "Portfolio summary", note, totals=False),
        Sheet("Members", member_register(branch_id, as_of), "Member register", note),
        Sheet("Loans outstanding", outstanding_loans(branch_id, as_of), "Loans outstanding", note),
        Sheet("Overdue", overdue_loans(branch_id, as_of), "Overdue loans, worst first", note),
        Sheet("Savings balances", savings_balances(branch_id), "Savings balances", note),
        Sheet("Group membership", group_membership(branch_id, as_of), "Group membership", note),
    ]


SPREADSHEETS = {
    "members": ("member_register", "Member register", member_register),
    "group-membership": ("group_membership", "Group membership", group_membership),
    "savings-balances": ("savings_balances", "Savings balances", lambda branch_id=None, as_of=None:
                         savings_balances(branch_id)),
    "loans-outstanding": ("loans_outstanding", "Loans outstanding", outstanding_loans),
    "overdue": ("overdue_loans", "Overdue loans", overdue_loans),
}
