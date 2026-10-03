"""The risk register: risks, their owners, and the reviews that keep them current.

Everyone signed in can read the register. Raising a risk needs a working role
(admin, loan officer or teller); maintaining one needs to OWN it, or to be an
administrator. Handing a risk to someone else, closing it and reopening it are
administrators' decisions.
"""
from datetime import date

from django.db import transaction
from django.db.models import F, Prefetch, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import Risk, RiskReview, RiskStatus, Role
from ..permissions import IsAdmin, IsTeller
from ..serializers import (
    RiskCreateSerializer,
    RiskDetailSerializer,
    RiskReasonSerializer,
    RiskReviewRequestSerializer,
    RiskSerializer,
    RiskUpdateSerializer,
)
from ..services import risks as svc
from .helpers import csv_response, paginate, parse_int

NOT_YOURS = "Only the risk's owner or an administrator can change it"


def risk_queryset():
    return (Risk.objects
            .select_related("owner", "branch", "raised_by")
            .annotate(sort_score=F("residual_likelihood") * F("residual_impact")))


def get_risk_or_404(risk_id, with_reviews: bool = False) -> Risk:
    qs = risk_queryset()
    if with_reviews:
        qs = qs.prefetch_related(Prefetch(
            "reviews", queryset=RiskReview.objects.select_related("reviewed_by")))
    risk = qs.filter(pk=risk_id).first()
    if risk is None:
        raise NotFound("Risk not found")
    return risk


def _for(request):
    """Attach can_edit for the signed-in user, so the client never has to guess it."""
    def attach(risk: Risk) -> Risk:
        risk.can_edit = svc.may_edit(request.user, risk)
        return risk
    return attach


def _detail(request, risk_id: int) -> dict:
    return RiskDetailSerializer(_for(request)(get_risk_or_404(risk_id, with_reviews=True))).data


def _filtered(request):
    params = request.query_params
    qs = risk_queryset()

    state = params.get("status") or RiskStatus.OPEN
    if state != "all":
        if state not in RiskStatus.values:
            raise BusinessRuleError("status must be open, closed or all")
        qs = qs.filter(status=state)

    term = params.get("q")
    if term:
        qs = qs.filter(Q(title__icontains=term) | Q(risk_no__icontains=term)
                       | Q(description__icontains=term))
    if params.get("category"):
        qs = qs.filter(category=params["category"])
    branch_id = parse_int(request, "branch_id")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)

    owner = params.get("owner")
    if owner == "me":
        qs = qs.filter(owner_id=request.user.id)
    elif owner == "none":
        qs = qs.filter(Q(owner__isnull=True) | Q(owner__is_active=False))
    elif owner:
        qs = qs.filter(owner_id=parse_int(request, "owner"))

    if params.get("rating"):
        low, high = svc.score_range(params["rating"])
        qs = qs.filter(sort_score__gte=low, sort_score__lte=high)
    if params.get("overdue") in ("1", "true"):
        qs = qs.filter(next_review_on__lt=date.today())

    # One cell of a heat map: both axes, on whichever rating the map was showing.
    basis = params.get("basis") or "residual"
    if basis not in ("residual", "inherent"):
        raise BusinessRuleError("basis must be residual or inherent")
    likelihood, impact = parse_int(request, "likelihood"), parse_int(request, "impact")
    if likelihood:
        qs = qs.filter(**{f"{basis}_likelihood": likelihood})
    if impact:
        qs = qs.filter(**{f"{basis}_impact": impact})

    # Worst first; among equals, the one whose review falls due soonest.
    return qs.order_by("-sort_score", "next_review_on", "id")


