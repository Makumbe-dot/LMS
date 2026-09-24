"""Portfolio reports. Every listing report also serves CSV via ?fmt=csv."""
from datetime import date

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError
from ..models import AuditLog, Notification, NotificationStatus
from ..permissions import IsAdmin, IsOfficer, IsTeller
from ..serializers import (
    NOTIFICATION_STATUS_CHOICES,
    TXN_TYPE_CHOICES,
    AuditSerializer,
    BulkImportSerializer,
    DashboardSerializer,
    NotificationActionSerializer,
    NotificationSerializer,
)
from ..services import imports as imp
from ..services import notifications as notify
from ..services import reports as rpt
from ..services.amortisation import add_months
from ..services.penalties import accrue_penalties
from .helpers import csv_response, paginate, parse_date, parse_int


def _render(request, rows: list[dict], name: str):
    if request.query_params.get("fmt") == "csv":
        return csv_response(rows, name)
    return Response(rows)


@api_view(["GET"])
def dashboard(request):
    data = rpt.dashboard(parse_date(request, "as_of"), parse_int(request, "branch_id"))
    return Response(DashboardSerializer(data).data)


@api_view(["GET"])
def par(request):
    rows = rpt.portfolio_at_risk(parse_date(request, "as_of"), parse_int(request, "branch_id"))
    return _render(request, rows, "portfolio_at_risk")


@api_view(["GET"])
def collections_due(request):
    start = parse_date(request, "start", date.today().replace(day=1))
    end = parse_date(request, "end", add_months(start, 1))
    if end < start:
        raise BusinessRuleError("end must be on or after start")
    rows = rpt.collections_due(start, end)
    return _render(request, rows, "collections_due")


@api_view(["GET"])
def loan_book(request):
    return _render(request, rpt.loan_book(), "loan_book")


@api_view(["GET"])
def transactions(request):
    start = parse_date(request, "start", date.today().replace(day=1))
    end = parse_date(request, "end", date.today())
    if end < start:
        raise BusinessRuleError("end must be on or after start")
    txn_type = request.query_params.get("txn_type")
    if txn_type and txn_type not in TXN_TYPE_CHOICES:
        raise BusinessRuleError(f"Unknown transaction type '{txn_type}'")
    rows = rpt.transactions_report(start, end, txn_type)
    return _render(request, rows, "transactions")


@api_view(["POST"])
@permission_classes([IsOfficer])
def run_penalties(request):
    """End-of-day job: accrue late-payment penalties across all active loans."""
    as_of = parse_date(request, "as_of")
    with transaction.atomic():
        result = accrue_penalties(as_of)
        audit(request.user, "run_penalties", "system", None, str(result))
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAdmin])
def audit_log(request):
    qs = AuditLog.objects.select_related("user").order_by("-id")
    action = request.query_params.get("action")
    if action:
        qs = qs.filter(action__icontains=action)
    entity = request.query_params.get("entity")
    if entity:
        qs = qs.filter(entity=entity)
    user_id = parse_int(request, "user_id")
    if user_id:
        qs = qs.filter(user_id=user_id)
    term = request.query_params.get("q")
    if term:
        qs = qs.filter(Q(detail__icontains=term) | Q(action__icontains=term)
                       | Q(entity__icontains=term))
    start = parse_date(request, "start")
    if start:
        qs = qs.filter(created_at__date__gte=start)
    end = parse_date(request, "end")
    if end:
        qs = qs.filter(created_at__date__lte=end)

    if request.query_params.get("fmt") == "csv":
        rows = [{k: v for k, v in AuditSerializer(row).data.items()} for row in qs[:5000]]
        return csv_response(rows, "audit_log")
    return Response(paginate(request, qs, AuditSerializer, default_size=100))


# ---------------------------------------------------------------- IFRS 9
@api_view(["GET"])
def ecl(request):
    """IFRS 9 staging and expected credit loss provisioning."""
    data = rpt.ecl_report(parse_date(request, "as_of"), parse_int(request, "branch_id"))
    if request.query_params.get("fmt") == "csv":
        return csv_response(data["rows"], "ecl_provision")
    return Response(data)


# ---------------------------------------------------------------- performance
@api_view(["GET"])
def officer_performance(request):
    return _render(request, rpt.officer_performance(parse_int(request, "branch_id")),
                   "officer_performance")


@api_view(["GET"])
def product_performance(request):
    return _render(request, rpt.product_performance(parse_int(request, "branch_id")),
                   "product_performance")


