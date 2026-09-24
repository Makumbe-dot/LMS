"""Booking the IFRS 9 expected credit loss provision to the ledger.

Built on LedgerBase, not FeatureTestBase: FeatureTestBase never calls
ensure_chart_of_accounts(), so every ledger posting in that file is silently
skipped. A provisioning test needs a real chart of accounts.
"""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import connection

from core.models import (
    JournalEntry,
    Loan,
    LedgerAccount,
    ProvisionRun,
    ProvisionRunStatus,
    Transaction,
)
from core.services import ledger as gl
from core.services import provisioning
from core.services.amortisation import month_end

from .test_components import LedgerBase, dec


class ProvisionBase(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def book(self, as_of, **body):
        payload = {"as_of": as_of, **body}
        return self.admin.post("/api/provisions/run", payload, format="json")

    def balances(self):
        return {row["code"]: row for row in gl.trial_balance()["rows"]}


class ProvisionRunTests(ProvisionBase):
    def test_a_first_run_books_the_whole_required_provision(self):
        # At 2026-03-31 instalment 1 (due 2026-03-25) is 6 days past due -> stage 1
        # at 1% on a carrying amount of 1000.00.
        response = self.book("2026-03-31")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["period_end"], "2026-03-31")
        self.assertEqual(dec(body["movement"]), dec("10.00"))

        entry = JournalEntry.objects.get(source="provision")
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["5100"].debit, dec("10.00"))
        self.assertEqual(lines["1900"].credit, dec("10.00"))
        self.assertIsNone(entry.transaction_id)
        self.assertIsNone(entry.savings_transaction_id)

        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).provision_held, dec("10.00"))
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_a_second_run_books_only_the_movement(self):
        self.book("2026-03-31")
        # By 2026-07-31 instalment 1 is 128 days past due -> stage 3 at 60%.
        response = self.book("2026-07-31")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(dec(body["provision_required"]), dec("600.00"))
        self.assertEqual(dec(body["provision_before"]), dec("10.00"))
        self.assertEqual(dec(body["movement"]), dec("590.00"))

        self.assertEqual(JournalEntry.objects.filter(source="provision").count(), 2)
        by_code = self.balances()
        # 1900 is typed ASSET (a contra-asset), so its reported balance is negative.
        self.assertEqual(by_code["1900"]["balance"], dec("-600.00"))
        self.assertEqual(by_code["5100"]["balance"], dec("600.00"))

    def test_re_running_a_period_posts_nothing(self):
        first = self.book("2026-03-31").json()
        again = self.book("2026-03-31")
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["created"])
        self.assertEqual(again.json()["run_no"], first["run_no"])
        self.assertEqual(ProvisionRun.objects.count(), 1)
        self.assertEqual(JournalEntry.objects.filter(source="provision").count(), 1)

    def test_force_reverses_the_run_and_reposts_it(self):
        first = self.book("2026-03-31").json()
        forced = self.book("2026-03-31", force=True)
        self.assertEqual(forced.status_code, 201, forced.content)

        original = ProvisionRun.objects.get(run_no=first["run_no"])
        self.assertEqual(original.status, ProvisionRunStatus.REVERSED)
        mirror = {l.account.code: l for l in original.reversal_entry.lines.all()}
        self.assertEqual(mirror["1900"].debit, dec("10.00"))
        self.assertEqual(mirror["5100"].credit, dec("10.00"))

        self.assertEqual(ProvisionRun.objects.filter(
            period_end=date(2026, 3, 31), status=ProvisionRunStatus.POSTED).count(), 1)
        self.assertEqual(provisioning.ledger_provision(), dec("10.00"))
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_a_reversal_is_dated_on_the_original_entry(self):
        """A run and its mirror must land in the same income-statement window."""
        self.book("2026-03-31")
        run = ProvisionRun.objects.get()
        response = self.admin.post(f"/api/provisions/{run.id}/reverse",
                                   {"narration": "booked in error"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        run.refresh_from_db()
        self.assertEqual(run.reversal_entry.entry_date, run.journal_entry.entry_date)
        self.assertEqual(provisioning.ledger_provision(), dec("0.00"))
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).provision_held, dec("0.00"))

    def test_a_release_is_posted_when_the_required_provision_falls(self):
        self.book("2026-07-31")
        self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                         {"amount": "600.00", "txn_date": "2026-08-01"}, format="json")
        response = self.book("2026-08-31")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()

        self.assertLess(dec(body["movement"]), 0)
        entry = JournalEntry.objects.filter(source="provision").order_by("-id").first()
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["1900"].debit, dec(body["movement"]) * -1)
        self.assertEqual(lines["5100"].credit, dec(body["movement"]) * -1)
        # The identity, not a hardcoded figure.
        self.assertEqual(provisioning.ledger_provision(), provisioning.booked_provision())

    def test_the_ledger_provision_always_equals_the_provision_carried(self):
        second = self.make_borrower(national_id="63-777777P63")
        self.disbursed_loan(self.product, second, principal=2000, term=6)

        self.book("2026-04-30")
        self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                         {"amount": "200.00", "txn_date": "2026-05-02"}, format="json")
        self.officer.post(f"/api/loans/{self.loan['id']}/accrue-penalties?as_of=2026-05-20")
        self.book("2026-05-31")

        self.assertEqual(provisioning.ledger_provision(), provisioning.booked_provision())
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_a_run_cannot_be_back_dated_behind_the_latest(self):
        self.book("2026-07-31")
        response = self.book("2026-03-31")
        self.assertEqual(response.status_code, 400)
        self.assertIn("behind the latest posted run", response.json()["detail"])
        self.assertEqual(ProvisionRun.objects.count(), 1)

    def test_an_as_of_inside_the_month_snaps_to_the_month_end(self):
        """Nothing is ever posted with a future entry date."""
        today = date.today()
        response = self.book(today.replace(day=1).isoformat())
        self.assertEqual(response.status_code, 201, response.content)
        run = ProvisionRun.objects.get()
        self.assertEqual(run.period_end, month_end(today))
        self.assertEqual(run.journal_entry.entry_date, today)

    def test_the_filtered_unique_index_exists(self):
        """The one-posted-run-per-period rule is an index, not a convention."""
        constraints = connection.introspection.get_constraints(
            connection.cursor(), "provision_runs")
        self.assertIn("uq_provision_run_posted_period", constraints)

    def test_a_run_is_refused_when_the_provision_account_is_missing(self):
        LedgerAccount.objects.filter(code="1900").delete()
        response = self.book("2026-03-31")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Rebuild", response.json()["detail"])
        # The atomic block rolled the whole run back rather than half-posting.
        self.assertEqual(ProvisionRun.objects.count(), 0)
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).provision_held, dec("0.00"))

    def test_a_provision_run_raises_no_transaction(self):
        before = Transaction.objects.count()
        self.book("2026-03-31")
        self.assertEqual(Transaction.objects.count(), before)

    def test_only_an_admin_may_book_or_reverse(self):
        self.assertEqual(self.officer.post("/api/provisions/run", {}, format="json").status_code,
                         403)
        self.assertEqual(self.teller.post("/api/provisions/run", {}, format="json").status_code,
                         403)
        self.assertEqual(self.officer.get("/api/provisions").status_code, 200)
        self.assertEqual(self.officer.get("/api/provisions/preview").status_code, 200)


