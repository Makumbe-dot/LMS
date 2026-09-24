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


def mark_sent(ids: list[int] | None = None, as_of=None) -> dict:
    """Mark queued messages as sent.

    This is the seam a real SMS or email gateway plugs into: call the provider
    per message and record success or failure instead of blanket-marking.
    """
    qs = Notification.objects.filter(status=NotificationStatus.QUEUED)
    if ids:
        qs = qs.filter(id__in=ids)
    else:
        qs = qs.filter(scheduled_for__lte=as_of or date.today())
    count = qs.update(status=NotificationStatus.SENT, sent_at=timezone.now())
    return {"sent": count}


def cancel(ids: list[int]) -> dict:
    count = (Notification.objects
             .filter(id__in=ids, status=NotificationStatus.QUEUED)
             .update(status=NotificationStatus.CANCELLED))
    return {"cancelled": count}
