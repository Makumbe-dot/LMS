"""End-of-day penalty accrual, for Task Scheduler / cron.

    python manage.py run_penalties
    python manage.py run_penalties --as-of 2026-09-30

Idempotent: each instalment records the date penalties were accrued to, so
running it twice in one day charges nothing extra.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.services.penalties import accrue_penalties


class Command(BaseCommand):
    help = "Accrue late-payment penalties on overdue instalments across all active loans."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of",
                           help="ISO date to accrue up to (default: today)")

    def handle(self, *args, **options):
        as_of = None
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError:
                raise CommandError("--as-of must be an ISO date, e.g. 2026-09-30")

        with transaction.atomic():
            result = accrue_penalties(as_of)
            audit(None, "run_penalties", "system", None, str(result))

        self.stdout.write(self.style.SUCCESS(
            f"{result['as_of']}: penalties on {result['loans_penalised']} loans, "
            f"total {result['total_penalties']}"))
