"""Queue borrower reminders and arrears notices, for Task Scheduler / cron.

    python manage.py send_reminders                 queue only
    python manage.py send_reminders --send          queue, then mark due ones sent
    python manage.py send_reminders --as-of 2026-09-30 --days-before 5

Idempotent: every message carries a dedupe key, so running it twice in a day
queues nothing extra.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.services.notifications import generate_reminders, mark_sent


class Command(BaseCommand):
    help = "Queue instalment reminders and arrears notices for active loans."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of", help="ISO date to run for (default: today)")
        parser.add_argument("--days-before", dest="days_before", type=int,
                           help="Reminder window in days (default: the organisation setting)")
        parser.add_argument("--send", action="store_true",
                           help="Also mark messages scheduled up to today as sent")

    def handle(self, *args, **options):
        as_of = None
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError:
                raise CommandError("--as-of must be an ISO date, e.g. 2026-09-30")

        with transaction.atomic():
            result = generate_reminders(as_of, options.get("days_before"))
            audit(None, "generate_notifications", "system", None, str(result))

        self.stdout.write(self.style.SUCCESS(
            f"{result['as_of']}: queued {result['reminders_queued']} reminders and "
            f"{result['arrears_notices_queued']} arrears notices"))

        if options["send"]:
            with transaction.atomic():
                sent = mark_sent(as_of=as_of)
                audit(None, "send_notifications", "system", None, str(sent))
            self.stdout.write(self.style.SUCCESS(f"Marked {sent['sent']} message(s) sent"))
