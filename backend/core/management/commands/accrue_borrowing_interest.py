"""Monthly interest accrual on funder borrowings, for Task Scheduler / cron.

    python manage.py accrue_borrowing_interest
    python manage.py accrue_borrowing_interest --as-of 2026-09-30
    python manage.py accrue_borrowing_interest --facility FAC-000001

Catches up any months missed, so a scheduler that was down for a quarter does not
leave two months of a real debt off the books. A second run in the same month
accrues nothing.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.models import FundingFacility
from core.services.funding import accrue_interest
from core.services.periods import is_closed


class Command(BaseCommand):
    help = "Accrue interest owed to funders on drawn facility balances."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of", help="ISO date to accrue up to (default: today)")
        parser.add_argument("--facility", dest="facility",
                            help="Accrue on one facility only, by facility number")
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

        facility = None
        if options.get("facility"):
            facility = FundingFacility.objects.filter(
                facility_no__iexact=options["facility"]).first()
            if facility is None:
                raise CommandError(f"No facility numbered {options['facility']}")

        if options.get("skip_closed") and is_closed(as_of or date.today()):
            target = (as_of or date.today()).isoformat()
            audit(None, "borrowing_interest_skipped", "system", None,
                  f"{target} falls in a closed accounting period")
            self.stdout.write(self.style.WARNING(
                f"{target} falls in a closed accounting period; nothing accrued."))
            return

        with transaction.atomic():
            result = accrue_interest(as_of, facility)
            audit(None, "borrowing_interest_run", "system", None, str(result))

        self.stdout.write(self.style.SUCCESS(
            f"{result['as_of']}: accrued {result['interest_accrued']} over "
            f"{result['months_posted']} month(s) on {result['facilities_accrued']} facility/ies"))

        # Never silent: a month that could not be accrued is a real debt missing
        # from the books, and the operator needs to know which one.
        if result["months_skipped"]:
            self.stdout.write(self.style.WARNING(
                f"{result['months_skipped']} month(s) fell in a closed accounting period and "
                f"were NOT accrued: {', '.join(result['skipped'])}. Reopen the period to pick "
                f"them up."))
