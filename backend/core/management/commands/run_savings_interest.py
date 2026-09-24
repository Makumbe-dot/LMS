"""Monthly savings interest and the account fee, for Task Scheduler / cron.

    python manage.py run_savings_interest
    python manage.py run_savings_interest --as-of 2026-09-30
    python manage.py run_savings_interest --dormant-after 6

Idempotent within a calendar month: each account records the date it was last
credited, so a second run in the same month credits nothing.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.services.savings import accrue_interest, mark_dormant


class Command(BaseCommand):
    help = "Credit monthly savings interest and take the monthly account fee."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of", help="ISO date to run for (default: today)")
        parser.add_argument("--dormant-after", dest="dormant_after", type=int,
                           help="Also flag accounts with no activity for this many months")

    def handle(self, *args, **options):
        as_of = None
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError:
                raise CommandError("--as-of must be an ISO date, e.g. 2026-09-30")

        with transaction.atomic():
            result = accrue_interest(as_of)
            audit(None, "savings_interest_run", "system", None, str(result))

        self.stdout.write(self.style.SUCCESS(
            f"{result['as_of']}: credited {result['interest_credited']} to "
            f"{result['accounts_credited']} account(s), took {result['fees_taken']} in fees from "
            f"{result['accounts_charged']}"))

        if options.get("dormant_after"):
            with transaction.atomic():
                dormant = mark_dormant(options["dormant_after"], as_of)
                audit(None, "savings_dormancy_run", "system", None, str(dormant))
            self.stdout.write(self.style.SUCCESS(
                f"Marked {dormant['marked_dormant']} account(s) dormant"))
