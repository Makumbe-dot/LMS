"""The scheduled jobs, run by the application and recorded run by run.

The operating system's scheduler (Task Scheduler, cron, the Docker jobs service)
needs to call one thing, `manage.py run_jobs`, as often as it likes: each job
knows when it is due, and a job that has already succeeded for its date is not
run again. Each run is kept with what it reported or the error it raised, so
"did last night's penalties run?" is a page, not a log file on a server.

A job that fails is retried on the next call. Administrators see a warning while
any job's latest run failed, and while a daily job has not succeeded for more
than a day, which is how a scheduler that stopped calling at all gets noticed.
If JOB_ALERT_EMAILS is set, a failure is also emailed.

The database backup stays outside: it is SQL Server's own BACKUP, run by the
scripts that call this (scripts/run_nightly_jobs.ps1, the Docker jobs service).
"""
import io
import logging
import traceback
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from django.conf import settings
from django.core.mail import send_mail
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import JobRun, JobStatus, OrganisationSetting, User

log = logging.getLogger(__name__)

DAILY, MONTHLY = "daily", "monthly"
STALE_AFTER = timedelta(hours=26)  # a nightly job a day and a bit overdue
RUNNING_FOR = timedelta(hours=2)   # longer than this "running" is a crashed run


def _command(name: str, *args) -> Callable[[date], str]:
    def run(as_of: date) -> str:
        out = io.StringIO()
        call_command(name, *args, "--as-of", as_of.isoformat(), stdout=out, stderr=out)
        return out.getvalue().strip()
    return run


def _match_payments(as_of: date) -> str:
    from .inbound import retry_waiting

    result = retry_waiting()
    return f"{result['posted']} waiting payment(s) posted; {result['still_waiting']} still waiting"


def _prune_tokens(as_of: date) -> str:
    out = io.StringIO()
    call_command("prune_tokens", stdout=out, stderr=out)
    return out.getvalue().strip()


def _provisions(as_of: date) -> str:
    # On the 1st, for the month just ended.
    return _command("run_provisions")(as_of - timedelta(days=1))


@dataclass(frozen=True)
class Job:
    key: str
    label: str
    schedule: str
    run: Callable[[date], str]
    about: str


JOBS = [
    Job("penalties", "Penalty accrual", DAILY,
        _command("run_penalties", "--skip-closed"),
        "Penalties on overdue instalments, up to the day."),
    Job("payments", "Match waiting payments", DAILY, _match_payments,
        "Retries incoming payments that matched no loan."),
    Job("reminders", "Borrower reminders", DAILY,
        _command("send_reminders", "--send"),
        "Queues reminders, arrears notices and promise reminders, then sends what is due."),
    Job("tokens", "Prune expired sessions", DAILY, _prune_tokens,
        "Forgets revoked sign-ins that would have expired anyway."),
    Job("savings_interest", "Savings interest", MONTHLY,
        _command("run_savings_interest", "--skip-closed"),
        "On the 1st: interest credited and monthly fees taken."),
    Job("borrowing_interest", "Interest on borrowings", MONTHLY,
        _command("accrue_borrowing_interest", "--skip-closed"),
        "On the 1st: interest accrued on the funding facilities."),
    Job("provisions", "Provision run", MONTHLY, _provisions,
        "On the 1st: IFRS 9 provisions for the month just ended."),
]
BY_KEY = {job.key: job for job in JOBS}


def is_due(job: Job, today: date) -> bool:
    if job.schedule == MONTHLY and today.day != 1:
        return False
    return not JobRun.objects.filter(job=job.key, as_of=today, status=JobStatus.OK).exists()


def run_one(job: Job, as_of: date, user: User | None = None) -> JobRun:
    """Run a job now, whatever its schedule, and keep the outcome."""
    with transaction.atomic():
        busy = (JobRun.objects.select_for_update()
                .filter(job=job.key, status=JobStatus.RUNNING,
                        started_at__gte=timezone.now() - RUNNING_FOR).exists())
        if busy:
            raise BusinessRuleError(f"{job.label} is already running")
        run = JobRun.objects.create(job=job.key, as_of=as_of, triggered_by=user)
    try:
        output = job.run(as_of)
    except BaseException as exc:  # noqa: BLE001 - the run is recorded whatever happened
        run.status = JobStatus.FAILED
        run.error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:4000]
        run.output = traceback.format_exc()[-8000:]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "output", "finished_at"])
        _alert(job, run)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return run
    run.status = JobStatus.OK
    run.output = (output or "")[:8000]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "output", "finished_at"])
    return run


def run_due(today: date | None = None, only: str | None = None,
            skip: list[str] | tuple = ()) -> list[JobRun]:
    """Everything due today, in order. One failure does not stop the others."""
    today = today or date.today()
    runs = []
    for job in JOBS:
        if (only and job.key != only) or job.key in skip:
            continue
        if is_due(job, today):
            runs.append(run_one(job, today))
    return runs


def _alert(job: Job, run: JobRun) -> None:
    recipients = getattr(settings, "JOB_ALERT_EMAILS", []) or []
    log.error("Scheduled job %s failed for %s: %s", job.key, run.as_of, run.error)
    if not recipients:
        return
    org = OrganisationSetting.load().name
    try:
        send_mail(f"[{org}] {job.label} failed", f"{job.label} for {run.as_of} failed:\n\n"
                  f"{run.error}\n\nIt will be retried on the next scheduled run.",
                  None, recipients, fail_silently=False)
    except Exception:  # noqa: BLE001 - an alert that cannot be sent must not hide the failure
        log.exception("Could not email the failure of %s", job.key)


def overview(today: date | None = None) -> list[dict]:
    """Each job with its latest run and last success, from one query over the
    recent history (a monthly job's last success falls inside it too)."""
    today = today or date.today()
    now = timezone.now()
    recent = list(JobRun.objects.filter(started_at__gte=now - timedelta(days=70))
                  .order_by("-started_at", "-id"))
    rows = []
    for job in JOBS:
        mine = [run for run in recent if run.job == job.key]
        latest = mine[0] if mine else None
        ok = next((run for run in mine if run.status == JobStatus.OK), None)
        done_today = any(run.as_of == today and run.status == JobStatus.OK for run in mine)
        due = (job.schedule == DAILY or today.day == 1) and not done_today
        stale = (job.schedule == DAILY and
                 (ok is None or now - (ok.finished_at or ok.started_at) > STALE_AFTER))
        failed = bool(latest and latest.status == JobStatus.FAILED)
        rows.append({
            "key": job.key, "label": job.label, "schedule": job.schedule, "about": job.about,
            "due_today": due,
            "last_status": latest.status if latest else None,
            "last_run_at": latest.started_at if latest else None,
            "last_as_of": latest.as_of if latest else None,
            "last_error": latest.error if failed else "",
            "last_success_at": (ok.finished_at or ok.started_at) if ok else None,
            "stale": stale,
            "needs_attention": failed or stale,
        })
    return rows


def attention(today: date | None = None) -> int:
    """How many jobs an administrator should look at: for the navigation badge."""
    return sum(1 for row in overview(today) if row["needs_attention"])
