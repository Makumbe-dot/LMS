"""Payroll returns: check an employer's deduction file against the schedule, then
post it. See services/payroll.py."""
from datetime import date

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..exceptions import BusinessRuleError, NotFound
from ..models import PayrollRun
from ..permissions import CanCash
from ..serializers import PayrollRunLineSerializer, PayrollRunSerializer
from ..services import payroll as svc
from .helpers import table_response, wants_table


def _form_date(request, key: str) -> date | None:
    raw = request.data.get(key)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise BusinessRuleError(f"{key} must be a date (YYYY-MM-DD)")


def _detail(run: PayrollRun, code=status.HTTP_200_OK) -> Response:
    lines = run.lines.select_related("loan").order_by("status", "name", "id")
    return Response({**PayrollRunSerializer(run).data, **svc.summary(run),
                     "lines": PayrollRunLineSerializer(lines, many=True).data}, status=code)


@api_view(["GET", "POST"])
def runs(request):
    """GET the returns checked so far; POST a new one as multipart: file, employer,
    start, end, received_on and an optional reference. Posts nothing."""
    if request.method == "GET":
        qs = PayrollRun.objects.select_related("created_by", "posted_by")
        employer = request.query_params.get("employer")
        if employer:
            qs = qs.filter(employer__iexact=employer)
        return Response(PayrollRunSerializer(qs[:200], many=True).data)

    if not CanCash().has_permission(request, None):
        return Response({"detail": CanCash.message}, status=status.HTTP_403_FORBIDDEN)
    upload = request.FILES.get("file")
    if upload is None:
        raise BusinessRuleError("Attach the employer's file as 'file'")
    if upload.size > 5 * 1024 * 1024:
        raise BusinessRuleError("The file is larger than 5 MB")
    start = _form_date(request, "start")
    end = _form_date(request, "end")
    received_on = _form_date(request, "received_on")
    if not (start and end and received_on):
        raise BusinessRuleError("Give the period's start and end and the day the money arrived")
    run = svc.check(request.data.get("employer"), start, end, received_on, upload.read(),
                    request.user, upload.name, request.data.get("reference") or None)
    return _detail(run, status.HTTP_201_CREATED)


@api_view(["GET", "DELETE"])
def run_detail(request, run_id: int):
    run = PayrollRun.objects.filter(pk=run_id).first()
    if run is None:
        raise NotFound("Payroll run not found")
    if request.method == "DELETE":
        if not CanCash().has_permission(request, None):
            return Response({"detail": CanCash.message}, status=status.HTTP_403_FORBIDDEN)
        svc.discard(run_id, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
    if wants_table(request):
        # The shortfall list to send back to the employer, or to chase.
        rows = [{"status": line.get_status_display(), "loan_no": line.loan.loan_no if line.loan
                 else "", "employee_no": line.employee_no or "", "name": line.name or "",
                 "expected": line.expected, "deducted": line.deducted,
                 "shortfall": line.shortfall, "note": line.note or ""}
                for line in run.lines.select_related("loan").order_by("status", "name")]
        return table_response(request, rows, f"payroll_return_{run.id}")
    return _detail(run)


@api_view(["POST"])
@permission_classes([CanCash])
def post_run(request, run_id: int):
    run = svc.post(run_id, request.user)
    return _detail(run)
