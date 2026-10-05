"""The effective interest method, as a setting: fees deferred, interest accrued
at the effective rate, and the two extra identities the ledger then has to keep."""
from decimal import Decimal

from core.models import Instalment, JournalEntry, Loan, OrganisationSetting
from core.services import eir
from core.services import ledger as gl

from .test_components import LedgerBase, dec

ZERO = Decimal("0")


class EffectiveInterestTests(LedgerBase):
    def setUp(self):
        super().setUp()
        config = OrganisationSetting.load()
        config.interest_method = "effective"
        config.save()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def assert_reconciled(self, note=""):
        result = gl.reconciliation()
        self.assertTrue(result["trial_balance_balanced"], f"trial balance {note}")
        codes = {r["code"] for r in result["rows"]}
        self.assertIn("1200", codes)
        self.assertIn("1150", codes)
        for row in result["rows"]:
            self.assertTrue(row["agrees"], f"{row['code']} ledger {row['ledger']} vs book "
                                           f"{row['book']} {note}")

    def lines(self, source, last=True):
        entries = JournalEntry.objects.filter(source=source).order_by("id")
        entry = entries.last() if last else entries.first()
        return {line.account.code: line for line in entry.lines.all()}

    # ------------------------------------------------------------------ the setting
    def test_the_method_cannot_change_while_loans_are_running(self):
        self.disbursed_loan(self.product, self.borrower)
        response = self.admin.patch("/api/settings", {"interest_method": "collected"},
                                    format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot change while 1 loan", str(response.json()))
        self.assertEqual(OrganisationSetting.load().interest_method, "effective")

    def test_the_method_changes_freely_on_an_empty_book(self):
        response = self.admin.patch("/api/settings", {"interest_method": "collected"},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(OrganisationSetting.load().interest_method, "collected")
        self.assertEqual(self.admin.post("/api/ledger/accrue-interest").status_code, 400)

    # ------------------------------------------------------------------ the schedule
    def test_income_at_the_effective_rate_is_the_interest_plus_the_fees(self):
        loan = Loan.objects.get(pk=self.disbursed_loan(self.product, self.borrower)["id"])
        rows = eir.rows(loan)
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum((r["eir_interest"] for r in rows), ZERO),
                         loan.total_interest + dec("40.00"))
        self.assertEqual(sum((r["fee_unwind"] for r in rows), ZERO), dec("40.00"))
        # a constant yield on a falling balance: the income falls period by period
        incomes = [r["eir_interest"] for r in rows]
        self.assertEqual(incomes, sorted(incomes, reverse=True))

    def test_a_loan_without_fees_accrues_the_contractual_interest(self):
        product = self.make_product(code="NOFEE", admin_fee_pct=0, insurance_fee_pct=0)
        loan = Loan.objects.get(pk=self.disbursed_loan(product, self.borrower)["id"])
        for row in eir.rows(loan):
            self.assertEqual(row["fee_unwind"], ZERO)
            self.assertEqual(row["eir_interest"], row["contractual"])

    # ------------------------------------------------------------------ posting
    def test_disbursement_defers_the_fees_instead_of_taking_them_to_income(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        lines = self.lines("disbursement")
        self.assertEqual(lines["1100"].debit, dec("1000.00"))
        self.assertEqual(lines["1000"].credit, dec("960.00"))
        self.assertEqual(lines["1150"].credit, dec("40.00"))
        self.assertNotIn("4100", lines)
        self.assertEqual(Loan.objects.get(pk=loan["id"]).fees_deferred, dec("40.00"))
        self.assert_reconciled("after disbursement")

    def test_the_accrual_raises_a_receivable_and_unwinds_the_fees(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        response = self.admin.post("/api/ledger/accrue-interest?as_of=2026-03-31")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["loans_accrued"], 1)

        db = Loan.objects.get(pk=loan["id"])
        plan = eir.rows(db)[0]
        lines = self.lines("accrual")
        self.assertEqual(lines["1200"].debit, plan["contractual"])      # 50.00
        self.assertEqual(lines["1150"].debit, plan["fee_unwind"])
        self.assertEqual(lines["4000"].credit, plan["eir_interest"])
        self.assertEqual(db.interest_accrued, plan["contractual"])
        self.assertEqual(db.fees_deferred, dec("40.00") - plan["fee_unwind"])
        self.assertEqual(Instalment.objects.filter(loan=db, accrued_on__isnull=False).count(), 1)
        self.assert_reconciled("after the first accrual")

        # Idempotent: the same month again raises nothing.
        again = self.admin.post("/api/ledger/accrue-interest?as_of=2026-03-31").json()
        self.assertEqual(again["loans_accrued"], 0)

    def test_a_repayment_settles_the_receivable(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-03-31")
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": 197.02, "txn_date": "2026-04-02"}, format="json")
        lines = self.lines("repayment")
        self.assertEqual(lines["1000"].debit, dec("197.02"))
        self.assertEqual(lines["1200"].credit, dec("50.00"))
        self.assertEqual(lines["1100"].credit, dec("147.02"))
        self.assertNotIn("4000", lines)
        self.assert_reconciled("after a repayment against accrued interest")

    def test_interest_collected_before_the_accrual_is_income_when_it_arrives(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": 197.02, "txn_date": "2026-03-20"}, format="json")
        lines = self.lines("repayment")
        self.assertEqual(lines["4000"].credit, dec("50.00"))
        self.assertNotIn("1200", lines)
        self.assert_reconciled("after an early repayment")

        # The accrual that follows recognises only the fee unwind for that period.
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-03-31")
        plan = eir.rows(Loan.objects.get(pk=loan["id"]))[0]
        lines = self.lines("accrual")
        self.assertNotIn("1200", lines)
        self.assertEqual(lines["4000"].credit, plan["fee_unwind"])
        self.assert_reconciled("after the accrual following an early repayment")

    def test_a_reversal_restores_the_receivable(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-03-31")
        txn = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                               {"amount": 197.02, "txn_date": "2026-04-02"}, format="json").json()
        self.admin.post(f"/api/loans/{loan['id']}/transactions/{txn['id']}/reverse",
                        {"narration": "wrong loan"}, format="json")
        lines = self.lines("reversal")
        self.assertEqual(lines["1200"].debit, dec("50.00"))
        self.assert_reconciled("after a reversal")

    def test_early_settlement_releases_the_fees_still_deferred(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-03-31")
        response = self.teller.post(f"/api/loans/{loan['id']}/settle",
                                    {"txn_date": "2026-04-10"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        db = Loan.objects.get(pk=loan["id"])
        self.assertEqual(db.status, "closed")
        self.assertEqual(db.fees_deferred, ZERO)
        release = self.lines("accrual")
        self.assertIn("1150", release)
        self.assertEqual(release["1150"].debit, release["4000"].credit)
        self.assertEqual(gl.trial_balance()["rows"] and
                         next(r["balance"] for r in gl.trial_balance()["rows"]
                              if r["code"] == "1150"), ZERO)
        self.assert_reconciled("after an early settlement")

    def test_a_write_off_takes_the_accrued_interest_and_releases_the_fees(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-04-30")
        db = Loan.objects.get(pk=loan["id"])
        accrued = db.interest_accrued
        deferred = db.fees_deferred
        self.admin.post(f"/api/loans/{loan['id']}/write-off", {"narration": "Absconded"},
                        format="json")
        lines = self.lines("write_off")
        self.assertEqual(lines["1100"].credit, dec("1000.00"))
        self.assertEqual(lines["1200"].credit, accrued)
        self.assertEqual(lines["1150"].debit, deferred)
        self.assertEqual(lines["5000"].debit, dec("1000.00") + accrued - deferred)
        self.assert_reconciled("after a write-off")

    def test_a_reschedule_releases_the_fees_and_starts_the_accrual_again(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-04-30")
        response = self.admin.post(f"/api/loans/{loan['id']}/reschedule",
                                   {"new_term_months": 8, "narration": "Hardship"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        db = Loan.objects.get(pk=loan["id"])
        self.assertEqual(db.fees_deferred, ZERO)
        self.assertEqual(db.interest_accrued, ZERO)
        self.assert_reconciled("after a reschedule")
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-12-31")
        self.assert_reconciled("after accruing on the new schedule")

    def test_the_month_end_check_names_periods_not_yet_accrued(self):
        self.disbursed_loan(self.product, self.borrower)
        checks = self.admin.get("/api/periods/2026-3/preflight").json()["checks"]
        check = next(c for c in checks if c["key"] == "interest_accrued")
        self.assertFalse(check["passed"])
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-03-31")
        checks = self.admin.get("/api/periods/2026-3/preflight").json()["checks"]
        self.assertTrue(next(c for c in checks if c["key"] == "interest_accrued")["passed"])

    def test_rebuild_reposts_the_accruals(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.admin.post("/api/ledger/accrue-interest?as_of=2026-04-30")
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": 197.02, "txn_date": "2026-05-02"}, format="json")
        JournalEntry.objects.all().delete()
        gl.backfill()
        self.assert_reconciled("after a rebuild")

    def test_the_accrual_is_for_admins_only(self):
        self.assertEqual(self.officer.post("/api/ledger/accrue-interest").status_code, 403)
