"""Incoming payments: the provider webhook, statement uploads and the queue of
payments waiting to be matched. See services/inbound.py."""
from django.conf import settings
from django.db.models import Count, Q, Sum
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from ..exceptions import BusinessRuleError, NotFound
from ..models import InboundPayment, Loan, PaymentMethod
from ..permissions import CanCash
from ..serializers import InboundPaymentSerializer
from ..services import inbound
from .helpers import paginate


class WebhookThrottle(SimpleRateThrottle):
    """Per calling address: a provider retrying hard cannot crowd out the rest."""
    scope = "inbound_payments"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([WebhookThrottle])
def webhook(request, provider: str):
    """A provider's notification. No user signs in: the signature is the proof,
    and an unsigned or wrongly signed body is refused before anything is kept."""
    header = getattr(settings, "INBOUND_PAYMENT_SIGNATURE_HEADER", "X-Signature")
    try:
        result = inbound.receive_notification(provider.lower(), request.body,
                                              request.headers.get(header))
    except BusinessRuleError as exc:
        code = (status.HTTP_401_UNAUTHORIZED if "signature" in str(exc).lower()
                or "secret" in str(exc).lower() else status.HTTP_400_BAD_REQUEST)
        return Response({"detail": str(exc)}, status=code)
    return Response(result, status=status.HTTP_200_OK if result["duplicate"]
                    else status.HTTP_201_CREATED)


@api_view(["GET"])
def payments(request):
    """The register, newest first. ?status=unmatched|posted|rejected, ?q= a
    reference, phone, name or loan number."""
    qs = InboundPayment.objects.select_related("loan__borrower", "resolved_by")
    wanted = request.query_params.get("status")
    if wanted:
        qs = qs.filter(status=wanted)
    term = (request.query_params.get("q") or "").strip()
    if term:
        qs = qs.filter(Q(external_id__icontains=term) | Q(account_ref__icontains=term)
                       | Q(payer_phone__icontains=term) | Q(payer_name__icontains=term)
                       | Q(loan__loan_no__icontains=term))
    page = paginate(request, qs, InboundPaymentSerializer)
    totals = {row["status"]: {"count": row["n"], "amount": row["total"]}
              for row in InboundPayment.objects.values("status")
              .annotate(n=Count("id"), total=Sum("amount"))}
    return Response({**page, "totals": totals})


@api_view(["POST"])
@permission_classes([CanCash])
def assign(request, payment_id: int):
    """Post a waiting payment to the loan staff chose: {"loan_no": "LN-000123"}."""
    loan_no = (request.data.get("loan_no") or "").strip()
    loan = Loan.objects.filter(loan_no__iexact=loan_no).first()
    if loan is None:
        raise NotFound(f"No loan {loan_no or '(none given)'}")
    payment = inbound.assign(payment_id, loan, request.user)
    return Response(InboundPaymentSerializer(payment).data)


@api_view(["POST"])
@permission_classes([CanCash])
def reject(request, payment_id: int):
    payment = inbound.reject(payment_id, request.user, request.data.get("reason") or "")
    return Response(InboundPaymentSerializer(payment).data)


@api_view(["POST"])
@permission_classes([CanCash])
def retry(request):
    """Try every waiting payment again."""
    return Response(inbound.retry_waiting())


@api_view(["POST"])
@permission_classes([CanCash])
def import_statement(request):
    """A CSV of payments received: multipart with file, provider and method."""
    upload = request.FILES.get("file")
    if upload is None:
        raise BusinessRuleError("Attach the statement as 'file'")
    if upload.size > 5 * 1024 * 1024:
        raise BusinessRuleError("The file is larger than 5 MB; split it into smaller files.")
    provider = (request.data.get("provider") or "").strip().lower()
    if not provider:
        raise BusinessRuleError("Name the provider the statement is from")
    method = request.data.get("method") or PaymentMethod.MOBILE_MONEY
    result = inbound.import_statement(provider[:40], method, upload.read(), request.user)
    return Response(result, status=status.HTTP_201_CREATED)
