"""Borrower messaging outbox.

The generator queues messages; nothing is transmitted here. Marking a batch sent
is a separate, explicit step, so the queue can be reviewed, exported for a bulk
SMS provider, or wired to a real gateway later without touching the loan book.

Every message carries a dedupe_key, so running the generator repeatedly on the
same day queues nothing extra.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone

from ..exceptions import BusinessRuleError

from ..models import (
    SECRET_KINDS,
    Instalment,
    Loan,
    LoanNote,
    LoanStatus,
    Notification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    OrganisationSetting,
)
from . import templates
from .amortisation import q
from .loans import arrears, sched

ZERO = Decimal("0")


def _money(value, currency: str) -> str:
    return f"{currency} {q(value):,.2f}"


def _queue(*, borrower, loan, kind, channel, to_address, body, scheduled_for,
           dedupe_key, subject=None, template_vars=None, fallback_of=None) -> Notification | None:
    """Insert one message, ignoring it if the dedupe key is already present."""
    if not to_address:
        return None
    try:
        with transaction.atomic():
            return Notification.objects.create(
                borrower=borrower, loan=loan, kind=kind, channel=channel,
                to_address=to_address, subject=subject, body=body,
                scheduled_for=scheduled_for, dedupe_key=dedupe_key,
                template_vars=template_vars, fallback_of=fallback_of,
            )
    except IntegrityError:
        return None  # already queued on an earlier run


def _channel_for(borrower) -> tuple[str, str]:
    """(channel, address) for a borrower: their chosen channel, unless WhatsApp has
    been switched off or they chose email with no address on file - then SMS."""
    from . import gateways

    preferred = getattr(borrower, "preferred_channel", None)
    if preferred == NotificationChannel.WHATSAPP and gateways.whatsapp_enabled():
        return NotificationChannel.WHATSAPP, borrower.phone
    if preferred == NotificationChannel.EMAIL and "@" in (borrower.email or ""):
        return NotificationChannel.EMAIL, borrower.email.strip()
    return NotificationChannel.SMS, borrower.phone


def _message(kind, borrower, settings_row, **values) -> dict:
    """The body, the channel, the address and the values behind them, for _queue(**...)."""
    channel, address = _channel_for(borrower)
    return {
        "body": templates.render(kind, borrower, settings_row, **values),
        "template_vars": templates.context(borrower, settings_row, **values),
        "channel": channel,
        "to_address": address,
        # Email needs a subject; the other channels ignore it.
        "subject": (f"{settings_row.name}: {templates.TEMPLATES[kind]['label']}"
                    if channel == NotificationChannel.EMAIL else None),
    }


def generate_reminders(as_of: date | None = None, days_before: int | None = None) -> dict:
    """Queue instalment reminders and arrears notices for every active loan."""
    settings_row = OrganisationSetting.load()
    as_of = as_of or date.today()
    days_before = settings_row.reminder_days_before if days_before is None else days_before
    horizon = as_of + timedelta(days=days_before)
    currency = settings_row.currency

    loans = (Loan.objects
             .filter(status=LoanStatus.ACTIVE)
             .select_related("borrower", "product")
             .prefetch_related(Prefetch("instalments",
                                        queryset=Instalment.objects.order_by("number"))))

    from .communications import rules as current_rules

    rule = current_rules(settings_row)
    wants = rule["kinds"]
    every = max(1, int(rule.get("arrears_every_days") or 1))

    reminders = notices = 0
    for loan in loans:
        borrower = loan.borrower

        # 1. instalments falling due inside the reminder window
        for ins in sched(loan):
            if ins.balance <= 0 or not wants.get("reminder", True):
                continue
            if as_of <= ins.due_date <= horizon:
                message = _message(
                    "reminder", borrower, settings_row, number=ins.number,
                    amount=_money(ins.balance, currency), loan_no=loan.loan_no,
                    due_date=f"{ins.due_date:%d %b %Y}")
                if _queue(borrower=borrower, loan=loan, kind=NotificationKind.REMINDER,
                          **message,
                          scheduled_for=max(as_of, ins.due_date - timedelta(days=days_before)),
                          dedupe_key=f"reminder:{ins.id}"):
                    reminders += 1

        # 2. an arrears notice while it is overdue: at most one every `every` days
        amount, days = arrears(loan, as_of)
        recently = every > 1 and Notification.objects.filter(
            loan=loan, kind=NotificationKind.ARREARS,
            scheduled_for__gt=as_of - timedelta(days=every)).exists()
        if amount > 0 and wants.get("arrears", True) and not recently:
            message = _message("arrears", borrower, settings_row, loan_no=loan.loan_no,
                               amount=_money(amount, currency), days=days)
            if _queue(borrower=borrower, loan=loan, kind=NotificationKind.ARREARS,
                      **message,
                      scheduled_for=as_of, dedupe_key=f"arrears:{loan.id}:{as_of.isoformat()}"):
                notices += 1

    # 3. a promise to pay falls due tomorrow: one reminder per promise
    promises = 0
    tomorrow = as_of + timedelta(days=1)
    for note in ([] if not wants.get("promise", True) else LoanNote.objects
                 .filter(resolved=False, promised_amount__isnull=False,
                         promised_date__gte=as_of, promised_date__lte=tomorrow,
                         loan__status=LoanStatus.ACTIVE)
                 .select_related("loan__borrower")):
        loan, borrower = note.loan, note.loan.borrower
        message = _message("promise", borrower, settings_row, loan_no=loan.loan_no,
                           amount=_money(note.promised_amount, currency),
                           promised_date=f"{note.promised_date:%d %b %Y}")
        # Stored as a reminder (its kind), but worded and templated as a promise.
        message["template_vars"]["_template"] = "promise"
        if _queue(borrower=borrower, loan=loan, kind=NotificationKind.REMINDER,
                  **message,
                  scheduled_for=as_of, dedupe_key=f"promise:{note.id}"):
            promises += 1

    return {
        "as_of": as_of.isoformat(),
        "reminder_window_days": days_before,
        "promise_reminders_queued": promises,
        "reminders_queued": reminders,
        "arrears_notices_queued": notices,
        "total_queued": reminders + notices + promises,
    }


def queue_receipt(loan: Loan, amount: Decimal, txn_id: int, txn_date: date) -> Notification | None:
    """Acknowledge a repayment. Called inside the posting's transaction; with the
    rules' send_immediately on, it goes out as soon as the posting commits."""
    from .communications import enabled

    settings_row = OrganisationSetting.load()
    if not enabled("receipt", settings_row):
        return None
    borrower = loan.borrower
    message = _message("receipt", borrower, settings_row, loan_no=loan.loan_no,
                       amount=_money(amount, settings_row.currency),
                       balance=_money(loan.total_outstanding, settings_row.currency))
    return _send_soon(_queue(borrower=borrower, loan=loan, kind=NotificationKind.RECEIPT,
                             scheduled_for=txn_date, dedupe_key=f"receipt:{txn_id}", **message))


