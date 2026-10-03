"""The risk register.

Every risk has an owner, and the owner maintains it: re-rates it, records its
controls and actions, and reviews it on a schedule. Administrators oversee the
whole register — they hand risks to owners, and they close and reopen them.

Ratings are likelihood times impact on a 5 x 5 grid, banded the way most
registers band them:

    score   1-4 low    5-9 medium    10-16 high    20-25 critical

A product of two numbers from 1 to 5 is never 17, 18 or 19, so the bands have no
gap in practice.

The residual rating changes only through a review. That is what gives every
change of rating a date, a person and a reason, and what lets the history answer
"is this risk getting better or worse".
"""
from datetime import date

from django.db.models import Count, Q
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import Risk, RiskReview, RiskStatus, Role, User
from .amortisation import add_months
from .loans import next_number

REVIEW_INTERVALS = (1, 3, 6, 12)

# (band, lowest score in it), worst first.
BANDS = (("critical", 20), ("high", 10), ("medium", 5), ("low", 1))

# What an owner may change with an edit. The residual rating is deliberately not
# here: it moves only through record_review.
EDITABLE = frozenset({
    "title", "description", "category", "branch", "owner", "inherent_likelihood",
    "inherent_impact", "controls", "treatment", "action_plan", "action_due",
    "review_every_months",
})

# What a review may update alongside the rating, since re-rating a risk usually
# means saying what changed in the controls or the plan.
REVIEW_UPDATABLE = frozenset({"controls", "action_plan", "action_due"})


def rating(likelihood: int, impact: int) -> str:
    score = likelihood * impact
    for band, floor in BANDS:
        if score >= floor:
            return band
    return "low"


def score_range(band: str) -> tuple[int, int]:
    """(lowest, highest) score in a band, for filtering in SQL."""
    names = [name for name, _ in BANDS]
    if band not in names:
        raise BusinessRuleError(f"rating must be one of: {', '.join(names)}")
    index = names.index(band)
    low = BANDS[index][1]
    high = 25 if index == 0 else BANDS[index - 1][1] - 1
    return low, high


def may_edit(user: User | None, risk: Risk) -> bool:
    """The owner and administrators maintain a risk; nobody else does.

    Deliberately not a role check. A teller who owns "cash shortages at the
    counter" maintains that risk, and a loan officer who does not own it cannot.
    Being made accountable for a risk is what grants the right to keep it current.
    """
    if not (user and user.is_authenticated):
        return False
    if user.has_role(Role.ADMIN):
        return True
    return risk.owner_id is not None and risk.owner_id == user.id


def _check_ratings(inherent_likelihood: int, inherent_impact: int,
                   residual_likelihood: int, residual_impact: int) -> None:
    for label, value in (("Inherent likelihood", inherent_likelihood),
                         ("Inherent impact", inherent_impact),
                         ("Residual likelihood", residual_likelihood),
                         ("Residual impact", residual_impact)):
        if not 1 <= value <= 5:
            raise BusinessRuleError(f"{label} must be between 1 and 5")
    # Controls reduce a risk; they cannot make it more likely or worse. A residual
    # rating above the inherent one means one of the two was set wrongly.
    if residual_likelihood > inherent_likelihood:
        raise BusinessRuleError(
            f"Residual likelihood ({residual_likelihood}) cannot be higher than inherent "
            f"likelihood ({inherent_likelihood}): controls do not make a risk more likely")
    if residual_impact > inherent_impact:
        raise BusinessRuleError(
            f"Residual impact ({residual_impact}) cannot be higher than inherent impact "
            f"({inherent_impact}): controls do not make a risk worse")


def _check_interval(months: int) -> None:
    if months not in REVIEW_INTERVALS:
        raise BusinessRuleError(
            f"Review every {', '.join(map(str, REVIEW_INTERVALS[:-1]))} or "
            f"{REVIEW_INTERVALS[-1]} months")


def _check_owner(owner: User | None) -> None:
    if owner is None:
        raise BusinessRuleError("A risk needs an owner")
    if not owner.is_active:
        raise BusinessRuleError(
            f"{owner.full_name}'s account is disabled; choose an active owner")


def _require_open(risk: Risk) -> None:
    if risk.status != RiskStatus.OPEN:
        raise BusinessRuleError(f"{risk.risk_no} is closed. Reopen it before changing it.")


