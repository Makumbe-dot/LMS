"""General ledger: chart of accounts, journal, trial balance and income statement."""
from datetime import date

from django.db import transaction
from django.db.models import Prefetch, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import JournalEntry, JournalLine, LedgerAccount
from ..permissions import IsAdmin
from ..serializers import (
    BalanceSheetSerializer,
    JournalEntrySerializer,
    LedgerAccountSerializer,
    ReconciliationSerializer,
)
from ..services import ledger as gl
from ..services.amortisation import add_months
from .helpers import table_response, wants_table, paginate, parse_date, parse_int


@api_view(["GET", "POST"])
def accounts(request):
    if request.method == "GET":
        qs = LedgerAccount.objects.order_by("code")
        if request.query_params.get("type"):
            qs = qs.filter(type=request.query_params["type"])
        return Response(LedgerAccountSerializer(qs, many=True).data)

    if not IsAdmin().has_permission(request, None):
        return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)
    if LedgerAccount.objects.filter(code=request.data.get("code")).exists():
        raise BusinessRuleError("An account with this code already exists")
    body = LedgerAccountSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        account = body.save()
        audit(request.user, "create", "ledger_account", account.id, account.code)
    return Response(LedgerAccountSerializer(account).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAdmin])
def account_detail(request, account_id: int):
    account = LedgerAccount.objects.filter(pk=account_id).first()
    if account is None:
        raise NotFound("Account not found")
    body = LedgerAccountSerializer(account, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "ledger_account", account.id,
              str(list(body.validated_data.keys())))
    return Response(LedgerAccountSerializer(account).data)


@api_view(["GET"])
def journal(request):
    """The journal, newest first, with each entry's lines."""
    qs = (JournalEntry.objects
          .select_related("loan", "branch", "posted_by")
          .prefetch_related(Prefetch("lines", queryset=JournalLine.objects.select_related("account")))
          .order_by("-id"))

    start = parse_date(request, "start")
    if start:
        qs = qs.filter(entry_date__gte=start)
    end = parse_date(request, "end")
    if end:
        qs = qs.filter(entry_date__lte=end)
    branch_id = parse_int(request, "branch_id")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    loan_id = parse_int(request, "loan_id")
    if loan_id:
        qs = qs.filter(loan_id=loan_id)
    source = request.query_params.get("source")
    if source:
        qs = qs.filter(source=source)
    account_code = request.query_params.get("account")
    if account_code:
        qs = qs.filter(lines__account__code=account_code).distinct()
    term = request.query_params.get("q")
    if term:
        qs = qs.filter(Q(narration__icontains=term) | Q(entry_no__icontains=term)
                       | Q(loan__loan_no__icontains=term))

    if wants_table(request):
        rows = []
        for entry in qs[:2000]:
            for line in entry.lines.all():
                rows.append({
                    "entry_no": entry.entry_no, "date": entry.entry_date,
                    "narration": entry.narration, "source": entry.source,
                    "loan_no": entry.loan.loan_no if entry.loan_id else "",
                    "account_code": line.account.code, "account_name": line.account.name,
                    "debit": line.debit, "credit": line.credit,
                })
        return table_response(request, rows, "journal")

    return Response(paginate(request, qs, JournalEntrySerializer, default_size=25))


@api_view(["GET"])
def trial_balance(request):
    start = parse_date(request, "start")
    end = parse_date(request, "end")
    data = gl.trial_balance(start, end, parse_int(request, "branch_id"))
    if wants_table(request):
        return table_response(request, data["rows"], "trial_balance")
    return Response(data)


@api_view(["GET"])
def income_statement(request):
    start = parse_date(request, "start", date.today().replace(month=1, day=1))
    end = parse_date(request, "end", date.today())
    if end < start:
        raise BusinessRuleError("end must be on or after start")
    data = gl.income_statement(start, end, parse_int(request, "branch_id"))
    if wants_table(request):
        return table_response(request, data["income"] + data["expense"], "income_statement")
    return Response(data)


@api_view(["GET"])
def balance_sheet(request):
    """Assets, liabilities and equity as at one date.

    A branch slice is a sub-book, not the institution's balance sheet: capital and
    facility entries carry no branch, so they are absent from it. The response says
    which branch it covers so the page can label it honestly.
    """
    data = gl.balance_sheet(parse_date(request, "as_of", date.today()),
                            parse_int(request, "branch_id"))
    if wants_table(request):
        return table_response(request, data["assets"] + data["liabilities"] + data["equity"],
                            "balance_sheet")
    # Through a serializer so money renders as a decimal string; a bare dict of
    # Decimals comes out as JSON floats and loses cents.
    return Response(BalanceSheetSerializer(data).data)


@api_view(["GET"])
def reconciliation(request):
    """Every ledger account that claims to equal a sub-ledger, checked against it.

    This, not the balance sheet's `balanced` flag, is the report to read before
    trusting a set of numbers: a balanced sheet follows from balanced entries, while
    these identities can genuinely break.
    """
    data = gl.reconciliation(parse_date(request, "as_of"))
    if wants_table(request):
        return table_response(request, data["rows"], "reconciliation")
    return Response(ReconciliationSerializer(data).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def rebuild(request):
    """Create the default chart of accounts and post any transaction missing an entry.

    Used when the ledger is switched on over a book that already has history.
    """
    with transaction.atomic():
        created = gl.ensure_chart_of_accounts()
        result = gl.backfill()
        audit(request.user, "rebuild_ledger", "system", None,
              f"{created} account(s) created, {result['posted']} entry/entries posted")
    return Response({"accounts_created": created, **result})