def queue_welcome(loan: Loan) -> Notification | None:
    """Confirm a payout to the borrower: the amount and the first instalment."""
    from .communications import enabled

    settings_row = OrganisationSetting.load()
    if not enabled("welcome", settings_row):
        return None
    currency = loan.currency or settings_row.currency
    first = next((i for i in sched(loan) if i.total_due > 0), None)
    message = _message(
        "welcome", loan.borrower, settings_row, loan_no=loan.loan_no,
        amount=_money(loan.principal, currency),
        instalment=_money(first.total_due if first else loan.instalment_amount, currency),
        due_date=f"{first.due_date:%d %b %Y}" if first else "-")
    return _send_soon(_queue(borrower=loan.borrower, loan=loan, kind=NotificationKind.WELCOME,
                             scheduled_for=loan.disbursement_date or date.today(),
                             dedupe_key=f"welcome:{loan.id}", **message))


def _send_soon(message: Notification | None) -> Notification | None:
    """Send a receipt or payout confirmation once the posting it belongs to has
    committed, when the rules say to. A gateway problem never undoes the posting:
    the message stays queued for the daily run, and the error is logged."""
    if message is None:
        return None
    from .communications import rules

    rule = rules()
    if not (rule["auto_send"] and rule["send_immediately"]):
        return message
    message_id = message.id

    def go():
        try:
            send([message_id])
        except Exception:  # noqa: BLE001 - logged; the daily run retries
            logging.getLogger(__name__).exception(
                "Sending message %s straight away failed; it stays queued", message_id)

    transaction.on_commit(go)
    return message


def due(ids: list[int] | None = None, as_of=None):
    """Messages the outbox should try to deliver now."""
    qs = Notification.objects.filter(status=NotificationStatus.QUEUED)
    if ids:
        return qs.filter(id__in=ids)
    return qs.filter(scheduled_for__lte=as_of or date.today())


