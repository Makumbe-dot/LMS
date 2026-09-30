"""Period close: a closed month refuses new postings.

NOTHING IN A SHARED FIXTURE MAY EVER CLOSE A PERIOD. Every other test file in this
suite posts with dates in 2025-01, 2026-03, 2026-04, 2026-05 and 2026-08, and none
of them creates an AccountingPeriod row. A closed period in a shared setUp would
fail roughly forty tests at once. Close inside the test that needs it.
"""
from datetime import date
from decimal import Decimal

from django.core.management import CommandError, call_command

from core.models import (
    AccountingPeriod,
    AuditLog,
    JournalEntry,
    PeriodState,
    SavingsProduct,
    Transaction,
    TxnType,
)
from core.services import ledger as gl
from core.services import periods, savings

from .test_components import LedgerBase, dec


class PeriodCloseBase(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def close(self, year, month, **kwargs):
        return periods.close_period(year, month, user=None, **kwargs)


# ------------------------------------------------------------------ the default
class NothingClosedTests(PeriodCloseBase):
    """The decision that lets this ship onto a book with back-dated history."""

    def test_with_no_period_rows_everything_posts(self):
        self.assertEqual(AccountingPeriod.objects.count(), 0)
        loan = self.disbursed_loan(self.product, self.borrower)
        response = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                                    {"amount": "100.00", "txn_date": "2026-03-25"},
                                    format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(
            Transaction.objects.filter(pk=response.json()["id"],
                                       journal_entry__isnull=False).exists())

    def test_the_status_endpoint_says_everything_is_open(self):
        body = self.teller.get("/api/periods/status").json()
        self.assertTrue(body["is_open"])
        self.assertIsNone(body["closed_through"])
        self.assertIsNone(body["earliest_postable_date"])


# ------------------------------------------------------------------ the guard
class GuardTests(PeriodCloseBase):
    def setUp(self):
        super().setUp()
        self.loan = self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)

    def test_a_repayment_into_a_closed_month_is_refused_with_409(self):
        response = self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                                    {"amount": "100.00", "txn_date": "2026-03-25"},
                                    format="json")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("closed accounting period", response.json()["detail"])
        self.assertIn("2026-04-01", response.json()["detail"])

    def test_the_refused_repayment_changed_nothing(self):
        """pre_save, not post_save: no row, no allocation, no balance movement."""
        before_txns = Transaction.objects.count()
        before_entries = JournalEntry.objects.count()
        before = self.teller.get(f"/api/loans/{self.loan['id']}").json()
        instalment_paid = before["schedule"][0]["principal_paid"]

        self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                         {"amount": "100.00", "txn_date": "2026-03-25"}, format="json")

        self.assertEqual(Transaction.objects.count(), before_txns)
        self.assertEqual(JournalEntry.objects.count(), before_entries)
        after = self.teller.get(f"/api/loans/{self.loan['id']}").json()
        self.assertEqual(after["principal_outstanding"], before["principal_outstanding"])
        self.assertEqual(after["schedule"][0]["principal_paid"], instalment_paid)

    def test_a_later_date_still_posts(self):
        response = self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                                    {"amount": "100.00", "txn_date": "2026-04-02"},
                                    format="json")
        self.assertEqual(response.status_code, 201, response.content)

    def test_the_guard_cannot_be_bypassed_by_objects_create(self):
        """No view, no serializer, no service. Straight at the model."""
        from core.exceptions import PeriodClosedError
        from core.models import Loan

        loan = Loan.objects.get(pk=self.loan["id"])
        with self.assertRaises(PeriodClosedError):
            Transaction.objects.create(loan=loan, txn_type=TxnType.REPAYMENT,
                                       txn_date=date(2026, 3, 20), amount=dec("50.00"))

    def test_a_journal_entry_dated_into_a_closed_month_is_refused(self):
        from core.exceptions import PeriodClosedError

        with self.assertRaises(PeriodClosedError):
            JournalEntry.objects.create(entry_no="JE-TEST", entry_date=date(2026, 3, 10),
                                        narration="by hand", source="manual")

    def test_updating_an_existing_posting_in_a_closed_month_still_works(self):
        """The guard is for new postings. Marking one reversed is not a new posting."""
        repayment = self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                                     {"amount": "100.00", "txn_date": "2026-04-05"},
                                     format="json").json()
        self.close(2026, 4)

        txn = Transaction.objects.get(pk=repayment["id"])
        txn.narration = "annotated after the close"
        txn.save(update_fields=["narration"])  # must not raise
        self.assertEqual(Transaction.objects.get(pk=txn.id).narration,
                         "annotated after the close")

    def test_a_disbursement_into_a_closed_month_is_refused(self):
        other = self.make_borrower(national_id="63-999999Z63")
        created = self.officer.post("/api/loans", {
            "borrower_id": other["id"], "product_id": self.product["id"],
            "principal": 500, "term_months": 3,
        }, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        second = created.json()
        self.admin.post(f"/api/loans/{second['id']}/approve")
        response = self.officer.post(f"/api/loans/{second['id']}/disburse",
                                     {"disbursement_date": "2026-03-15"}, format="json")
        self.assertEqual(response.status_code, 409, response.content)
        # The application and the approval were not postings and stand.
        self.assertEqual(self.officer.get(f"/api/loans/{second['id']}").json()["status"],
                         "approved")

    def test_a_charge_into_a_closed_month_is_refused(self):
        response = self.officer.post(f"/api/loans/{self.loan['id']}/charges",
                                     {"name": "Stamp duty", "amount": "10.00",
                                      "collection": "counter", "applied_on": "2026-03-20"},
                                     format="json")
        self.assertEqual(response.status_code, 409, response.content)

    def test_penalty_accrual_into_a_closed_month_is_refused(self):
        response = self.officer.post(
            f"/api/loans/{self.loan['id']}/accrue-penalties?as_of=2026-03-31")
        self.assertEqual(response.status_code, 409, response.content)


# ------------------------------------------------------------------ savings
class SavingsGuardTests(PeriodCloseBase):
    def test_a_deposit_into_a_closed_month_leaves_the_balance_alone(self):
        from core.models import Borrower, User

        product = SavingsProduct.objects.create(code="SAV", name="Ordinary")
        borrower = Borrower.objects.get(pk=self.borrower["id"])
        admin = User.objects.get(username="admin")
        account = savings.open_account(borrower, product, admin, opening_deposit=dec("100.00"),
                                       opened_on=date(2026, 3, 1))
        self.close(2026, 3)

        from core.exceptions import PeriodClosedError

        with self.assertRaises(PeriodClosedError):
            savings.deposit(account, admin, dec("40.00"), date(2026, 3, 20))

        account.refresh_from_db()
        self.assertEqual(account.balance, dec("100.00"))
        self.assertEqual(account.transactions.count(), 1)


# ------------------------------------------------------------------ the repair path
class BackfillTests(PeriodCloseBase):
    def test_backfill_may_post_into_a_closed_month_and_says_it_did(self):
        """A transaction that already exists must be accountable for, closed or not."""
        loan = self.disbursed_loan(self.product, self.borrower)
        repayment = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                                     {"amount": "100.00", "txn_date": "2026-03-25"},
                                     format="json").json()
        self.close(2026, 3)

        entry_id = Transaction.objects.get(pk=repayment["id"]).journal_entry.id
        JournalEntry.objects.filter(pk=entry_id).delete()
        AuditLog.objects.filter(action="post_into_closed_period").delete()

        result = gl.backfill()

        self.assertGreaterEqual(result["posted"], 1)
        self.assertTrue(Transaction.objects.filter(pk=repayment["id"],
                                                   journal_entry__isnull=False).exists())
        self.assertTrue(AuditLog.objects.filter(action="post_into_closed_period").exists(),
                        "a repair that posted into a closed month must say so")

    def test_backfill_with_nothing_to_repair_writes_no_audit_row(self):
        """The audit row is about what happened, not about the button being pressed."""
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)
        AuditLog.objects.filter(action="post_into_closed_period").delete()

        gl.backfill()

        self.assertFalse(AuditLog.objects.filter(action="post_into_closed_period").exists())


