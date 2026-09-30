"""Capital, funder borrowings, and a balance sheet that has something in equity.

The complaint this answers was concrete: account 1000 sat at -4,194 because nothing
in the system said where the money to lend had come from. So the test that matters
most here is test_cash_stops_being_negative_once_the_funding_is_booked.

Note what is NOT asserted as an achievement: that the balance sheet balances. Given
entries where every debit has a credit, assets minus liabilities always equals
derived equity — it is arithmetic, not evidence. The checks with teeth are the
sub-ledger identities in gl.reconciliation(), and they get their own tests.
"""
from datetime import date, timedelta
from decimal import Decimal

from core.models import (
    CapitalTransaction,
    CapitalTxnType,
    FacilityTransaction,
    FacilityTxnType,
    FundingFacility,
    JournalEntry,
)
from core.services import funding as svc
from core.services import ledger as gl

from .test_components import LedgerBase, dec

ZERO = Decimal("0")


class FundingBase(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def facility(self, **overrides):
        kwargs = {
            "funder_name": "CBZ Bank Wholesale", "name": "On-lending line",
            "facility_limit": dec("50000"), "interest_rate_pct_pa": dec("12"),
            "start_date": date(2025, 1, 1),
        }
        kwargs.update(overrides)
        return svc.open_facility(None, **kwargs)

    def cash(self):
        return svc.cash_balance()

    def lines(self, source):
        entry = JournalEntry.objects.get(source=source)
        return {line.account.code: line for line in entry.lines.all()}


# ------------------------------------------------------------------ capital
class CapitalTests(FundingBase):
    def test_an_injection_debits_cash_and_credits_share_capital(self):
        response = self.admin.post("/api/funding/capital", {
            "txn_type": "injection", "amount": "10000.00",
            "contributor": "Founding shareholders"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)

        lines = self.lines("capital_injection")
        self.assertEqual(lines["1000"].debit, dec("10000.00"))
        self.assertEqual(lines["3100"].credit, dec("10000.00"))
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertEqual(self.cash(), dec("10000.00"))

    def test_a_dividend_debits_distributions_and_nets_off_equity(self):
        svc.inject_capital(None, dec("10000"), "Shareholders")
        self.admin.post("/api/funding/capital", {
            "txn_type": "dividend", "amount": "1500.00", "contributor": "Shareholders"},
            format="json")

        lines = self.lines("capital_dividend")
        self.assertEqual(lines["3200"].debit, dec("1500.00"))
        self.assertEqual(lines["1000"].credit, dec("1500.00"))
        # 3200 is typed EQUITY, so its balance reads negative and reduces equity
        # without any special handling on the balance sheet.
        sheet = gl.balance_sheet()
        distributions = next(r for r in sheet["equity"] if r["code"] == "3200")
        self.assertEqual(distributions["balance"], dec("-1500.00"))

    def test_a_return_of_capital_cannot_exceed_what_was_contributed(self):
        svc.inject_capital(None, dec("5000"), "Shareholders")
        response = self.admin.post("/api/funding/capital", {
            "txn_type": "return_of_capital", "amount": "6000.00",
            "contributor": "Shareholders"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("5000.00", response.json()["detail"])

    def test_a_dividend_that_would_make_equity_negative_is_refused(self):
        svc.inject_capital(None, dec("1000"), "Shareholders")
        response = self.admin.post("/api/funding/capital", {
            "txn_type": "dividend", "amount": "5000.00", "contributor": "Shareholders"},
            format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("negative equity", response.json()["detail"])

    def test_a_reversal_mirrors_the_injection_and_leaves_the_ledger_where_it_started(self):
        before = self.cash()
        injection = svc.inject_capital(None, dec("8000"), "Shareholders")
        self.assertEqual(self.cash(), before + dec("8000"))

        response = self.admin.post(f"/api/funding/capital/{injection.id}/reverse",
                                   {"narration": "Cheque bounced"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.cash(), before)
        self.assertEqual(svc.capital_summary()["net_capital"], ZERO)
        # The reversal raised its own entry rather than relying on a later backfill.
        self.assertTrue(CapitalTransaction.objects
                        .filter(pk=response.json()["id"], journal_entry__isnull=False).exists())

    def test_net_capital_is_injected_less_returned_and_ignores_dividends(self):
        svc.inject_capital(None, dec("10000"), "Shareholders")
        svc.return_capital(None, dec("2000"), "Shareholders")
        svc.pay_dividend(None, dec("500"), "Shareholders")
        summary = svc.capital_summary()

        self.assertEqual(summary["net_capital"], dec("8000.00"))
        # Which is exactly what account 3100 carries; dividends live on 3200.
        by_code = {r["code"]: r["balance"] for r in gl.trial_balance()["rows"]}
        self.assertEqual(by_code["3100"], dec("8000.00"))
        self.assertEqual(by_code["3200"], dec("-500.00"))


# ------------------------------------------------------------------ facilities
class FacilityTests(FundingBase):
    def test_a_drawdown_puts_cash_in_and_a_liability_on(self):
        facility = self.facility()
        response = self.admin.post(f"/api/funding/facilities/{facility.id}/drawdown",
                                   {"amount": "20000.00"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)

        lines = self.lines("facility_drawdown")
        self.assertEqual(lines["1000"].debit, dec("20000.00"))
        self.assertEqual(lines["2100"].credit, dec("20000.00"))
        facility.refresh_from_db()
        self.assertEqual(facility.principal_outstanding, dec("20000.00"))
        self.assertEqual(response.json()["principal_after"], "20000.00")

    def test_a_drawdown_cannot_exceed_what_is_available(self):
        facility = self.facility(facility_limit=dec("5000"))
        svc.drawdown(facility, None, dec("3000"))
        response = self.admin.post(f"/api/funding/facilities/{facility.id}/drawdown",
                                   {"amount": "2500.00"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("2000.00", response.json()["detail"])

    def test_a_revolving_facility_frees_its_limit_and_a_term_facility_does_not(self):
        """Draw the WHOLE limit, repay ALL of it, draw again.

        Repaying only half would pass on a facility that can never be redrawn, which
        is the shape of bug this test exists to catch.
        """
        revolving = self.facility(facility_limit=dec("5000"), is_revolving=True)
        svc.drawdown(revolving, None, dec("5000"))
        svc.repay(revolving, None, dec("5000"))
        revolving.refresh_from_db()
        self.assertEqual(revolving.available, dec("5000.00"))
        svc.drawdown(revolving, None, dec("5000"))  # must not raise
        revolving.refresh_from_db()
        self.assertEqual(revolving.principal_outstanding, dec("5000.00"))

        term = self.facility(facility_limit=dec("5000"), is_revolving=False)
        svc.drawdown(term, None, dec("5000"))
        svc.repay(term, None, dec("5000"))
        term.refresh_from_db()
        self.assertEqual(term.available, ZERO, "a term facility does not free its limit")
        with self.assertRaises(Exception) as caught:
            svc.drawdown(term, None, dec("1000"))
        self.assertIn("term facility", str(caught.exception))

    def test_a_repayment_reduces_the_liability_and_the_cash(self):
        facility = self.facility()
        svc.drawdown(facility, None, dec("20000"))
        cash_before = self.cash()

        response = self.admin.post(f"/api/funding/facilities/{facility.id}/repay",
                                   {"amount": "5000.00"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        lines = self.lines("facility_repayment")
        self.assertEqual(lines["2100"].debit, dec("5000.00"))
        self.assertEqual(lines["1000"].credit, dec("5000.00"))
        facility.refresh_from_db()
        self.assertEqual(facility.principal_outstanding, dec("15000.00"))
        self.assertEqual(self.cash(), cash_before - dec("5000.00"))

    def test_a_fee_is_an_expense_paid_in_cash(self):
        facility = self.facility()
        svc.drawdown(facility, None, dec("20000"))
        self.admin.post(f"/api/funding/facilities/{facility.id}/fee",
                        {"amount": "250.00", "narration": "Arrangement fee"}, format="json")

        lines = self.lines("facility_fee")
        self.assertEqual(lines["5310"].debit, dec("250.00"))
        self.assertEqual(lines["1000"].credit, dec("250.00"))
        facility.refresh_from_db()
        self.assertEqual(facility.principal_outstanding, dec("20000.00"),
                         "a fee is not a drawdown")

    def test_a_facility_cannot_be_closed_while_it_owes_anything(self):
        facility = self.facility()
        svc.drawdown(facility, None, dec("20000"))
        response = self.admin.post(f"/api/funding/facilities/{facility.id}/close", {},
                                   format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("20000.00", response.json()["detail"])

        svc.repay(facility, None, dec("20000"))
        response = self.admin.post(f"/api/funding/facilities/{facility.id}/close", {},
                                   format="json")
        self.assertEqual(response.status_code, 200, response.content)
        facility.refresh_from_db()
        self.assertIsNotNone(facility.closed_on)
        with self.assertRaises(Exception):
            svc.drawdown(facility, None, dec("100"))

    def test_a_limit_cannot_be_lowered_below_what_is_drawn(self):
        facility = self.facility()
        svc.drawdown(facility, None, dec("20000"))
        response = self.admin.patch(f"/api/funding/facilities/{facility.id}",
                                    {"facility_limit": "10000.00"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("20000.00", response.json()["detail"])


# ------------------------------------------------------------------ interest
class BorrowingInterestTests(FundingBase):
    def test_interest_accrues_monthly_to_5300_and_2110(self):
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))

        result = svc.accrue_interest(date(2026, 2, 1), facility)

        self.assertEqual(result["months_posted"], 1)
        self.assertEqual(result["interest_accrued"], dec("200.00"))
        lines = self.lines("facility_interest_accrual")
        self.assertEqual(lines["5300"].debit, dec("200.00"))
        self.assertEqual(lines["2110"].credit, dec("200.00"))
        facility.refresh_from_db()
        self.assertEqual(facility.interest_accrued, dec("200.00"))
        self.assertEqual(facility.last_accrual_date, date(2026, 2, 1))

    def test_a_second_run_in_the_same_month_accrues_nothing(self):
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 2, 1), facility)

        again = svc.accrue_interest(date(2026, 2, 15), facility)
        self.assertEqual(again["months_posted"], 0)
        facility.refresh_from_db()
        self.assertEqual(facility.interest_accrued, dec("200.00"))

    def test_missed_months_are_caught_up_rather_than_lost(self):
        """A scheduler down for a quarter must not silently lose a real debt."""
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))

        result = svc.accrue_interest(date(2026, 5, 1), facility)

        self.assertEqual(result["months_posted"], 4)
        self.assertEqual(result["interest_accrued"], dec("800.00"))
        facility.refresh_from_db()
        self.assertEqual(facility.interest_accrued, dec("800.00"))
        self.assertEqual(facility.last_accrual_date, date(2026, 5, 1))

    def test_a_zero_rate_facility_is_stamped_and_not_rescanned(self):
        facility = self.facility(start_date=date(2026, 1, 1), interest_rate_pct_pa=ZERO)
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))

        result = svc.accrue_interest(date(2026, 5, 1), facility)

        self.assertEqual(result["months_posted"], 0)
        self.assertEqual(FacilityTransaction.objects.filter(
            txn_type=FacilityTxnType.INTEREST_ACCRUAL).count(), 0)
        facility.refresh_from_db()
        self.assertEqual(facility.last_accrual_date, date(2026, 5, 1),
                         "an interest-free facility must not be rescanned from its start "
                         "date every night")

    def test_paying_interest_settles_the_accrual(self):
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 3, 1), facility)  # 400.00
        cash_before = self.cash()

        response = self.admin.post(f"/api/funding/facilities/{facility.id}/interest",
                                   {"amount": "400.00"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        lines = self.lines("facility_interest_payment")
        self.assertEqual(lines["2110"].debit, dec("400.00"))
        self.assertEqual(lines["1000"].credit, dec("400.00"))
        facility.refresh_from_db()
        self.assertEqual(facility.interest_accrued, ZERO)
        self.assertEqual(self.cash(), cash_before - dec("400.00"))

    def test_paying_more_than_has_accrued_is_refused_rather_than_expensed(self):
        """The rule that stops 5300 being charged twice for one period.

        Expensing the unaccrued remainder is the tempting alternative: pay on the
        1st before anything has accrued, and the month-end accrual then charges the
        same period again.
        """
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 2, 1), facility)  # 200.00

        response = self.admin.post(f"/api/funding/facilities/{facility.id}/interest",
                                   {"amount": "350.00"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("200.00", response.json()["detail"])
        self.assertIn("twice", response.json()["detail"])

        by_code = {r["code"]: r["balance"] for r in gl.trial_balance()["rows"]}
        self.assertEqual(by_code["5300"], dec("200.00"), "one month accrued, one month expensed")

    def test_a_month_in_a_closed_period_is_skipped_and_named(self):
        """Neither posted into a closed month nor silently dropped.

        Refusing the whole run would make a book with any closed period
        un-accruable forever; dropping the month quietly would lose a real debt with
        both sides of the 2110 check wrong together.
        """
        from core.services import periods

        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))
        periods.close_period(2026, 2, None)

        result = svc.accrue_interest(date(2026, 5, 1), facility)

        self.assertEqual(result["months_skipped"], 1)
        self.assertIn("2026-02-01", result["skipped"][0])
        self.assertEqual(result["months_posted"], 3)
        self.assertEqual(result["interest_accrued"], dec("600.00"))
        facility.refresh_from_db()
        self.assertEqual(facility.last_accrual_date, date(2026, 5, 1),
                         "the cursor still advances, or the skipped month is retried forever")
        self.assertTrue(gl.reconciliation()["agrees"])

    def test_an_accrual_cannot_be_reversed(self):
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("20000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 2, 1), facility)
        accrual = FacilityTransaction.objects.get(txn_type=FacilityTxnType.INTEREST_ACCRUAL)

        response = self.admin.post(
            f"/api/funding/facilities/{facility.id}/transactions/{accrual.id}/reverse",
            {"narration": "changed my mind"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("accrual cannot be reversed", response.json()["detail"])


# ------------------------------------------------------------------ cash
class CashGuardTests(FundingBase):
    """Cash is drained by LENDING it out, not by expensing it.

    A loan is an asset, so the equity is untouched and the cash guard is tested on
    its own. Spending the money on a fee would reduce equity as well, and the equity
    check would fire first — which is what an earlier version of these tests did,
    passing for the wrong reason.
    """

    def lend_out(self, count, principal):
        for index in range(count):
            borrower = self.make_borrower(national_id=f"63-20000{index}A63")
            self.disbursed_loan(self.product, borrower, principal=principal)

    def test_a_repayment_the_bank_cannot_fund_is_refused(self):
        """The slice exists to stop cash going negative; it must not be the cause."""
        facility = self.facility()
        svc.drawdown(facility, None, dec("6000"))
        response = self.admin.post(f"/api/funding/facilities/{facility.id}/repay",
                                   {"amount": "6000.00"}, format="json")
        self.assertEqual(response.status_code, 201, "there is cash for this one")

        svc.drawdown(facility, None, dec("6000"))
        self.lend_out(3, 1900)  # most of the cash is now out on loan
        self.assertLess(self.cash(), dec("6000"))

        response = self.admin.post(f"/api/funding/facilities/{facility.id}/repay",
                                   {"amount": "6000.00"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("in the bank", response.json()["detail"])
        self.assertGreaterEqual(self.cash(), ZERO, "the guard must leave cash non-negative")

    def test_the_cash_guard_measures_as_at_the_posting_date(self):
        """A payment back-dated into a month when the bank was empty is refused."""
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("5000"), date(2026, 6, 1))
        # There is 5,000 in the bank today, but there was nothing in March.
        self.assertEqual(self.cash(), dec("5000.00"))

        with self.assertRaises(Exception) as caught:
            svc.repay(facility, None, dec("1000"), date(2026, 3, 1))
        self.assertIn("as at 2026-03-01", str(caught.exception))

        svc.repay(facility, None, dec("1000"), date(2026, 7, 1))  # must not raise

    def test_reversing_an_injection_cannot_drive_share_capital_negative(self):
        injection = svc.inject_capital(None, dec("5000"), "Shareholders")
        svc.return_capital(None, dec("5000"), "Shareholders")
        # Net capital is zero; reversing the injection would put 3100 at -5000.
        svc.inject_capital(None, dec("5000"), "Someone else")  # cash for the reversal

        with self.assertRaises(Exception) as caught:
            svc.reverse_capital_transaction(injection, None, "Cheque bounced")
        self.assertIn("share capital", str(caught.exception))
        self.assertTrue(gl.reconciliation()["agrees"])

    def test_a_dividend_with_no_cash_is_refused_even_with_the_equity_for_it(self):
        svc.inject_capital(None, dec("8000"), "Shareholders")
        self.lend_out(4, 1900)  # equity intact, but the money is out on loan

        cash = self.cash()
        equity = gl.balance_sheet()["total_equity"]
        # An amount the equity covers and the bank does not, so the cash guard is
        # what refuses it rather than the equity guard.
        amount = cash + dec("100")
        self.assertLess(amount, equity, "the fixture must isolate the cash guard")

        response = self.admin.post("/api/funding/capital", {
            "txn_type": "dividend", "amount": str(amount), "contributor": "Shareholders"},
            format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("in the bank", response.json()["detail"])


# ------------------------------------------------------------------ the complaint
class BalanceSheetTests(FundingBase):
    def test_cash_stops_being_negative_once_the_funding_is_booked(self):
        """The actual defect: lending money the system never said you had."""
        for index in range(4):
            borrower = self.make_borrower(national_id=f"63-10000{index}A63")
            self.disbursed_loan(self.product, borrower, principal=2000)
        self.assertLess(self.cash(), ZERO, "the fixture has to reproduce the complaint")

        svc.inject_capital(None, dec("5000"), "Founding shareholders")
        facility = self.facility()
        svc.drawdown(facility, None, dec("10000"))

        self.assertGreater(self.cash(), ZERO)

    def test_the_balance_sheet_derives_retained_earnings_from_income_less_expense(self):
        svc.inject_capital(None, dec("20000"), "Shareholders")
        self.disbursed_loan(self.product, self.borrower)

        sheet = gl.balance_sheet()
        statement = gl.income_statement(None, date.today())
        self.assertEqual(sheet["retained_earnings"], statement["surplus"])

        retained = next(r for r in sheet["equity"] if r["code"] == "3000")
        # Same keys as every other row: csv_response builds its DictWriter from the
        # first row and a row with different keys raises.
        self.assertEqual(set(retained), {"code", "name", "type", "debit", "credit", "balance",
                                         "side"})
        self.assertEqual(retained["name"], "Retained earnings")

    def test_equity_holds_capital_borrowings_sit_in_liabilities_and_the_sheet_adds_up(self):
        svc.inject_capital(None, dec("20000"), "Shareholders")
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("15000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 3, 1), facility)
        loan = self.disbursed_loan(self.product, self.borrower)
        self.teller.post(f"/api/loans/{loan['id']}/repayments",
                         {"amount": "197.02", "txn_date": "2026-03-25"}, format="json")

        sheet = gl.balance_sheet()
        by_code = {r["code"]: r["balance"] for r in
                   sheet["assets"] + sheet["liabilities"] + sheet["equity"]}
        self.assertEqual(by_code["2100"], dec("15000.00"))
        self.assertEqual(by_code["2110"], dec("300.00"))
        self.assertEqual(by_code["3100"], dec("20000.00"))
        self.assertEqual(sheet["total_assets"], sheet["total_liabilities_and_equity"])
        self.assertTrue(sheet["balanced"])

    def test_the_balance_sheet_is_served_as_json_and_csv_to_every_role(self):
        svc.inject_capital(None, dec("5000"), "Shareholders")
        for client in (self.admin, self.officer, self.teller):
            body = client.get("/api/ledger/balance-sheet").json()
            self.assertTrue(body["balanced"])
        csv = self.teller.get("/api/ledger/balance-sheet?fmt=csv")
        self.assertEqual(csv.status_code, 200)
        self.assertEqual(csv["Content-Type"], "text/csv")
        self.assertIn("code", csv.content.decode().splitlines()[0])

    def test_a_branch_slice_is_a_sub_book_without_the_capital(self):
        svc.inject_capital(None, dec("5000"), "Shareholders")
        self.disbursed_loan(self.product, self.borrower)

        sheet = gl.balance_sheet(branch_id=self.branch.id)
        codes = {r["code"] for r in sheet["assets"] + sheet["liabilities"] + sheet["equity"]}
        self.assertNotIn("3100", codes,
                         "capital carries no branch, so a branch slice cannot show it")
        self.assertEqual(sheet["branch_id"], self.branch.id)
        self.assertTrue(sheet["balanced"])

    def test_an_empty_book_balances_at_zero(self):
        sheet = gl.balance_sheet()
        self.assertEqual(sheet["total_assets"], ZERO)
        self.assertEqual(sheet["retained_earnings"], ZERO)
        self.assertTrue(sheet["balanced"])
        self.assertEqual(svc.funding_summary()["utilisation_pct"], ZERO,
                         "no facility means no utilisation, not a division by zero")


# ------------------------------------------------------------------ the real checks
class ReconciliationTests(FundingBase):
    def test_every_new_account_ties_to_its_sub_ledger(self):
        svc.inject_capital(None, dec("20000"), "Shareholders")
        svc.return_capital(None, dec("2000"), "Shareholders")
        svc.pay_dividend(None, dec("1000"), "Shareholders")
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("15000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 3, 1), facility)
        svc.pay_interest(facility, None, dec("100"), date(2026, 3, 2))
        svc.repay(facility, None, dec("5000"), date(2026, 3, 3))

        ties = gl.reconciliation()

        self.assertTrue(ties["agrees"], ties["breaks"])
        by_code = {r["code"]: r for r in ties["rows"]}
        self.assertEqual(by_code["2100"]["book"], dec("10000.00"))
        self.assertEqual(by_code["2110"]["book"], dec("200.00"))
        self.assertEqual(by_code["3100"]["book"], dec("18000.00"))
        self.assertEqual(by_code["3200"]["book"], dec("-1000.00"))

    def test_a_reversed_movement_and_its_reversal_both_leave_the_sub_ledger(self):
        facility = self.facility()
        drawdown = svc.drawdown(facility, None, dec("15000"))
        self.admin.post(
            f"/api/funding/facilities/{facility.id}/transactions/{drawdown.id}/reverse",
            {"narration": "Funder recalled it"}, format="json")

        facility.refresh_from_db()
        self.assertEqual(facility.principal_outstanding, ZERO)
        self.assertTrue(gl.reconciliation()["agrees"])
        self.assertEqual(self.cash(), ZERO)

    def test_the_reconciliation_endpoint_names_a_break(self):
        facility = self.facility()
        svc.drawdown(facility, None, dec("15000"))
        # Move the sub-ledger behind the ledger's back, the way a bad migration or a
        # hand-edit in SSMS would.
        FundingFacility.objects.filter(pk=facility.id).update(
            principal_outstanding=dec("9000"))

        body = self.teller.get("/api/ledger/reconciliation").json()
        self.assertFalse(body["agrees"])
        break_row = next(r for r in body["breaks"] if r["code"] == "2100")
        self.assertEqual(break_row["difference"], "6000.00")

    def test_backfill_reposts_facility_and_capital_entries(self):
        svc.inject_capital(None, dec("20000"), "Shareholders")
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("15000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 3, 1), facility)
        self.disbursed_loan(self.product, self.borrower)

        JournalEntry.objects.all().delete()
        first = gl.backfill()
        second = gl.backfill()

        self.assertGreater(first["posted"], 0)
        self.assertEqual(second["posted"], 0, "backfill must be idempotent")
        self.assertGreater(first["by_source"]["facility"], 0)
        self.assertEqual(first["by_source"]["capital"], 1)
        self.assertFalse(FacilityTransaction.objects.filter(journal_entry__isnull=True).exists())
        self.assertFalse(CapitalTransaction.objects.filter(journal_entry__isnull=True).exists())
        self.assertTrue(gl.reconciliation()["agrees"])

    def test_the_journal_can_be_filtered_by_the_new_sources(self):
        """Proves the widened `source` column round-trips rather than truncating."""
        svc.inject_capital(None, dec("5000"), "Shareholders")
        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("15000"), date(2026, 1, 1))
        svc.accrue_interest(date(2026, 3, 1), facility)
        svc.pay_interest(facility, None, dec("150"), date(2026, 3, 2))

        for source, expected in [("capital_injection", 1), ("facility_drawdown", 1),
                                 ("facility_interest_accrual", 2),
                                 ("facility_interest_payment", 1)]:
            body = self.admin.get(f"/api/ledger/journal?source={source}").json()
            self.assertEqual(body["count"], expected, source)

        borrowings = self.admin.get("/api/ledger/journal?account=2100").json()
        self.assertEqual(borrowings["count"], 1)
        # The entry carries a back-link to what raised it.
        entry = borrowings["results"][0]
        self.assertIsNotNone(entry["facility_transaction_id"])
        self.assertIsNone(entry["transaction_id"])


# ------------------------------------------------------------------ summary and roles
class SummaryAndPermissionTests(FundingBase):
    def test_the_summary_reports_utilisation_and_what_is_maturing(self):
        svc.inject_capital(None, dec("15000"), "Shareholders")
        soon = date.today() + timedelta(days=30)
        facility = self.facility(facility_limit=dec("50000"), maturity_date=soon)
        svc.drawdown(facility, None, dec("20000"))

        body = self.teller.get("/api/funding/summary").json()
        self.assertEqual(body["total_limit"], "50000.00")
        self.assertEqual(body["drawn"], "20000.00")
        self.assertEqual(body["available"], "30000.00")
        self.assertEqual(body["utilisation_pct"], "40.00")
        self.assertEqual(body["capital"]["net_capital"], "15000.00")
        self.assertEqual(len(body["maturing_soon"]), 1)
        self.assertEqual(body["maturing_soon"][0]["days"], 30)

    def test_reads_are_open_to_every_role_and_writes_are_admin_only(self):
        facility = self.facility()
        for client in (self.admin, self.officer, self.teller):
            self.assertEqual(client.get("/api/funding/facilities").status_code, 200)
            self.assertEqual(client.get("/api/funding/capital").status_code, 200)
            self.assertEqual(client.get("/api/funding/summary").status_code, 200)
            self.assertEqual(client.get("/api/ledger/reconciliation").status_code, 200)

        for client in (self.officer, self.teller):
            self.assertEqual(
                client.post("/api/funding/facilities",
                            {"funder_name": "X", "name": "Y", "facility_limit": "100"},
                            format="json").status_code, 400,
                "the inline admin check answers 400 on a GET+POST route")
            self.assertEqual(
                client.post(f"/api/funding/facilities/{facility.id}/drawdown",
                            {"amount": "100"}, format="json").status_code, 403)
            self.assertEqual(
                client.post("/api/funding/capital",
                            {"txn_type": "injection", "amount": "100", "contributor": "X"},
                            format="json").status_code, 400)

    def test_a_movement_into_a_closed_period_is_refused(self):
        from core.services import periods

        facility = self.facility(start_date=date(2026, 1, 1))
        svc.drawdown(facility, None, dec("15000"), date(2026, 1, 1))
        periods.close_period(2026, 3, None)

        response = self.admin.post(f"/api/funding/facilities/{facility.id}/repay",
                                   {"amount": "100.00", "txn_date": "2026-03-15"}, format="json")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("closed accounting period", response.json()["detail"])

        response = self.admin.post("/api/funding/capital", {
            "txn_type": "injection", "amount": "100.00", "contributor": "X",
            "txn_date": "2026-02-01"}, format="json")
        self.assertEqual(response.status_code, 409, response.content)
