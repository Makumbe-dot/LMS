"""Multi-currency: a loan in another currency keeps the ledger, in the base
currency, tied to the book to the cent through every posting; and the same for
savings accounts and funding facilities in another currency."""
from datetime import date
from decimal import Decimal

from core.models import (
    Borrower,
    ExchangeRate,
    FundingFacility,
    JournalEntry,
    Loan,
    RevaluationRun,
    SavingsAccount,
    SavingsProduct,
)
from core.services import fx
from core.services import ledger as gl

from .test_components import LedgerBase, dec

ZERO = Decimal("0")


class ForeignBase(LedgerBase):
    """ZWG loans on a USD book: 1 ZWG = 0.04 USD at the start."""

    def setUp(self):
        super().setUp()
        self.borrower = self.make_borrower()
        self.rate("2026-01-01", "0.040000")
        self.product = self.make_product(code="ZWG-SAL", currency="zwg")

    def rate(self, on, value, code="ZWG"):
        response = self.admin.post("/api/currencies/rates",
                                   {"code": code, "rate_date": on, "rate": value}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def assert_reconciled(self, note=""):
        result = gl.reconciliation()
        self.assertTrue(result["trial_balance_balanced"], f"trial balance {note}")
        for row in result["rows"]:
            self.assertTrue(row["agrees"], f"{row['code']} ledger {row['ledger']} vs book "
                                           f"{row['book']} {note}")

    def lines(self, source):
        entry = JournalEntry.objects.filter(source=source).order_by("-id").first()
        return {line.account.code: line for line in entry.lines.all()}


class ForeignCurrencyTests(ForeignBase):
    """A ZWG loan through every posting."""

    # ------------------------------------------------------------------ setup
    def test_a_product_cannot_be_priced_in_a_currency_with_no_rate(self):
        response = self.admin.post("/api/products", {
            "code": "ZAR-X", "name": "Rand loan", "interest_rate_pct": 5, "currency": "ZAR",
            "min_amount": 100, "max_amount": 1000, "min_term_months": 1, "max_term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("No exchange rate for ZAR", str(response.json()))

    def test_the_base_currency_is_stored_as_blank_and_cannot_be_given_a_rate(self):
        self.assertEqual(self.make_product(code="USD-X", currency="usd")["currency"], "")
        response = self.admin.post("/api/currencies/rates",
                                   {"code": "USD", "rate_date": "2026-01-01", "rate": 2},
                                   format="json")
        self.assertEqual(response.status_code, 400)

    def test_the_rate_for_a_date_is_the_latest_on_or_before_it(self):
        from datetime import date

        self.rate("2026-03-01", "0.035000")
        self.assertEqual(fx.rate_on("ZWG", date(2026, 2, 15)), dec("0.040000"))
        self.assertEqual(fx.rate_on("ZWG", date(2026, 3, 1)), dec("0.035000"))
        self.assertEqual(fx.rate_on("USD", date(2026, 3, 1)), dec("1"))
        self.assertEqual(fx.rate_on("ZWG", date(2025, 12, 31), strict=False), None)

    # ------------------------------------------------------------------ posting
    def test_a_foreign_disbursement_is_posted_in_the_base_currency(self):
        loan = self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        self.assertEqual(loan["currency"], "ZWG")
        self.assertEqual(loan["fx_rate"], "0.040000")
        lines = self.lines("disbursement")
        self.assertEqual(lines["1100"].debit, dec("400.00"))    # 10 000 ZWG at 0.04
        self.assertEqual(lines["1000"].credit, dec("384.00"))   # less 4% fees
        self.assertEqual(lines["4100"].credit, dec("16.00"))
        self.assert_reconciled("after a foreign disbursement")

    def test_a_repayment_after_the_rate_moves_books_a_realised_difference(self):
        loan = self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        self.rate("2026-03-20", "0.050000")   # the ZWG strengthened
        response = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                                    {"amount": 1970.18, "txn_date": "2026-03-25"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        lines = self.lines("repayment")
        # cash and interest at the spot rate, the receivable relieved at the booked rate
        self.assertEqual(lines["1000"].debit, dec("98.51"))              # 1 970.18 x 0.05
        self.assertEqual(lines["4000"].credit, dec("25.00"))             # 500 x 0.05
        self.assertEqual(lines["1100"].credit, dec("58.81"))             # 1 470.18 x 0.04
        self.assertEqual(lines["4800"].credit, dec("14.70"))             # the gain
        self.assertEqual(sum(l.debit for l in lines.values()),
                         sum(l.credit for l in lines.values()))
        self.assert_reconciled("after a repayment at a new rate")

    def test_a_reversal_puts_the_cash_back_at_the_rate_it_came_in_at(self):
        loan = self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        self.rate("2026-03-20", "0.050000")
        txn = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                               {"amount": 1970.18, "txn_date": "2026-03-25"},
                               format="json").json()
        self.rate("2026-04-01", "0.060000")
        response = self.admin.post(
            f"/api/loans/{loan['id']}/transactions/{txn['id']}/reverse",
            {"narration": "wrong loan"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        lines = self.lines("reversal")
        self.assertEqual(lines["1000"].credit, dec("98.51"))
        self.assertEqual(lines["1100"].debit, dec("58.81"))
        self.assert_reconciled("after a reversal")

    def test_many_small_postings_still_reconcile_to_the_cent(self):
        """Rounding each conversion must never let the ledger drift from the book."""
        self.rate("2026-01-01", "0.002731")
        product = self.make_product(code="ZWG-W", currency="ZWG", min_amount=100,
                                    max_amount=200000, interest_rate_pct=7)
        loan = self.disbursed_loan(product, self.borrower, principal=123457, term=12)
        for day in range(1, 10):
            response = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                                        {"amount": 1234.57, "txn_date": f"2026-03-{day:02d}"},
                                        format="json")
            self.assertEqual(response.status_code, 201, response.content)
        self.assert_reconciled("after nine small repayments")
        db = Loan.objects.get(pk=loan["id"])
        loans_receivable = next(r for r in gl.reconciliation()["rows"] if r["code"] == "1100")
        self.assertEqual(loans_receivable["book"], fx.to_base(db.principal_outstanding, db.fx_rate))

    # ------------------------------------------------------------------ revaluation
    def test_a_revaluation_restates_the_receivables_and_moves_the_booked_rate(self):
        loan = self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        self.rate("2026-03-31", "0.030000")   # the ZWG weakened

        preview = self.admin.get("/api/currencies/revaluations/preview?as_of=2026-03-31").json()
        self.assertEqual(preview["loans"], 1)
        self.assertEqual(preview["movement"], "-100.00")   # 10 000 x (0.03 - 0.04)

        response = self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"},
                                   format="json")
        self.assertEqual(response.status_code, 201, response.content)
        run = response.json()
        self.assertEqual(run["movement"], "-100.00")
        lines = self.lines("fx_revaluation")
        self.assertEqual(lines["1100"].credit, dec("100.00"))
        self.assertEqual(lines["4800"].debit, dec("100.00"))        # an unrealised loss
        self.assertEqual(Loan.objects.get(pk=loan["id"]).fx_rate, dec("0.030000"))
        self.assert_reconciled("after a revaluation")

        # A second run at the same rate has nothing to post.
        again = self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"},
                                format="json")
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again.json()["movement"], "0.00")
        self.assertIsNone(again.json()["entry_no"])

        # And the next repayment telescopes from the restated figure.
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": 1970.18, "txn_date": "2026-04-02"}, format="json")
        self.assert_reconciled("after a repayment following a revaluation")

    def test_a_revaluation_without_a_closing_rate_is_refused(self):
        self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        ExchangeRate.objects.all().delete()
        response = self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"},
                                   format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("ZWG", response.json()["detail"])
        self.assertEqual(RevaluationRun.objects.count(), 0)

    def test_rebuild_reposts_a_revaluation_run(self):
        self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        self.rate("2026-03-31", "0.030000")
        self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"}, format="json")
        JournalEntry.objects.all().delete()
        result = gl.backfill()
        self.assertEqual(result["revaluation_runs_reposted"], 1)
        self.assert_reconciled("after a rebuild")

    def test_the_month_end_check_names_loans_still_at_an_old_rate(self):
        self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        self.rate("2026-03-31", "0.030000")
        checks = self.admin.get("/api/periods/2026-3/preflight").json()["checks"]
        check = next(c for c in checks if c["key"] == "foreign_loans_revalued")
        self.assertFalse(check["passed"])
        self.assertFalse(check["blocking"])
        self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"}, format="json")
        checks = self.admin.get("/api/periods/2026-3/preflight").json()["checks"]
        self.assertTrue(next(c for c in checks if c["key"] == "foreign_loans_revalued")["passed"])

    # ------------------------------------------------------------------ reporting
    def test_the_dashboard_and_the_borrower_count_a_foreign_loan_in_the_base_currency(self):
        loan = self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        dashboard = self.admin.get("/api/reports/dashboard?as_of=2026-03-02").json()
        self.assertEqual(dashboard["principal_outstanding"], "400.00")
        borrower = self.admin.get(f"/api/borrowers/{self.borrower['id']}").json()
        self.assertEqual(Decimal(borrower["total_outstanding"]),
                         fx.to_base(Loan.objects.get(pk=loan["id"]).total_outstanding, "0.04"))
        detail = self.admin.get(f"/api/loans/{loan['id']}").json()
        self.assertEqual(detail["currency"], "ZWG")
        self.assertEqual(detail["principal_outstanding"], "10000.00")

    def test_affordability_measures_a_foreign_instalment_against_a_base_salary(self):
        quote = self.officer.post("/api/loans/quote", {
            "product_id": self.product["id"], "principal": 10000, "term_months": 6,
            "borrower_id": self.borrower["id"],
        }, format="json").json()
        self.assertEqual(quote["currency"], "ZWG")
        # 1 970.18 ZWG a month is 78.81 USD against a 1 500 USD salary
        self.assertEqual(quote["affordability_pct"], "5.25")
        self.assertTrue(quote["affordable"])

    def test_only_an_admin_sets_rates_and_revalues(self):
        self.assertEqual(self.officer.post("/api/currencies/rates", {
            "code": "ZAR", "rate_date": "2026-01-01", "rate": "0.05"}, format="json").status_code, 403)
        self.assertEqual(self.officer.post("/api/currencies/revaluations", {}, format="json")
                         .status_code, 403)
        listing = self.officer.get("/api/currencies").json()
        self.assertEqual(listing["base_currency"], "USD")
        self.assertEqual(listing["currencies"][0]["code"], "ZWG")


class ForeignBalancesBase(ForeignBase):
    """Shared helpers for ZWG savings and facilities on the same USD book."""

    def lines_of(self, entry_no=None, source=None):
        entry = (JournalEntry.objects.get(entry_no=entry_no) if entry_no else
                 JournalEntry.objects.filter(source=source).order_by("-id").first())
        return {line.account.code: line for line in entry.lines.all()}

    def assert_balanced_entry(self, lines):
        self.assertEqual(sum(l.debit for l in lines.values()),
                         sum(l.credit for l in lines.values()))


class ForeignSavingsTests(ForeignBalancesBase):
    """A ZWG savings product: 1 000 ZWG opened at 0.04, the rate moving to 0.05."""

    def setUp(self):
        super().setUp()
        response = self.admin.post("/api/savings/products", {
            "code": "ZWG-SAV", "name": "ZWG savings", "interest_rate_pct_pa": 12,
            "currency": "zwg"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.savings_product = response.json()
        response = self.teller.post("/api/savings/accounts", {
            "borrower_id": self.borrower["id"], "product_id": self.savings_product["id"],
            "opening_deposit": "1000.00", "opened_on": "2026-03-01",
            "method": "bank_transfer"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.account = response.json()

    def move(self, kind, amount, on):
        response = self.teller.post(f"/api/savings/accounts/{self.account['id']}/{kind}", {
            "amount": amount, "txn_date": on, "method": "bank_transfer"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def savings_ledger(self):
        return next(r for r in gl.reconciliation()["rows"] if r["code"] == "2000")

    def test_the_product_and_the_account_carry_the_currency(self):
        self.assertEqual(self.savings_product["currency"], "ZWG")
        self.assertEqual(self.account["currency"], "ZWG")
        self.assertEqual(self.account["fx_rate"], "0.040000")
        self.assertEqual(self.account["balance"], "1000.00")
        lines = self.lines_of(source="savings_deposit")
        self.assertEqual(lines["1000"].debit, dec("40.00"))
        self.assertEqual(lines["2000"].credit, dec("40.00"))
        self.assertNotIn("4800", lines)
        self.assert_reconciled("after a foreign opening deposit")

    def test_a_deposit_and_a_withdrawal_at_a_new_rate_realise_the_difference(self):
        self.rate("2026-03-20", "0.050000")
        deposit = self.move("deposit", "500.00", "2026-03-25")
        self.assertEqual(deposit["fx_rate"], "0.050000")
        lines = self.lines_of(source="savings_deposit")
        self.assertEqual(lines["1000"].debit, dec("25.00"))     # 500 x 0.05, the cash
        self.assertEqual(lines["2000"].credit, dec("20.00"))    # 500 x 0.04, the booked rate
        self.assertEqual(lines["4800"].credit, dec("5.00"))
        self.assert_balanced_entry(lines)
        self.assert_reconciled("after a deposit at a new rate")

        self.move("withdraw", "300.00", "2026-03-26")
        lines = self.lines_of(source="savings_withdrawal")
        self.assertEqual(lines["2000"].debit, dec("12.00"))
        self.assertEqual(lines["1000"].credit, dec("15.00"))
        self.assertEqual(lines["4800"].debit, dec("3.00"))
        self.assert_reconciled("after a withdrawal at a new rate")
        self.assertEqual(self.savings_ledger()["book"], dec("48.00"))   # 1 200 x 0.04

    def test_interest_is_an_expense_at_the_days_rate(self):
        self.rate("2026-03-20", "0.050000")
        response = self.admin.post("/api/savings/run-interest?as_of=2026-03-31")
        self.assertEqual(response.status_code, 200, response.content)
        lines = self.lines_of(source="savings_interest")
        self.assertEqual(lines["5200"].debit, dec("0.50"))     # 10 ZWG x 0.05
        self.assertEqual(lines["2000"].credit, dec("0.40"))    # 10 ZWG x 0.04
        self.assertEqual(lines["4800"].credit, dec("0.10"))
        self.assert_reconciled("after foreign savings interest")

    def test_a_reversal_puts_the_cash_back_at_the_rate_it_came_in_at(self):
        self.rate("2026-03-20", "0.050000")
        withdrawal = self.move("withdraw", "300.00", "2026-03-26")
        self.rate("2026-04-01", "0.060000")
        response = self.teller.post(
            f"/api/savings/accounts/{self.account['id']}/transactions/{withdrawal['id']}/reverse",
            {"narration": "wrong account"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["fx_rate"], "0.050000")
        lines = self.lines_of(source="savings_reversal")
        self.assertEqual(lines["1000"].debit, dec("15.00"))
        self.assertEqual(lines["2000"].credit, dec("12.00"))
        self.assertEqual(lines["4800"].credit, dec("3.00"))
        self.assert_reconciled("after a foreign savings reversal")

    def test_a_revaluation_restates_the_liability_the_other_way_round(self):
        self.rate("2026-03-31", "0.050000")   # the ZWG strengthened: we owe more
        preview = self.admin.get("/api/currencies/revaluations/preview?as_of=2026-03-31").json()
        self.assertEqual(preview["savings_accounts"], 1)
        line = preview["lines"][0]
        self.assertEqual((line["kind"], line["reference"]), ("savings", self.account["account_no"]))
        self.assertEqual(line["savings_movement"], "10.00")
        self.assertEqual(preview["movement"], "-10.00")         # a loss

        run = self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"},
                              format="json").json()
        self.assertEqual(run["savings_revalued"], 1)
        lines = self.lines_of(source="fx_revaluation")
        self.assertEqual(lines["2000"].credit, dec("10.00"))
        self.assertEqual(lines["4800"].debit, dec("10.00"))
        self.assertEqual(SavingsAccount.objects.get(pk=self.account["id"]).fx_rate,
                         dec("0.050000"))
        self.assert_reconciled("after a savings revaluation")

        # The next posting telescopes from the restated figure: no difference now.
        self.move("deposit", "200.00", "2026-04-02")
        self.assertNotIn("4800", self.lines_of(source="savings_deposit"))
        self.assertEqual(self.savings_ledger()["book"], dec("60.00"))   # 1 200 x 0.05
        self.assert_reconciled("after a deposit following a revaluation")

    def test_the_month_end_check_names_savings_still_at_an_old_rate(self):
        self.rate("2026-03-31", "0.050000")
        checks = self.admin.get("/api/periods/2026-3/preflight").json()["checks"]
        check = next(c for c in checks if c["key"] == "foreign_loans_revalued")
        self.assertFalse(check["passed"])
        self.assertIn("1 savings account(s)", check["detail"])

    def test_rebuild_reposts_every_foreign_savings_entry_to_the_cent(self):
        self.rate("2026-03-20", "0.050000")
        deposit = self.move("deposit", "333.33", "2026-03-25")
        self.move("withdraw", "123.45", "2026-03-26")
        self.admin.post("/api/savings/run-interest?as_of=2026-03-31")
        self.rate("2026-03-31", "0.047311")
        self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"}, format="json")
        self.teller.post(
            f"/api/savings/accounts/{self.account['id']}/transactions/{deposit['id']}/reverse",
            {"narration": "bounced"}, format="json")
        self.assert_reconciled("before the rebuild")
        before = {r["code"]: r["balance"] for r in gl.trial_balance()["rows"]}

        JournalEntry.objects.all().delete()
        gl.backfill()
        self.assertEqual({r["code"]: r["balance"] for r in gl.trial_balance()["rows"]}, before)
        self.assert_reconciled("after the rebuild")

    def test_the_currency_is_fixed_once_an_account_is_open(self):
        response = self.admin.patch(f"/api/savings/products/{self.savings_product['id']}",
                                    {"currency": ""}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot change", str(response.json()))
        # Other fields still change.
        response = self.admin.patch(f"/api/savings/products/{self.savings_product['id']}",
                                    {"name": "ZWG saver", "currency": "ZWG"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_product_cannot_take_a_currency_with_no_rate(self):
        response = self.admin.post("/api/savings/products", {
            "code": "ZAR-SAV", "name": "Rand savings", "currency": "ZAR"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("No exchange rate for ZAR", str(response.json()))

    def test_the_statement_is_in_the_accounts_currency_and_the_portfolio_in_the_base(self):
        statement = self.teller.get(
            f"/api/savings/accounts/{self.account['id']}/statement").json()
        self.assertEqual(statement["currency"], "ZWG")
        self.assertEqual(dec(str(statement["closing_balance"])), dec("1000.00"))
        portfolio = self.teller.get("/api/savings/portfolio").json()
        self.assertEqual(portfolio["total_balance"], "40.00")
        row = next(p for p in portfolio["by_product"] if p["product"] == "ZWG savings")
        self.assertEqual((row["currency"], row["balance"], row["balance_base"]),
                         ("ZWG", "1000.00", "40.00"))


class ForeignFacilityTests(ForeignBalancesBase):
    """A ZWG facility: 50 000 drawn at 0.04, repaid and paid at 0.05."""

    def setUp(self):
        super().setUp()
        response = self.admin.post("/api/funding/facilities", {
            "funder_name": "Harare Bank", "name": "ZWG line", "facility_limit": "100000.00",
            "interest_rate_pct_pa": 12, "start_date": "2026-01-01", "currency": "zwg"},
            format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.facility = response.json()
        self.post("drawdown", "50000.00", "2026-01-10")

    def post(self, action, amount, on):
        response = self.admin.post(f"/api/funding/facilities/{self.facility['id']}/{action}", {
            "amount": amount, "txn_date": on}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_a_drawdown_is_carried_at_the_booked_rate(self):
        self.assertEqual(self.facility["currency"], "ZWG")
        self.assertEqual(self.facility["fx_rate"], "0.040000")
        lines = self.lines_of(source="facility_drawdown")
        self.assertEqual(lines["1000"].debit, dec("2000.00"))
        self.assertEqual(lines["2100"].credit, dec("2000.00"))
        self.assert_reconciled("after a foreign drawdown")

    def test_interest_accrues_at_the_days_rate_and_is_paid_at_a_new_one(self):
        self.admin.post("/api/funding/accrue-interest?as_of=2026-02-10")
        lines = self.lines_of(source="facility_interest_accrual")
        self.assertEqual(lines["5300"].debit, dec("20.00"))     # 500 ZWG x 0.04
        self.assertEqual(lines["2110"].credit, dec("20.00"))
        self.assert_reconciled("after a foreign accrual")

        self.rate("2026-03-20", "0.050000")
        self.post("interest", "500.00", "2026-03-25")
        lines = self.lines_of(source="facility_interest_payment")
        self.assertEqual(lines["2110"].debit, dec("20.00"))
        self.assertEqual(lines["1000"].credit, dec("25.00"))
        self.assertEqual(lines["4800"].debit, dec("5.00"))      # a realised loss
        self.assert_reconciled("after a foreign interest payment")

    def test_a_repayment_and_its_reversal_at_new_rates(self):
        self.rate("2026-03-20", "0.050000")
        repayment = self.post("repay", "10000.00", "2026-03-26")
        lines = self.lines_of(source="facility_repayment")
        self.assertEqual(lines["2100"].debit, dec("400.00"))
        self.assertEqual(lines["1000"].credit, dec("500.00"))
        self.assertEqual(lines["4800"].debit, dec("100.00"))
        self.assert_reconciled("after a foreign repayment")

        self.rate("2026-04-01", "0.060000")
        response = self.admin.post(
            f"/api/funding/facilities/{self.facility['id']}/transactions/{repayment['id']}/reverse",
            {"narration": "bounced"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        lines = self.lines_of(source="facility_reversal")
        self.assertEqual(lines["1000"].debit, dec("500.00"))    # back at the rate it left at
        self.assertEqual(lines["2100"].credit, dec("400.00"))
        self.assertEqual(lines["4800"].credit, dec("100.00"))
        self.assert_reconciled("after a foreign facility reversal")

    def test_a_fee_is_an_expense_at_the_days_rate(self):
        self.post("fee", "100.00", "2026-03-27")
        lines = self.lines_of(source="facility_fee")
        self.assertEqual(lines["5310"].debit, dec("4.00"))
        self.assertEqual(lines["1000"].credit, dec("4.00"))

    def test_the_cash_guard_measures_a_foreign_payment_at_the_days_rate(self):
        # 2 000 USD in the bank; 50 000 ZWG at 0.05 is 2 500 USD.
        self.rate("2026-03-20", "0.050000")
        response = self.admin.post(f"/api/funding/facilities/{self.facility['id']}/repay", {
            "amount": "50000.00", "txn_date": "2026-03-26"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("2500.00", response.json()["detail"])

    def test_a_revaluation_restates_borrowings_and_accrued_interest(self):
        self.admin.post("/api/funding/accrue-interest?as_of=2026-02-10")
        self.rate("2026-03-31", "0.050000")
        run = self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"},
                              format="json").json()
        self.assertEqual(run["facilities_revalued"], 1)
        self.assertEqual(run["movement"], "-505.00")             # 50 500 ZWG x 0.01, a loss
        lines = self.lines_of(source="fx_revaluation")
        self.assertEqual(lines["2100"].credit, dec("500.00"))
        self.assertEqual(lines["2110"].credit, dec("5.00"))
        self.assertEqual(lines["4800"].debit, dec("505.00"))
        self.assertEqual(FundingFacility.objects.get(pk=self.facility["id"]).fx_rate,
                         dec("0.050000"))
        self.assert_reconciled("after a facility revaluation")

        summary = self.admin.get("/api/funding/summary").json()
        self.assertEqual(summary["drawn"], "2500.00")
        self.assertEqual(summary["accrued_interest"], "25.00")

        JournalEntry.objects.all().delete()
        gl.backfill()
        self.assert_reconciled("after a rebuild")

    def test_the_currency_is_fixed_once_the_facility_has_moved(self):
        response = self.admin.patch(f"/api/funding/facilities/{self.facility['id']}",
                                    {"currency": ""}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot change", str(response.json()))

    def test_a_facility_without_movements_may_change_currency(self):
        fresh = self.admin.post("/api/funding/facilities", {
            "funder_name": "Other", "name": "Spare", "facility_limit": "1000.00",
            "start_date": "2026-01-01"}, format="json").json()
        self.assertEqual(fresh["currency"], "")
        response = self.admin.patch(f"/api/funding/facilities/{fresh['id']}",
                                    {"currency": "ZWG"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["fx_rate"], "0.040000")


class ForeignBookTests(ForeignBalancesBase):
    """A loan, a savings account and a facility in ZWG, restated in one run."""

    def test_one_run_restates_all_three_and_reconciles(self):
        from core.services import funding, savings

        self.disbursed_loan(self.product, self.borrower, principal=10000, term=6)
        account = savings.open_account(Borrower.objects.get(pk=self.borrower["id"]),
                                       SavingsProduct.objects.create(code="Z", name="Z",
                                                                     currency="ZWG"),
                                       None, dec("2000"), date(2026, 3, 1))
        facility = funding.open_facility(None, funder_name="F", name="F",
                                         facility_limit=dec("30000"), currency="ZWG",
                                         start_date=date(2026, 1, 1))
        funding.drawdown(facility, None, dec("30000"), date(2026, 1, 5))
        self.rate("2026-03-31", "0.030000")

        run = self.admin.post("/api/currencies/revaluations", {"as_of": "2026-03-31"},
                              format="json").json()
        self.assertEqual((run["loans_revalued"], run["savings_revalued"],
                          run["facilities_revalued"]), (1, 1, 1))
        # The loan loses 100, the savings and the borrowings gain 20 and 300.
        self.assertEqual(run["movement"], "220.00")
        lines = self.lines_of(source="fx_revaluation")
        self.assertEqual(lines["1100"].credit, dec("100.00"))
        self.assertEqual(lines["2000"].debit, dec("20.00"))
        self.assertEqual(lines["2100"].debit, dec("300.00"))
        self.assertEqual(lines["4800"].credit, dec("220.00"))
        self.assertEqual(SavingsAccount.objects.get(pk=account.pk).fx_rate, dec("0.030000"))
        self.assert_reconciled("after restating all three")
        listing = self.admin.get("/api/currencies").json()["currencies"][0]
        self.assertEqual((listing["open_loans"], listing["open_savings"],
                          listing["open_facilities"]), (1, 1, 1))
