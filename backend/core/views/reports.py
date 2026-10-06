"""Portfolio reports. Every listing report also serves CSV via ?fmt=csv."""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError
from ..models import SECRET_KINDS, AuditLog, Notification, NotificationStatus
from ..permissions import CanCash, CanMessages, CanSupervise, IsAdmin
from ..serializers import (
    NOTIFICATION_STATUS_CHOICES,
    TXN_TYPE_CHOICES,
    AuditSerializer,
    BulkImportSerializer,
    DashboardSerializer,
    LoanBookImportSerializer,
    NotificationActionSerializer,
    NotificationSerializer,
)
from ..services import imports as imp
from ..services import loanbook
from ..services import notifications as notify
from ..services import reports as rpt
from ..services.amortisation import add_months
from ..services.penalties import accrue_penalties
from .helpers import table_response, wants_table, paginate, paginate_list, parse_date, parse_int


def _render(request, rows: list[dict], name: str):
    if wants_table(request):
        return table_response(request, rows, name)
    return Response(rows)


@api_view(["GET"])
def dashboard(request):
    data = rpt.dashboard(parse_date(request, "as_of"), parse_int(request, "branch_id"))
    return Response(DashboardSerializer(data).data)


@api_view(["GET"])
def par(request):
    """Loans in arrears, worst first.

    Paginated when `page` is asked for, and whole otherwise, so the CSV export and
    any existing caller keep getting the entire book. The totals travel with the
    page: summing one page's rows in the browser would silently under-report how
    much is overdue.
    """
    as_of = parse_date(request, "as_of")
    rows = rpt.portfolio_at_risk(as_of, parse_int(request, "branch_id"))
    if wants_table(request):
        return table_response(request, rows, "portfolio_at_risk")
    if "page" not in request.query_params:
        return Response(rows)
    return Response(paginate_list(request, rows, extra={
        "arrears_total": sum((r["arrears_amount"] for r in rows), Decimal("0")),
        "principal_total": sum((r["principal_outstanding"] for r in rows), Decimal("0")),
    }))


@api_view(["GET"])
def arrears_ageing(request):
    rows = rpt.arrears_ageing(parse_date(request, "as_of"), parse_int(request, "branch_id"))
    return _render(request, rows, "arrears_ageing")


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
@permission_classes([CanSupervise])
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

    if wants_table(request):
        rows = [{k: v for k, v in AuditSerializer(row).data.items()} for row in qs[:5000]]
        return table_response(request, rows, "audit_log")
    return Response(paginate(request, qs, AuditSerializer, default_size=100))


# ---------------------------------------------------------------- IFRS 9
@api_view(["GET"])
def ecl(request):
    """IFRS 9 staging and expected credit loss provisioning."""
    data = rpt.ecl_report(parse_date(request, "as_of"), parse_int(request, "branch_id"))
    if wants_table(request):
        return table_response(request, data["rows"], "ecl_provision")
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
        # A code's text is never searchable: searching digits would find it.
        qs = qs.filter(Q(to_address__icontains=term)
                       | (Q(body__icontains=term) & ~Q(kind__in=SECRET_KINDS))
                       | Q(borrower__first_name__icontains=term)
                       | Q(borrower__last_name__icontains=term))

    if wants_table(request):
        rows = [{"id": n.id, "scheduled_for": n.scheduled_for, "channel": n.channel,
                 "to": n.to_address, "borrower": n.borrower.full_name,
                 "loan_no": n.loan.loan_no if n.loan_id else "", "kind": n.kind,
                 "status": n.status, "message": n.shown_body} for n in qs[:5000]]
        return table_response(request, rows, "notifications")
    return Response(paginate(request, qs, NotificationSerializer, default_size=50))


@api_view(["POST"])
@permission_classes([CanMessages])
def generate_notifications(request):
    """Queue instalment reminders and arrears notices across the active book."""
    as_of = parse_date(request, "as_of")
    with transaction.atomic():
        result = notify.generate_reminders(as_of)
        audit(request.user, "generate_notifications", "system", None, str(result))
    return Response(result)


@api_view(["POST"])
@permission_classes([CanMessages])
def send_notifications(request):
    """Deliver queued messages through the configured gateway.

    Not wrapped in one transaction: each message records its own outcome, and a
    rollback on the last of two hundred would lose the record of the first
    hundred and ninety-nine that genuinely went out.
    """
    body = NotificationActionSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    result = notify.send(body.validated_data.get("ids"), body.validated_data.get("as_of"))
    audit(request.user, "send_notifications", "system", None,
          str({k: v for k, v in result.items() if k != "gateway"}))
    return Response(result)


