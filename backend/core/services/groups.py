"""Joint-liability groups.

Members stand behind each other's borrowing, so the group's standing is a fact
about every member's application. The rule this enforces is the usual one: while
any member is materially behind, the group does not take on new debt.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Prefetch

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    BorrowerGroup,
    GroupMember,
    GroupStatus,
    Instalment,
    Loan,
    LoanStatus,
    OrganisationSetting,
)
from . import arrears as arrears_svc
from .amortisation import q
from .fx import to_base
from .loans import next_number

ZERO = Decimal("0")


def create_group(name: str, branch_id, officer, **fields) -> BorrowerGroup:
    return BorrowerGroup.objects.create(
        group_no=next_number("GRP", width=5), name=name, branch_id=branch_id, officer=officer,
        **fields)


def add_member(group: BorrowerGroup, borrower: Borrower, role: str = "member",
               joined_on: date | None = None) -> GroupMember:
    if borrower.is_blacklisted:
        raise BusinessRuleError(f"{borrower.full_name} is blacklisted and cannot join a group")
    existing = GroupMember.objects.filter(group=group, borrower=borrower).first()
    if existing:
        if existing.is_active:
            raise BusinessRuleError(f"{borrower.full_name} is already in this group")
        existing.is_active = True
        existing.role = role
        existing.save(update_fields=["is_active", "role"])
        return existing

    other = (GroupMember.objects
             .filter(borrower=borrower, is_active=True)
             .exclude(group=group)
             .select_related("group").first())
    if other:
        raise BusinessRuleError(
            f"{borrower.full_name} already belongs to {other.group.name}; "
            f"a borrower stands behind one group at a time")

    return GroupMember.objects.create(group=group, borrower=borrower, role=role,
                                      joined_on=joined_on or date.today())


def remove_member(group: BorrowerGroup, member: GroupMember) -> GroupMember:
    """A member with a running loan cannot simply walk away from the liability."""
    running = member.borrower.loans.filter(
        status__in=[LoanStatus.PENDING, LoanStatus.APPROVED, LoanStatus.ACTIVE]).first()
    if running:
        raise BusinessRuleError(
            f"{member.borrower.full_name} has a running loan ({running.loan_no}); "
            f"settle it before leaving the group")
    member.is_active = False
    member.save(update_fields=["is_active"])
    return member


def member_loans(group: BorrowerGroup, *, with_schedule: bool = True):
    """Every loan of every current member, not only loans booked to the group.

    `with_schedule=False` for callers that read arrears off a set-based annotation
    rather than by walking the instalments.
    """
    borrower_ids = list(group.members.filter(is_active=True).values_list("borrower_id", flat=True))
    qs = (Loan.objects
          .filter(borrower_id__in=borrower_ids)
          .select_related("borrower", "product")
          .order_by("-id"))
    if with_schedule:
        qs = qs.prefetch_related(
            Prefetch("instalments", queryset=Instalment.objects.order_by("number")))
    return qs


def standing(group: BorrowerGroup, as_of: date | None = None) -> dict:
    """What the group owes, and how far behind its worst member is.

    Reads arrears from the database rather than by walking each member's schedule:
    `performance()` calls this once per group, so the schedules of every member of
    every group were being fetched to compute one number each.
    """
    as_of = as_of or date.today()
    loans = [l for l in arrears_svc.with_arrears(
        member_loans(group, with_schedule=False), as_of) if l.status == LoanStatus.ACTIVE]

    outstanding = arrears_amount = ZERO
    worst_days = 0
    behind = []
    for loan in loans:
        # In the organisation's currency, at the loan's booked rate.
        amount = to_base(loan.arrears_amount or 0, loan.fx_rate)
        days = arrears_svc.days_from(loan.oldest_arrears_due, as_of)
        outstanding += to_base(loan.total_outstanding, loan.fx_rate)
        arrears_amount += amount
        if days > worst_days:
            worst_days = days
        if amount > 0:
            behind.append({
                "loan_no": loan.loan_no, "loan_id": loan.id,
                "borrower": loan.borrower.full_name, "arrears_amount": q(amount),
                "days_in_arrears": days,
            })

    members = group.members.filter(is_active=True).count()
    return {
        "group_no": group.group_no,
        "name": group.name,
        "as_of": as_of,
        "members": members,
        "active_loans": len(loans),
        "total_outstanding": q(outstanding),
        "arrears_amount": q(arrears_amount),
        "worst_days_in_arrears": worst_days,
        "members_behind": behind,
        "average_exposure": q(outstanding / members) if members else ZERO,
    }


def check_can_borrow(borrower: Borrower, as_of: date | None = None) -> None:
    """Refuse a new loan while the borrower's group is materially behind.

    Raises BusinessRuleError, or returns quietly when the borrower is not in a
    group, the rule is switched off, or the group is current.
    """
    membership = (GroupMember.objects
                  .filter(borrower=borrower, is_active=True)
                  .select_related("group").first())
    if membership is None:
        return

    block_days = OrganisationSetting.load().group_arrears_block_days
    if not block_days:
        return

    state = standing(membership.group, as_of)
    if state["worst_days_in_arrears"] > block_days:
        worst = max(state["members_behind"], key=lambda m: m["days_in_arrears"])
        raise BusinessRuleError(
            f"{membership.group.name} is behind on joint liability: {worst['borrower']} is "
            f"{worst['days_in_arrears']} days in arrears on {worst['loan_no']}. "
            f"The group must come current before it borrows again.")


def performance(branch_id=None, as_of: date | None = None) -> list[dict]:
    """One row per group, worst first."""
    as_of = as_of or date.today()
    qs = BorrowerGroup.objects.exclude(status=GroupStatus.CLOSED).select_related("branch", "officer")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)

    rows = []
    for group in qs:
        state = standing(group, as_of)
        rows.append({
            "group_id": group.id,
            "group_no": group.group_no,
            "name": group.name,
            "branch": group.branch.name if group.branch else "-",
            "officer": group.officer.full_name if group.officer else "-",
            "status": group.status,
            "members": state["members"],
            "active_loans": state["active_loans"],
            "total_outstanding": state["total_outstanding"],
            "arrears_amount": state["arrears_amount"],
            "worst_days_in_arrears": state["worst_days_in_arrears"],
        })
    rows.sort(key=lambda r: (-r["worst_days_in_arrears"], -r["total_outstanding"]))
    return rows
