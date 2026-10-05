"""Payments that providers report as they arrive, and the queue of ones nobody
could place automatically."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import IncomingPayment, IncomingPaymentStatus
from ..permissions import IsOfficer, IsTeller
from ..serializers import (
    IncomingAssignSerializer,
    IncomingDismissSerializer,
    IncomingPaymentSerializer,
)
from ..services import incoming as svc
from ..services.imports import _loan_for
from .helpers import paginate, parse_date


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def receive(request, provider: str):
    """Called by the provider, not a person: trust comes from the body's signature,
    so there is no sign-in. Answers 200 for a payment stored, new or repeated, so the
    provider stops retrying; an error status only when the delivery is refused."""
    try:
        header = svc.provider_config(provider)["signature_header"]
        payment, new = svc.receive(provider, request.body, request.headers.get(header))
    except svc.Refused as exc:
        return Response({"detail": str(exc)}, status=exc.status)
    if new:
        audit(None, "incoming_payment", "incoming_payment", payment.id,
              f"{payment.provider} {payment.reference} {payment.amount}: {payment.status}"
              + (f" to {payment.loan.loan_no}" if payment.status == "posted" else ""))
    return Response({"id": payment.id, "status": payment.status, "duplicate": not new})


@api_view(["GET"])
@permission_classes([IsTeller])
def payments(request):
    qs = IncomingPayment.objects.select_related("loan__borrower", "resolved_by")
    state = request.query_params.get("status")
    if state:
        if state not in IncomingPaymentStatus.values:
            raise BusinessRuleError(f"Unknown status '{state}'")
        qs = qs.filter(status=state)
    provider = request.query_params.get("provider")
    if provider:
        qs = qs.filter(provider=provider.lower())
    start = parse_date(request, "start")
    end = parse_date(request, "end")
    if start:
        qs = qs.filter(received_on__gte=start)
    if end:
        qs = qs.filter(received_on__lte=end)
    payload = paginate(request, qs, IncomingPaymentSerializer, default_size=25)
    payload["unmatched"] = IncomingPayment.objects.filter(
        status=IncomingPaymentStatus.UNMATCHED).count()
    return Response(payload)


def _payment_or_404(payment_id: int) -> IncomingPayment:
    payment = (IncomingPayment.objects.select_for_update()
               .filter(pk=payment_id).first())
    if payment is None:
        raise NotFound("Payment not found")
    return payment


@api_view(["POST"])
@permission_classes([IsTeller])
def assign(request, payment_id: int):
    """Place an unmatched payment on the loan a person has identified."""
    body = IncomingAssignSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        payment = _payment_or_404(payment_id)
        loan = _loan_for(body.validated_data["loan_no"].strip())
        if loan is None:
            raise NotFound(f"No loan numbered {body.validated_data['loan_no']}")
        svc.assign(payment, loan, request.user)
        audit(request.user, "assign_incoming_payment", "incoming_payment", payment.id,
              f"{payment.reference} {payment.amount} to {loan.loan_no}")
    return Response(IncomingPaymentSerializer(payment).data)


@api_view(["POST"])
@permission_classes([IsOfficer])
def dismiss(request, payment_id: int):
    """Set a payment aside that is not going on any loan. An officer's call, not a
    teller's: money received and not posted has to be explained."""
    body = IncomingDismissSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        payment = _payment_or_404(payment_id)
        svc.dismiss(payment, request.user, body.validated_data["note"])
        audit(request.user, "dismiss_incoming_payment", "incoming_payment", payment.id,
              f"{payment.reference} {payment.amount}: {payment.note}")
    return Response(IncomingPaymentSerializer(payment).data)
