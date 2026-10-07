"""The Communications section: the overview, the automation rules and a test send."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError
from ..permissions import CanMessages, IsAdmin
from ..services import communications as comms


def _may_change(request) -> bool:
    return IsAdmin().has_permission(request, None) or CanMessages().has_permission(request, None)


@api_view(["GET"])
def overview(request):
    return Response(comms.overview())


@api_view(["GET", "PUT"])
def rules(request):
    """GET the rules and the kinds they cover; PUT {auto_send, send_immediately,
    kinds: {kind: bool}, arrears_every_days, reminder_days_before} to change them."""
    if request.method == "GET":
        return Response({**comms.rules(), "catalogue": comms.describe_rules()})
    if not _may_change(request):
        return Response({"detail": "You do not have the right to change messaging."},
                        status=status.HTTP_403_FORBIDDEN)
    with transaction.atomic():
        saved = comms.save_rules(request.data or {})
        audit(request.user, "update", "communication_rules", 1, str(request.data))
    return Response({**saved, "catalogue": comms.describe_rules()})


@api_view(["POST"])
def send_test(request):
    """{channel, to, text?}: one message, straight away, to check a channel works."""
    if not _may_change(request):
        return Response({"detail": "You do not have the right to send messages."},
                        status=status.HTTP_403_FORBIDDEN)
    data = request.data or {}
    try:
        result = comms.send_test(data.get("channel"), data.get("to"), data.get("text"), request.user)
    except RuntimeError as exc:  # the channel is not set up: say what is missing
        raise BusinessRuleError(str(exc))
    audit(request.user, "test_message", "communications", None,
          f"{result['channel']} to {result['to']}: {'ok' if result['ok'] else result['error']}")
    return Response(result)


# ---------------------------------------------------------------- bulk messages
@api_view(["GET", "POST"])
def campaigns(request):
    """GET recent bulk messages with how they went; POST {audience: {who, branch_id,
    product_id}, channel, text, subject, scheduled_for, name} to send one."""
    from datetime import date as _date

    from ..models import Campaign
    from ..services import campaigns as svc

    if request.method == "GET":
        recent = Campaign.objects.select_related("created_by")[:50]
        return Response({"audiences": svc.AUDIENCES, "placeholders": svc.PLACEHOLDERS,
                         "campaigns": [svc.summary(c) for c in recent]})
    if not _may_change(request):
        return Response({"detail": "Sending bulk messages needs the messaging right."},
                        status=status.HTTP_403_FORBIDDEN)
    data = request.data or {}
    when = data.get("scheduled_for")
    try:
        when = _date.fromisoformat(when) if when else None
    except ValueError:
        raise BusinessRuleError("The send date must be a date")
    with transaction.atomic():
        made = svc.create(data.get("audience") or {}, data.get("channel") or "preferred",
                          data.get("text"), data.get("subject"), when, data.get("name"),
                          request.user)
        audit(request.user, "bulk_message", "campaign", made.id,
              f"{made.name}: {made.queued} queued, {made.skipped} skipped, {made.channel}")
    return Response(svc.summary(made), status=status.HTTP_201_CREATED)


@api_view(["POST"])
def campaign_preview(request):
    """{audience, channel, text}: how many it reaches and three messages as they will read."""
    from ..services import campaigns as svc

    data = request.data or {}
    return Response(svc.preview(data.get("audience") or {}, data.get("channel") or "preferred",
                                data.get("text")))
