"""Scheduled jobs: run when due, once, recorded, and failures made visible."""
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.models import JobRun, JobStatus
from core.services import jobs

from .fixtures import LoanFixtures

DAILY = {job.key for job in jobs.JOBS if job.schedule == jobs.DAILY}


def failing_matcher():
    return mock.patch("core.services.inbound.retry_waiting", side_effect=RuntimeError("boom"))


class JobTests(LoanFixtures):
    def setUp(self):
        super().setUp()
        self.loan = self.make_loan()

    def test_an_ordinary_day_runs_the_daily_jobs_once(self):
        runs = jobs.run_due(date(2026, 4, 10))
        self.assertEqual({run.job for run in runs}, DAILY)
        self.assertTrue(all(run.status == JobStatus.OK for run in runs), [r.error for r in runs])
        self.assertEqual(jobs.run_due(date(2026, 4, 10)), [])

    def test_the_first_of_the_month_adds_the_monthly_jobs(self):
        runs = jobs.run_due(date(2026, 5, 1))
        self.assertEqual({run.job for run in runs}, set(jobs.BY_KEY))
        failed = [(run.job, run.error) for run in runs if run.status != JobStatus.OK]
        self.assertEqual(failed, [])

    def test_the_penalty_run_really_runs(self):
        jobs.run_due(date(2026, 4, 10), only="penalties")
        run = JobRun.objects.get(job="penalties")
        self.assertEqual(run.as_of, date(2026, 4, 10))
        detail = self.admin.get(f"/api/loans/{self.loan['id']}").json()
        self.assertGreater(float(detail["penalties_outstanding"]), 0)

    def test_a_failure_is_kept_retried_and_does_not_stop_the_rest(self):
        with failing_matcher():
            runs = jobs.run_due(date(2026, 4, 10))
        failed = [run for run in runs if run.status == JobStatus.FAILED]
        self.assertEqual([run.job for run in failed], ["payments"])
        self.assertIn("boom", failed[0].error)
        self.assertEqual(len(runs), len(DAILY))
        # The next call tries only what has not yet succeeded today.
        again = jobs.run_due(date(2026, 4, 10))
        self.assertEqual([run.job for run in again], ["payments"])
        self.assertEqual(again[0].status, JobStatus.OK)

    @override_settings(JOB_ALERT_EMAILS=["ops@example.com"])
    def test_a_failure_is_emailed_when_asked(self):
        with failing_matcher():
            jobs.run_due(date(2026, 4, 10), only="payments")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Match waiting payments failed", mail.outbox[0].subject)

    def test_failures_and_silence_need_attention(self):
        self.assertEqual(jobs.attention(), len(DAILY))  # nothing has ever run
        jobs.run_due(date.today())
        self.assertEqual(jobs.attention(), 0)
        JobRun.objects.filter(job="tokens").update(
            finished_at=timezone.now() - timedelta(days=2),
            started_at=timezone.now() - timedelta(days=2))
        self.assertEqual(jobs.attention(), 1)

    def test_the_badge_is_for_administrators(self):
        self.assertIn("jobs_needing_attention", self.admin.get("/api/nav-summary").json())
        self.assertNotIn("jobs_needing_attention", self.officer.get("/api/nav-summary").json())

    def test_run_now_is_for_administrators(self):
        self.assertEqual(self.officer.post("/api/jobs/tokens/run").status_code, 403)
        response = self.admin.post("/api/jobs/tokens/run")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["triggered_by_name"], "Admin")
        history = self.officer.get("/api/jobs/runs?job=tokens").json()
        self.assertEqual(history["count"], 1)

    def test_a_job_already_running_is_not_started_twice(self):
        JobRun.objects.create(job="tokens", as_of=date.today())
        self.assertEqual(self.admin.post("/api/jobs/tokens/run").status_code, 400)

    def test_the_command_fails_when_a_job_fails(self):
        with failing_matcher(), self.assertRaises(SystemExit) as stop:
            call_command("run_jobs", "--as-of", "2026-04-10", stdout=StringIO())
        self.assertEqual(stop.exception.code, 1)
