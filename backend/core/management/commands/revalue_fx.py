"""Restate every open foreign-currency loan, savings account and facility at the
closing rate.

    python manage.py revalue_fx
    python manage.py revalue_fx --as-of 2026-09-30
    python manage.py revalue_fx --as-of 2026-09-30 --dry-run

Run it at every month end after the closing rates are entered. A run at a rate
the balances already carry posts nothing, so a retried batch is not a failure.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.exceptions import BusinessRuleError
from core.services import fx


class Command(BaseCommand):
    help = ("Restate open foreign-currency loans, savings accounts and facilities at the "
            "closing exchange rate.")

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of", help="ISO date (default: today)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Show what would move, and post nothing")

    def handle(self, *args, **options):
        as_of = date.today()
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError:
                raise CommandError("--as-of must be an ISO date, e.g. 2026-09-30")

        plan = fx.preview(as_of)
        if plan["missing_rates"]:
            raise CommandError(f"No rate on or before {as_of} for "
                               f"{', '.join(plan['missing_rates'])}")
        if options["dry_run"] or not plan["lines"]:
            self.stdout.write(f"{as_of}: {plan['loans']} loan(s), {plan['savings_accounts']} "
                              f"savings account(s), {plan['facilities']} facility(ies), "
                              f"movement {plan['movement']} {plan['base_currency']}")
            for line in plan["lines"]:
                self.stdout.write(f"  {line['reference']} {line['currency']} "
                                  f"{line['old_rate']} -> {line['new_rate']}: {line['movement']}")
            return
        try:
            run = fx.revalue(as_of, None)
        except BusinessRuleError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"{run.run_no}: {run.loans_revalued} loan(s), {run.savings_revalued} savings "
            f"account(s) and {run.facilities_revalued} facility(ies) restated at {as_of}, "
            f"movement {run.movement} {run.base_currency}"))
