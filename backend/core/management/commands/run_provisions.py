"""Book the month-end movement in the IFRS 9 expected credit loss provision.

    python manage.py run_provisions
    python manage.py run_provisions --as-of 2026-09-30
    python manage.py run_provisions --as-of 2026-09-30 --dry-run
    python manage.py run_provisions --as-of 2026-09-30 --force

Idempotent per calendar month: a second run for the same period posts nothing
and exits 0, so a retried nightly batch is not a failure.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import audit
from core.exceptions import BusinessRuleError
from core.services import provisioning


class Command(BaseCommand):
    help = "Book the month-end movement in the IFRS 9 expected credit loss provision."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", dest="as_of",
                           help="ISO date inside the month to provision (default: today)")
        parser.add_argument("--dry-run", action="store_true",
                           help="Show what would be posted, and post nothing")
        parser.add_argument("--force", action="store_true",
                           help="Reverse the posted run for that period and re-post it")

    def handle(self, *args, **options):
        as_of = None
        if options.get("as_of"):
            try:
                as_of = date.fromisoformat(options["as_of"])
            except ValueError:
                raise CommandError("--as-of must be an ISO date, e.g. 2026-09-30")

        if options["dry_run"]:
            preview = provisioning.preview(as_of)
            self.stdout.write(
                f"{preview['period_end']}: required {preview['provision_required']}, "
                f"booked {preview['provision_booked']}, movement {preview['movement']} "
                f"across {preview['loans_assessed']} active loan(s) "
                f"and {preview['loans_released']} release(s)")
            if preview["already_posted"]:
                self.stdout.write(self.style.WARNING(
                    f"Already provisioned by {preview['existing_run_no']}; a run would post "
                    f"nothing unless --force is given"))
            return

        try:
            with transaction.atomic():
                run = provisioning.run_provision(None, as_of, force=options["force"])
                if getattr(run, "created_now", False):
                    audit(None, "provision_run", "provision_run", run.id,
                          f"{run.run_no} {run.period_end}: movement {run.movement}")
        except BusinessRuleError as exc:
            raise CommandError(str(exc))

        if getattr(run, "created_now", False):
            self.stdout.write(self.style.SUCCESS(
                f"{run.period_end}: {run.run_no} booked a movement of {run.movement} on a "
                f"required provision of {run.provision_required} across {run.loans_assessed} "
                f"loan(s), releasing {run.loans_released}"))
        else:
            self.stdout.write(self.style.WARNING(
                f"{run.period_end} was already provisioned by {run.run_no}; nothing posted"))