@api_view(["POST"])
@permission_classes([CanMessages])
def mark_notifications_sent(request):
    """Mark messages sent WITHOUT delivering them.

    For the operator who exported the queue and sent it through an aggregator's
    own console: the outbox still has to be reconciled afterwards. Recorded as
    not-delivered-from-here so the audit trail does not claim otherwise.
    """
    body = NotificationActionSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        result = notify.mark_sent(body.validated_data.get("ids"),
                                  body.validated_data.get("as_of"))
        audit(request.user, "mark_notifications_sent", "system", None,
              f"{result['marked_sent']} message(s) marked sent by hand, not delivered")
    return Response(result)


@api_view(["GET"])
def message_gateway(request):
    """What would happen to a message right now.

    "Is this actually going anywhere?" is the first question anyone asks about an
    outbox, and for most of this system's life the answer was no.
    """
    from ..services import gateways

    return Response(gateways.describe())


@api_view(["POST"])
@permission_classes([CanMessages])
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
@permission_classes([CanCash])
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


@api_view(["GET"])
def spreadsheet(request, kind: str):
    """The member register and the other keepable listings: JSON, ?fmt=csv or ?fmt=xlsx.

    kind is one of services.spreadsheets.SPREADSHEETS: members, group-membership,
    savings-balances, loans-outstanding, overdue. ?branch_id= narrows it.
    """
    from ..exports import Sheet, xlsx_response
    from ..models import OrganisationSetting
    from ..services import spreadsheets as sheets

    if kind not in sheets.SPREADSHEETS:
        raise BusinessRuleError(f"Unknown spreadsheet '{kind}'")
    file_name, title, build = sheets.SPREADSHEETS[kind]
    rows = build(branch_id=parse_int(request, "branch_id"))
    fmt = request.query_params.get("fmt")
    if fmt == "xlsx":
        org = OrganisationSetting.load()
        note = [f"{org.name} · as at {date.today().isoformat()} · amounts in {org.currency}"]
        return xlsx_response([Sheet(title, rows, title, note)], file_name)
    if fmt == "csv":
        return table_response(request, rows, file_name)
    # JSON turns Decimal into a float, so say which columns are money for the screen.
    money = sorted({k for row in rows for k, v in row.items() if isinstance(v, Decimal)})
    return Response(paginate_list(request, rows, default_size=100,
                                  extra={"title": title, "money_columns": money}))


@api_view(["GET"])
def workbook(request):
    """Everything in one Excel file: summary, members, loans outstanding, overdue,
    savings balances and group membership, one sheet each."""
    from ..exports import xlsx_response
    from ..services.spreadsheets import portfolio_workbook

    branch_id = parse_int(request, "branch_id")
    name = f"portfolio_{date.today().isoformat()}" + (f"_branch_{branch_id}" if branch_id else "")
    return xlsx_response(portfolio_workbook(branch_id), name)


@api_view(["GET", "POST"])
@permission_classes([IsAdmin])
@parser_classes([MultiPartParser, FormParser])
def loan_book_import(request):
    """Bring running loans over from another system: GET the template, POST the file.

    POSTed without `commit` it checks every row and reports what would be brought
    over; with `commit=true` it imports the lot, or nothing.
    """
    if request.method == "GET":
        from django.http import HttpResponse

        response = HttpResponse(loanbook.TEMPLATE, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="loan_book_template.csv"'
        return response

    body = LoanBookImportSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    upload = body.validated_data["file"]
    if upload.size > 5 * 1024 * 1024:
        raise BusinessRuleError("The file is larger than 5 MB; split it into smaller batches.")
    cutover = body.validated_data.get("cutover_date") or date.today()

    rows = loanbook.parse(upload.read())
    validation = loanbook.validate(rows, cutover)
    if not body.validated_data["commit"]:
        return Response({"committed": False, **validation})

    result = loanbook.commit(rows, validation, request.user, cutover)
    audit(request.user, "loan_book_import", "system", None,
          f"{result['imported_rows']} loans brought over at {cutover.isoformat()}, principal "
          f"{result['principal_outstanding']}")
    return Response({"committed": True, **validation, **result}, status=status.HTTP_201_CREATED)
