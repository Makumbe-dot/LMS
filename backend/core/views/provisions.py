"""Booking and reviewing the IFRS 9 expected credit loss provision."""
from django.db import transaction
from django.db.models import Prefetch
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import ProvisionRun, ProvisionRunLine, ProvisionRunStatus
from ..permissions import IsAdmin
from ..serializers import (
    NarrationSerializer,
    ProvisionPreviewSerializer,
    ProvisionRunDetailSerializer,
    ProvisionRunRequestSerializer,
    ProvisionRunSerializer,
)
from ..services import provisioning as svc
from .helpers import table_response, wants_table, paginate, parse_date


def _run_queryset():
    return (ProvisionRun.objects
            .select_related("journal_entry", "reversal_entry", "run_by", "reversed_by"))


@api_view(["GET"])
def provisions(request):
    """The run history, newest first."""
    qs = _run_queryset()
    state = request.query_params.get("status")
    if state:
        if state not in ProvisionRunStatus.values:
            raise BusinessRuleError(f"Unknown status '{state}'")
        qs = qs.filter(status=state)

    if wants_table(request):
        rows = [{
            "run_no": r.run_no, "period_end": r.period_end, "status": r.status,
            "loans_assessed": r.loans_assessed, "loans_released": r.loans_released,
            "total_carrying_amount": r.total_carrying_amount,
            "provision_required": r.provision_required,
            "provision_before": r.provision_before, "movement": r.movement,
            "entry_no": r.journal_entry.entry_no if r.journal_entry_id else "",
            "run_by": r.run_by.full_name if r.run_by_id else "",
            "created_at": r.created_at,
        } for r in qs[:5000]]
        return table_response(request, rows, "provision_runs")

    return Response(paginate(request, qs, ProvisionRunSerializer, default_size=25))


@api_view(["GET"])
def preview(request):
    """What a run for this period would post. Posts nothing."""
    return Response(ProvisionPreviewSerializer(svc.preview(parse_date(request, "as_of"))).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def run(request):
    """Book the month's movement in the provision."""
    body = ProvisionRunRequestSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    data = body.validated_data

    with transaction.atomic():
        provision_run = svc.run_provision(request.user, data.get("as_of"),
                                          narration=data.get("narration"),
                                          force=data["force"])
        if getattr(provision_run, "created_now", False):
            audit(request.user, "provision_run", "provision_run", provision_run.id,
                  f"{provision_run.run_no} {provision_run.period_end}: movement "
                  f"{provision_run.movement} on a required provision of "
                  f"{provision_run.provision_required}")

    payload = ProvisionRunDetailSerializer(provision_run).data
    payload["created"] = getattr(provision_run, "created_now", False)
    return Response(payload, status=status.HTTP_201_CREATED if payload["created"]
                    else status.HTTP_200_OK)


@api_view(["GET"])
def provision_detail(request, run_id: int):
    provision_run = (_run_queryset()
                     .prefetch_related(Prefetch(
                         "lines",
                         queryset=ProvisionRunLine.objects.select_related(
                             "loan", "loan__borrower", "loan__product")))
                     .filter(pk=run_id).first())
    if provision_run is None:
        raise NotFound("Provision run not found")

    if wants_table(request):
        rows = [{
            "run_no": provision_run.run_no, "loan_no": line.loan.loan_no,
            "borrower": line.loan.borrower.full_name, "loan_status": line.loan_status,
            "stage": line.stage or "", "days_past_due": line.days_past_due,
            "exposure": line.exposure, "carrying_amount": line.carrying_amount,
            "rate_pct": line.rate_pct, "provision_required": line.provision_required,
            "provision_before": line.provision_before, "movement": line.movement,
        } for line in provision_run.lines.all()]
        return table_response(request, rows, f"provision_run_{provision_run.run_no}")

    return Response(ProvisionRunDetailSerializer(provision_run).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def reverse(request, run_id: int):
    body = NarrationSerializer(data=request.data)
    body.is_valid(raise_exception=True)

    with transaction.atomic():
        provision_run = _run_queryset().filter(pk=run_id).first()
        if provision_run is None:
            raise NotFound("Provision run not found")
        svc.reverse_run(provision_run, request.user, body.validated_data["narration"])
        audit(request.user, "provision_reverse", "provision_run", provision_run.id,
              f"{provision_run.run_no}: {body.validated_data['narration']}")

    return Response(ProvisionRunDetailSerializer(provision_run).data)
