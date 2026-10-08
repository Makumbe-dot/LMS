"""The borrower portal's API, under /api/portal/. See services/portal.py.

Every view here takes the borrower from the portal session and looks loans up
through that borrower, so a borrower can only ever reach their own. None of them
accepts a staff token, and no staff view accepts a portal token.
"""
from functools import wraps

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    renderer_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer, StaticHTMLRenderer
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import (
    LoanStatus,
    OrganisationSetting,
    PortalRequest,
    PortalRequestKind,
    PortalRequestStatus,
    TxnType,
)
from ..permissions import CanLoans
from ..serializers import PortalRequestSerializer
from ..services import portal as svc
from ..services import signatures
from ..services.reports import loan_statement
from .helpers import loan_queryset, parse_date, with_arrears
from .loans import agreement_html


class _ByAddress(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class PortalThrottle(_ByAddress):
    scope = "portal"


class PortalLoginThrottle(_ByAddress):
    scope = "portal_login"


def _client(request):
    return request.META.get("REMOTE_ADDR"), request.headers.get("User-Agent")


def portal_view(methods, *, login=False, html=False):
    """A portal endpoint: no staff authentication, throttled by address, and,
    unless it is part of signing in, a live portal session required."""
    def build(func):
        @wraps(func)
        def inner(request, *args, **kwargs):
            if not login:
                try:
                    request.portal = svc.session_for(request.headers.get("Authorization"))
                except svc.PortalAuthError as exc:
                    return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
            return func(request, *args, **kwargs)

        view = inner
        if html:
            view = renderer_classes([StaticHTMLRenderer, JSONRenderer])(view)
        view = throttle_classes([PortalLoginThrottle if login else PortalThrottle])(view)
        view = permission_classes([AllowAny])(view)
        view = authentication_classes([])(view)
        return api_view(methods)(view)
    return build


def _loan(request, loan_id: int, with_transactions=False):
    loan = (loan_queryset(with_transactions)
            .filter(pk=loan_id, borrower=request.portal.borrower).first())
    if loan is None:
        raise NotFound("Loan not found")
    return loan


def _summary(loan) -> dict:
    with_arrears(loan)
    upcoming = next((i for i in loan.instalments.all() if i.balance > 0), None)
    return {
        "id": loan.id, "loan_no": loan.loan_no, "status": loan.status,
        "status_label": loan.get_status_display(), "product": loan.product.name,
        "currency": loan.currency or OrganisationSetting.load().currency,
        "principal": str(loan.principal), "term": loan.term_months,
        "frequency": loan.get_repayment_frequency_display(),
        "instalment": str(loan.instalment_amount or ""),
        "outstanding": str(loan.total_outstanding),
        "arrears": str(getattr(loan, "arrears_amount", 0) or 0),
        "days_in_arrears": getattr(loan, "days_in_arrears", 0) or 0,
        "next_due_date": upcoming.due_date if upcoming else None,
        "next_due_amount": str(upcoming.balance) if upcoming else None,
        "disbursement_date": loan.disbursement_date,
        "maturity_date": loan.maturity_date,
    }


# ---------------------------------------------------------------- signing in
@portal_view(["POST"], login=True)
def login(request):
    """{"national_id", "phone"} -> {"challenge"}; a code is texted if they match."""
    ip, _ = _client(request)
    challenge = svc.start(request.data.get("national_id"), request.data.get("phone"), ip)
    return Response({"challenge": challenge,
                     "detail": "If those details match our records, a code is on its way."})


@portal_view(["POST"], login=True)
def login_verify(request):
    ip, agent = _client(request)
    token, borrower = svc.verify(request.data.get("challenge"), request.data.get("code"),
                                 ip, agent)
    return Response({"token": token, "first_name": borrower.first_name,
                     "full_name": borrower.full_name})


@portal_view(["POST"])
def logout(request):
    svc.end(request.portal)
    return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- what is theirs
@portal_view(["GET"])
def me(request):
    borrower = request.portal.borrower
    loans = (loan_queryset().filter(borrower=borrower)
             .exclude(status=LoanStatus.REJECTED).order_by("-id"))
    org = OrganisationSetting.load()
    return Response({
        "first_name": borrower.first_name, "full_name": borrower.full_name,
        "borrower_no": borrower.borrower_no, "phone": borrower.phone,
        "institution": org.name, "institution_phone": org.phone,
        "loans": [_summary(loan) for loan in loans],
    })


@portal_view(["GET"])
def loan_detail(request, loan_id: int):
    loan = _loan(request, loan_id, with_transactions=True)
    return Response({
        **_summary(loan),
        "schedule": [{
            "number": i.number, "due_date": i.due_date, "amount": str(i.total_due),
            "paid": str(i.total_paid), "balance": str(i.balance), "status": i.status,
        } for i in loan.instalments.all()],
        "payments": [{
            "date": t.txn_date, "amount": str(t.amount), "method": t.get_method_display()
            if t.method else "", "reference": t.reference or "",
        } for t in loan.transactions.all()
            if t.txn_type in (TxnType.REPAYMENT, TxnType.RECOVERY) and not t.reversed],
        "signature": signatures.state(loan),
    })


@portal_view(["GET"])
def statement(request, loan_id: int):
    """The statement PDF, the same document staff print."""
    from ..documents import loan_statement_pdf

    loan = _loan(request, loan_id, with_transactions=True)
    if loan.status in (LoanStatus.PENDING, LoanStatus.APPROVED):
        raise BusinessRuleError("A statement starts once the loan is paid out")
    return loan_statement_pdf(loan_statement(loan, parse_date(request, "start"),
                                             parse_date(request, "end")))


@portal_view(["GET"], html=True)
def agreement(request, loan_id: int):
    return Response(agreement_html(_loan(request, loan_id)))


@portal_view(["POST"])
def signature_code(request, loan_id: int):
    loan = _loan(request, loan_id)
    sent = signatures.send_code(loan, None, "portal")
    return Response({**signatures.state(loan), "sent_to": sent.phone},
                    status=status.HTTP_201_CREATED)


@portal_view(["POST"])
def signature_verify(request, loan_id: int):
    loan = _loan(request, loan_id)
    ip, agent = _client(request)
    signatures.verify(loan, request.data.get("code"), channel="portal", ip=ip,
                      user_agent=agent)
    return Response(signatures.state(loan))


@portal_view(["GET", "POST"])
def requests(request):
    """The borrower's own requests; POST {"kind": "top_up"|"call_back", "loan_id",
    "amount", "message"} to make one."""
    borrower = request.portal.borrower
    if request.method == "GET":
        mine = PortalRequest.objects.filter(borrower=borrower).order_by("-created_at")[:50]
        return Response(PortalRequestSerializer(mine, many=True).data)
    kind = request.data.get("kind")
    if kind not in PortalRequestKind.values:
        raise BusinessRuleError("Choose what you are asking for")
    loan = None
    if request.data.get("loan_id"):
        loan = _loan(request, int(request.data["loan_id"]))
    body = PortalRequestSerializer(data={**request.data, "loan": loan.id if loan else None})
    body.is_valid(raise_exception=True)
    if kind == PortalRequestKind.TOP_UP and not body.validated_data.get("amount"):
        raise BusinessRuleError("Say how much you would like")
    if PortalRequest.objects.filter(borrower=borrower, status=PortalRequestStatus.OPEN,
                                    kind=kind).count() >= 3:
        raise BusinessRuleError("You already have requests waiting; we will be in touch")
    with transaction.atomic():
        made = PortalRequest.objects.create(
            borrower=borrower, loan=loan, kind=kind, amount=body.validated_data.get("amount"),
            message=(body.validated_data.get("message") or "")[:2000])
        audit(None, "portal_request", "borrower", borrower.id,
              f"{made.get_kind_display()} {made.amount or ''}".strip())
    return Response(PortalRequestSerializer(made).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- staff side
@api_view(["GET"])
def staff_requests(request):
    """What borrowers asked for in the portal. ?status=open|done"""
    qs = PortalRequest.objects.select_related("borrower", "loan", "handled_by")
    wanted = request.query_params.get("status")
    if wanted:
        qs = qs.filter(status=wanted)
    return Response(PortalRequestSerializer(qs[:500], many=True).data)


@api_view(["POST"])
@permission_classes([CanLoans])
def staff_request_done(request, request_id: int):
    with transaction.atomic():
        item = PortalRequest.objects.select_for_update().filter(pk=request_id).first()
        if item is None:
            raise NotFound("Request not found")
        if item.status == PortalRequestStatus.DONE:
            raise BusinessRuleError("Already dealt with")
        item.status = PortalRequestStatus.DONE
        item.outcome = (request.data.get("outcome") or "").strip()[:255] or None
        item.handled_by = request.user
        item.handled_at = timezone.now()
        item.save(update_fields=["status", "outcome", "handled_by", "handled_at"])
        audit(request.user, "portal_request_done", "borrower", item.borrower_id,
              item.outcome or "")
    return Response(PortalRequestSerializer(item).data)
