"""The loan lifecycle end to end, through the HTTP API.

Covers roles, affordability, maker-checker approval, disbursement, the repayment
waterfall, penalty accrual and its idempotency, waivers, settlement, reversal,
reschedule, write-off, the reports and the audit trail.
"""
from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from core.models import Role, User


def as_decimal(value) -> Decimal:
    return Decimal(str(value))


class LoanLifecycleTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user("admin", "admin123", full_name="Admin", role=Role.ADMIN)
        User.objects.create_user("officer", "officer123", full_name="Officer",
                                 role=Role.LOAN_OFFICER)
        User.objects.create_user("teller", "teller123", full_name="Teller", role=Role.TELLER)

    def client_for(self, username: str, password: str) -> APIClient:
        client = APIClient()
        response = client.post("/api/auth/login",
                               {"username": username, "password": password}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access_token"])
        return client

    def setUp(self):
        self.admin = self.client_for("admin", "admin123")
        self.officer = self.client_for("officer", "officer123")
        self.teller = self.client_for("teller", "teller123")
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def make_product(self) -> dict:
        response = self.admin.post("/api/products", {
            "code": "T-SAL", "name": "Test Salary", "interest_rate_pct": 5, "min_amount": 100,
            "max_amount": 5000, "min_term_months": 1, "max_term_months": 12, "admin_fee_pct": 3,
            "insurance_fee_pct": 1, "penalty_rate_pct_per_day": 0.5, "grace_days": 3,
            "max_instalment_to_salary_pct": 40,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def make_borrower(self) -> dict:
        response = self.officer.post("/api/borrowers", {
            "first_name": "Test", "last_name": "Borrower", "national_id": "63-123456A63",
            "phone": "0771234567", "net_salary": 1500, "payday": 25, "kyc_verified": True,
            "employer": "Test Employer",
            "guarantors": [{"full_name": "G One", "national_id": "63-999999Z63",
                            "phone": "0779999999"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertTrue(body["borrower_no"].startswith("BRW-"))
        self.assertEqual(len(body["guarantors"]), 1)
        return body

    # ------------------------------------------------------------------ roles
    def test_teller_cannot_create_borrower(self):
        response = self.teller.post("/api/borrowers", {
            "first_name": "x", "last_name": "y", "national_id": "1", "phone": "1",
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_reach_the_audit_log(self):
        User.objects.create_user("viewer", "viewer123", full_name="Viewer", role=Role.VIEWER)
        viewer = self.client_for("viewer", "viewer123")
        self.assertEqual(viewer.get("/api/reports/audit").status_code, 403)
        self.assertEqual(viewer.get("/api/reports/dashboard").status_code, 200)

    def test_anonymous_is_rejected(self):
        self.assertEqual(APIClient().get("/api/loans").status_code, 401)

    # ------------------------------------------------------------------ pricing
    def test_quote_affordability(self):
        response = self.officer.post("/api/loans/quote", {
            "product_id": self.product["id"], "principal": 1000, "term_months": 6,
            "borrower_id": self.borrower["id"],
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        quote = response.json()
        self.assertEqual(as_decimal(quote["instalment_amount"]), Decimal("197.02"))
        self.assertEqual(as_decimal(quote["net_disbursed"]), Decimal("960.00"))
        self.assertIs(quote["affordable"], True)
        self.assertEqual(len(quote["schedule"]), 6)

    def test_unaffordable_application_rejected(self):
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.product["id"],
            "principal": 5000, "term_months": 3,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("net salary", response.json()["detail"])

    def test_application_blocked_until_kyc_is_verified(self):
        response = self.officer.post("/api/borrowers", {
            "first_name": "No", "last_name": "Kyc", "national_id": "63-000001B63",
            "phone": "0770000001", "net_salary": 2000, "kyc_verified": False,
        }, format="json")
        self.assertEqual(response.status_code, 201)
        unverified = response.json()
        response = self.officer.post("/api/loans", {
            "borrower_id": unverified["id"], "product_id": self.product["id"],
            "principal": 1000, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("KYC", response.json()["detail"])

    # ------------------------------------------------------------------ lifecycle
    def test_full_lifecycle(self):
        product_id, borrower_id = self.product["id"], self.borrower["id"]

        # apply
        response = self.officer.post("/api/loans", {
            "borrower_id": borrower_id, "product_id": product_id, "principal": 1000,
            "term_months": 6, "purpose": "Test",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        loan = response.json()
        lid = loan["id"]
        self.assertEqual(loan["status"], "pending")
        self.assertTrue(loan["loan_no"].startswith("LN-"))

        # a second open loan is blocked
        response = self.officer.post("/api/loans", {
            "borrower_id": borrower_id, "product_id": product_id, "principal": 500,
            "term_months": 3,
        }, format="json")
        self.assertEqual(response.status_code, 400)

        # maker-checker: the originating officer cannot approve their own loan; admin can
        self.assertEqual(self.officer.post(f"/api/loans/{lid}/approve").status_code, 400)
        self.assertEqual(self.admin.post(f"/api/loans/{lid}/approve").json()["status"], "approved")

        # a teller cannot disburse
        self.assertEqual(
            self.teller.post(f"/api/loans/{lid}/disburse", {}, format="json").status_code, 403)

        response = self.officer.post(f"/api/loans/{lid}/disburse", {
            "disbursement_date": "2026-03-01", "reference": "TRF1",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        detail = response.json()
        self.assertEqual(detail["status"], "active")
        self.assertEqual(len(detail["schedule"]), 6)
        self.assertEqual(detail["schedule"][0]["due_date"], "2026-03-25")
        self.assertEqual(as_decimal(detail["schedule"][-1]["closing_balance"]), Decimal("0.00"))
        self.assertEqual(as_decimal(detail["principal_outstanding"]), Decimal("1000.00"))
        self.assertEqual(as_decimal(detail["total_outstanding"]),
                         Decimal("1000.00") + as_decimal(detail["total_interest"]))
        types = [t["txn_type"] for t in detail["transactions"]]
        self.assertIn("disbursement", types)
        self.assertIn("fee", types)

        # repayment waterfall: interest first, then principal, within instalment 1
        response = self.teller.post(f"/api/loans/{lid}/repayments", {
            "amount": 197.02, "txn_date": "2026-03-25", "method": "salary_deduction",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        txn = response.json()
        self.assertEqual(as_decimal(txn["interest_component"]), Decimal("50.00"))
        self.assertEqual(as_decimal(txn["principal_component"]), Decimal("147.02"))
        detail = self.teller.get(f"/api/loans/{lid}").json()
        self.assertEqual(detail["schedule"][0]["status"], "paid")
        self.assertEqual(detail["schedule"][0]["paid_date"], "2026-03-25")

        # penalties on the missed instalment 2 (due 2026-04-25, grace 3 days) at 2026-05-05 -> 7 days
        response = self.officer.post(f"/api/loans/{lid}/accrue-penalties?as_of=2026-05-05")
        self.assertEqual(response.status_code, 200)
        detail = self.teller.get(f"/api/loans/{lid}").json()
        ins2 = detail["schedule"][1]
        expected = ((as_decimal(ins2["principal_due"]) + as_decimal(ins2["interest_due"]))
                    * Decimal("0.005") * 7).quantize(Decimal("0.01"))
        self.assertEqual(as_decimal(ins2["penalty_due"]), expected)

        # idempotent: running it again for the same date charges nothing extra
        self.officer.post(f"/api/loans/{lid}/accrue-penalties?as_of=2026-05-05")
        again = self.teller.get(f"/api/loans/{lid}").json()["schedule"][1]
        self.assertEqual(as_decimal(again["penalty_due"]), expected)

        # arrears reported: instalment 2 is 10 days overdue at 2026-05-05
        par = self.admin.get("/api/reports/par?as_of=2026-05-05").json()
        self.assertTrue(any(p["loan_id"] == lid and p["days_in_arrears"] == 10 for p in par))

        # waiver is admin-only
        self.assertEqual(self.officer.post(f"/api/loans/{lid}/waive-penalties",
                                          {"amount": 1, "narration": "x"},
                                          format="json").status_code, 403)
        response = self.admin.post(f"/api/loans/{lid}/waive-penalties",
                                   {"amount": ins2["penalty_due"], "narration": "Goodwill"},
                                   format="json")
        self.assertEqual(response.status_code, 200, response.content)
        detail = self.teller.get(f"/api/loans/{lid}").json()
        self.assertEqual(as_decimal(detail["penalties_outstanding"]), Decimal("0"))

        # overpayment is blocked
        self.assertEqual(self.teller.post(f"/api/loans/{lid}/repayments", {"amount": 99999},
                                          format="json").status_code, 400)

        # settle in full -> closed
        response = self.teller.post(f"/api/loans/{lid}/repayments", {
            "amount": detail["total_outstanding"], "txn_date": "2026-05-10",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        detail = self.teller.get(f"/api/loans/{lid}").json()
        self.assertEqual(detail["status"], "closed")
        self.assertEqual(as_decimal(detail["total_outstanding"]), Decimal("0"))

        # reverse the settlement -> active again, balance restored
        settlement = [t for t in detail["transactions"] if t["txn_type"] == "repayment"][-1]
        response = self.officer.post(
            f"/api/loans/{lid}/transactions/{settlement['id']}/reverse",
            {"narration": "Posted to wrong account"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        detail = self.teller.get(f"/api/loans/{lid}").json()
        self.assertEqual(detail["status"], "active")
        self.assertGreater(as_decimal(detail["total_outstanding"]), 0)

        # statement and reports respond
        self.assertEqual(self.teller.get(f"/api/loans/{lid}/statement").status_code, 200)
        dashboard = self.teller.get("/api/reports/dashboard").json()
        self.assertGreaterEqual(dashboard["active_loans"], 1)
        csv = self.admin.get("/api/reports/loan-book?fmt=csv")
        self.assertTrue(csv["content-type"].startswith("text/csv"))

        # reschedule (admin) then write off
        response = self.admin.post(f"/api/loans/{lid}/reschedule", {"new_term_months": 12},
                                   format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["schedule"]), 12)

        response = self.admin.post(f"/api/loans/{lid}/write-off", {"narration": "Absconded"},
                                   format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "written_off")

        audit = self.admin.get("/api/reports/audit").json()["results"]
        self.assertTrue(any(a["action"] == "write_off" for a in audit))
        self.assertTrue(any(a["action"] == "disburse" for a in audit))
