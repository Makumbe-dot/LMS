"""Signing the loan agreement with a one-time code."""
from datetime import timedelta
from unittest import mock

from django.utils import timezone

from core.models import LoanSignature, Notification, OrganisationSetting, SignatureStatus

from .fixtures import LoanFixtures

CODE = "123456"


def fixed_code():
    return mock.patch("core.services.signatures.secrets.randbelow", return_value=int(CODE))


class SignatureTests(LoanFixtures):
    def setUp(self):
        super().setUp()
        self.loan = self.make_loan(disbursed=None)  # approved, not yet paid out
        self.base = f"/api/loans/{self.loan['id']}/signature"

    def send(self, client=None):
        # The code is handed to the gateway once the request's work is committed.
        with fixed_code(), self.captureOnCommitCallbacks(execute=True):
            return (client or self.officer).post(f"{self.base}/code")

    def enter(self, code=CODE):
        return self.officer.post(f"{self.base}/verify", {"code": code}, format="json")

    def require(self, on=True):
        row = OrganisationSetting.load()
        row.require_signature = on
        row.save()

    def disburse(self):
        return self.officer.post(f"/api/loans/{self.loan['id']}/disburse", {
            "disbursement_date": "2026-03-01"}, format="json")

    # ---- signing
    def test_the_code_goes_to_the_borrower_and_signs_the_agreement(self):
        response = self.send()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["pending"])
        self.assertEqual(self.enter().status_code, 200)
        state = self.officer.get(self.base).json()
        self.assertTrue(state["signed"])
        self.assertEqual(state["channel"], "counter")
        html = self.officer.get(f"/api/loans/{self.loan['id']}/agreement").content.decode()
        self.assertIn("Signed electronically", html)

    def test_a_code_works_once(self):
        self.send()
        self.enter()
        self.assertEqual(self.enter().status_code, 400)

    def test_staff_never_see_the_code(self):
        self.send()
        message = Notification.objects.get(kind="signing_code")
        self.assertEqual(message.status, "sent")
        self.assertNotIn(CODE, message.body)  # blanked once delivered
        listed = self.admin.get("/api/notifications?status=").json()
        rows = listed.get("results", listed)
        self.assertTrue(all(CODE not in row["body"] for row in rows))
        found = self.admin.get(f"/api/notifications?q={CODE}").json()
        self.assertEqual(found.get("results", found), [])

    def test_wrong_guesses_are_counted_and_five_end_the_code(self):
        self.send()
        for left in (4, 3, 2, 1):
            response = self.enter("000000")
            self.assertEqual(response.status_code, 400)
            self.assertIn(str(left), response.json()["detail"])
        self.assertEqual(LoanSignature.objects.get().attempts, 4)
        self.enter("000000")
        self.assertEqual(LoanSignature.objects.get().status, SignatureStatus.EXPIRED)
        self.assertEqual(self.enter().status_code, 400)  # even the right one, now

    def test_an_old_code_expires(self):
        self.send()
        LoanSignature.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.enter()
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.json()["detail"])

    def test_a_second_code_needs_a_minute_and_replaces_the_first(self):
        self.send()
        self.assertEqual(self.send().status_code, 400)
        LoanSignature.objects.update(sent_at=timezone.now() - timedelta(minutes=2))
        self.assertEqual(self.send().status_code, 201)
        self.assertEqual(LoanSignature.objects.filter(status="superseded").count(), 1)

    def test_sending_a_code_needs_the_loans_right(self):
        self.assertEqual(self.send(client=self.teller).status_code, 403)

    # ---- disbursement
    def test_when_required_an_unsigned_loan_is_not_disbursed(self):
        self.require()
        response = self.disburse()
        self.assertEqual(response.status_code, 400)
        self.assertIn("not signed", response.json()["detail"])
        self.send()
        self.enter()
        self.assertEqual(self.disburse().status_code, 200)

    def test_changing_the_terms_after_signing_wants_a_new_signature(self):
        self.require()
        self.send()
        self.enter()
        self.officer.post(f"/api/loans/{self.loan['id']}/collateral", {
            "type": "vehicle", "description": "Honda Fit", "estimated_value": 3000,
        }, format="json")
        state = self.officer.get(self.base).json()
        self.assertFalse(state["signed"])
        self.assertTrue(state["stale"])
        self.assertEqual(self.disburse().status_code, 400)

    def test_terms_changed_while_a_code_is_out_refuse_the_code(self):
        self.send()
        self.officer.post(f"/api/loans/{self.loan['id']}/collateral", {
            "type": "vehicle", "description": "Honda Fit", "estimated_value": 3000,
        }, format="json")
        response = self.enter()
        self.assertEqual(response.status_code, 400)
        self.assertIn("terms changed", response.json()["detail"])

    def test_not_required_disbursement_goes_ahead_unsigned(self):
        self.assertEqual(self.disburse().status_code, 200)