@api_view(["GET"])
def branch_performance(request):
    return _render(request, rpt.branch_performance(), "branch_performance")


# ---------------------------------------------------------------- payroll
@api_view(["GET"])
def payroll(request):
    """The deduction schedule to send an employer for a pay period."""
    start = parse_date(request, "start", date.today().replace(day=1))
    end = parse_date(request, "end", add_months(start, 1))
    if end < start:
        raise BusinessRuleError("end must be on or after start")
    rows = rpt.payroll_deduction(start, end, request.query_params.get("employer"),
                                 parse_int(request, "branch_id"))
    name = "payroll_deduction"
    if request.query_params.get("employer"):
        slug = "".join(c if c.isalnum() else "_" for c in request.query_params["employer"])
        name = f"payroll_{slug}_{start:%Y%m}"
    return _render(request, rows, name)


@api_view(["GET"])
def employers(request):
    return Response(rpt.employers_with_active_loans(parse_int(request, "branch_id")))


# ---------------------------------------------------------------- notifications
@api_view(["GET"])
def notifications(request):
    qs = Notification.objects.select_related("borrower", "loan").order_by("-id")
    state = request.query_params.get("status")
    if state:
        if state not in NOTIFICATION_STATUS_CHOICES:
            raise BusinessRuleError(f"Unknown status '{state}'")
        qs = qs.filter(status=state)
    kind = request.query_params.get("kind")
    if kind:
        qs = qs.filter(kind=kind)
    term = request.query_params.get("q")
    if term:
        qs = qs.filter(Q(to_address__icontains=term) | Q(body__icontains=term)
                       | Q(borrower__first_name__icontains=term)
                       | Q(borrower__last_name__icontains=term))

    if request.query_params.get("fmt") == "csv":
        rows = [{"id": n.id, "scheduled_for": n.scheduled_for, "channel": n.channel,
                 "to": n.to_address, "borrower": n.borrower.full_name,
                 "loan_no": n.loan.loan_no if n.loan_id else "", "kind": n.kind,
                 "status": n.status, "message": n.body} for n in qs[:5000]]
        return csv_response(rows, "notifications")
    return Response(paginate(request, qs, NotificationSerializer, default_size=50))


@api_view(["POST"])
@permission_classes([IsOfficer])
def generate_notifications(request):
    """Queue instalment reminders and arrears notices across the active book."""
    as_of = parse_date(request, "as_of")
    with transaction.atomic():
        result = notify.generate_reminders(as_of)
        audit(request.user, "generate_notifications", "system", None, str(result))
    return Response(result)


@api_view(["POST"])
@permission_classes([IsOfficer])
def send_notifications(request):
    """Mark queued messages as sent. This is where a real SMS gateway plugs in."""
    body = NotificationActionSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        result = notify.mark_sent(body.validated_data.get("ids"),
                                  body.validated_data.get("as_of"))
        audit(request.user, "send_notifications", "system", None, str(result))
    return Response(result)


@api_view(["POST"])
@permission_classes([IsOfficer])
def cancel_notifications(request):
    body = NotificationActionSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    ids = body.validated_data.get("ids") or []
    if not ids:
        raise BusinessRuleError("Select at least one message to cancel")
    with transaction.atomic():
        result = notify.cancel(ids)
        audit(request.user, "cancel_notifications", "system", None, str(result))
    return Response(result)


# ---------------------------------------------------------------- bulk import
@api_view(["POST"])
@permission_classes([IsTeller])
@parser_classes([MultiPartParser, FormParser])
def bulk_repayments(request):
    """Validate, and optionally post, a CSV of repayments.

    Called first without `commit` to preview what would happen, then again with
    `commit=true` to post the batch.
    """
    body = BulkImportSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    upload = body.validated_data["file"]
    if upload.size > 5 * 1024 * 1024:
        raise BusinessRuleError("The file is larger than 5 MB; split it into smaller batches.")

    rows = imp.parse(upload.read())
    validation = imp.validate(rows)

    if not body.validated_data["commit"]:
        return Response({"committed": False, **validation})

    with transaction.atomic():
        result = imp.commit(validation, request.user, body.validated_data["allow_partial"])
        audit(request.user, "bulk_repayments", "system", None,
              f"{result['posted_rows']} postings totalling {result['total_amount']}")
    return Response({"committed": True, **validation, **result},
                    status=status.HTTP_201_CREATED)
