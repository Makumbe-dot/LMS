"""Bulk messages: one text to many borrowers, by SMS, WhatsApp or email.

A campaign picks an audience, a channel and a text with placeholders, and queues
one ordinary outbox message per borrower - so delivery, retries, the SMS fallback,
test mode and the Outbox all work exactly as for reminders. Sending "now" hands the
batch to the gateway in the background, so a thousand messages do not hold up the
page; a later date leaves it for that day's run.
"""
import logging
import threading
from datetime import date, timedelta
from string import Formatter

from django.db import connection, transaction
from django.db.models import Exists, OuterRef

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    Campaign,
    Instalment,
    Loan,
    LoanStatus,
    Notification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    OrganisationSetting,
)
from . import arrears as arrears_svc

log = logging.getLogger(__name__)

AUDIENCES = {
    "active": "Borrowers with a running loan",
    "arrears": "Borrowers behind on a payment",
    "due_soon": "Borrowers with a payment due in the next 7 days",
    "past": "Past clients: no running loan now",
    "all": "Every borrower on the register",
}
CHANNELS = ("preferred", "sms", "whatsapp", "email")
PLACEHOLDERS = {
    "first_name": "First name",
    "last_name": "Surname",
    "institution": "Your institution's name",
    "institution_phone": "Your institution's phone",
    "loan_no": "Their running loan's number (blank if none)",
    "outstanding": "What they still owe on it, with currency",
    "overdue": "What is overdue on it, with currency",
}


def _validate_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise BusinessRuleError("Write the message")
    if len(text) > 1000:
        raise BusinessRuleError("At most 1,000 characters")
    try:
        names = {n for _, n, _, _ in Formatter().parse(text) if n is not None}
    except ValueError as exc:
        raise BusinessRuleError(f"The message cannot be read: {exc}")
    unknown = sorted(names - set(PLACEHOLDERS))
    if unknown:
        raise BusinessRuleError("Unknown placeholder " + ", ".join("{" + n + "}" for n in unknown)
                                + ". Use " + ", ".join("{" + n + "}" for n in PLACEHOLDERS))
    return text


def audience(spec: dict) -> list[tuple[Borrower, Loan | None]]:
    """The borrowers a spec reaches, each with their running loan (if any).
    Blacklisted borrowers are never messaged in bulk."""
    who = spec.get("who") or "active"
    if who not in AUDIENCES:
        raise BusinessRuleError(f"Choose who to send to: {', '.join(AUDIENCES)}")
    today = date.today()
    loans = Loan.objects.filter(status=LoanStatus.ACTIVE)
    if spec.get("product_id"):
        loans = loans.filter(product_id=spec["product_id"])
    if who == "arrears":
        loans = loans.filter(arrears_svc.is_overdue(today))
    elif who == "due_soon":
        soon = Instalment.objects.filter(loan=OuterRef("pk"), due_date__gte=today,
                                         due_date__lte=today + timedelta(days=7))
        loans = loans.filter(Exists(soon))

    borrowers = Borrower.objects.filter(is_blacklisted=False)
    if spec.get("branch_id"):
        borrowers = borrowers.filter(branch_id=spec["branch_id"])
    if who in ("active", "arrears", "due_soon"):
        borrowers = borrowers.filter(id__in=loans.values("borrower_id"))
    elif who == "past":
        borrowers = borrowers.exclude(loans__status=LoanStatus.ACTIVE)

    running = {}
    for loan in (Loan.objects.filter(status=LoanStatus.ACTIVE,
                                     borrower_id__in=borrowers.values("id")).order_by("id")):
        running.setdefault(loan.borrower_id, loan)
    return [(b, running.get(b.id)) for b in borrowers.order_by("last_name", "first_name")]


def _values(borrower, loan, org, overdue_by_loan) -> dict:
    currency = (loan.currency if loan and loan.currency else None) or org.currency
    return {
        "first_name": borrower.first_name, "last_name": borrower.last_name,
        "institution": org.name or "", "institution_phone": org.phone or "",
        "loan_no": loan.loan_no if loan else "",
        "outstanding": f"{currency} {loan.total_outstanding:,.2f}" if loan else "",
        "overdue": f"{currency} {overdue_by_loan.get(loan.id, 0):,.2f}" if loan else "",
    }


def _overdue(loans) -> dict:
    ids = [loan.id for loan in loans if loan]
    if not ids:
        return {}
    return {row["id"]: row["arrears_amount"]
            for row in arrears_svc.rows(date.today(), loan_ids=ids)}


