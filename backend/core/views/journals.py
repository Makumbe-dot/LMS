"""Manual journals: operating expenses, other income, fixed assets, opening balances."""
from django.db import transaction
from django.db.models import Prefetch, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import ManualJournal, ManualJournalLine, ManualJournalStatus
from ..permissions import IsAdmin, IsTeller
from ..serializers import (
    ManualJournalCreateSerializer,
    ManualJournalSerializer,
    ReasonSerializer,
)
from ..services import journals as svc
from .helpers import table_response, wants_table, paginate, parse_date, parse_int


def _queryset():
    return (ManualJournal.objects
            .select_related("prepared_by", "posted_by", "reversed_by", "branch",
                            "journal_entry", "reversal_entry")
            .prefetch_related(Prefetch(
                "lines", queryset=ManualJournalLine.objects.select_related("account")
                .order_by("id"))))


def _journal_or_404(journal_id: int, *, for_update: bool = False) -> ManualJournal:
    qs = ManualJournal.objects.filter(pk=journal_id)
    if for_update:
        qs = qs.select_for_update()
    journal = qs.first()
    if journal is None:
        raise NotFound("Journal not found")
    return journal


def _response(journal_id: int, code=status.HTTP_200_OK) -> Response:
    return Response(ManualJournalSerializer(_queryset().get(pk=journal_id)).data, status=code)


@api_view(["GET", "POST"])
def journals(request):
    if request.method == "POST":
        if not IsTeller().has_permission(request, None):
            return Response({"detail": IsTeller.message}, status=status.HTTP_403_FORBIDDEN)
        body = ManualJournalCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        with transaction.atomic():
            journal = svc.prepare(request.user, entry_date=data.get("entry_date"),
                                  narration=data["narration"], lines=data["lines"],
                                  reference=data.get("reference"), branch_id=data.get("branch"))
            audit(request.user, "prepare_journal", "manual_journal", journal.id,
                  f"{journal.journal_no}: {journal.narration[:120]}")
        return _response(journal.id, status.HTTP_201_CREATED)

    qs = _queryset()
    state = request.query_params.get("status")
    if state:
        if state not in ManualJournalStatus.values:
            raise BusinessRuleError(f"Unknown status '{state}'")
        qs = qs.filter(status=state)
    start = parse_date(request, "start")
    end = parse_date(request, "end")
    if start:
        qs = qs.filter(entry_date__gte=start)
    if end:
        qs = qs.filter(entry_date__lte=end)
    account = request.query_params.get("account")
    if account:
        qs = qs.filter(lines__account__code=account).distinct()
    branch_id = parse_int(request, "branch_id")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    search = (request.query_params.get("q") or "").strip()
    if search:
        qs = qs.filter(Q(journal_no__icontains=search) | Q(narration__icontains=search)
                       | Q(reference__icontains=search))
    qs = qs.order_by("-entry_date", "-id")

    if wants_table(request):
        rows = [{
            "journal_no": j.journal_no, "entry_date": j.entry_date, "status": j.status,
            "narration": j.narration, "reference": j.reference or "",
            "account_code": line.account.code, "account_name": line.account.name,
            "debit": line.debit, "credit": line.credit, "description": line.description or "",
            "prepared_by": j.prepared_by.full_name if j.prepared_by_id else "",
            "posted_by": j.posted_by.full_name if j.posted_by_id else "",
            "entry_no": j.journal_entry.entry_no if j.journal_entry_id else "",
        } for j in qs[:5000] for line in j.lines.all()]
        return table_response(request, rows, "manual_journals")

    payload = paginate(request, qs, ManualJournalSerializer, default_size=25)
    payload["awaiting_approval"] = ManualJournal.objects.filter(
        status=ManualJournalStatus.DRAFT).count()
    return Response(payload)


@api_view(["GET", "DELETE"])
def journal_detail(request, journal_id: int):
    if request.method == "DELETE":
        if not IsTeller().has_permission(request, None):
            return Response({"detail": IsTeller.message}, status=status.HTTP_403_FORBIDDEN)
        with transaction.atomic():
            journal = _journal_or_404(journal_id, for_update=True)
            number = journal.journal_no
            svc.withdraw(journal, request.user)
            audit(request.user, "withdraw_journal", "manual_journal", journal_id, number)
        return Response(status=status.HTTP_204_NO_CONTENT)

    _journal_or_404(journal_id)
    return _response(journal_id)


@api_view(["POST"])
@permission_classes([IsAdmin])
def post_journal(request, journal_id: int):
    with transaction.atomic():
        journal = _journal_or_404(journal_id, for_update=True)
        svc.post(journal, request.user)
        audit(request.user, "post_journal", "manual_journal", journal.id,
              f"{journal.journal_no} as {journal.journal_entry.entry_no}")
    return _response(journal_id)


@api_view(["POST"])
@permission_classes([IsAdmin])
def reject_journal(request, journal_id: int):
    body = ReasonSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        journal = _journal_or_404(journal_id, for_update=True)
        svc.reject(journal, request.user, body.validated_data["reason"])
        audit(request.user, "reject_journal", "manual_journal", journal.id,
              f"{journal.journal_no}: {body.validated_data['reason']}")
    return _response(journal_id)


@api_view(["POST"])
@permission_classes([IsAdmin])
def reverse_journal(request, journal_id: int):
    body = ReasonSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        journal = _journal_or_404(journal_id, for_update=True)
        svc.reverse(journal, request.user, body.validated_data["reason"],
                    parse_date(request, "on"))
        audit(request.user, "reverse_journal", "manual_journal", journal.id,
              f"{journal.journal_no}: {body.validated_data['reason']}")
    return _response(journal_id)
