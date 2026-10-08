"""What goes out to borrowers by itself, and the Communications overview.

The rules are one JSON value on the settings row. A blank row means the defaults
below, which are what the system did before the rules existed (every kind on,
sent by the daily job, an arrears notice every day), plus two additions: a
receipt goes out the moment a payment is posted, and a payout confirmation when a
loan is disbursed.
"""
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    JobRun,
    MessageChannel,
    Notification,
    NotificationChannel,
    NotificationStatus,
    OrganisationSetting,
)

# The kinds a rule can switch on or off, in the order the Automation page lists them.
KINDS = {
    "reminder": "Instalment reminders, a few days before each instalment falls due",
    "arrears": "Arrears notices, while a loan is overdue",
    "promise": "Promise-to-pay reminders, the day before a promised payment",
    "receipt": "Receipts, when a repayment is posted",
    "welcome": "Payout confirmation, when a loan is disbursed",
}

DEFAULTS = {
    # The daily job sends what it queues. Off: messages wait in the Outbox for
    # someone to press Send.
    "auto_send": True,
    # Receipts and payout confirmations go the moment they are queued, not with the
    # next daily run. Only while auto_send is on.
    "send_immediately": True,
    "kinds": {kind: True for kind in KINDS},
    # A loan in arrears gets a notice at most this often.
    "arrears_every_days": 1,
}


def rules(row: OrganisationSetting | None = None) -> dict:
    """The rules in force: the saved ones laid over the defaults."""
    row = row or OrganisationSetting.load()
    saved = row.communication_rules or {}
    merged = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    merged["kinds"] = {**DEFAULTS["kinds"], **(saved.get("kinds") or {})}
    merged["reminder_days_before"] = row.reminder_days_before
    return merged


def enabled(kind: str, row: OrganisationSetting | None = None) -> bool:
    return bool(rules(row)["kinds"].get(kind, True))


def save_rules(body: dict) -> dict:
    row = OrganisationSetting.load()
    current = {**DEFAULTS, **(row.communication_rules or {})}
    for key in ("auto_send", "send_immediately"):
        if key in body:
            current[key] = bool(body[key])
    if "kinds" in body:
        if not isinstance(body["kinds"], dict):
            raise BusinessRuleError("kinds must be {kind: true|false}")
        unknown = sorted(set(body["kinds"]) - set(KINDS))
        if unknown:
            raise BusinessRuleError(f"Unknown message kind: {', '.join(unknown)}")
        current["kinds"] = {**DEFAULTS["kinds"], **(current.get("kinds") or {}),
                            **{k: bool(v) for k, v in body["kinds"].items()}}
    if "arrears_every_days" in body:
        every = _whole(body["arrears_every_days"], "Arrears notices every", 1, 90)
        current["arrears_every_days"] = every
    fields = ["communication_rules"]
    if "reminder_days_before" in body:
        row.reminder_days_before = _whole(body["reminder_days_before"],
                                          "Reminder days before", 0, 30)
        fields.append("reminder_days_before")
    row.communication_rules = current
    row.save(update_fields=fields)
    return rules(row)


def _whole(value, label: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise BusinessRuleError(f"{label}: a whole number of days")
    if not low <= number <= high:
        raise BusinessRuleError(f"{label}: between {low} and {high} days")
    return number


def describe_rules() -> list[dict]:
    """The kinds with their descriptions and whether each is on, for the page."""
    current = rules()
    return [{"kind": kind, "about": about, "enabled": current["kinds"].get(kind, True)}
            for kind, about in KINDS.items()]


# ---------------------------------------------------------------- overview
def overview(days: int = 30) -> dict:
    """What the Communications overview shows: traffic by channel, what is waiting
    or has failed, how borrowers prefer to hear, and whether each channel is live."""
    from . import gateways

    since = timezone.now() - timedelta(days=days)
    by_channel = {c: {"sent": 0, "failed": 0, "delivered": 0, "read": 0, "by_hand": 0}
                  for c in (NotificationChannel.SMS, NotificationChannel.WHATSAPP,
                            NotificationChannel.EMAIL)}
    recent = Notification.objects.filter(created_at__gte=since)
    for row in recent.values("channel", "status").annotate(n=Count("id")):
        bucket = by_channel.setdefault(row["channel"], {"sent": 0, "failed": 0, "delivered": 0,
                                                        "read": 0, "by_hand": 0})
        if row["status"] in (NotificationStatus.SENT, NotificationStatus.FAILED):
            bucket[row["status"]] += row["n"]
    for row in (recent.filter(delivery_status__in=["delivered", "read"])
                .values("channel", "delivery_status").annotate(n=Count("id"))):
        by_channel[row["channel"]][row["delivery_status"]] += row["n"]
    for row in recent.filter(provider="by hand").values("channel").annotate(n=Count("id")):
        by_channel[row["channel"]]["by_hand"] += row["n"]

    preference = {c: 0 for c in MessageChannel.values}
    for row in Borrower.objects.values("preferred_channel").annotate(n=Count("id")):
        preference[row["preferred_channel"] or MessageChannel.SMS] = row["n"]

    last_run = JobRun.objects.filter(job="reminders").order_by("-started_at").first()
    gateway = gateways.describe()
    return {
        "days": days,
        "channels": by_channel,
        "queued": Notification.objects.filter(status=NotificationStatus.QUEUED).count(),
        "failed_recently": Notification.objects.filter(
            status=NotificationStatus.FAILED, created_at__gte=timezone.now() - timedelta(days=7)
        ).count(),
        "fell_back_to_sms": recent.filter(fallback_of__isnull=False).count(),
        "preference": preference,
        "borrowers_with_email": Borrower.objects.exclude(email__isnull=True).exclude(email="").count(),
        "borrowers": Borrower.objects.count(),
        "last_daily_run": ({"status": last_run.status, "started_at": last_run.started_at,
                            "as_of": last_run.as_of, "output": last_run.output or "",
                            "error": last_run.error or ""} if last_run else None),
        "gateway": gateway,
        "rules": rules(),
    }


# ---------------------------------------------------------------- a test message
def send_test(channel: str, to: str, text: str | None, user=None) -> dict:
    """Send one message straight away, to any address, outside the outbox. For
    checking a channel works before borrowers depend on it. Nothing is kept but the
    audit entry the view writes."""
    from . import gateways

    if channel not in (NotificationChannel.SMS, NotificationChannel.WHATSAPP,
                       NotificationChannel.EMAIL):
        raise BusinessRuleError("Choose sms, whatsapp or email")
    to = (to or "").strip()
    if not to:
        raise BusinessRuleError("Give the number or email address to send the test to")
    org = OrganisationSetting.load()
    body = (text or "").strip() or (
        f"This is a test message from {org.name}'s loan system. If it reached you, "
        f"{dict(NotificationChannel.choices)[channel]} is working.")
    message = SimpleNamespace(
        id=0, channel=channel, to_address=to, body=body, kind="test", template_vars={},
        subject=f"{org.name}: test message", get_kind_display=lambda: "Test message")
    backend = gateways.backend_for(channel)  # raises with what is missing
    result = backend.send(message)
    return {"ok": result.ok, "provider": result.provider, "message_id": result.message_id,
            "error": result.error, "delivers": backend.name not in ("console", "file"),
            "to": to, "channel": channel}


def email_configured() -> bool:
    return bool(getattr(settings, "EMAIL_HOST", ""))
