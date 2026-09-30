"""End-of-day penalty accrual, for Task Scheduler / cron.

    python manage.py run_penalties
    python manage.py run_penalties --as-of 2026-09-30
    python manage.py run_penalties --as-of 2026-09-30 --skip-closed

Idempotent: each instalment records the date penalties were accrued to, so
running it twice in one day charges nothing extra.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.services.penalties import accrue_penalties
from core.services.periods import is_closed


class Command(BaseCommand):
    help = "Accrue late-payment penalties on overdue instalments across all active loans."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of",
                           help="ISO date to accrue up to (default: today)")
        parser.add_argument("--skip-closed", action="store_true", dest="skip_closed",
                            help="Exit 0 with a warning when the date falls in a closed "
                                 "accounting period, instead of failing")

    def handle(self, *args, **options):
        as_of = None
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError:
                raise CommandError("--as-of must be an ISO date, e.g. 2026-09-30")

        # A month closed at 09:00 on the 1st must not fail that evening's batch.
        if options.get("skip_closed") and is_closed(as_of or date.today()):
            target = (as_of or date.today()).isoformat()
            audit(None, "run_penalties_skipped", "system", None,
                  f"{target} falls in a closed accounting period")
            self.stdout.write(self.style.WARNING(
                f"{target} falls in a closed accounting period; nothing accrued."))
            return

        with transaction.atomic():
            result = accrue_penalties(as_of)
            audit(None, "run_penalties", "system", None, str(result))

        self.stdout.write(self.style.SUCCESS(
            f"{result['as_of']}: penalties on {result['loans_penalised']} loans, "
            f"total {result['total_penalties']}"))