class ProvisionReleaseTests(ProvisionBase):
    def test_write_off_releases_the_provision_carried_against_the_loan(self):
        self.book("2026-07-31")
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).provision_held, dec("600.00"))

        self.admin.post(f"/api/loans/{self.loan['id']}/write-off",
                        {"narration": "Absconded"}, format="json")

        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).provision_held, dec("0.00"))
        release = JournalEntry.objects.get(source="provision_rel")
        lines = {line.account.code: line for line in release.lines.all()}
        self.assertEqual(lines["1900"].debit, dec("600.00"))
        self.assertEqual(lines["5100"].credit, dec("600.00"))
        self.assertEqual(release.loan_id, self.loan["id"])

        by_code = self.balances()
        self.assertEqual(by_code["1900"]["balance"], dec("0.00"))
        # Over the loan's life the impairment charge nets to nothing; the real
        # loss sits in 5000.
        self.assertEqual(by_code["5100"]["balance"], dec("0.00"))
        self.assertEqual(by_code["5000"]["balance"], dec("1000.00"))
        self.assertEqual(JournalEntry.objects.get(source="write_off").total_debit,
                         dec("1000.00"))
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_a_closed_loan_is_swept_and_its_provision_released(self):
        self.book("2026-03-31")
        self.teller.post(f"/api/loans/{self.loan['id']}/settle",
                         {"txn_date": "2026-04-10"}, format="json")
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).status, "closed")

        response = self.book("2026-04-30")
        body = response.json()
        self.assertEqual(body["loans_released"], 1)
        self.assertEqual(body["loans_assessed"], 0)
        line = next(l for l in body["lines"] if l["loan_id"] == self.loan["id"])
        self.assertIsNone(line["stage"])
        self.assertEqual(line["loan_status"], "closed")
        self.assertEqual(dec(line["provision_required"]), dec("0.00"))
        self.assertEqual(dec(line["movement"]), dec("-10.00"))
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).provision_held, dec("0.00"))
        self.assertEqual(provisioning.ledger_provision(), dec("0.00"))

    def test_a_reversal_is_refused_once_a_loan_has_moved_on(self):
        self.book("2026-07-31")
        run = ProvisionRun.objects.get()
        self.admin.post(f"/api/loans/{self.loan['id']}/write-off", {"narration": "gone"},
                        format="json")

        response = self.admin.post(f"/api/provisions/{run.id}/reverse",
                                   {"narration": "undo"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn(self.loan["loan_no"], response.json()["detail"])
        run.refresh_from_db()
        self.assertEqual(run.status, ProvisionRunStatus.POSTED)

    def test_a_reversal_is_refused_when_a_later_run_exists(self):
        self.book("2026-04-30")
        earlier = ProvisionRun.objects.get()
        self.book("2026-05-31")
        response = self.admin.post(f"/api/provisions/{earlier.id}/reverse",
                                   {"narration": "undo"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("was booked after this one", response.json()["detail"])


class ProvisionReportTests(ProvisionBase):
    def test_gross_ecl_and_the_bookable_provision_differ_by_unearned_interest(self):
        """Only the provision on the recognised carrying amount is booked.

        Interest is recognised when collected and there is no interest
        receivable, so provisioning gross exposure would put a provision against
        an asset the ledger does not hold.
        """
        report = self.admin.get("/api/reports/ecl?as_of=2026-07-31").json()
        row = next(r for r in report["rows"] if r["loan_id"] == self.loan["id"])
        loan = Loan.objects.get(pk=self.loan["id"])

        self.assertEqual(dec(row["exposure"]) - dec(row["carrying_amount"]),
                         loan.interest_outstanding)
        self.assertEqual(dec(row["provision_required"]), dec("600.00"))
        self.assertGreater(dec(row["provision"]), dec(row["provision_required"]))

    def test_the_ecl_report_shows_booked_against_required(self):
        self.book("2026-03-31")
        report = self.admin.get("/api/reports/ecl?as_of=2026-03-31").json()
        self.assertEqual(dec(report["total_provision_booked"]), dec("10.00"))
        self.assertEqual(dec(report["total_provision_required"]), dec("10.00"))
        self.assertEqual(dec(report["total_provision_movement"]), dec("0.00"))
        self.assertTrue(report["ledger_agrees"])
        self.assertEqual(report["last_run"]["run_no"], ProvisionRun.objects.get().run_no)

    def test_a_branch_slice_cannot_be_reconciled_to_an_institution_account(self):
        self.book("2026-03-31")
        report = self.admin.get(f"/api/reports/ecl?branch_id={self.branch.id}").json()
        self.assertIsNone(report["ledger_provision"])
        self.assertIsNone(report["ledger_agrees"])
        self.assertIsNone(report["total_provision_booked"])

    def test_rebuild_reposts_the_provision_entries(self):
        """The fifth identity has to survive a JournalEntry wipe."""
        self.book("2026-03-31")
        self.assertEqual(provisioning.ledger_provision(), dec("10.00"))

        JournalEntry.objects.all().delete()
        self.assertEqual(provisioning.ledger_provision(), dec("0.00"))
        self.assertEqual(provisioning.booked_provision(), dec("10.00"))

        result = gl.backfill()
        self.assertEqual(result["provision_runs_reposted"], 1)
        self.assertEqual(provisioning.ledger_provision(), provisioning.booked_provision())
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_the_management_command_books_and_is_idempotent(self):
        out = StringIO()
        call_command("run_provisions", "--as-of", "2026-03-31", stdout=out)
        self.assertEqual(ProvisionRun.objects.count(), 1)

        call_command("run_provisions", "--as-of", "2026-03-31", stdout=out)
        self.assertEqual(ProvisionRun.objects.count(), 1)
        self.assertEqual(JournalEntry.objects.filter(source="provision").count(), 1)
        self.assertIn("already provisioned", out.getvalue())

        dry = StringIO()
        call_command("run_provisions", "--as-of", "2026-04-30", "--dry-run", stdout=dry)
        self.assertEqual(ProvisionRun.objects.count(), 1)
        self.assertIn("movement", dry.getvalue())

    def test_preview_posts_nothing(self):
        preview = self.admin.get("/api/provisions/preview?as_of=2026-03-31").json()
        self.assertEqual(dec(preview["movement"]), dec("10.00"))
        self.assertFalse(preview["already_posted"])
        self.assertEqual(ProvisionRun.objects.count(), 0)
        self.assertEqual(JournalEntry.objects.filter(source="provision").count(), 0)