def raise_risk(*, title: str, owner: User, raised_by: User, inherent_likelihood: int,
               inherent_impact: int, residual_likelihood: int, residual_impact: int,
               review_every_months: int = 3, today: date | None = None, **fields) -> Risk:
    """Add a risk to the register, with its first assessment in the history."""
    today = today or date.today()
    _check_owner(owner)
    _check_ratings(inherent_likelihood, inherent_impact, residual_likelihood, residual_impact)
    _check_interval(review_every_months)
    next_review = add_months(today, review_every_months)

    risk = Risk.objects.create(
        risk_no=next_number("RSK", width=5), title=title, owner=owner, raised_by=raised_by,
        inherent_likelihood=inherent_likelihood, inherent_impact=inherent_impact,
        residual_likelihood=residual_likelihood, residual_impact=residual_impact,
        review_every_months=review_every_months, next_review_on=next_review,
        last_reviewed_on=today, **fields)
    RiskReview.objects.create(
        risk=risk, reviewed_by=raised_by, reviewed_on=today,
        residual_likelihood=residual_likelihood, residual_impact=residual_impact,
        note="Raised: initial assessment", next_review_on=next_review)
    return risk


def update_risk(risk: Risk, **changes) -> list[str]:
    """Apply an edit and return the names of the fields that actually changed."""
    unknown = set(changes) - EDITABLE
    if unknown:
        raise BusinessRuleError(f"Cannot change {', '.join(sorted(unknown))} with an edit")
    _require_open(risk)
    # Only a NEW owner is checked: an edit that leaves a disabled owner in place
    # must still save, or the risk could not be touched until someone reassigned it.
    if "owner" in changes and getattr(changes["owner"], "id", None) != risk.owner_id:
        _check_owner(changes["owner"])
    if "review_every_months" in changes:
        _check_interval(changes["review_every_months"])
    _check_ratings(changes.get("inherent_likelihood", risk.inherent_likelihood),
                   changes.get("inherent_impact", risk.inherent_impact),
                   risk.residual_likelihood, risk.residual_impact)

    changed = []
    for field, value in changes.items():
        if getattr(risk, field) != value:
            setattr(risk, field, value)
            changed.append(field)

    # A shorter interval has to bring the next review forward, or moving a risk
    # from yearly to monthly review would change nothing until next year.
    if "review_every_months" in changed:
        risk.next_review_on = add_months(risk.last_reviewed_on or date.today(),
                                         risk.review_every_months)
        changed.append("next_review_on")

    if changed:
        risk.updated_at = timezone.now()
        risk.save(update_fields=changed + ["updated_at"])
    return changed


def record_review(risk: Risk, reviewer: User, *, residual_likelihood: int,
                  residual_impact: int, note: str, reviewed_on: date | None = None,
                  next_review_on: date | None = None, today: date | None = None,
                  **updates) -> RiskReview:
    """Re-rate a risk, optionally update its controls and plan, and set the next review."""
    unknown = set(updates) - REVIEW_UPDATABLE
    if unknown:
        raise BusinessRuleError(f"A review cannot change {', '.join(sorted(unknown))}")
    _require_open(risk)
    today = today or date.today()
    reviewed_on = reviewed_on or today
    if reviewed_on > today:
        raise BusinessRuleError("A review cannot be dated in the future")
    # The history is read in date order; one dated before the last would make
    # last_reviewed_on go backwards and the trend tell the story out of sequence.
    if risk.last_reviewed_on and reviewed_on < risk.last_reviewed_on:
        raise BusinessRuleError(
            f"A review cannot be dated before the last one, on {risk.last_reviewed_on}")
    _check_ratings(risk.inherent_likelihood, risk.inherent_impact,
                   residual_likelihood, residual_impact)
    next_review_on = next_review_on or add_months(reviewed_on, risk.review_every_months)
    if next_review_on <= reviewed_on:
        raise BusinessRuleError("The next review must be after this one")

    for field, value in updates.items():
        setattr(risk, field, value)
    risk.residual_likelihood = residual_likelihood
    risk.residual_impact = residual_impact
    risk.last_reviewed_on = reviewed_on
    risk.next_review_on = next_review_on
    risk.updated_at = timezone.now()
    risk.save()

    return RiskReview.objects.create(
        risk=risk, reviewed_by=reviewer, reviewed_on=reviewed_on,
        residual_likelihood=residual_likelihood, residual_impact=residual_impact,
        note=note, next_review_on=next_review_on)


