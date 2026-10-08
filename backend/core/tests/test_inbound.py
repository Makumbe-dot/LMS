"""Incoming payments: signed notifications and statements, matched and posted once."""
import hashlib
import hmac
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from core.models import (
    RIGHT_PRESETS,
    InboundPayment,
    InboundStatus,
    Notification,
    NotificationKind,
    Transaction,
    TxnType,
    User,
)

from .fixtures import LoanFixtures

SECRET = "test-secret-for-ecocash"


@override_settings(INBOUND_PAYMENT_SECRETS={"ecocash": SECRET},
                   INBOUND_PAYMENT_PROVIDER_PATHS={
                       "bank": {"id": "data.txn.ref", "amount": "data.txn.value"}})
class InboundPaymentTests(LoanFixtures):
    def setUp(self):
        super().setUp()
        self.loan = self.make_loan()
        self.borrower = self.admin.get(f"/api/borrowers/{self.loan['borrower_id']}").json()

    def notify(self, provider="ecocash", secret=SECRET, **fields):
        body = json.dumps({"id": "MP001", "amount": "100.00", "date": "2026-03-10",
                           "phone": "263770000000", "reference": self.loan["loan_no"],
                           **fields}).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return APIClient().post(f"/api/payments/inbound/{provider}", body,
                                content_type="application/json", HTTP_X_SIGNATURE=signature)

    def repayments(self):
        return Transaction.objects.filter(loan_id=self.loan["id"], txn_type=TxnType.REPAYMENT)

    # ---- receiving
    def test_a_signed_notification_naming_the_loan_is_posted_at_once(self):
        response = self.notify()
        self.assertEqual(response.status_code, 201, response.content)
        payment = InboundPayment.objects.get()
        self.assertEqual(payment.status, InboundStatus.POSTED)
        self.assertEqual(payment.matched_by, "loan_no")
        txn = self.repayments().get()
        self.assertEqual(txn.amount, 100)
        self.assertEqual(txn.method, "mobile_money")
        self.assertEqual(txn.reference, "ecocash:MP001")
        self.assertTrue(Notification.objects.filter(kind=NotificationKind.RECEIPT).exists())

    def test_the_same_notification_twice_is_posted_once(self):
        self.notify()
        response = self.notify()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["duplicate"])
        self.assertEqual(self.repayments().count(), 1)
        self.assertEqual(InboundPayment.objects.count(), 1)

    def test_a_wrong_signature_keeps_nothing(self):
        response = self.notify(secret="not-the-secret")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(InboundPayment.objects.exists())

    def test_a_provider_without_a_secret_is_refused(self):
        response = self.notify(provider="mystery")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(InboundPayment.objects.exists())

    def test_a_providers_own_field_paths_are_read(self):
        body = json.dumps({"data": {"txn": {"ref": "B-77", "value": 50}},
                           "reference": self.loan["loan_no"]}).encode()
        with override_settings(INBOUND_PAYMENT_SECRETS={"bank": "b"}):
            signature = hmac.new(b"b", body, hashlib.sha256).hexdigest()
            response = APIClient().post("/api/payments/inbound/bank", body,
                                        content_type="application/json",
                                        HTTP_X_SIGNATURE="sha256=" + signature)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(InboundPayment.objects.get().external_id, "B-77")
        self.assertEqual(self.repayments().get().amount, 50)

    # ---- matching
    def test_a_short_loan_number_is_understood(self):
        number = int(self.loan["loan_no"].split("-")[1])
        self.notify(reference=f"ln {number}")
        self.assertEqual(InboundPayment.objects.get().status, InboundStatus.POSTED)

    def test_the_national_id_finds_the_one_active_loan(self):
        self.notify(reference=self.borrower["national_id"].lower())
        payment = InboundPayment.objects.get()
        self.assertEqual(payment.status, InboundStatus.POSTED)
        self.assertEqual(payment.matched_by, "national_id")

    def test_with_no_account_the_paying_phone_is_tried(self):
        phone = "263" + self.borrower["phone"][1:]
        self.notify(reference="", phone=phone)
        payment = InboundPayment.objects.get()
        self.assertEqual(payment.status, InboundStatus.POSTED)
        self.assertEqual(payment.matched_by, "phone")

    def test_more_than_is_owed_waits_with_the_reason_and_the_loan_it_nearly_matched(self):
        self.notify(amount="99999.00")
        payment = InboundPayment.objects.get()
        self.assertEqual(payment.status, InboundStatus.UNMATCHED)
        self.assertEqual(payment.loan_id, self.loan["id"])
        self.assertIn("exceeds", payment.reason)
        self.assertFalse(self.repayments().exists())

    def test_another_currency_waits(self):
        self.notify(currency="ZWG")
        payment = InboundPayment.objects.get()
        self.assertEqual(payment.status, InboundStatus.UNMATCHED)
        self.assertIn("ZWG", payment.reason)

    # ---- the queue
    def test_staff_assign_a_payment_nothing_matched(self):
        self.notify(reference="my loan", phone="0000")
        payment = InboundPayment.objects.get()
        self.assertEqual(payment.status, InboundStatus.UNMATCHED)
        self.assertIn("my loan", payment.reason)

        User.objects.create_user("clerk", "clerk-pass1", full_name="Clerk")
        clerk = self.client_for("clerk", "clerk-pass1")
        url = f"/api/payments/{payment.id}/assign"
        self.assertEqual(clerk.post(url, {"loan_no": self.loan["loan_no"]}).status_code, 403)

        response = self.teller.post(url, {"loan_no": self.loan["loan_no"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "posted")
        self.assertEqual(response.json()["matched_by"], "staff")
        self.assertEqual(self.repayments().get().posted_by.username, "teller")

        again = self.teller.post(url, {"loan_no": self.loan["loan_no"]}, format="json")
        self.assertEqual(again.status_code, 400)

    def test_a_rejected_payment_needs_a_reason_and_is_never_posted(self):
        self.notify(reference="nobody", phone="0000")
        payment = InboundPayment.objects.get()
        url = f"/api/payments/{payment.id}/reject"
        self.assertEqual(self.teller.post(url, {}, format="json").status_code, 400)
        response = self.teller.post(url, {"reason": "Refunded to sender"}, format="json")
        self.assertEqual(response.json()["status"], "rejected")
        response = self.teller.post(f"/api/payments/{payment.id}/assign",
                                    {"loan_no": self.loan["loan_no"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.repayments().exists())

    def test_retrying_posts_what_now_matches(self):
        self.notify(reference="", phone="263719999999")
        self.assertEqual(InboundPayment.objects.get().status, InboundStatus.UNMATCHED)
        self.officer.patch(f"/api/borrowers/{self.borrower['id']}", {"phone": "0719999999"},
                           format="json")
        result = self.teller.post("/api/payments/retry").json()
        self.assertEqual(result["posted"], 1)
        self.assertEqual(InboundPayment.objects.get().status, InboundStatus.POSTED)

    def test_the_register_lists_and_totals_by_status(self):
        self.notify()
        self.notify(id="MP002", reference="nobody", phone="0000")
        body = self.teller.get("/api/payments?status=unmatched").json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["totals"]["posted"]["count"], 1)

    # ---- statements
    def statement(self, text):
        upload = SimpleUploadedFile("statement.csv", text.encode(), content_type="text/csv")
        return self.teller.post("/api/payments/import", {
            "file": upload, "provider": "CBZ", "method": "bank_transfer"}, format="multipart")

    def test_a_statement_is_matched_line_by_line_and_never_twice(self):
        text = ("Transaction ID,Date,Amount,Narration,Phone\n"
                f"T1,10/03/2026,120.00,{self.loan['loan_no']},\n"
                "T2,10/03/2026,30.00,unknown person,\n")
        response = self.statement(text)
        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertEqual((result["new"], result["posted"], result["waiting"]), (2, 1, 1))
        self.assertEqual(self.repayments().get().method, "bank_transfer")

        again = self.statement(text).json()
        self.assertEqual((again["new"], again["duplicates"]), (0, 2))
        self.assertEqual(self.repayments().count(), 1)

    def test_a_statement_without_the_needed_columns_is_refused(self):
        response = self.statement("Date,Narration\n10/03/2026,x\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("id", response.json()["detail"])


class InboundRightsTests(LoanFixtures):
    def test_reading_the_register_needs_no_right(self):
        User.objects.create_user("viewer", "viewer-pass1", full_name="Viewer",
                                 rights=RIGHT_PRESETS["viewer"])
        viewer = self.client_for("viewer", "viewer-pass1")
        self.assertEqual(viewer.get("/api/payments").status_code, 200)
        self.assertEqual(viewer.post("/api/payments/retry").status_code, 403)
