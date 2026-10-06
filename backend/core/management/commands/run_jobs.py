"""Run the scheduled jobs that are due. Point the operating system's scheduler at
this, as often as you like: a job already done for the day is not run again.

    python manage.py run_jobs                 # everything due today
    python manage.py run_jobs --only penalties
    python manage.py run_jobs --list

Exits 1 when any job failed, so the scheduler's own history shows it too.
"""
import sys
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import JobStatus
from core.services import jobs


class Command(BaseCommand):
    help = "Run the scheduled jobs that are due (services/jobs.py)."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=list(jobs.BY_KEY), help="Run just this job")
        parser.add_argument("--as-of", dest="as_of", help="ISO date to run for (default: today)")
        parser.add_argument("--skip", action="append", default=[], choices=list(jobs.BY_KEY),
                            help="Leave this job out (repeatable)")
        parser.add_argument("--list", action="store_true", help="Say what is due and stop")

    def handle(self, *args, **options):
        try:
            today = date.fromisoformat(options["as_of"]) if options.get("as_of") else date.today()
        except ValueError:
            raise CommandError("--as-of must be YYYY-MM-DD")
        if options["list"]:
            for row in jobs.overview(today):
                due = "due" if row["due_today"] else "done"
                flag = " (needs attention)" if row["needs_attention"] else ""
                self.stdout.write(f"{row['key']:<20} {row['schedule']:<8} {due}{flag}")
            return
        runs = jobs.run_due(today, options.get("only"), skip=options["skip"])
        if not runs:
            self.stdout.write("Nothing due.")
        for run in runs:
            label = jobs.BY_KEY[run.job].label
            if run.status == JobStatus.OK:
                self.stdout.write(self.style.SUCCESS(f"{label}: ok"))
                if run.output:
                    self.stdout.write("  " + run.output.replace("\n", "\n  "))
            else:
                self.stdout.write(self.style.ERROR(f"{label}: FAILED - {run.error}"))
        if any(run.status == JobStatus.FAILED for run in runs):
            sys.exit(1)