# ------------------------------------------------------------------ bulk import
class BulkImportTests(PeriodCloseBase):
    def test_a_closed_dated_row_is_marked_invalid_without_killing_the_file(self):
        from core.services import imports

        loan = self.disbursed_loan(self.product, self.borrower)
        loan_no = loan["loan_no"]
        self.close(2026, 3)

        rows = imports.parse(
            f"loan_no,amount,date\n{loan_no},50.00,2026-03-20\n{loan_no},60.00,2026-04-20\n"
            .encode())
        result = imports.validate(rows)

        self.assertEqual(result["valid_rows"], 1)
        self.assertEqual(result["invalid_rows"], 1)
        self.assertIn("closed accounting period", result["rows"][0]["error"])
        self.assertEqual(result["total_amount"], dec("60.00"))


# ------------------------------------------------------------------ preflight
class PreflightTests(PeriodCloseBase):
    def test_a_month_that_has_not_ended_cannot_be_closed(self):
        today = date.today()
        checks = periods.preflight(today.year, today.month)
        ended = next(c for c in checks["checks"] if c["key"] == "month_has_ended")
        self.assertFalse(ended["passed"])
        self.assertFalse(checks["can_close"])
        self.assertFalse(checks["can_force"], "force must not open a month still running")

    def test_fee_transactions_do_not_fail_the_every_posting_check(self):
        """The disbursement fees raise a FEE transaction that posts no entry.

        This is the check that would otherwise be red on every real book, because
        the demo product carries 3% admin and 1% credit life.
        """
        self.disbursed_loan(self.product, self.borrower)
        self.assertTrue(
            Transaction.objects.filter(txn_type=TxnType.FEE, journal_entry__isnull=True).exists(),
            "the fixture no longer produces an un-entried FEE; this test has lost its point")

        checks = periods.preflight(2026, 3)
        posted = next(c for c in checks["checks"] if c["key"] == "every_posting_has_an_entry")
        self.assertTrue(posted["passed"], posted["detail"])
        self.assertTrue(checks["can_close"], [c for c in checks["checks"] if not c["passed"]])

    def test_a_missing_journal_entry_does_fail_it_and_force_gets_past(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        repayment = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                                     {"amount": "100.00", "txn_date": "2026-03-25"},
                                     format="json").json()
        JournalEntry.objects.filter(transaction_id=repayment["id"]).delete()

        checks = periods.preflight(2026, 3)
        posted = next(c for c in checks["checks"] if c["key"] == "every_posting_has_an_entry")
        self.assertFalse(posted["passed"])
        self.assertIn("repayment", posted["detail"])
        self.assertFalse(checks["can_close"])
        self.assertTrue(checks["can_force"])

        period = self.close(2026, 3, force=True)
        self.assertEqual(period.state, PeriodState.CLOSED)
        self.assertIn("Closed over:", period.note)

    def test_closing_backwards_is_refused_and_force_cannot_override_it(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 4)

        checks = periods.preflight(2026, 3)
        forwards = next(c for c in checks["checks"] if c["key"] == "closes_forwards")
        self.assertFalse(forwards["passed"])
        self.assertFalse(checks["can_force"])
        with self.assertRaises(Exception) as caught:
            self.close(2026, 3, force=True)
        self.assertIn("already signed off", str(caught.exception))

    def test_a_later_month_can_be_closed_and_absorbs_the_ones_between(self):
        """Closing a month means the books are closed through it, full stop."""
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)

        checks = periods.preflight(2026, 6)
        self.assertTrue(checks["can_close"], [c for c in checks["checks"] if not c["passed"]])
        self.close(2026, 6)

        self.assertEqual(periods.closed_through(), date(2026, 6, 30))
        self.assertTrue(periods.is_closed(date(2026, 5, 10)))
        self.assertFalse(AccountingPeriod.objects.filter(year=2026, month=5).exists())

    def test_the_register_offers_close_on_every_month_it_would_accept(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)
        body = self.admin.get("/api/periods?year=2026").json()
        by_month = {m["month"]: m for m in body["months"]}
        self.assertFalse(by_month[3]["closable"], "already closed")
        self.assertTrue(by_month[4]["closable"])
        self.assertTrue(by_month[6]["closable"], "a later ended month is equally legal")
        self.assertFalse(by_month[12]["closable"], "December 2026 has not ended")

    def test_closing_twice_is_refused(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)
        with self.assertRaises(Exception) as caught:
            self.close(2026, 3)
        self.assertIn("not already closed", str(caught.exception))

    def test_advisory_checks_never_block(self):
        """A stale penalty accrual is worth saying and not worth refusing over."""
        self.disbursed_loan(self.product, self.borrower)
        checks = periods.preflight(2026, 4)
        advisory = [c for c in checks["checks"] if not c["blocking"]]
        self.assertEqual(len(advisory), 3)
        self.assertTrue(checks["can_close"])


