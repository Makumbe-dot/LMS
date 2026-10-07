"""Paying disbursed loans out: wallets through a provider, banks through a bulk file."""
import io
import json
import unittest.mock as mock

from django.test import override_settings

from core.models import Borrower, Payout, PayoutStatus

from .fixtures import LoanFixtures

PROVIDER = {"url": "https://wallet.example/send", "format": "json", "to_field": "msisdn",
            "amount_field": "amount", "reference_field": "reference", "number_format": "plain",
            "id_path": "transactionId", "timeout": 5, "extra": {"merchant": "ZINMAD"},
            "headers": {"Authorization": "Bearer key"}}


class PayoutTests(LoanFixtures):
    def disburse(self, method, **borrower_fields):
        borrower = self.make_borrower(**borrower_fields)
        loan = self.officer.post("/api/loans", {"borrower_id": borrower["id"],
                                                "product_id": self.product["id"],
                                                "principal": 1000, "term_months": 6},
                                 format="json").json()
        self.assertEqual(self.admin.post(f"/api/loans/{loan['id']}/approve").status_code, 200)
        response = self.officer.post(f"/api/loans/{loan['id']}/disburse",
                                     {"method": method, "disbursement_date": "2026-03-01"},
                                     format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_a_cash_disbursement_needs_no_payout(self):
        self.disburse("cash")
        self.assertFalse(Payout.objects.exists())

    def test_a_wallet_payout_is_the_net_amount_to_the_borrowers_number(self):
        loan = self.disburse("mobile_money")
        payout = Payout.objects.get()
        self.assertEqual(payout.amount + Payout.objects.get().loan.upfront_fees, 1000)
        self.assertEqual(payout.account, Borrower.objects.get(pk=loan["borrower_id"]).phone)
        self.assertEqual(payout.status, PayoutStatus.PENDING)

    @override_settings(PAYOUT_HTTP=PROVIDER)
    def test_wallet_payouts_go_to_the_configured_provider(self):
        self.disburse("mobile_money")
        captured = {}

        class Answer:
            def read(self):
                return json.dumps({"transactionId": "MP123"}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            captured["body"] = json.loads(request.data)
            captured["auth"] = request.headers.get("Authorization")
            return Answer()

        with mock.patch("urllib.request.urlopen", opener):
            result = self.officer.post("/api/payouts/send-mobile").json()
        self.assertEqual((result["sent"], result["live"]), (1, True))
        self.assertTrue(captured["body"]["msisdn"].startswith("2637"))
        self.assertEqual(captured["body"]["merchant"], "ZINMAD")
        self.assertEqual(captured["auth"], "Bearer key")
        payout = Payout.objects.get()
        self.assertEqual((payout.status, payout.provider_reference), (PayoutStatus.SENT, "MP123"))

    def test_without_a_provider_wallet_payouts_are_logged_and_say_so(self):
        self.disburse("mobile_money")
        result = self.officer.post("/api/payouts/send-mobile").json()
        self.assertFalse(result["live"])
        self.assertIn("Logged only", Payout.objects.get().error)

    def test_the_bank_file_lists_pending_bank_payouts_once(self):
        self.disburse("bank_transfer", bank_name="CBZ", bank_branch="Kwame Nkrumah",
                      bank_account_no="01234567890", bank_account_name="T Borrower")
        response = self.officer.post("/api/payouts/bank-file")
        self.assertEqual(response.status_code, 200)
        lines = io.StringIO(response.content.decode("utf-8-sig")).read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("01234567890", lines[1])
        self.assertEqual(Payout.objects.get().status, PayoutStatus.SENT)
        # Already in a file: a second file has nothing to put in it.
        self.assertEqual(self.officer.post("/api/payouts/bank-file").status_code, 400)

    def test_a_bank_payout_with_no_account_waits_and_says_why(self):
        self.disburse("bank_transfer")
        payout = Payout.objects.get()
        self.assertIn("No account on file", payout.error)
        self.assertEqual(self.officer.post("/api/payouts/bank-file").status_code, 400)

    def test_marking_paid_failed_and_trying_again(self):
        self.disburse("mobile_money")
        payout = Payout.objects.get()
        failed = self.officer.post(f"/api/payouts/{payout.id}/failed", {"reason": "Wallet closed"},
                                   format="json")
        self.assertEqual(failed.json()["status"], "failed")
        Borrower.objects.filter(pk=payout.loan.borrower_id).update(mobile_wallet="0782 222 333")
        retried = self.officer.post(f"/api/payouts/{payout.id}/retry").json()
        self.assertEqual((retried["status"], retried["account"]), ("pending", "0782 222 333"))
        paid = self.officer.post(f"/api/payouts/{payout.id}/paid", {"reference": "EC-1"},
                                 format="json").json()
        self.assertEqual((paid["status"], paid["provider_reference"]), ("paid", "EC-1"))

    def test_bank_details_are_kept_on_the_borrower(self):
        borrower = self.make_borrower(bank_name="Stanbic", bank_account_no="9100")
        response = self.officer.patch(f"/api/borrowers/{borrower['id']}",
                                      {"mobile_wallet": None, "bank_branch": "Harare"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["bank_name"], response.json()["bank_branch"]), ("Stanbic", "Harare"))