def close_risk(risk: Risk, reason: str, today: date | None = None) -> Risk:
    _require_open(risk)
    risk.status = RiskStatus.CLOSED
    risk.closed_on = today or date.today()
    risk.closed_reason = reason
    risk.updated_at = timezone.now()
    risk.save(update_fields=["status", "closed_on", "closed_reason", "updated_at"])
    return risk


def reopen_risk(risk: Risk, today: date | None = None) -> Risk:
    """Reopen a closed risk, due for review at once: its rating is stale by definition."""
    if risk.status == RiskStatus.OPEN:
        raise BusinessRuleError(f"{risk.risk_no} is already open")
    risk.status = RiskStatus.OPEN
    risk.closed_on = None
    risk.closed_reason = None
    risk.next_review_on = today or date.today()
    risk.updated_at = timezone.now()
    risk.save(update_fields=["status", "closed_on", "closed_reason", "next_review_on",
                             "updated_at"])
    return risk


def _cells(rows, prefix: str) -> list[dict]:
    return [{"likelihood": row[f"{prefix}_likelihood"], "impact": row[f"{prefix}_impact"],
             "count": row["count"]} for row in rows]


def summary(user: User, branch_id: int | None = None, today: date | None = None) -> dict:
    """The headline counts and both heat maps, in three queries whatever the size."""
    today = today or date.today()
    risks = Risk.objects.all()
    if branch_id:
        risks = risks.filter(branch_id=branch_id)
    open_ = Q(status=RiskStatus.OPEN)

    counts = risks.aggregate(
        open=Count("id", filter=open_),
        closed=Count("id", filter=Q(status=RiskStatus.CLOSED)),
        review_overdue=Count("id", filter=open_ & Q(next_review_on__lt=today)),
        action_overdue=Count("id", filter=open_ & Q(action_due__lt=today)),
        # A disabled owner is as good as none: nobody is keeping the risk current.
        unowned=Count("id", filter=open_ & (Q(owner__isnull=True) | Q(owner__is_active=False))),
        mine=Count("id", filter=open_ & Q(owner_id=user.id)),
        mine_overdue=Count("id", filter=open_ & Q(owner_id=user.id,
                                                   next_review_on__lt=today)),
    )

    residual = _cells(risks.filter(open_)
                      .values("residual_likelihood", "residual_impact")
                      .annotate(count=Count("id")).order_by(), "residual")
    inherent = _cells(risks.filter(open_)
                      .values("inherent_likelihood", "inherent_impact")
                      .annotate(count=Count("id")).order_by(), "inherent")

    by_rating = {band: 0 for band, _ in BANDS}
    for cell in residual:
        by_rating[rating(cell["likelihood"], cell["impact"])] += cell["count"]

    return {**counts, "by_rating": by_rating,
            "heatmap": {"residual": residual, "inherent": inherent}}


def export_rows(risks) -> list[dict]:
    """The register as CSV rows, readable without a key to the codes."""
    today = date.today()
    rows = []
    for risk in risks:
        rows.append({
            "risk_no": risk.risk_no,
            "title": risk.title,
            "category": risk.get_category_display(),
            "branch": risk.branch.name if risk.branch_id else "Organisation-wide",
            "owner": risk.owner.full_name if risk.owner_id else "",
            "inherent_likelihood": risk.inherent_likelihood,
            "inherent_impact": risk.inherent_impact,
            "inherent_score": risk.inherent_likelihood * risk.inherent_impact,
            "inherent_rating": rating(risk.inherent_likelihood, risk.inherent_impact),
            "controls": risk.controls or "",
            "residual_likelihood": risk.residual_likelihood,
            "residual_impact": risk.residual_impact,
            "residual_score": risk.residual_likelihood * risk.residual_impact,
            "residual_rating": rating(risk.residual_likelihood, risk.residual_impact),
            "treatment": risk.get_treatment_display(),
            "action_plan": risk.action_plan or "",
            "action_due": risk.action_due or "",
            "last_reviewed_on": risk.last_reviewed_on or "",
            "next_review_on": risk.next_review_on,
            "review_overdue": (risk.status == RiskStatus.OPEN
                               and risk.next_review_on < today),
            "status": risk.status,
        })
    return rows
