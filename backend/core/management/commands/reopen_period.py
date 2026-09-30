"""Reopen the most recently closed month.

    python manage.py reopen_period --year 2026 --month 8 \
        --reason "Bank confirmed a 14 August deposit was captured twice"

--reason is required and recorded in the audit trail with the trial balance the
close was signed off on, so the numbers being superseded stay visible.

Only the latest closed month can be reopened: the closed months are a contiguous
run, and putting a hole in the middle would make "the earliest date you can post
to" meaningless. To revisit an earlier month, reopen the later ones first.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.exceptions import BusinessRuleError
from core.services import periods


class Command(BaseCommand):
    help = "Reopen a closed accounting period, recording why."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--month", type=int, required=True)
        parser.add_argument("--reason", required=True,
                            help="At least 10 characters; goes in the audit trail")

    def handle(self, *args, **options):
        if not 1 <= options["month"] <= 12:
            raise CommandError("--month must be 1-12")
        try:
            with transaction.atomic():
                period = periods.reopen_period(options["year"], options["month"], user=None,
                                               reason=options["reason"])
        except BusinessRuleError as exc:
            raise CommandError(str(exc.detail if hasattr(exc, "detail") else exc))

        self.stdout.write(self.style.WARNING(
            f"\n{period.label} is open again (reopen #{period.reopen_count}). The trial balance "
            f"it was closed on — Dr {period.snapshot_debits} / Cr {period.snapshot_credits} — is "
            f"kept on the period row, so any change from here is visible against it."))
        through = periods.closed_through()
        self.stdout.write(
            f"The books are now closed through {through}.\n" if through
            else "Nothing is closed now; every date is postable.\n")
