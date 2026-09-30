"""Close a month to further postings.

    python manage.py close_period --year 2026 --month 8 --dry-run
    python manage.py close_period --year 2026 --month 8 --note "Board pack issued"
    python manage.py close_period --year 2026 --month 8 --force

--dry-run prints the pre-close checks and the trial balance that would be frozen,
and changes nothing. Read it before closing anything for real.

Months close in order and only forwards, so closing August closes everything up
to 31 August whether or not July was closed separately.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.exceptions import BusinessRuleError
from core.services import periods


class Command(BaseCommand):
    help = "Close an accounting period, snapshotting the trial balance it is signed off on."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True)
        parser.add_argument("--month", type=int, required=True)
        parser.add_argument("--note", help="Recorded on the period row and in the audit trail")
        parser.add_argument("--force", action="store_true",
                            help="Close despite a failing overridable check, recording which")
        parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                            help="Print the checks and the trial balance; change nothing")

    def handle(self, *args, **options):
        year, month = options["year"], options["month"]
        if not 1 <= month <= 12:
            raise CommandError("--month must be 1-12")

        try:
            checks = periods.preflight(year, month)
        except BusinessRuleError as exc:
            raise CommandError(str(exc.detail if hasattr(exc, "detail") else exc))

        self.stdout.write(f"\n{checks['label']}  ({checks['start_date']} to {checks['end_date']})")
        self.stdout.write("-" * 78)
        for check in checks["checks"]:
            if check["passed"]:
                mark, style = "PASS", self.style.SUCCESS
            elif check["blocking"]:
                mark, style = "FAIL", self.style.ERROR
            else:
                mark, style = "WARN", self.style.WARNING
            self.stdout.write(style(f"  {mark}  {check['label']}"))
            self.stdout.write(f"        {check['detail']}")

        balance = checks["trial_balance"]
        self.stdout.write("-" * 78)
        self.stdout.write(f"  Trial balance for the month: Dr {balance['total_debit']} / "
                          f"Cr {balance['total_credit']} over {checks['entries']} entries")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\n--dry-run: nothing was closed.\n"))
            return

        try:
            with transaction.atomic():
                period = periods.close_period(year, month, user=None,
                                              note=options.get("note"),
                                              force=options["force"])
        except BusinessRuleError as exc:
            raise CommandError(str(exc.detail if hasattr(exc, "detail") else exc))

        self.stdout.write(self.style.SUCCESS(
            f"\n{period.label} closed. The earliest date you can post to is now "
            f"{periods.earliest_postable_date()}.\n"))