def send(ids: list[int] | None = None, as_of=None, limit: int | None = None) -> dict:
    """Hand each due message to the gateway and record what happened.

    One message at a time, each in its own transaction. The previous version was a
    single UPDATE marking the whole queue sent without contacting anyone, so the
    system reported arrears notices as delivered that no borrower had received.

    A failure does not stop the batch: one bad phone number must not hold up
    everybody else's receipt. A message is retried until MESSAGE_MAX_ATTEMPTS and
    then marked FAILED, except for a permanent failure — a malformed address, a
    4xx from the provider — which is marked FAILED at once, because retrying it
    twice more only delays someone noticing.

    A misconfigured gateway raises instead, because that is not one message's
    problem and marking a whole queue failed over it would lose the queue.
    """
    from . import gateways

    # First, what became of what was sent before: a WhatsApp message Twilio could
    # not deliver is queued again by SMS, and this run sends it.
    checked = refresh_deliveries()

    queue = due(ids, as_of).select_related("borrower", "loan").order_by("attempts", "id")
    if limit:
        queue = queue[:limit]

    sent = failed = retrying = 0
    errors: list[str] = []
    fallbacks: list[int] = []
    for message in list(queue):
        backend = gateways.backend_for(message.channel)  # raises if misconfigured
        result = backend.send(message)

        message.attempts += 1
        message.last_attempt_at = timezone.now()
        message.provider = result.provider
        if result.ok:
            message.status = NotificationStatus.SENT
            message.sent_at = timezone.now()
            message.provider_message_id = result.message_id
            message.error = None
            sent += 1
        else:
            message.error = result.error
            if result.permanent or not message.can_retry:
                message.status = NotificationStatus.FAILED
                failed += 1
                errors.append(f"{message.id} to {message.to_address}: {result.error}")
                replacement = _fall_back(message)
                if replacement:
                    fallbacks.append(replacement.id)
            else:
                # Stays QUEUED, so the next run picks it up.
                retrying += 1
        fields = ["status", "sent_at", "error", "attempts", "last_attempt_at", "provider",
                  "provider_message_id"]
        if message.kind in SECRET_KINDS and message.status != NotificationStatus.QUEUED:
            # Delivered or given up on: the code is no use to anyone now, so keep none.
            message.body = "[one-time code]"
            fields.append("body")
        message.save(update_fields=fields)

    # A WhatsApp message refused outright is replaced by an SMS in the same run,
    # so the borrower is not a day late hearing about an instalment.
    resent = send(fallbacks, as_of) if fallbacks else None

    return {
        "sent": sent + (resent["sent"] if resent else 0),
        "failed": failed + (resent["failed"] if resent else 0),
        "retrying": retrying + (resent["retrying"] if resent else 0),
        "attempted": sent + failed + retrying + (resent["attempted"] if resent else 0),
        "fell_back_to_sms": len(fallbacks) + checked["fell_back_to_sms"],
        "deliveries_checked": checked["checked"],
        "errors": (errors + (resent["errors"] if resent else []))[:20],
        "gateway": gateways.describe(),
    }


def _fall_back(message: Notification) -> Notification | None:
    """Queue an SMS in place of a WhatsApp or email message that could not be
    delivered.

    Codes are never re-sent this way (they are sent by SMS in the first place),
    and an SMS that failed has nowhere further to fall back to.
    """
    if message.channel == NotificationChannel.SMS or message.kind in SECRET_KINDS:
        return None
    return _queue(borrower=message.borrower, loan=message.loan, kind=message.kind,
                  channel=NotificationChannel.SMS, to_address=message.borrower.phone,
                  body=message.body, subject=message.subject, scheduled_for=date.today(),
                  dedupe_key=f"{message.dedupe_key}:sms"[:120],
                  template_vars=message.template_vars, fallback_of=message)


def apply_delivery(message: Notification, delivery: str | None, code=None, text=None,
                   source: str = "The provider") -> bool:
    """Record what the provider says became of a message. A failed one is marked
    FAILED with the reason, and a WhatsApp one is queued again by SMS; True when it
    was. Shared by the Twilio lookup and the Meta webhook."""
    message.delivery_status = delivery
    message.delivery_checked_at = timezone.now()
    fields = ["delivery_status", "delivery_checked_at"]
    fell_back = False
    if delivery in ("failed", "undelivered") and message.status != NotificationStatus.FAILED:
        message.status = NotificationStatus.FAILED
        message.error = (f"{source} reported it {delivery}" + (f" (error {code})" if code else "")
                         + (f": {text}" if text else ""))
        fields += ["status", "error"]
        fell_back = _fall_back(message) is not None
    message.save(update_fields=fields)
    return fell_back


def record_meta_statuses(payload: dict) -> dict:
    """Meta's webhook body: entry[].changes[].value.statuses[], each with the
    message id (wamid), a status (sent, delivered, read, failed) and any errors."""
    seen = updated = fell_back = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for item in (change.get("value") or {}).get("statuses") or []:
                seen += 1
                message = (Notification.objects.select_related("borrower", "loan")
                           .filter(provider="meta", provider_message_id=item.get("id")).first())
                if message is None:
                    continue
                error = (item.get("errors") or [{}])[0]
                detail = (error.get("error_data") or {}).get("details") or error.get("title")
                if apply_delivery(message, item.get("status"), error.get("code"), detail,
                                  source="WhatsApp"):
                    fell_back += 1
                updated += 1
    return {"statuses": seen, "updated": updated, "fell_back_to_sms": fell_back}


