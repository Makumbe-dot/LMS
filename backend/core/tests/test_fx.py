"""Multi-currency: a loan in another currency keeps the ledger, in the base
currency, tied to the book to the cent through every posting."""
from decimal import Decimal

from core.models import ExchangeRate, JournalEntry, Loan, RevaluationRun
from core.services import fx
from core.services import ledger as gl

from .test_components import LedgerBase, dec

ZERO = Decimal("0")


class ForeignCurrencyTests(LedgerBase):
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
        self.assertEqual(gl.reconciliation()["rows"][0]["book"],
                         fx.to_base(db.principal_outstanding, db.fx_rate))

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