@api_view(["GET", "POST"])
def risks(request):
    if request.method == "GET":
        qs = _filtered(request)
        if request.query_params.get("fmt") == "csv":
            return csv_response(svc.export_rows(qs), "risk_register")
        return Response(paginate(request, qs, RiskSerializer, transform=_for(request)))

    if not IsTeller().has_permission(request, None):
        return Response({"detail": IsTeller.message}, status=status.HTTP_403_FORBIDDEN)
    body = RiskCreateSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    data = dict(body.validated_data)
    owner = data.pop("owner", None) or request.user
    if owner.id != request.user.id and not request.user.has_role(Role.ADMIN):
        return Response({"detail": "Only an administrator can raise a risk for someone else"},
                        status=status.HTTP_403_FORBIDDEN)
    with transaction.atomic():
        risk = svc.raise_risk(owner=owner, raised_by=request.user, **data)
        audit(request.user, "create", "risk", risk.id,
              f"{risk.risk_no} {risk.title} (owner {owner.username})")
    return Response(_detail(request, risk.id), status=status.HTTP_201_CREATED)


@api_view(["GET"])
def summary(request):
    return Response(svc.summary(request.user, parse_int(request, "branch_id")))


@api_view(["GET", "PATCH"])
def risk_detail(request, risk_id: int):
    risk = get_risk_or_404(risk_id)
    if request.method == "GET":
        return Response(_detail(request, risk_id))

    if not svc.may_edit(request.user, risk):
        return Response({"detail": NOT_YOURS}, status=status.HTTP_403_FORBIDDEN)
    # Refused out loud rather than ignored: a client that sent a new rating would
    # otherwise believe it had been saved.
    if {"residual_likelihood", "residual_impact"} & set(request.data):
        raise BusinessRuleError(
            "Change the residual rating by recording a review, so the change has a date "
            "and a reason")
    body = RiskUpdateSerializer(data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    changes = dict(body.validated_data)

    previous_owner = risk.owner
    if ("owner" in changes and changes["owner"].id != risk.owner_id
            and not request.user.has_role(Role.ADMIN)):
        return Response({"detail": "Only an administrator can hand a risk to someone else"},
                        status=status.HTTP_403_FORBIDDEN)

    with transaction.atomic():
        changed = svc.update_risk(risk, **changes)
        if "owner" in changed:
            audit(request.user, "reassign", "risk", risk.id,
                  f"{risk.risk_no} from "
                  f"{previous_owner.username if previous_owner else 'nobody'} "
                  f"to {risk.owner.username}")
        if set(changed) - {"owner"}:
            audit(request.user, "update", "risk", risk.id,
                  f"{risk.risk_no}: {', '.join(f for f in changed if f != 'owner')}")
    return Response(_detail(request, risk_id))


@api_view(["POST"])
def reviews(request, risk_id: int):
    risk = get_risk_or_404(risk_id)
    if not svc.may_edit(request.user, risk):
        return Response({"detail": "Only the risk's owner or an administrator can review it"},
                        status=status.HTTP_403_FORBIDDEN)
    body = RiskReviewRequestSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        review = svc.record_review(risk, request.user, **body.validated_data)
        audit(request.user, "review", "risk", risk.id,
              f"{risk.risk_no} rated {review.residual_likelihood}x{review.residual_impact} "
              f"({svc.rating(review.residual_likelihood, review.residual_impact)}), "
              f"next review {review.next_review_on}")
    return Response(_detail(request, risk_id), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAdmin])
def close(request, risk_id: int):
    body = RiskReasonSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    risk = get_risk_or_404(risk_id)
    with transaction.atomic():
        svc.close_risk(risk, body.validated_data["reason"])
        audit(request.user, "close", "risk", risk.id,
              f"{risk.risk_no}: {body.validated_data['reason']}")
    return Response(_detail(request, risk_id))


@api_view(["POST"])
@permission_classes([IsAdmin])
def reopen(request, risk_id: int):
    body = RiskReasonSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    risk = get_risk_or_404(risk_id)
    with transaction.atomic():
        svc.reopen_risk(risk)
        audit(request.user, "reopen", "risk", risk.id,
              f"{risk.risk_no}: {body.validated_data['reason']}")
    return Response(_detail(request, risk_id))
