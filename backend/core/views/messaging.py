"""WhatsApp: Meta's delivery webhook, and the button that opens WhatsApp by hand."""
import json

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from ..audit import audit
from ..permissions import CanCollections, CanMessages
from ..services import gateways
from ..services import notifications as notify
from .helpers import get_loan_or_404


class MetaWebhookThrottle(AnonRateThrottle):
    scope = "inbound_payments"  # the same generous allowance as the payments webhook


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([MetaWebhookThrottle])
def meta_webhook(request):
    """Meta's calls about WhatsApp messages: delivered, read, failed.

    GET is Meta checking the address when the webhook is set up: it sends back the
    challenge if the verify token matches. POST is a status report, signed with the
    app secret; an unsigned or wrongly signed one is refused before it is read.
    """
    config = getattr(settings, "META_WHATSAPP", {}) or {}
    if request.method == "GET":
        token = config.get("verify_token") or ""
        if (request.query_params.get("hub.mode") == "subscribe" and token
                and request.query_params.get("hub.verify_token") == token):
            return HttpResponse(request.query_params.get("hub.challenge", ""),
                                content_type="text/plain")
        return Response({"detail": "Verify token does not match."},
                        status=status.HTTP_403_FORBIDDEN)

    if not gateways.verify_meta_signature(request.body, request.headers.get("X-Hub-Signature-256")):
        return Response({"detail": "Missing or wrong signature."},
                        status=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return Response({"detail": "Not JSON."}, status=status.HTTP_400_BAD_REQUEST)
    # Meta retries anything that is not a 200, so a report about a message this
    # system did not send is acknowledged and ignored rather than refused.
    return Response(notify.record_meta_statuses(payload))


@api_view(["POST"])
def whatsapp_by_hand(request, loan_id: int):
    """Today's reminder or arrears notice for a loan, as a link that opens WhatsApp
    with the text typed in. Anyone who may chase payments or send messages may."""
    if not (CanCollections().has_permission(request, None)
            or CanMessages().has_permission(request, None)):
        return Response({"detail": "You do not have the right to message borrowers."},
                        status=status.HTTP_403_FORBIDDEN)
    loan = get_loan_or_404(loan_id)
    result = notify.whatsapp_by_hand(loan, request.user)
    audit(request.user, "whatsapp", "loan", loan.id, f"{result['kind']} opened in WhatsApp")
    return Response(result)
