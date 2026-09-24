"""The accounting and credit-assessment components: the general ledger, the
credit scorecard, approval limits, collateral and post-write-off recoveries."""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient, APITestCase

from core.models import (
    Branch,
    JournalEntry,
    LedgerAccount,
    Loan,
    LoanStatus,
    OrganisationSetting,
    Role,
    Transaction,
    TxnType,
    User,
)
from core.services import ledger as gl
from core.services.scoring import grade_for, score_application


def dec(value) -> Decimal:
    return Decimal(str(value))


class LedgerBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="HQ", name="Head Office")
        User.objects.create_user("admin", "admin123", full_name="Admin", role=Role.ADMIN)
        User.objects.create_user("officer", "officer123", full_name="Officer",
                                 role=Role.LOAN_OFFICER)
        User.objects.create_user("teller", "teller123", full_name="Teller", role=Role.TELLER)

    def client_for(self, username, password):
        client = APIClient()
        response = client.post("/api/auth/login", {"username": username, "password": password},
                               format="json")
        self.assertEqual(response.status_code, 200, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access_token"])
        return client

    def setUp(self):
        gl.ensure_chart_of_accounts()
        self.admin = self.client_for("admin", "admin123")
        self.officer = self.client_for("officer", "officer123")
        self.teller = self.client_for("teller", "teller123")

    def make_product(self, **overrides):
        payload = {
            "code": "T-SAL", "name": "Test Salary", "interest_rate_pct": 5,
            "rate_method": "reducing", "min_amount": 100, "max_amount": 20000,
            "min_term_months": 1, "max_term_months": 12, "admin_fee_pct": 3,
            "insurance_fee_pct": 1, "penalty_rate_pct_per_day": 0.5, "grace_days": 3,
            "max_instalment_to_salary_pct": 40,
        }
        payload.update(overrides)
        response = self.admin.post("/api/products", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def make_borrower(self, national_id="63-123456A63", **overrides):
        payload = {
            "first_name": "Test", "last_name": "Borrower", "national_id": national_id,
            "phone": "0771234567", "net_salary": 1500, "payday": 25, "kyc_verified": True,
            "employer": "Test Employer", "employee_no": "EMP1", "job_title": "Clerk",
            "branch": self.branch.id,
        }
        payload.update(overrides)
        response = self.officer.post("/api/borrowers", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def disbursed_loan(self, product, borrower, principal=1000, term=6,
                       disbursement_date="2026-03-01"):
        response = self.officer.post("/api/loans", {
            "borrower_id": borrower["id"], "product_id": product["id"],
            "principal": principal, "term_months": term,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        loan = response.json()
        self.admin.post(f"/api/loans/{loan['id']}/approve")
        response = self.officer.post(f"/api/loans/{loan['id']}/disburse",
                                     {"disbursement_date": disbursement_date}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def balances(self):
        return {row["code"]: row for row in gl.trial_balance()["rows"]}


# ---------------------------------------------------------------- the ledger
class GeneralLedgerTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def test_the_chart_of_accounts_is_created_once(self):
        before = LedgerAccount.objects.count()
        self.assertGreater(before, 0)
        gl.ensure_chart_of_accounts()
        self.assertEqual(LedgerAccount.objects.count(), before)

    def test_disbursement_posts_principal_bank_and_fees(self):
        self.disbursed_loan(self.product, self.borrower)
        entry = JournalEntry.objects.get(source=TxnType.DISBURSEMENT)
        lines = {line.account.code: line for line in entry.lines.all()}

        self.assertEqual(lines["1100"].debit, dec("1000.00"))   # loans receivable
        self.assertEqual(lines["1000"].credit, dec("960.00"))   # bank, net of fees
        self.assertEqual(lines["4100"].credit, dec("40.00"))    # fee income
        self.assertEqual(entry.total_debit, entry.total_credit)

    def test_repayment_splits_across_principal_interest_and_penalty(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": 197.02, "txn_date": "2026-03-25"}, format="json")

        entry = JournalEntry.objects.get(source=TxnType.REPAYMENT)
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["1000"].debit, dec("197.02"))    # cash in
        self.assertEqual(lines["1100"].credit, dec("147.02"))   # principal repaid
        self.assertEqual(lines["4000"].credit, dec("50.00"))    # interest recognised
        self.assertEqual(entry.total_debit, entry.total_credit)

    def test_penalty_accrual_raises_a_receivable_and_income(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.officer.post(f"/api/loans/{loan['id']}/accrue-penalties?as_of=2026-05-05")

        entry = JournalEntry.objects.get(source=TxnType.PENALTY)
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertGreater(lines["1300"].debit, 0)
        self.assertEqual(lines["1300"].debit, lines["4200"].credit)

    def test_a_reversal_mirrors_the_repayment_exactly(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        paid = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                                {"amount": 197.02, "txn_date": "2026-03-25"},
                                format="json").json()
        before = self.balances()

        self.officer.post(f"/api/loans/{loan['id']}/transactions/{paid['id']}/reverse",
                          {"narration": "wrong account"}, format="json")
        after = self.balances()

        # The repayment's effect on every account is undone.
        self.assertEqual(after["1100"]["balance"], before["1100"]["balance"] + dec("147.02"))
        self.assertEqual(after["4000"]["balance"], before["4000"]["balance"] - dec("50.00"))
        self.assertEqual(after["1000"]["balance"], before["1000"]["balance"] - dec("197.02"))

    def test_a_waiver_reverses_penalty_income_but_not_interest(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.officer.post(f"/api/loans/{loan['id']}/accrue-penalties?as_of=2026-05-05")
        detail = self.teller.get(f"/api/loans/{loan['id']}").json()
        penalties = detail["penalties_outstanding"]

        self.admin.post(f"/api/loans/{loan['id']}/waive-penalties",
                        {"amount": penalties, "narration": "Goodwill"}, format="json")
        after = self.balances()
        self.assertEqual(after["4200"]["balance"], dec("0.00"))
        self.assertEqual(after["1300"]["balance"], dec("0.00"))

    def test_an_early_settlement_rebate_does_not_touch_the_ledger(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.teller.post(f"/api/loans/{loan['id']}/settle", {"txn_date": "2026-04-10"},
                         format="json")
        # The rebate is a waiver of interest that was never recognised as income.
        waivers = JournalEntry.objects.filter(source=TxnType.WAIVER)
        self.assertEqual(waivers.count(), 0)
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_write_off_removes_only_recognised_balances(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post(f"/api/loans/{loan['id']}/write-off", {"narration": "Absconded"},
                        format="json")

        entry = JournalEntry.objects.get(source=TxnType.WRITE_OFF)
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["1100"].credit, dec("1000.00"))
        self.assertEqual(lines["5000"].debit, dec("1000.00"))
        # Unearned interest was never income, so it is not an expense now.
        self.assertEqual(entry.total_debit, dec("1000.00"))

    def test_the_ledger_reconciles_to_the_loan_book(self):
        """The invariant that matters: loans receivable equals principal outstanding."""
        first = self.disbursed_loan(self.product, self.borrower)
        second_borrower = self.make_borrower(national_id="63-222222B63")
        second = self.disbursed_loan(self.product, second_borrower, principal=2000, term=6)

        self.teller.post(f"/api/loans/{first['id']}/repayments",
                         {"amount": 300, "txn_date": "2026-03-25"}, format="json")
        self.officer.post(f"/api/loans/{second['id']}/accrue-penalties?as_of=2026-06-05")

        balance = gl.trial_balance()
        self.assertTrue(balance["balanced"], balance)
        by_code = {row["code"]: row for row in balance["rows"]}

        book_principal = sum(
            (l.principal_outstanding for l in Loan.objects.filter(status=LoanStatus.ACTIVE)),
            Decimal("0"))
        book_penalties = sum(
            (l.penalties_outstanding for l in Loan.objects.filter(status=LoanStatus.ACTIVE)),
            Decimal("0"))
        self.assertEqual(by_code["1100"]["balance"], book_principal)
        self.assertEqual(by_code["1300"]["balance"], book_penalties)

    def test_every_transaction_gets_exactly_one_entry(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": 197.02, "txn_date": "2026-03-25"}, format="json")

        # The fee transaction is informational; its money is inside the disbursement entry.
        posted = Transaction.objects.exclude(txn_type=TxnType.FEE)
        self.assertEqual(JournalEntry.objects.count(), posted.count())
        for txn in posted:
            self.assertTrue(hasattr(txn, "journal_entry"))

    def test_backfill_posts_history_and_is_idempotent(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": 100, "txn_date": "2026-03-25"}, format="json")
        JournalEntry.objects.all().delete()

        first = gl.backfill()
        self.assertGreater(first["posted"], 0)
        second = gl.backfill()
        self.assertEqual(second["posted"], 0)
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_the_api_serves_the_journal_and_trial_balance(self):
        self.disbursed_loan(self.product, self.borrower)

        journal = self.admin.get("/api/ledger/journal").json()
        self.assertGreaterEqual(journal["count"], 1)
        self.assertGreaterEqual(len(journal["results"][0]["lines"]), 2)

        balance = self.admin.get("/api/ledger/trial-balance").json()
        self.assertTrue(balance["balanced"])
        self.assertEqual(balance["total_debit"], balance["total_credit"])

        income = self.admin.get("/api/ledger/income-statement").json()
        self.assertGreater(Decimal(income["total_income"]), 0)

        csv = self.admin.get("/api/ledger/trial-balance?fmt=csv")
        self.assertTrue(csv["content-type"].startswith("text/csv"))

    def test_the_journal_can_be_filtered_by_account_and_loan(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        by_account = self.admin.get("/api/ledger/journal?account=4100").json()
        self.assertEqual(by_account["count"], 1)
        by_loan = self.admin.get(f"/api/ledger/journal?loan_id={loan['id']}").json()
        self.assertGreaterEqual(by_loan["count"], 1)

    def test_only_an_admin_may_change_the_chart_of_accounts(self):
        self.assertEqual(self.officer.get("/api/ledger/accounts").status_code, 200)
        self.assertEqual(
            self.officer.post("/api/ledger/accounts",
                              {"code": "9999", "name": "X", "type": "asset"},
                              format="json").status_code, 403)
        self.assertEqual(
            self.admin.post("/api/ledger/accounts",
                            {"code": "9999", "name": "Suspense", "type": "asset"},
                            format="json").status_code, 201)


# ---------------------------------------------------------------- scorecard
class ScorecardTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()

    def test_grades_follow_the_boundaries(self):
        self.assertEqual(grade_for(95), "A")
        self.assertEqual(grade_for(80), "A")
        self.assertEqual(grade_for(79), "B")
        self.assertEqual(grade_for(50), "C")
        self.assertEqual(grade_for(10), "E")

    def test_the_quote_carries_a_scorecard(self):
        borrower = self.make_borrower()
        quote = self.officer.post("/api/loans/quote", {
            "product_id": self.product["id"], "principal": 1000, "term_months": 6,
            "borrower_id": borrower["id"],
        }, format="json").json()

        card = quote["scorecard"]
        self.assertIsNotNone(card)
        self.assertEqual(sum(f["max"] for f in card["factors"]), 100)
        self.assertLessEqual(card["score"], 100)
        self.assertIn(card["grade"], list("ABCDE"))

    def test_a_quote_without_a_borrower_has_no_scorecard(self):
        quote = self.officer.post("/api/loans/quote", {
            "product_id": self.product["id"], "principal": 1000, "term_months": 6,
        }, format="json").json()
        self.assertIsNone(quote["scorecard"])

    def test_the_score_is_stored_on_the_application(self):
        borrower = self.make_borrower()
        loan = self.officer.post("/api/loans", {
            "borrower_id": borrower["id"], "product_id": self.product["id"],
            "principal": 1000, "term_months": 6,
        }, format="json").json()

        self.assertIsNotNone(loan["credit_score"])
        detail = self.officer.get(f"/api/loans/{loan['id']}").json()
        self.assertEqual(detail["scorecard"]["score"], loan["credit_score"])
        self.assertEqual(len(detail["scorecard"]["factors"]), 5)

    def test_a_thin_file_scores_below_a_strong_one(self):
        from core.models import Borrower, LoanProduct

        product = LoanProduct.objects.get(pk=self.product["id"])
        strong = Borrower.objects.get(pk=self.make_borrower()["id"])
        thin = Borrower.objects.get(pk=self.make_borrower(
            national_id="63-333333C63", employer=None, employee_no=None, job_title=None,
            kyc_verified=False, net_salary=600)["id"])

        strong_card = score_application(strong, product, dec(1000), 6, dec("197.02"))
        thin_card = score_application(thin, product, dec(1000), 6, dec("197.02"))
        self.assertGreater(strong_card["score"], thin_card["score"])

    def test_a_blacklisted_borrower_scores_zero(self):
        from core.models import Borrower, LoanProduct

        product = LoanProduct.objects.get(pk=self.product["id"])
        borrower = Borrower.objects.get(pk=self.make_borrower(
            national_id="63-444444D63", is_blacklisted=True)["id"])
        card = score_application(borrower, product, dec(1000), 6, dec("197.02"))
        self.assertEqual(card["score"], 0)
        self.assertEqual(card["grade"], "E")


# ---------------------------------------------------------------- approval limits
class ApprovalLimitTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower(net_salary=20000)

    def apply_for(self, principal):
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.product["id"],
            "principal": principal, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_an_officer_cannot_approve_above_the_limit(self):
        config = OrganisationSetting.load()
        config.officer_approval_limit = dec(2000)
        config.save()

        loan = self.apply_for(5000)
        # A second officer, so the maker-checker rule is not what blocks it.
        User.objects.create_user("officer2", "officer123", full_name="Officer Two",
                                 role=Role.LOAN_OFFICER)
        other = self.client_for("officer2", "officer123")

        response = other.post(f"/api/loans/{loan['id']}/approve")
        self.assertEqual(response.status_code, 400)
        self.assertIn("needs an administrator", response.json()["detail"])

        # An admin can
        self.assertEqual(
            self.admin.post(f"/api/loans/{loan['id']}/approve").json()["status"], "approved")

    def test_an_officer_can_approve_inside_the_limit(self):
        config = OrganisationSetting.load()
        config.officer_approval_limit = dec(2000)
        config.save()

        loan = self.apply_for(1500)
        User.objects.create_user("officer2", "officer123", full_name="Officer Two",
                                 role=Role.LOAN_OFFICER)
        other = self.client_for("officer2", "officer123")
        self.assertEqual(
            other.post(f"/api/loans/{loan['id']}/approve").json()["status"], "approved")


# ---------------------------------------------------------------- collateral
class CollateralTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def test_security_can_be_pledged_updated_and_released(self):
        lid = self.loan["id"]
        response = self.officer.post(f"/api/loans/{lid}/collateral", {
            "type": "vehicle", "description": "Toyota Hilux, white",
            "estimated_value": "4500.00", "valuation_date": "2026-03-01",
            "reference": "ABC1234",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        item = response.json()
        self.assertEqual(item["status"], "pledged")
        self.assertEqual(item["recorded_by_name"], "Officer")

        detail = self.officer.get(f"/api/loans/{lid}").json()
        self.assertEqual(len(detail["collateral"]), 1)

        released = self.officer.patch(f"/api/loans/{lid}/collateral/{item['id']}",
                                      {"status": "released"}, format="json")
        self.assertEqual(released.json()["status"], "released")

        self.assertEqual(
            self.officer.delete(f"/api/loans/{lid}/collateral/{item['id']}").status_code, 204)

    def test_a_teller_cannot_pledge_collateral(self):
        response = self.teller.post(f"/api/loans/{self.loan['id']}/collateral",
                                    {"description": "x"}, format="json")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------- recoveries
class RecoveryTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post(f"/api/loans/{self.loan['id']}/write-off",
                        {"narration": "Absconded"}, format="json")

    def test_a_recovery_is_recorded_and_taken_to_income(self):
        response = self.teller.post(f"/api/loans/{self.loan['id']}/recovery", {
            "amount": "250.00", "txn_date": "2026-08-01", "method": "cash",
            "reference": "REC1",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)

        entry = JournalEntry.objects.get(source=TxnType.RECOVERY)
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["1000"].debit, dec("250.00"))
        self.assertEqual(lines["4300"].credit, dec("250.00"))
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_recoveries_cannot_exceed_what_was_written_off(self):
        written_off = Transaction.objects.get(txn_type=TxnType.WRITE_OFF).amount
        response = self.teller.post(f"/api/loans/{self.loan['id']}/recovery",
                                    {"amount": str(written_off + dec(1))}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("more than the", response.json()["detail"])

    def test_a_running_loan_cannot_take_a_recovery(self):
        borrower = self.make_borrower(national_id="63-555555E63")
        running = self.disbursed_loan(self.product, borrower)
        response = self.teller.post(f"/api/loans/{running['id']}/recovery",
                                    {"amount": "10.00"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("written-off", response.json()["detail"])
