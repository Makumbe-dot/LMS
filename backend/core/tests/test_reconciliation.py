"""The ledger must stay tied to the sub-ledgers through every operation.

These are regression tests for two defects that shipped: a reschedule moved the
loan book without posting anything, and a savings reversal raised no journal
entry at all. Both broke identities the README claims always hold.

Every test here asserts the identities themselves rather than a figure, so they
keep their meaning as the book changes.
"""
from datetime import date
from decimal import Decimal

from core.models import (
    FundingFacility,
    JournalEntry,
    Loan,
    LoanStatus,
    SavingsAccount,
    SavingsProduct,
    SavingsTransaction,
    TxnType,
)
from core.services import ledger as gl
from core.services import savings as savings_svc
from core.services.fx import to_base

from .test_components import LedgerBase, dec

ZERO = Decimal("0")


class ReconciliationTests(LedgerBase):
    """After any operation, the ledger equals the book."""

    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    # ------------------------------------------------------------------ helper
    def assert_reconciled(self, note=""):
        balance = gl.trial_balance()
        self.assertTrue(balance["balanced"], f"debits != credits {note}")
        by_code = {row["code"]: row["balance"] for row in balance["rows"]}
        active = Loan.objects.filter(status=LoanStatus.ACTIVE)
        facilities = FundingFacility.objects.all()

        def carried(rows, field):
            # each row at its booked rate, rounded per row; 1 in the base currency
            return sum((to_base(getattr(r, field), r.fx_rate) for r in rows), ZERO)

        for code, book in [
            ("1100", carried(active, "principal_outstanding")),
            ("1300", carried(active, "penalties_outstanding")),
            ("1400", carried(active, "charges_outstanding")),
            ("2000", carried(SavingsAccount.objects.all(), "balance")),
            ("2100", carried(facilities, "principal_outstanding")),
            ("2110", carried(facilities, "interest_accrued")),
        ]:
            self.assertEqual(by_code.get(code, ZERO), book,
                             f"account {code} does not equal its sub-ledger {note}")

    # ------------------------------------------------------------------ savings
    def test_a_savings_reversal_raises_a_journal_entry(self):
        """The reversal must know what it reverses BEFORE post_save fires."""
        product = SavingsProduct.objects.create(code="SAV", name="Ordinary")
        account = savings_svc.open_account(self.borrower_obj(), product, self.admin_user(),
                                           opening_deposit=dec("100.00"))
        deposit = savings_svc.deposit(account, self.admin_user(), dec("40.00"))
        self.assert_reconciled("after a deposit")

        reversal = savings_svc.reverse(account, deposit, self.admin_user(), "counted wrong")

        self.assertIsNotNone(reversal.reversal_of_id)
        self.assertTrue(
            SavingsTransaction.objects.filter(pk=reversal.id,
                                              journal_entry__isnull=False).exists(),
            "a savings reversal must raise its own journal entry")
        self.assert_reconciled("after a reversed deposit")

    def test_a_reversed_withdrawal_also_reconciles(self):
        product = SavingsProduct.objects.create(code="SAV2", name="Ordinary")
        account = savings_svc.open_account(self.borrower_obj(), product, self.admin_user(),
                                           opening_deposit=dec("100.00"))
        withdrawal = savings_svc.withdraw(account, self.admin_user(), dec("30.00"))
        savings_svc.reverse(account, withdrawal, self.admin_user(), "wrong account")
        self.assert_reconciled("after a reversed withdrawal")

    def test_foreign_savings_and_a_foreign_facility_reconcile_at_every_step(self):
        """In another currency the liabilities are carried at booked rates and every
        posting at a new rate leaves a difference on 4800, never on 2000 or 2100."""
        from core.services import funding

        self.admin.post("/api/currencies/rates", {"code": "ZAR", "rate_date": "2026-01-01",
                                                  "rate": "0.054321"}, format="json")
        product = SavingsProduct.objects.create(code="ZAR", name="Rand", currency="ZAR",
                                                interest_rate_pct_pa=dec("7.5"))
        account = savings_svc.open_account(self.borrower_obj(), product, self.admin_user(),
                                           dec("1234.56"), date(2026, 3, 1))
        facility = funding.open_facility(None, funder_name="Joburg", name="ZAR line",
                                         facility_limit=dec("99999"), currency="ZAR",
                                         interest_rate_pct_pa=dec("11"),
                                         start_date=date(2026, 1, 1))
        funding.drawdown(facility, None, dec("45678.91"), date(2026, 1, 3))
        self.assert_reconciled("after opening both")

        for day, rate in [(10, "0.051234"), (20, "0.057777"), (28, "0.049999")]:
            self.admin.post("/api/currencies/rates", {"code": "ZAR",
                                                      "rate_date": f"2026-03-{day:02d}",
                                                      "rate": rate}, format="json")
            savings_svc.deposit(account, self.admin_user(), dec("77.77"), date(2026, 3, day))
            withdrawal = savings_svc.withdraw(account, self.admin_user(), dec("33.33"),
                                              date(2026, 3, day))
            funding.repay(facility, None, dec("1111.11"), date(2026, 3, day))
            self.assert_reconciled(f"after postings at {rate}")
        savings_svc.accrue_interest(date(2026, 3, 31))
        funding.accrue_interest(date(2026, 3, 31), facility)
        self.assert_reconciled("after interest on both")

        from core.services import fx
        fx.revalue(date(2026, 3, 31), None)
        self.assert_reconciled("after the revaluation")
        account.refresh_from_db()
        facility.refresh_from_db()
        funding.pay_interest(facility, None, facility.interest_accrued, date(2026, 4, 2))
        savings_svc.reverse(account, withdrawal, self.admin_user(), "wrong member")
        self.assert_reconciled("after a payment and a reversal at the restated rate")

        JournalEntry.objects.all().delete()
        gl.backfill()
        self.assert_reconciled("after a rebuild")

    # ------------------------------------------------------------------ loans
    def test_a_reschedule_posts_the_capitalisation(self):
        """Rolling arrears into a new principal has to move the ledger with it."""
        loan = self.disbursed_loan(self.product, self.borrower)
        self.officer.post(f"/api/loans/{loan['id']}/accrue-penalties?as_of=2026-06-05")
        self.officer.post(f"/api/loans/{loan['id']}/charges",
                          {"name": "Restructure fee", "amount": "15.00",
                           "collection": "balance"}, format="json")
        self.assert_reconciled("before the reschedule")

        before = self.admin.get(f"/api/loans/{loan['id']}").json()
        response = self.admin.post(f"/api/loans/{loan['id']}/reschedule",
                                   {"new_term_months": 12}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

        entry = JournalEntry.objects.get(source=TxnType.CAPITALISATION)
        lines = {line.account.code: line for line in entry.lines.all()}
        # The receivable grows by exactly what the other legs shed.
        self.assertEqual(lines["1100"].debit,
                         lines.get("4000", _Zero()).credit
                         + lines.get("1300", _Zero()).credit
                         + lines.get("1400", _Zero()).credit)
        # The penalties and charges that were capitalised have left their accounts.
        self.assertEqual(lines["1300"].credit, dec(before["penalties_outstanding"]))
        self.assertEqual(lines["1400"].credit, dec(before["charges_outstanding"]))
        self.assert_reconciled("after the reschedule")

    def test_a_reschedule_with_nothing_in_arrears_posts_nothing(self):
        # Disbursed TODAY, so every instalment is in the future whatever the date
        # the suite runs on. A hardcoded date here silently starts testing the
        # opposite case once the calendar passes the first due date.
        loan = self.disbursed_loan(self.product, self.borrower,
                                   disbursement_date=date.today().isoformat())
        self.admin.post(f"/api/loans/{loan['id']}/reschedule", {"new_term_months": 9},
                        format="json")
        # Nothing was overdue, so there is nothing to capitalise and no entry.
        self.assertFalse(JournalEntry.objects.filter(source=TxnType.CAPITALISATION).exists())
        self.assert_reconciled("after a reschedule with nothing overdue")

    def test_the_full_lifecycle_reconciles_at_every_step(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.assert_reconciled("after disbursement")

        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": "197.02", "txn_date": "2026-03-25"}, format="json")
        self.assert_reconciled("after a repayment")

        self.officer.post(f"/api/loans/{loan['id']}/accrue-penalties?as_of=2026-05-05")
        self.assert_reconciled("after penalties")

        detail = self.teller.get(f"/api/loans/{loan['id']}").json()
        self.admin.post(f"/api/loans/{loan['id']}/waive-penalties",
                        {"amount": detail["penalties_outstanding"], "narration": "goodwill"},
                        format="json")
        self.assert_reconciled("after a waiver")

        paid = self.teller.post(f"/api/loans/{loan['id']}/repayments",
                                {"amount": "100.00", "txn_date": "2026-05-10"},
                                format="json").json()
        self.officer.post(f"/api/loans/{loan['id']}/transactions/{paid['id']}/reverse",
                          {"narration": "wrong account"}, format="json")
        self.assert_reconciled("after a reversal")

        self.admin.post(f"/api/loans/{loan['id']}/reschedule", {"new_term_months": 12},
                        format="json")
        self.assert_reconciled("after a reschedule")

        self.admin.post(f"/api/loans/{loan['id']}/write-off", {"narration": "absconded"},
                        format="json")
        self.assert_reconciled("after a write-off")

    # ------------------------------------------------------------------ fixtures
    def borrower_obj(self):
        from core.models import Borrower
        return Borrower.objects.get(pk=self.borrower["id"])

    def admin_user(self):
        from core.models import User
        return User.objects.get(username="admin")


class _Zero:
    """Stands in for a ledger line that was not raised, so sums stay simple."""
    credit = ZERO
    debit = ZERO
