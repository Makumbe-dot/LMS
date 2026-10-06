"""The borrower portal: sign-in by code, own loans only, and no way into staff APIs."""
from datetime import timedelta
from unittest import mock

from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Borrower,
    LoanSignature,
    Notification,
    OrganisationSetting,
    PortalCode,
    PortalRequest,
    PortalSession,
)

from .fixtures import LoanFixtures

CODE = "246810"


def fixed(module):
    return mock.patch(f"core.services.{module}.secrets.randbelow", return_value=int(CODE))


class PortalTests(LoanFixtures):
    def setUp(self):
        super().setUp()
        row = OrganisationSetting.load()
        row.portal_enabled = True
        row.save()
        self.loan = self.make_loan()
        self.borrower = Borrower.objects.get(pk=self.loan["borrower_id"])
        self.other = self.make_loan()

    def start(self, national_id=None, phone=None, client=None):
        client = client or APIClient()
        with fixed("portal"), self.captureOnCommitCallbacks(execute=True):
            return client.post("/api/portal/login", {
                "national_id": national_id or self.borrower.national_id,
                "phone": phone or "+263 " + self.borrower.phone[1:]}, format="json")

    def sign_in(self):
        challenge = self.start().json()["challenge"]
        response = APIClient().post("/api/portal/login/verify",
                                    {"challenge": challenge, "code": CODE}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Portal " + response.json()["token"])
        return client

    # ---- signing in
    def test_a_borrower_signs_in_with_id_phone_and_a_code(self):
        portal = self.sign_in()
        me = portal.get("/api/portal/me").json()
        self.assertEqual(me["borrower_no"], self.borrower.borrower_no)
        self.assertEqual([loan["loan_no"] for loan in me["loans"]], [self.loan["loan_no"]])

    def test_the_answer_is_the_same_when_the_details_do_not_match(self):
        right = self.start()
        wrong = self.start(national_id="00-000000X00")
        self.assertEqual(right.status_code, wrong.status_code)
        self.assertEqual(right.json().keys(), wrong.json().keys())
        self.assertEqual(right.json()["detail"], wrong.json()["detail"])
        self.assertEqual(Notification.objects.filter(kind="portal_code").count(), 1)
        response = APIClient().post("/api/portal/login/verify", {
            "challenge": wrong.json()["challenge"], "code": CODE}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_closed_portal_signs_nobody_in(self):
        row = OrganisationSetting.load()
        row.portal_enabled = False
        row.save()
        self.assertEqual(self.start().status_code, 400)

    def test_five_wrong_codes_end_the_code(self):
        challenge = self.start().json()["challenge"]
        for _ in range(5):
            APIClient().post("/api/portal/login/verify", {"challenge": challenge,
                                                          "code": "000000"}, format="json")
        self.assertEqual(PortalCode.objects.get().attempts, 5)
        response = APIClient().post("/api/portal/login/verify",
                                    {"challenge": challenge, "code": CODE}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_three_codes_an_hour_at_most(self):
        for _ in range(4):
            self.start()
        self.assertEqual(PortalCode.objects.count(), 3)

    def test_a_blacklisted_borrower_is_not_let_in(self):
        Borrower.objects.filter(pk=self.borrower.pk).update(is_blacklisted=True)
        self.start()
        self.assertFalse(PortalCode.objects.exists())

    # ---- sessions
    def test_signing_out_ends_the_session(self):
        portal = self.sign_in()
        self.assertEqual(portal.post("/api/portal/logout").status_code, 204)
        self.assertEqual(portal.get("/api/portal/me").status_code, 401)

    def test_an_idle_session_ends(self):
        portal = self.sign_in()
        PortalSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(portal.get("/api/portal/me").status_code, 401)

    def test_a_portal_token_opens_nothing_on_the_staff_side(self):
        portal = self.sign_in()
        token = portal._credentials["HTTP_AUTHORIZATION"].split(" ", 1)[1]
        staff_try = APIClient()
        staff_try.credentials(HTTP_AUTHORIZATION="Bearer " + token)
        self.assertEqual(staff_try.get("/api/loans").status_code, 401)
        self.assertEqual(portal.get("/api/loans").status_code, 401)

    def test_a_staff_token_opens_nothing_in_the_portal(self):
        self.assertEqual(self.admin.get("/api/portal/me").status_code, 401)

    # ---- what a borrower sees
    def test_only_their_own_loans(self):
        portal = self.sign_in()
        self.assertEqual(portal.get(f"/api/portal/loans/{self.loan['id']}").status_code, 200)
        self.assertEqual(portal.get(f"/api/portal/loans/{self.other['id']}").status_code, 404)
        self.assertEqual(
            portal.get(f"/api/portal/loans/{self.other['id']}/statement").status_code, 404)

    def test_the_schedule_payments_and_statement(self):
        self.repay(self.loan, "197.02", "2026-03-25")
        portal = self.sign_in()
        detail = portal.get(f"/api/portal/loans/{self.loan['id']}").json()
        self.assertEqual(len(detail["schedule"]), 6)
        self.assertEqual(detail["schedule"][0]["status"], "paid")
        self.assertEqual(detail["payments"][0]["amount"], "197.02")
        pdf = portal.get(f"/api/portal/loans/{self.loan['id']}/statement")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_signing_the_agreement_in_the_portal(self):
        pending = self.make_loan(disbursed=None)
        self.borrower = Borrower.objects.get(pk=pending["borrower_id"])
        portal = self.sign_in()
        with fixed("signatures"), self.captureOnCommitCallbacks(execute=True):
            response = portal.post(f"/api/portal/loans/{pending['id']}/signature/code")
        self.assertEqual(response.status_code, 201, response.content)
        response = portal.post(f"/api/portal/loans/{pending['id']}/signature/verify",
                               {"code": CODE}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(LoanSignature.objects.get().channel, "portal")

    def test_a_top_up_request_reaches_staff(self):
        portal = self.sign_in()
        response = portal.post("/api/portal/requests", {
            "kind": "top_up", "loan_id": self.loan["id"], "amount": "500",
            "message": "School fees"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        waiting = self.officer.get("/api/portal-requests?status=open").json()
        self.assertEqual(waiting[0]["borrower_name"], self.borrower.full_name)
        self.assertEqual(waiting[0]["amount"], "500.00")
        done = self.officer.post(f"/api/portal-requests/{waiting[0]['id']}/done",
                                 {"outcome": "Top-up applied for"}, format="json")
        self.assertEqual(done.json()["status"], "done")
        mine = portal.get("/api/portal/requests").json()
        self.assertEqual(mine[0]["outcome"], "Top-up applied for")

    def test_a_top_up_needs_an_amount_and_a_loan_of_theirs(self):
        portal = self.sign_in()
        self.assertEqual(portal.post("/api/portal/requests", {"kind": "top_up"},
                                     format="json").status_code, 400)
        response = portal.post("/api/portal/requests", {
            "kind": "top_up", "loan_id": self.other["id"], "amount": "500"}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PortalRequest.objects.exists())
