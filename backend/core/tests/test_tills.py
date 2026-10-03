"""Teller tills: a float, a count, a second pair of eyes, and the difference booked.

What the drawer should hold is read from the postings, never typed in, so these
tests post real movements and check the till arrives at the figure the ledger
implies. The one that matters most: a shortage found at verification reaches
account 1000, because until it does the ledger claims cash the building does not
hold.
"""
from datetime import date
from decimal import Decimal

from core.models import (
    JournalEntry,
    OrganisationSetting,
    SavingsProduct,
    TillSession,
    Transaction,
    TxnType,
)
from core.services import funding as funding_svc
from core.services import ledger as gl
from core.services import savings as savings_svc

from .test_components import LedgerBase, dec


class TillBase(LedgerBase):
    def setUp(self):
        super().setUp()
        funding_svc.inject_capital(None, dec("20000"), "Shareholders", date(2026, 1, 5))
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def open(self, client=None, opening_float="500.00"):
        response = (client or self.teller).post("/api/tills", {"opening_float": opening_float},
                                                format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def repay(self, amount, method="cash", client=None):
        response = (client or self.teller).post(f"/api/loans/{self.loan['id']}/repayments", {
            "amount": amount, "method": method, "txn_date": "2026-03-25"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def mine(self, client=None):
        return (client or self.teller).get("/api/tills/current").json()["till"]

    def count(self, till, counted, note=None, client=None):
        return (client or self.teller).post(f"/api/tills/{till['id']}/count",
                                            {"counted_cash": counted, "note": note},
                                            format="json")


class ExpectedCashTests(TillBase):
    def test_the_drawer_should_hold_the_float_plus_cash_taken(self):
        self.open()
        self.repay("150.00")
        self.repay("100.00", method="bank_transfer")  # never touched the drawer
        till = self.mine()
        self.assertEqual(dec(till["position"]["cash_in"]), dec("150.00"))
        self.assertEqual(dec(till["position"]["expected_cash"]), dec("650.00"))
        self.assertEqual(len(till["position"]["movements"]), 1)

    def test_savings_withdrawals_come_out_of_the_drawer(self):
        self.open()
        product = SavingsProduct.objects.create(code="SAV", name="Savings")
        from core.models import Borrower, User

        teller = User.objects.get(username="teller")
        account = savings_svc.open_account(Borrower.objects.get(pk=self.borrower["id"]),
                                           product, teller)
        savings_svc.deposit(account, teller, dec("300"), method="cash")
        savings_svc.withdraw(account, teller, dec("120"), method="cash")
        till = self.mine()
        self.assertEqual(dec(till["position"]["cash_in"]), dec("300.00"))
        self.assertEqual(dec(till["position"]["cash_out"]), dec("120.00"))
        self.assertEqual(dec(till["position"]["expected_cash"]), dec("680.00"))

    def test_a_counter_charge_is_cash_in(self):
        self.open()
        response = self.teller.post(f"/api/loans/{self.loan['id']}/charges", {
            "name": "Statement fee", "amount": "5.00", "collection": "counter"}, format="json")
        # Raising a charge is an officer's job; the teller is refused and nothing moves.
        self.assertEqual(response.status_code, 403)
        self.officer.post("/api/tills", {"opening_float": "0"}, format="json")
        self.officer.post(f"/api/loans/{self.loan['id']}/charges", {
            "name": "Statement fee", "amount": "5.00", "collection": "counter"}, format="json")
        self.assertEqual(dec(self.mine(self.officer)["position"]["cash_in"]), dec("5.00"))

    def test_reversing_a_cash_repayment_takes_it_back_out(self):
        self.open(self.officer)
        repayment = self.repay("150.00", client=self.officer)
        self.officer.post(f"/api/loans/{self.loan['id']}/transactions/{repayment['id']}/reverse",
                          {"narration": "Posted to the wrong loan"}, format="json")
        till = self.mine(self.officer)
        self.assertEqual(dec(till["position"]["expected_cash"]), dec("500.00"))

    def test_another_tellers_cash_is_not_in_my_drawer(self):
        self.open()
        self.open(self.officer, opening_float="100.00")
        self.repay("150.00", client=self.officer)
        self.assertEqual(dec(self.mine()["position"]["expected_cash"]), dec("500.00"))

    def test_one_drawer_at_a_time(self):
        self.open()
        response = self.teller.post("/api/tills", {"opening_float": "100"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already have", response.json()["detail"])


class CountAndVerifyTests(TillBase):
    def test_a_short_count_needs_a_reason(self):
        till = self.open()
        self.repay("150.00")
        response = self.count(till, "630.00")
        self.assertEqual(response.status_code, 400)
        self.assertIn("short by 20.00", response.json()["detail"])

        response = self.count(till, "630.00", note="Recounted twice; one note missing")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "counted")
        self.assertEqual(dec(body["variance"]), dec("-20.00"))
        self.assertIsNone(self.mine())

    def test_the_teller_cannot_verify_their_own_count(self):
        till = self.open(self.officer)
        self.count(till, "500.00", client=self.officer)
        response = self.officer.post(f"/api/tills/{till['id']}/verify", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("other than the teller", response.json()["detail"])
        self.assertEqual(self.teller.post(f"/api/tills/{till['id']}/verify").status_code, 403)

    def test_a_verified_shortage_reaches_the_ledger(self):
        till = self.open()
        self.repay("150.00")
        self.count(till, "630.00", note="One note missing")
        cash_before = funding_svc.cash_balance()

        response = self.officer.post(f"/api/tills/{till['id']}/verify",
                                     {"note": "Agreed with the teller"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "verified")
        entry = JournalEntry.objects.get(entry_no=body["variance_entry_no"])
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["6800"].debit, dec("20.00"))
        self.assertEqual(lines["1000"].credit, dec("20.00"))
        self.assertEqual(funding_svc.cash_balance(), cash_before - dec("20.00"))

    def test_an_overage_is_other_income(self):
        till = self.open()
        self.count(till, "505.00", note="Customer left change")
        body = self.admin.post(f"/api/tills/{till['id']}/verify", {}, format="json").json()
        entry = JournalEntry.objects.get(entry_no=body["variance_entry_no"])
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["1000"].debit, dec("5.00"))
        self.assertEqual(lines["4900"].credit, dec("5.00"))

    def test_a_till_that_balances_posts_nothing(self):
        till = self.open()
        self.repay("150.00")
        self.count(till, "650.00")
        body = self.officer.post(f"/api/tills/{till['id']}/verify", {}, format="json").json()
        self.assertIsNone(body["variance_entry_no"])

    def test_the_count_is_frozen_once_closed(self):
        till = self.open(self.officer)
        repayment = self.repay("150.00", client=self.officer)
        self.count(till, "650.00", client=self.officer)
        # A later reversal does not rewrite what the drawer held at the count.
        self.officer.post(f"/api/loans/{self.loan['id']}/transactions/{repayment['id']}/reverse",
                          {"narration": "Wrong loan"}, format="json")
        body = self.officer.get(f"/api/tills/{till['id']}").json()
        self.assertEqual(dec(body["expected_cash"]), dec("650.00"))

    def test_rebuild_reposts_a_verified_difference(self):
        till = self.open()
        self.count(till, "480.00", note="Short")
        self.officer.post(f"/api/tills/{till['id']}/verify", {}, format="json")
        JournalEntry.objects.all().delete()
        result = gl.backfill()
        self.assertEqual(result["till_variances_reposted"], 1)
        self.assertIsNotNone(TillSession.objects.get(pk=till["id"]).variance_entry_id)


class RequireOpenTillTests(TillBase):
    def setUp(self):
        super().setUp()
        settings = OrganisationSetting.load()
        settings.require_open_till = True
        settings.save()

    def test_cash_without_an_open_till_is_refused(self):
        response = self.teller.post(f"/api/loans/{self.loan['id']}/repayments", {
            "amount": "150.00", "method": "cash", "txn_date": "2026-03-25"}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("open till", response.json()["detail"])
        self.assertFalse(Transaction.objects.filter(txn_type=TxnType.REPAYMENT).exists())

    def test_cash_is_taken_once_the_till_is_open(self):
        self.open()
        self.repay("150.00")

    def test_a_bank_transfer_needs_no_till(self):
        self.repay("150.00", method="bank_transfer")

    def test_the_setting_is_on_the_settings_endpoint(self):
        self.assertTrue(self.admin.get("/api/settings").json()["require_open_till"])