# ------------------------------------------------------------------ the snapshot
class SnapshotTests(PeriodCloseBase):
    def test_closing_freezes_the_trial_balance_for_the_month(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": "197.02", "txn_date": "2026-03-25"}, format="json")

        expected = gl.trial_balance(date(2026, 3, 1), date(2026, 3, 31))
        period = self.close(2026, 3)

        self.assertEqual(period.snapshot_debits, expected["total_debit"])
        self.assertEqual(period.snapshot_credits, expected["total_credit"])
        self.assertEqual(
            period.snapshot_entries,
            JournalEntry.objects.filter(entry_date__range=(date(2026, 3, 1),
                                                           date(2026, 3, 31))).count())
        self.assertGreater(period.snapshot_debits, Decimal("0"))

    def test_the_snapshot_survives_a_reopen_and_further_postings(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        period = self.close(2026, 3)
        frozen = period.snapshot_debits

        periods.reopen_period(2026, 3, None, "Bank confirmed a double capture")
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": "100.00", "txn_date": "2026-03-28"}, format="json")

        period.refresh_from_db()
        self.assertEqual(period.snapshot_debits, frozen,
                         "reopening must not rewrite what the month was closed on")
        reclosed = self.close(2026, 3)
        self.assertGreater(reclosed.snapshot_debits, frozen)


# ------------------------------------------------------------------ reopening
class ReopenTests(PeriodCloseBase):
    def setUp(self):
        super().setUp()
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)

    def test_a_reason_is_required_and_audited(self):
        response = self.admin.post("/api/periods/2026-3/reopen", {"reason": "oops"},
                                   format="json")
        self.assertEqual(response.status_code, 400, response.content)

        response = self.admin.post("/api/periods/2026-3/reopen",
                                   {"reason": "Bank confirmed a double capture on 14 March"},
                                   format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["period"]["reopen_count"], 1)
        self.assertIsNone(response.json()["closed_through"])

        entry = AuditLog.objects.filter(action="reopen_period").first()
        self.assertIsNotNone(entry)
        self.assertIn("double capture", entry.detail)
        self.assertIn("Trial balance at the close being superseded", entry.detail)

    def test_reopening_lets_the_month_post_again(self):
        loan_id = self.admin.get("/api/loans").json()["results"][0]["id"]
        self.assertEqual(
            self.teller.post(f"/api/loans/{loan_id}/repayments",
                             {"amount": "50.00", "txn_date": "2026-03-20"},
                             format="json").status_code, 409)
        periods.reopen_period(2026, 3, None, "Correcting a mis-captured receipt")
        self.assertEqual(
            self.teller.post(f"/api/loans/{loan_id}/repayments",
                             {"amount": "50.00", "txn_date": "2026-03-20"},
                             format="json").status_code, 201)

    def test_only_the_latest_closed_month_can_be_reopened(self):
        self.close(2026, 4)
        response = self.admin.post("/api/periods/2026-3/reopen",
                                   {"reason": "Need to revisit the March numbers"},
                                   format="json")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("April 2026", response.json()["detail"])
        # Reopening April first, then March, works.
        self.assertEqual(
            self.admin.post("/api/periods/2026-4/reopen",
                            {"reason": "Need to revisit both months"},
                            format="json").status_code, 200)
        self.assertEqual(
            self.admin.post("/api/periods/2026-3/reopen",
                            {"reason": "Need to revisit the March numbers"},
                            format="json").status_code, 200)

    def test_reopening_a_month_that_was_never_closed_is_refused(self):
        response = self.admin.post("/api/periods/2026-7/reopen",
                                   {"reason": "There is nothing here to reopen"},
                                   format="json")
        self.assertEqual(response.status_code, 400, response.content)


# ------------------------------------------------------------------ contiguity
class ClosedThroughTests(PeriodCloseBase):
    def test_closing_a_month_closes_everything_before_it(self):
        """Closing August means the books are closed through August, full stop."""
        self.disbursed_loan(self.product, self.borrower, disbursement_date="2026-03-01")
        self.close(2026, 5)

        self.assertEqual(periods.closed_through(), date(2026, 5, 31))
        self.assertEqual(periods.earliest_postable_date(), date(2026, 6, 1))
        # March has no row of its own and is still shut.
        self.assertFalse(AccountingPeriod.objects.filter(year=2026, month=3).exists())
        self.assertTrue(periods.is_closed(date(2026, 3, 15)))

    def test_the_register_says_a_month_shut_by_implication_is_shut(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 5)
        body = self.teller.get("/api/periods?year=2026").json()
        march = next(m for m in body["months"] if m["month"] == 3)
        self.assertEqual(march["state"], "closed")
        self.assertTrue(march["closed_by_implication"])
        self.assertIsNone(march["period"])
        may = next(m for m in body["months"] if m["month"] == 5)
        self.assertFalse(may["closed_by_implication"])
        self.assertIsNotNone(may["period"])

    def test_the_status_endpoint_reports_the_earliest_postable_date(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 4)
        body = self.teller.get("/api/periods/status?on=2026-04-15").json()
        self.assertEqual(body["state"], "closed")
        self.assertFalse(body["is_open"])
        self.assertEqual(body["earliest_postable_date"], "2026-05-01")

    def test_the_register_names_the_next_month_to_close(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)
        body = self.admin.get("/api/periods?year=2026").json()
        self.assertEqual(body["next_to_close"]["month"], 4)
        self.assertEqual(body["closed_through"], "2026-03-31")


# ------------------------------------------------------------------ permissions
class PermissionTests(PeriodCloseBase):
    def setUp(self):
        super().setUp()
        self.disbursed_loan(self.product, self.borrower)

    def test_every_role_reads_the_register_and_the_status(self):
        for client in (self.admin, self.officer, self.teller):
            self.assertEqual(client.get("/api/periods").status_code, 200)
            self.assertEqual(client.get("/api/periods/status").status_code, 200)

    def test_preflight_is_for_officers_and_above(self):
        self.assertEqual(self.admin.get("/api/periods/2026-3/preflight").status_code, 200)
        self.assertEqual(self.officer.get("/api/periods/2026-3/preflight").status_code, 200)
        self.assertEqual(self.teller.get("/api/periods/2026-3/preflight").status_code, 403)

    def test_only_an_admin_may_close_or_reopen(self):
        for client in (self.officer, self.teller):
            self.assertEqual(client.post("/api/periods/2026-3/close", {}, format="json")
                             .status_code, 403)
            self.assertEqual(client.post("/api/periods/2026-3/reopen",
                                         {"reason": "Not my decision to make"}, format="json")
                             .status_code, 403)
        self.assertEqual(self.admin.post("/api/periods/2026-3/close", {}, format="json")
                         .status_code, 200)

    def test_a_nonsense_month_is_refused(self):
        """\\d{1,2} matches 0 and 13, so the view has to say so itself."""
        self.assertEqual(self.admin.get("/api/periods/2026-13").status_code, 400)
        self.assertEqual(self.admin.get("/api/periods/2026-0/preflight").status_code, 400)

    def test_a_month_never_closed_has_no_detail_row(self):
        self.assertEqual(self.admin.get("/api/periods/2026-7").status_code, 404)


# ------------------------------------------------------------------ the commands
class CommandTests(PeriodCloseBase):
    def test_dry_run_prints_the_checks_and_closes_nothing(self):
        self.disbursed_loan(self.product, self.borrower)
        call_command("close_period", "--year", "2026", "--month", "3", "--dry-run")
        self.assertEqual(AccountingPeriod.objects.count(), 0)

    def test_close_period_closes_and_reopen_period_reopens(self):
        self.disbursed_loan(self.product, self.borrower)
        call_command("close_period", "--year", "2026", "--month", "3",
                     "--note", "March board pack issued")
        period = AccountingPeriod.objects.get(year=2026, month=3)
        self.assertEqual(period.state, PeriodState.CLOSED)
        self.assertIsNone(period.closed_by_id, "a command run has no user")

        call_command("reopen_period", "--year", "2026", "--month", "3",
                     "--reason", "Board asked for a restated March")
        period.refresh_from_db()
        self.assertEqual(period.state, PeriodState.OPEN)
        self.assertEqual(period.reopen_count, 1)

    def test_a_bad_month_is_a_command_error(self):
        with self.assertRaises(CommandError):
            call_command("close_period", "--year", "2026", "--month", "13")

    def test_run_penalties_skip_closed_exits_cleanly(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)
        call_command("run_penalties", "--as-of", "2026-03-31", "--skip-closed")
        self.assertTrue(AuditLog.objects.filter(action="run_penalties_skipped").exists())

    def test_run_penalties_without_the_flag_fails_loudly(self):
        from core.exceptions import PeriodClosedError

        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)
        with self.assertRaises(PeriodClosedError):
            call_command("run_penalties", "--as-of", "2026-03-31")

    def test_run_savings_interest_skip_closed_exits_cleanly(self):
        self.disbursed_loan(self.product, self.borrower)
        self.close(2026, 3)
        call_command("run_savings_interest", "--as-of", "2026-03-31", "--skip-closed")
        self.assertTrue(AuditLog.objects.filter(action="savings_interest_skipped").exists())
