"""Teller tills: open with a float, count at close, verified by someone else."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import TillSession, TillStatus
from ..permissions import IsOfficer, IsTeller
from ..serializers import (
    NoteSerializer,
    TillCountSerializer,
    TillDetailSerializer,
    TillOpenSerializer,
    TillSessionSerializer,
)
from ..services import tills as svc
from .helpers import table_response, wants_table, paginate, parse_date, parse_int


def _till_or_404(till_id: int, *, for_update: bool = False) -> TillSession:
    qs = TillSession.objects.select_related("teller", "branch", "verified_by", "variance_entry")
    if for_update:
        qs = qs.select_for_update()
    till = qs.filter(pk=till_id).first()
    if till is None:
        raise NotFound("Till not found")
    return till


@api_view(["GET", "POST"])
def tills(request):
    if request.method == "POST":
        if not IsTeller().has_permission(request, None):
            return Response({"detail": IsTeller.message}, status=status.HTTP_403_FORBIDDEN)
        body = TillOpenSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with transaction.atomic():
            till = svc.open_till(request.user, body.validated_data["opening_float"])
            audit(request.user, "open_till", "till", till.id,
                  f"{till.session_no} with a float of {till.opening_float}")
        return Response(TillDetailSerializer(_till_or_404(till.id)).data,
                        status=status.HTTP_201_CREATED)

    qs = TillSession.objects.select_related("teller", "branch", "verified_by", "variance_entry")
    state = request.query_params.get("status")
    if state:
        if state not in TillStatus.values:
            raise BusinessRuleError(f"Unknown status '{state}'")
        qs = qs.filter(status=state)
    for param, field in (("teller_id", "teller_id"), ("branch_id", "branch_id")):
        value = parse_int(request, param)
        if value:
            qs = qs.filter(**{field: value})
    start = parse_date(request, "start")
    end = parse_date(request, "end")
    if start:
        qs = qs.filter(business_date__gte=start)
    if end:
        qs = qs.filter(business_date__lte=end)

    if wants_table(request):
        rows = [{
            "session_no": t.session_no, "teller": t.teller.full_name,
            "business_date": t.business_date, "status": t.status,
            "opening_float": t.opening_float, "cash_in": t.cash_in or "",
            "cash_out": t.cash_out or "", "expected_cash": t.expected_cash or "",
            "counted_cash": t.counted_cash or "", "variance": t.variance or "",
            "close_note": t.close_note or "",
            "verified_by": t.verified_by.full_name if t.verified_by_id else "",
        } for t in qs[:5000]]
        return table_response(request, rows, "tills")

    payload = paginate(request, qs, TillSessionSerializer, default_size=25)
    payload["awaiting_verification"] = TillSession.objects.filter(
        status=TillStatus.COUNTED).count()
    return Response(payload)


@api_view(["GET"])
def current(request):
    """The signed-in user's open till with its live position, under "till"; null if none.

    Wrapped rather than a bare null: DRF renders None as an empty body, which a
    client parsing JSON cannot read.
    """
    till = svc.current(request.user)
    return Response({"till": TillDetailSerializer(_till_or_404(till.id)).data if till else None})


@api_view(["GET"])
def till_detail(request, till_id: int):
    return Response(TillDetailSerializer(_till_or_404(till_id)).data)


@api_view(["POST"])
@permission_classes([IsTeller])
def count(request, till_id: int):
    body = TillCountSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        till = _till_or_404(till_id, for_update=True)
        svc.count(till, request.user, body.validated_data["counted_cash"],
                  body.validated_data.get("note"))
        audit(request.user, "count_till", "till", till.id,
              f"{till.session_no}: counted {till.counted_cash} against {till.expected_cash} "
              f"expected ({till.variance:+})")
    return Response(TillDetailSerializer(_till_or_404(till_id)).data)


@api_view(["POST"])
@permission_classes([IsOfficer])
def verify(request, till_id: int):
    body = NoteSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        till = _till_or_404(till_id, for_update=True)
        svc.verify(till, request.user, body.validated_data.get("note"))
        audit(request.user, "verify_till", "till", till.id,
              f"{till.session_no}: variance {till.variance}"
              + (f", posted as {till.variance_entry.entry_no}" if till.variance_entry_id else ""))
    return Response(TillDetailSerializer(_till_or_404(till_id)).data)
