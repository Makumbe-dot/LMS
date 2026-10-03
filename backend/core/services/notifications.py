"""Borrower messaging outbox.

The generator queues messages; nothing is transmitted here. Marking a batch sent
is a separate, explicit step, so the queue can be reviewed, exported for a bulk
SMS provider, or wired to a real gateway later without touching the loan book.

Every message carries a dedupe_key, so running the generator repeatedly on the
same day queues nothing extra.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone

from ..models import (
    Instalment,
    Loan,
    LoanStatus,
    Notification,
    NotificationChannel,
    NotificationKind,
    NotificationStatus,
    OrganisationSetting,
)
from .amortisation import q
from .loans import arrears, sched

ZERO = Decimal("0")


def _money(value, currency: str) -> str:
    return f"{currency} {q(value):,.2f}"


def _queue(*, borrower, loan, kind, channel, to_address, body, scheduled_for,
           dedupe_key, subject=None) -> Notification | None:
    """Insert one message, ignoring it if the dedupe key is already present."""
    if not to_address:
        return None
    try:
        with transaction.atomic():
            return Notification.objects.create(
                borrower=borrower, loan=loan, kind=kind, channel=channel,
                to_address=to_address, subject=subject, body=body,
                scheduled_for=scheduled_for, dedupe_key=dedupe_key,
            )
    except IntegrityError:
        return None  # already queued on an earlier run


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

    reminders = notices = 0
    for loan in loans:
        borrower = loan.borrower
        phone = borrower.phone

        # 1. instalments falling due inside the reminder window
        for ins in sched(loan):
            if ins.balance <= 0:
                continue
            if as_of <= ins.due_date <= horizon:
                body = (f"Dear {borrower.first_name}, instalment {ins.number} of "
                        f"{_money(ins.balance, currency)} on loan {loan.loan_no} is due on "
                        f"{ins.due_date:%d %b %Y}. Thank you.")
                if _queue(borrower=borrower, loan=loan, kind=NotificationKind.REMINDER,
                          channel=NotificationChannel.SMS, to_address=phone, body=body,
                          scheduled_for=max(as_of, ins.due_date - timedelta(days=days_before)),
                          dedupe_key=f"reminder:{ins.id}"):
                    reminders += 1

        # 2. one arrears notice per loan per day, while it is overdue
        amount, days = arrears(loan, as_of)
        if amount > 0:
            body = (f"Dear {borrower.first_name}, loan {loan.loan_no} is "
                    f"{_money(amount, currency)} in arrears ({days} days). Please pay to avoid "
                    f"further penalties.")
            if _queue(borrower=borrower, loan=loan, kind=NotificationKind.ARREARS,
                      channel=NotificationChannel.SMS, to_address=phone, body=body,
                      scheduled_for=as_of, dedupe_key=f"arrears:{loan.id}:{as_of.isoformat()}"):
                notices += 1

    return {
        "as_of": as_of.isoformat(),
        "reminder_window_days": days_before,
        "reminders_queued": reminders,
        "arrears_notices_queued": notices,
        "total_queued": reminders + notices,
    }


def queue_receipt(loan: Loan, amount: Decimal, txn_id: int, txn_date: date) -> Notification | None:
    """Acknowledge a repayment. Called after a posting is committed."""
    settings_row = OrganisationSetting.load()
    borrower = loan.borrower
    body = (f"Dear {borrower.first_name}, we have received "
            f"{_money(amount, settings_row.currency)} on loan {loan.loan_no}. Balance "
            f"{_money(loan.total_outstanding, settings_row.currency)}. Thank you.")
    return _queue(borrower=borrower, loan=loan, kind=NotificationKind.RECEIPT,
                  channel=NotificationChannel.SMS, to_address=borrower.phone, body=body,
                  scheduled_for=txn_date, dedupe_key=f"receipt:{txn_id}")


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

    queue = due(ids, as_of).select_related("borrower", "loan").order_by("attempts", "id")
    if limit:
        queue = queue[:limit]

    sent = failed = retrying = 0
    errors: list[str] = []
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
            else:
                # Stays QUEUED, so the next run picks it up.
                retrying += 1
        message.save(update_fields=["status", "sent_at", "error", "attempts",
                                    "last_attempt_at", "provider", "provider_message_id"])

    return {
        "sent": sent,
        "failed": failed,
        "retrying": retrying,
        "attempted": sent + failed + retrying,
        "errors": errors[:20],
        "gateway": gateways.describe(),
    }


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