def _route(borrower, channel: str) -> tuple[str | None, str | None]:
    """(channel, address) for one borrower, or (None, why not)."""
    from .notifications import _channel_for

    if channel == "preferred":
        return _channel_for(borrower)
    if channel == "email":
        if "@" not in (borrower.email or ""):
            return None, "no email address"
        return NotificationChannel.EMAIL, borrower.email.strip()
    if not borrower.phone:
        return None, "no phone number"
    return (NotificationChannel.WHATSAPP if channel == "whatsapp" else NotificationChannel.SMS,
            borrower.phone)


def preview(spec: dict, channel: str, text: str) -> dict:
    """How many it reaches, by channel, and three messages as they will read."""
    text = _validate_text(text)
    if channel not in CHANNELS:
        raise BusinessRuleError("Choose a channel")
    org = OrganisationSetting.load()
    people = audience(spec)
    overdue = _overdue([loan for _, loan in people])
    counts = {"sms": 0, "whatsapp": 0, "email": 0, "skipped": 0}
    samples = []
    for borrower, loan in people:
        routed, _ = _route(borrower, channel)
        counts[routed or "skipped"] += 1
        if routed and len(samples) < 3:
            samples.append({"to": borrower.full_name, "channel": routed,
                            "text": text.format_map(_values(borrower, loan, org, overdue))})
    return {"total": len(people), **counts, "samples": samples}


@transaction.atomic
def create(spec: dict, channel: str, text: str, subject: str, when: date | None, name: str,
           user) -> Campaign:
    text = _validate_text(text)
    if channel not in CHANNELS:
        raise BusinessRuleError("Choose a channel")
    when = when or date.today()
    if when < date.today():
        raise BusinessRuleError("The send date cannot be in the past")
    org = OrganisationSetting.load()
    people = audience(spec)
    if not people:
        raise BusinessRuleError("Nobody matches that audience")
    campaign = Campaign.objects.create(
        name=(name or "").strip()[:120] or f"Message of {date.today():%d %b %Y}",
        audience={k: spec.get(k) for k in ("who", "branch_id", "product_id") if spec.get(k)},
        channel=channel, subject=(subject or "").strip()[:200], text=text, scheduled_for=when,
        created_by=user)
    overdue = _overdue([loan for _, loan in people])
    rows, skipped = [], 0
    for borrower, loan in people:
        routed, address = _route(borrower, channel)
        if not routed:
            skipped += 1
            continue
        values = _values(borrower, loan, org, overdue)
        rows.append(Notification(
            borrower=borrower, loan=loan, kind=NotificationKind.BULK, channel=routed,
            to_address=address, body=text.format_map(values), scheduled_for=when,
            subject=(campaign.subject or f"{org.name}: {campaign.name}")
            if routed == NotificationChannel.EMAIL else None,
            template_vars=values, campaign=campaign,
            dedupe_key=f"campaign:{campaign.id}:{borrower.id}"))
    Notification.objects.bulk_create(rows, batch_size=200)
    campaign.queued, campaign.skipped = len(rows), skipped
    campaign.save(update_fields=["queued", "skipped"])
    if when == date.today():
        ids = list(Notification.objects.filter(campaign=campaign).values_list("id", flat=True))
        transaction.on_commit(lambda: _send_in_background(ids))
    return campaign


def _send_in_background(ids: list[int]) -> None:
    """Deliver a campaign without holding up the request that made it."""
    from .notifications import send

    def run():
        try:
            for start in range(0, len(ids), 100):
                send(ids[start:start + 100])
        except Exception:  # logged; the daily run picks up whatever is still queued
            log.exception("Sending a bulk message stopped; the rest stay queued")
        finally:
            connection.close()

    threading.Thread(target=run, name="campaign-send", daemon=True).start()


def summary(campaign: Campaign) -> dict:
    from django.db.models import Count

    by_status = {row["status"]: row["n"] for row in
                 Notification.objects.filter(campaign=campaign).values("status").annotate(n=Count("id"))}
    delivered = Notification.objects.filter(campaign=campaign,
                                            delivery_status__in=["delivered", "read"]).count()
    return {"id": campaign.id, "name": campaign.name, "channel": campaign.channel,
            "audience": campaign.audience, "text": campaign.text, "subject": campaign.subject,
            "scheduled_for": campaign.scheduled_for, "created_at": campaign.created_at,
            "created_by": campaign.created_by.full_name if campaign.created_by else None,
            "queued": campaign.queued, "skipped": campaign.skipped,
            "waiting": by_status.get(NotificationStatus.QUEUED, 0),
            "sent": by_status.get(NotificationStatus.SENT, 0),
            "failed": by_status.get(NotificationStatus.FAILED, 0),
            "cancelled": by_status.get(NotificationStatus.CANCELLED, 0),
            "delivered": delivered}
