"""Bank and mobile-money reconciliation: statements matched against the ledger."""
from django.db import transaction
from django.db.models import Prefetch
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import BankStatement, StatementLine
from ..permissions import CanAccounting
from ..serializers import (
    BankStatementDetailSerializer,
    BankStatementSerializer,
    ManualJournalSerializer,
    ReasonSerializer,
    StatementEntrySerializer,
    StatementJournalSerializer,
    StatementMatchSerializer,
    StatementUploadSerializer,
)
from ..services import bankrec as svc
from .helpers import paginate


def _statement_or_404(statement_id: int) -> BankStatement:
    statement = (BankStatement.objects.select_related("uploaded_by")
                 .prefetch_related(Prefetch(
                     "lines", queryset=StatementLine.objects
                     .select_related("journal_entry", "matched_by").order_by("line_no")))
                 .filter(pk=statement_id).first())
    if statement is None:
        raise NotFound("Statement not found")
    return statement


def _line_or_404(line_id: int, *, for_update: bool = False) -> StatementLine:
    qs = StatementLine.objects.select_related("statement")
    if for_update:
        qs = qs.select_for_update()
    line = qs.filter(pk=line_id).first()
    if line is None:
        raise NotFound("Statement line not found")
    return line


def _detail(statement_id: int, code=status.HTTP_200_OK) -> Response:
    return Response(BankStatementDetailSerializer(_statement_or_404(statement_id)).data,
                    status=code)


@api_view(["GET", "POST"])
@parser_classes([MultiPartParser, FormParser, JSONParser])
def statements(request):
    if request.method == "POST":
        if not CanAccounting().has_permission(request, None):
            return Response({"detail": CanAccounting.message}, status=status.HTTP_403_FORBIDDEN)
        body = StatementUploadSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        upload = data["file"]
        if upload.size > 5 * 1024 * 1024:
            raise BusinessRuleError("The file is larger than 5 MB; split it by month.")
        rows = svc.parse(upload.read())
        with transaction.atomic():
            statement = svc.create_statement(
                request.user, rows, account_name=data["account_name"], channel=data["channel"],
                file_name=upload.name, opening_balance=data.get("opening_balance"),
                closing_balance=data.get("closing_balance"))
            result = svc.auto_match(statement, request.user)
            audit(request.user, "upload_statement", "bank_statement", statement.id,
                  f"{statement.statement_no} {statement.account_name}: {len(rows)} lines, "
                  f"{result['matched']} matched automatically")
        return _detail(statement.id, status.HTTP_201_CREATED)

    qs = BankStatement.objects.select_related("uploaded_by").prefetch_related("lines")
    return Response(paginate(request, qs, BankStatementSerializer, default_size=25))


@api_view(["GET", "DELETE"])
def statement_detail(request, statement_id: int):
    if request.method == "DELETE":
        if not CanAccounting().has_permission(request, None):
            return Response({"detail": CanAccounting.message}, status=status.HTTP_403_FORBIDDEN)
        statement = _statement_or_404(statement_id)
        with transaction.atomic():
            number = statement.statement_no
            statement.delete()  # its lines go with it; the entries they matched are freed
            audit(request.user, "delete_statement", "bank_statement", statement_id, number)
        return Response(status=status.HTTP_204_NO_CONTENT)
    return _detail(statement_id)


@api_view(["POST"])
@permission_classes([CanAccounting])
def auto_match(request, statement_id: int):
    statement = _statement_or_404(statement_id)
    result = svc.auto_match(statement, request.user)
    audit(request.user, "auto_match_statement", "bank_statement", statement.id, str(result))
    return _detail(statement_id)


@api_view(["GET"])
def outstanding(request, statement_id: int):
    """Book entries for the statement's channel and period that no line has matched:
    what the books say happened and the bank has not shown."""
    statement = _statement_or_404(statement_id)
    rows = [svc.entry_row(e) for e in svc.entries(statement.period_start, statement.period_end,
                                                   statement.channel)]
    return Response(StatementEntrySerializer(rows, many=True).data)


@api_view(["GET"])
def line_candidates(request, line_id: int):
    line = _line_or_404(line_id)
    rows = [svc.entry_row(e) for e in svc.candidates(line)]
    return Response(StatementEntrySerializer(rows, many=True).data)


@api_view(["POST"])
@permission_classes([CanAccounting])
def line_match(request, line_id: int):
    body = StatementMatchSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        line = _line_or_404(line_id, for_update=True)
        svc.match(line, body.validated_data["entry_id"], request.user)
        audit(request.user, "match_statement_line", "bank_statement", line.statement_id,
              f"line {line.line_no} to {line.journal_entry.entry_no}")
    return _detail(line.statement_id)


@api_view(["POST"])
@permission_classes([CanAccounting])
def line_unmatch(request, line_id: int):
    with transaction.atomic():
        line = _line_or_404(line_id, for_update=True)
        svc.unmatch(line)
        audit(request.user, "unmatch_statement_line", "bank_statement", line.statement_id,
              f"line {line.line_no}")
    return _detail(line.statement_id)


@api_view(["POST"])
@permission_classes([CanAccounting])
def line_ignore(request, line_id: int):
    body = ReasonSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        line = _line_or_404(line_id, for_update=True)
        svc.ignore(line, request.user, body.validated_data["reason"])
        audit(request.user, "ignore_statement_line", "bank_statement", line.statement_id,
              f"line {line.line_no}: {body.validated_data['reason']}")
    return _detail(line.statement_id)


@api_view(["POST"])
@permission_classes([CanAccounting])
def line_journal(request, line_id: int):
    """Prepare the journal for a line the books do not know about, e.g. a bank charge."""
    body = StatementJournalSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        line = _line_or_404(line_id)
        journal = svc.journal_for(line, body.validated_data["account_code"], request.user)
        audit(request.user, "prepare_journal", "manual_journal", journal.id,
              f"{journal.journal_no} from {line.statement.statement_no} line {line.line_no}")
    return Response(ManualJournalSerializer(journal).data, status=status.HTTP_201_CREATED)
