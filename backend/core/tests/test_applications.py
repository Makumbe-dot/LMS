"""Online loan applications: the public form, the portal, and staff accepting or declining."""
from rest_framework.test import APIClient

from core.models import ApplicationStatus, Borrower, OnlineApplication

from .test_portal import PortalTests


class OnlineApplicationTests(PortalTests):
    def application(self, **overrides):
        body = {"first_name": "Rufaro", "last_name": "Dube", "national_id": "63-777777R63",
                "phone": "0772 000 111", "email": "rufaro@example.org", "employer": "Delta",
                "net_salary": "900", "payday": "25", "product_id": self.product["id"],
                "amount": "500", "term_months": "6", "purpose": "School fees", "consent": True}
        body.update(overrides)
        return body

    def apply(self, **overrides):
        return APIClient().post("/api/public/apply", self.application(**overrides), format="json")

    def test_anyone_can_see_the_products_and_apply(self):
        products = APIClient().get("/api/public/products").json()
        self.assertIn(self.product["id"], [p["id"] for p in products])
        self.assertNotIn("interest_rate_pct", products[0])

        response = self.apply()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()["reference"].startswith("APP-"))
        made = OnlineApplication.objects.get()
        self.assertEqual((made.status, made.source, made.borrower_id), (ApplicationStatus.NEW, "public", None))

    def test_the_amount_and_term_must_fit_the_product(self):
        self.assertEqual(self.apply(amount="999999").status_code, 400)
        self.assertEqual(self.apply(term_months="99").status_code, 400)

    def test_consent_is_required_and_robots_are_turned_away(self):
        self.assertEqual(self.apply(consent=False).status_code, 400)
        self.assertEqual(self.apply(website="http://spam").status_code, 400)
        self.assertFalse(OnlineApplication.objects.exists())

    def test_one_person_cannot_flood_the_queue(self):
        for _ in range(2):
            self.assertEqual(self.apply().status_code, 201)
        self.assertEqual(self.apply().status_code, 400)

    def test_accepting_creates_the_borrower_and_hands_over_the_loan_figures(self):
        self.apply()
        made = OnlineApplication.objects.get()
        response = self.officer.post(f"/api/online-applications/{made.id}/accept")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        borrower = Borrower.objects.get(pk=body["borrower_id"])
        self.assertEqual((borrower.national_id, borrower.phone), ("63-777777R63", "0772 000 111"))
        self.assertTrue(body["created_borrower"])
        self.assertEqual((body["product_id"], body["term_months"]), (self.product["id"], 6))
        made.refresh_from_db()
        self.assertEqual(made.status, ApplicationStatus.ACCEPTED)
        # Dealt with once only.
        self.assertEqual(self.officer.post(f"/api/online-applications/{made.id}/accept").status_code, 400)

    def test_an_existing_borrower_is_matched_by_national_id_not_duplicated(self):
        self.apply(national_id=self.borrower.national_id)
        made = OnlineApplication.objects.get()
        body = self.officer.post(f"/api/online-applications/{made.id}/accept").json()
        self.assertEqual(body["borrower_id"], self.borrower.id)
        self.assertFalse(body["created_borrower"])

    def test_declining_needs_a_reason(self):
        self.apply()
        made = OnlineApplication.objects.get()
        self.assertEqual(self.officer.post(f"/api/online-applications/{made.id}/decline").status_code, 400)
        response = self.officer.post(f"/api/online-applications/{made.id}/decline",
                                     {"reason": "Salary too low for the amount"}, format="json")
        self.assertEqual(response.json()["status"], "declined")

    def test_a_portal_borrower_applies_with_their_details_already_known(self):
        portal = self.sign_in()
        response = portal.post("/api/portal/applications",
                               {"product_id": self.product["id"], "amount": "400", "term_months": 6},
                               format="json")
        self.assertEqual(response.status_code, 201, response.content)
        made = OnlineApplication.objects.get()
        self.assertEqual((made.borrower_id, made.source, made.national_id),
                         (self.borrower.id, "portal", self.borrower.national_id))
        self.assertEqual(len(portal.get("/api/portal/applications").json()), 1)

    def test_staff_see_new_applications_and_nobody_else_does(self):
        self.apply()
        self.assertEqual(len(self.officer.get("/api/online-applications").json()), 1)
        self.assertEqual(APIClient().get("/api/online-applications").status_code, 401)
