"""Queue borrower reminders and arrears notices, for Task Scheduler / cron.

    python manage.py send_reminders                 queue only
    python manage.py send_reminders --send          queue, then deliver what is due
    python manage.py send_reminders --as-of 2026-09-30 --days-before 5
    python manage.py send_reminders --send --limit 50

Idempotent: every message carries a dedupe key, so running it twice in a day
queues nothing extra, and a message that failed stays queued for the next run
until it has had MESSAGE_MAX_ATTEMPTS.

--send DELIVERS through the configured gateway. On a development machine that is
the console backend, which logs rather than sends — deliberately, so a seeded
database cannot text real-looking numbers.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.services.notifications import generate_reminders, send


class Command(BaseCommand):
    help = "Queue instalment reminders and arrears notices, and optionally deliver them."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of", help="ISO date to run for (default: today)")
        parser.add_argument("--days-before", dest="days_before", type=int,
                           help="Reminder window in days (default: the organisation setting)")
        parser.add_argument("--send", action="store_true",
                           help="Deliver messages scheduled up to today through the gateway")
        parser.add_argument("--limit", type=int,
                           help="Deliver at most this many, for a first run on a big queue")

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

        if not options["send"]:
            return
        from core.services.communications import rules

        if not rules()["auto_send"]:
            self.stdout.write(self.style.WARNING(
                "Automatic sending is off (Communications > Automation): the messages wait "
                "in the Outbox for someone to send them."))
            return

        outcome = send(as_of=as_of, limit=options.get("limit"))
        audit(None, "send_notifications", "system", None, str(
            {k: v for k, v in outcome.items() if k != "gateway"}))

        gateway = outcome["gateway"]
        self.stdout.write(self.style.SUCCESS(
            f"Delivered {outcome['sent']} of {outcome['attempted']} message(s) "
            f"via SMS {gateway['sms_backend']}, WhatsApp {gateway['whatsapp_backend']}, "
            f"email {gateway['email_backend']}"))
        if outcome["retrying"]:
            self.stdout.write(self.style.WARNING(
                f"{outcome['retrying']} will be retried on the next run "
                f"(up to {gateway['max_attempts']} attempts)"))
        if outcome["failed"]:
            self.stdout.write(self.style.ERROR(
                f"{outcome['failed']} gave up permanently:"))
            for line in outcome["errors"]:
                self.stdout.write(self.style.ERROR(f"  {line}"))
        if not (gateway["sms_delivers"] or gateway["email_delivers"]
                or gateway["whatsapp_delivers"]):
            self.stdout.write(self.style.WARNING(
                "Nothing actually left the building: no channel is set to deliver. "
                "See Communications > Channels."))
