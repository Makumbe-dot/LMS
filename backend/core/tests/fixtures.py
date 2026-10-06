"""A disbursed loan in a few lines, for tests of what happens around one.

Builds through the HTTP API, the same way the lifecycle tests do, so a fixture
loan has a real schedule, real ledger postings and a real audit trail.
"""
from django.core.cache import cache
from rest_framework.test import APIClient, APITestCase

from core.models import RIGHT_PRESETS, Role, User
from core.services import ledger as gl


class LoanFixtures(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user("admin", "admin123", full_name="Admin", role=Role.ADMIN)
        User.objects.create_user("officer", "officer123", full_name="Officer",
                                 rights=RIGHT_PRESETS["loan_officer"])
        User.objects.create_user("teller", "teller123", full_name="Teller",
                                 rights=RIGHT_PRESETS["teller"])

    def client_for(self, username: str, password: str) -> APIClient:
        client = APIClient()
        response = client.post("/api/auth/login",
                               {"username": username, "password": password}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access_token"])
        return client

    def setUp(self):
        cache.clear()  # throttle counts would otherwise carry over between tests
        gl.ensure_chart_of_accounts()
        self.admin = self.client_for("admin", "admin123")
        self.officer = self.client_for("officer", "officer123")
        self.teller = self.client_for("teller", "teller123")
        self.product = self.make_product()
        self._borrowers = 0

    def make_product(self, **extra) -> dict:
        response = self.admin.post("/api/products", {
            "code": "T-SAL", "name": "Test Salary", "interest_rate_pct": 5, "min_amount": 100,
            "max_amount": 5000, "min_term_months": 1, "max_term_months": 12, "admin_fee_pct": 3,
            "insurance_fee_pct": 1, "penalty_rate_pct_per_day": 0.5, "grace_days": 3,
            "max_instalment_to_salary_pct": 40, **extra,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def make_borrower(self, **extra) -> dict:
        self._borrowers += 1
        n = self._borrowers
        response = self.officer.post("/api/borrowers", {
            "first_name": "Test", "last_name": f"Borrower{n}", "national_id": f"63-{n:06d}A63",
            "phone": f"0771{n:06d}", "net_salary": 1500, "payday": 25, "kyc_verified": True,
            "employer": "Test Employer", **extra,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def make_loan(self, borrower=None, principal=1000, term=6, disbursed="2026-03-01") -> dict:
        """A loan applied for by the officer, approved by the admin and, unless
        disbursed is None, paid out on that date. Returns the loan's detail."""
        borrower = borrower or self.make_borrower()
        response = self.officer.post("/api/loans", {
            "borrower_id": borrower["id"], "product_id": self.product["id"],
            "principal": principal, "term_months": term,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        loan_id = response.json()["id"]
        self.assertEqual(self.admin.post(f"/api/loans/{loan_id}/approve").status_code, 200)
        if disbursed is None:
            return self.admin.get(f"/api/loans/{loan_id}").json()
        response = self.officer.post(f"/api/loans/{loan_id}/disburse", {
            "disbursement_date": disbursed, "reference": f"TRF{loan_id}",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def repay(self, loan, amount, on, client=None):
        response = (client or self.teller).post(f"/api/loans/{loan['id']}/repayments", {
            "amount": amount, "txn_date": on, "method": "cash",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()
