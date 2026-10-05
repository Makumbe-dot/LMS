"""Month-end interest accrual at the effective rate (IFRS 9).

    python manage.py accrue_interest
    python manage.py accrue_interest --as-of 2026-09-30

Only under the effective interest method (Settings). Idempotent: an instalment
period is accrued once, so a retried batch raises nothing more.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.exceptions import BusinessRuleError
from core.services.eir import accrue_interest


class Command(BaseCommand):
    help = "Recognise interest at the effective rate for every instalment period that has ended."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of", help="ISO date (default: today)")

    def handle(self, *args, **options):
        as_of = None
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError:
                raise CommandError("--as-of must be an ISO date, e.g. 2026-09-30")
        try:
            with transaction.atomic():
                result = accrue_interest(as_of)
                audit(None, "accrue_interest", "system", None, str(result))
        except BusinessRuleError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"{result['as_of']}: {result['loans_accrued']} loan(s) accrued, interest income "
            f"{result['interest_income']}"))