def whatsapp_by_hand(loan: Loan, user=None) -> dict:
    """The WhatsApp button: today's message for a loan, as a wa.me link.

    WhatsApp opens on the staff member's phone or computer with the text typed in;
    they press send. Nothing is sent from here, so nothing needs an API. The
    message is kept in the outbox as sent "by hand", so the borrower's history
    shows that they were contacted and with what.
    """
    from . import gateways

    settings_row = OrganisationSetting.load()
    currency = settings_row.currency
    borrower = loan.borrower
    today = date.today()
    amount, days = arrears(loan, today)
    if amount > 0:
        kind, values = NotificationKind.ARREARS, dict(
            loan_no=loan.loan_no, amount=_money(amount, currency), days=days)
    else:
        upcoming = next((i for i in sched(loan) if i.balance > 0 and i.due_date >= today), None)
        if upcoming is None:
            raise BusinessRuleError("This loan has nothing falling due to remind the borrower about.")
        kind, values = NotificationKind.REMINDER, dict(
            number=upcoming.number, amount=_money(upcoming.balance, currency),
            loan_no=loan.loan_no, due_date=f"{upcoming.due_date:%d %b %Y}")
    body = templates.render(kind, borrower, settings_row, **values)
    phone = gateways.international(borrower.phone)
    if not phone:
        raise BusinessRuleError(f"{borrower.phone!r} is not a phone number WhatsApp can use.")
    url = f"https://wa.me/{phone.lstrip('+')}?text={quote(body)}"
    Notification.objects.create(
        borrower=borrower, loan=loan, kind=kind, channel=NotificationChannel.WHATSAPP,
        to_address=borrower.phone, body=body, status=NotificationStatus.SENT,
        scheduled_for=today, sent_at=timezone.now(), provider="by hand",
        error=f"Opened in WhatsApp by {getattr(user, 'full_name', None) or 'staff'}; "
              "sent from their own WhatsApp.",
        dedupe_key=f"by-hand:{loan.id}:{timezone.now().timestamp()}"[:120],
        template_vars=templates.context(borrower, settings_row, **values))
    return {"kind": kind, "phone": phone, "text": body, "url": url}


# Twilio's word for a message it is finished with.
FINAL_DELIVERY = {"delivered", "read", "failed", "undelivered", "canceled"}


def refresh_deliveries(days: int = 3) -> dict:
    """Ask the provider what became of recently sent messages.

    Only Twilio is asked: it is the one backend that can say. A message reported
    failed or undelivered is marked FAILED with Twilio's reason, and a WhatsApp one
    is queued again by SMS. One unreachable lookup is skipped, not fatal: the next
    run asks again.
    """
    from . import gateways

    since = timezone.now() - timedelta(days=days)
    pending = (Notification.objects
               .filter(provider="twilio", status=NotificationStatus.SENT, sent_at__gte=since)
               .exclude(provider_message_id__isnull=True)
               .exclude(delivery_status__in=FINAL_DELIVERY)
               .select_related("borrower", "loan"))
    checked = fell_back = 0
    for message in pending:
        backend = gateways.TwilioBackend(whatsapp=message.channel == NotificationChannel.WHATSAPP)
        try:
            answer = backend.status(message.provider_message_id)
        except Exception:
            continue
        checked += 1
        if apply_delivery(message, answer.get("status"), answer.get("error_code"),
                          answer.get("error_message"), source="Twilio"):
            fell_back += 1
    return {"checked": checked, "fell_back_to_sms": fell_back}


def mark_sent(ids: list[int] | None = None, as_of=None) -> dict:
    """Mark messages sent WITHOUT delivering them.

    Kept for the one honest use: an operator who exported the queue and sent it
    through an aggregator's own web console needs to reconcile the outbox
    afterwards. It is not the send path — `send()` is — and it records that nothing
    was delivered from here, so the audit trail does not claim otherwise.
    """
    rows = list(due(ids, as_of))
    for message in rows:
        message.status = NotificationStatus.SENT
        message.sent_at = timezone.now()
        message.provider = "manual"
        message.error = "Marked sent by hand; not delivered by this system."
        message.save(update_fields=["status", "sent_at", "provider", "error"])
    return {"marked_sent": len(rows)}


def cancel(ids: list[int]) -> dict:
    count = (Notification.objects
             .filter(id__in=ids, status=NotificationStatus.QUEUED)
             .update(status=NotificationStatus.CANCELLED))
    return {"cancelled": count}
