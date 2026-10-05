"""Payments reported by a mobile-money provider or a bank.

The rules that matter: nothing posts without the provider's signature; a payment
that names a loan is posted through the ordinary waterfall and lands in the ledger;
a provider's retry posts nothing twice; and anything that cannot be placed safely,
including a payer with two loans, waits for a person instead of being guessed.
"""
import json
from decimal import Decimal

from django.test import override_settings
from rest_framework.test import APIClient

from core.models import IncomingPayment, Transaction, TxnType
from core.services import ledger as gl
from core.services.incoming import sign

from .test_components import LedgerBase, dec

SECRET = "test-shared-secret"
PROVIDERS = {
    "ecocash": {"secret": SECRET, "signature_header": "X-Signature", "method": "mobile_money",
                "fields": {"reference": "txn.id", "account": "billRef", "phone": "msisdn",
                           "amount": "txn.amount"}},
    "bank": {"secret": SECRET, "signature_header": "X-Signature", "method": "bank_transfer",
             "fields": {}},
    "nosecret": {"secret": "", "signature_header": "X-Signature", "method": "mobile_money",
                 "fields": {}},
}


@override_settings(INCOMING_PAYMENTS=PROVIDERS)
class IncomingPaymentTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower(phone="0771234567")
        self.loan = self.disbursed_loan(self.product, self.borrower)
        self.provider = APIClient()

    def deliver(self, body: dict, provider="bank", signature=None):
        raw = json.dumps(body).encode()
        return self.provider.post(
            f"/api/payments/incoming/{provider}", raw, content_type="application/json",
            HTTP_X_SIGNATURE=signature if signature is not None else sign(SECRET, raw))

    def bank_payment(self, reference="B-1", amount="100.00", **extra):
        return self.deliver({"reference": reference, "amount": amount,
                             "date": "2026-03-10", **extra})

    def test_a_payment_naming_the_loan_is_posted_and_reconciles(self):
        response = self.bank_payment(account=self.loan["loan_no"])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "posted")
        txn = Transaction.objects.get(loan_id=self.loan["id"], txn_type=TxnType.REPAYMENT)
        self.assertEqual((txn.amount, txn.method, txn.reference),
                         (Decimal("100.00"), "bank_transfer", "B-1"))
        self.assertIsNone(txn.posted_by)
        rec = {row["code"]: row for row in gl.reconciliation()["rows"]}
        self.assertTrue(rec["1100"]["agrees"], rec["1100"])
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_the_providers_own_field_names_are_read_from_settings(self):
        response = self.deliver({"txn": {"id": "MP-77", "amount": 55}, "msisdn": "+263 77 123 4567"},
                                provider="ecocash")
        self.assertEqual(response.json()["status"], "posted")
        payment = IncomingPayment.objects.get(reference="MP-77")
        self.assertEqual((payment.amount, payment.loan_id), (Decimal("55.00"), self.loan["id"]))
        self.assertEqual(payment.transaction.method, "mobile_money")

    def test_a_retry_is_answered_but_posts_nothing_twice(self):
        self.bank_payment(account=self.loan["loan_no"])
        again = self.bank_payment(account=self.loan["loan_no"])
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["duplicate"])
        self.assertEqual(Transaction.objects.filter(txn_type=TxnType.REPAYMENT).count(), 1)

    def test_nothing_is_stored_without_a_good_signature(self):
        self.assertEqual(self.bank_payment(account="x").status_code, 200)  # control
        bad = self.deliver({"reference": "B-2", "amount": "100"}, signature="0" * 64)
        self.assertEqual(bad.status_code, 403)
        unsigned = self.deliver({"reference": "B-3", "amount": "100"}, signature="")
        self.assertEqual(unsigned.status_code, 403)
        self.assertEqual(self.deliver({"reference": "B-4", "amount": "1"},
                                      provider="nosecret").status_code, 403)
        self.assertEqual(self.deliver({"reference": "B-5", "amount": "1"},
                                      provider="unknown").status_code, 404)
        self.assertFalse(IncomingPayment.objects.filter(
            reference__in=["B-2", "B-3", "B-4", "B-5"]).exists())

    def test_an_unreadable_payment_is_refused(self):
        self.assertEqual(self.bank_payment(amount="lots").status_code, 400)
        self.assertEqual(self.bank_payment(amount="-5").status_code, 400)
        self.assertEqual(self.deliver({"amount": "5"}).status_code, 400)

    def test_a_national_id_finds_the_borrowers_only_loan(self):
        response = self.bank_payment(account=self.borrower["national_id"])
        self.assertEqual(response.json()["status"], "posted")

    def test_a_phone_shared_by_two_borrowers_waits_for_a_person(self):
        # A household paying from one handset: two loans, and nothing to choose by.
        spouse = self.make_borrower(national_id="63-654321B63", phone="+263771234567")
        self.disbursed_loan(self.product, spouse)
        response = self.bank_payment(phone="0771 234 567")
        self.assertEqual(response.json()["status"], "unmatched")
        payment = IncomingPayment.objects.get(reference="B-1")
        self.assertIn("more than one active loan", payment.note)
        self.assertFalse(Transaction.objects.filter(txn_type=TxnType.REPAYMENT).exists())

    def test_more_than_the_loan_owes_is_held_not_posted(self):
        response = self.bank_payment(account=self.loan["loan_no"], amount="999999")
        self.assertEqual(response.json()["status"], "unmatched")
        payment = IncomingPayment.objects.get(reference="B-1")
        self.assertEqual(payment.loan_id, self.loan["id"])
        self.assertIn("exceeds total outstanding", payment.note)

    def test_a_teller_places_an_unmatched_payment_and_it_cannot_be_placed_twice(self):
        self.bank_payment(account="SOMEONE-ELSE")
        payment = IncomingPayment.objects.get(reference="B-1")
        listed = self.teller.get("/api/payments/incoming?status=unmatched").json()
        self.assertEqual(listed["unmatched"], 1)
        self.assertEqual(listed["results"][0]["reference"], "B-1")

        response = self.teller.post(f"/api/payments/incoming/{payment.id}/assign",
                                    {"loan_no": self.loan["loan_no"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "posted")
        self.assertEqual(response.json()["resolved_by_name"], "Teller")
        again = self.teller.post(f"/api/payments/incoming/{payment.id}/assign",
                                 {"loan_no": self.loan["loan_no"]}, format="json")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(Transaction.objects.filter(txn_type=TxnType.REPAYMENT).count(), 1)

    def test_placing_on_a_loan_that_would_refuse_it_says_why(self):
        self.bank_payment(account="SOMEONE-ELSE", amount="999999")
        payment = IncomingPayment.objects.get(reference="B-1")
        response = self.teller.post(f"/api/payments/incoming/{payment.id}/assign",
                                    {"loan_no": self.loan["loan_no"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds total outstanding", response.json()["detail"])
        payment.refresh_from_db()
        self.assertEqual(payment.status, "unmatched")

    def test_only_an_officer_dismisses_and_must_say_why(self):
        self.bank_payment(account="SOMEONE-ELSE")
        payment = IncomingPayment.objects.get(reference="B-1")
        url = f"/api/payments/incoming/{payment.id}/dismiss"
        self.assertEqual(self.teller.post(url, {"note": "refunded"}, format="json").status_code,
                         403)
        self.assertEqual(self.officer.post(url, {"note": ""}, format="json").status_code, 400)
        response = self.officer.post(url, {"note": "Refunded to the payer"}, format="json")
        self.assertEqual(response.json()["status"], "dismissed")

    def test_the_queue_is_staff_only(self):
        self.assertEqual(self.provider.get("/api/payments/incoming").status_code, 401)

    def test_a_posted_payment_queues_a_receipt(self):
        from core.models import Notification

        self.bank_payment(account=self.loan["loan_no"])
        self.assertTrue(Notification.objects.filter(loan_id=self.loan["id"]).exists())

    def test_the_balance_falls_by_the_payment(self):
        before = dec(self.officer.get(f"/api/loans/{self.loan['id']}").json()["total_outstanding"])
        self.bank_payment(account=self.loan["loan_no"], amount="150")
        after = dec(self.officer.get(f"/api/loans/{self.loan['id']}").json()["total_outstanding"])
        self.assertEqual(before - after, Decimal("150.00"))
