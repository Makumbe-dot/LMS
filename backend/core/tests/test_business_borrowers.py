"""Borrowers who live off a business: their details, the cash-flow worksheet,
affordability against what the business leaves, and the scorecard."""
from datetime import date, timedelta
from decimal import Decimal

from rest_framework.test import APIClient

from core.models import Borrower, IncomeSource, OnlineApplication

from .fixtures import LoanFixtures

TRADER = {
    "income_source": "self_employed", "business_name": "Chari Hardware",
    "business_sector": "Retail", "business_registration_no": "CR-1234/2019",
    "trading_since": (date.today() - timedelta(days=365 * 4)).isoformat(),
    "business_address": "Stand 12, Mbare Musika",
    "monthly_sales": "3000", "monthly_cost_of_sales": "1500", "monthly_expenses": "400",
    "monthly_other_repayments": "100",
}


class BusinessBorrowerTests(LoanFixtures):
    def make_trader(self, **extra):
        return self.make_borrower(**{**TRADER, "employer": "", "net_salary": 0, **extra})

    def quote(self, borrower, principal=1000, term=6):
        response = self.officer.post("/api/loans/quote", {
            "product_id": self.product["id"], "borrower_id": borrower["id"],
            "principal": principal, "term_months": term}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def apply(self, borrower, principal=1000, term=6):
        return self.officer.post("/api/loans", {
            "borrower_id": borrower["id"], "product_id": self.product["id"],
            "principal": principal, "term_months": term}, format="json")

    def test_the_worksheet_works_out_net_monthly_income(self):
        trader = self.make_trader()
        # 3000 sales - 1500 stock - 400 costs - 100 to other lenders
        self.assertEqual(Decimal(trader["net_salary"]), Decimal("1000"))
        self.assertEqual(Decimal(trader["business_net_income"]), Decimal("1000"))
        self.assertEqual(trader["years_trading"], "4.0")

    def test_changing_the_figures_changes_the_income(self):
        trader = self.make_trader()
        response = self.officer.patch(f"/api/borrowers/{trader['id']}",
                                      {"monthly_sales": "4000"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Borrower.objects.get(pk=trader["id"]).net_salary, Decimal("2000"))

    def test_a_business_owner_must_name_the_business(self):
        response = self.officer.post("/api/borrowers", {
            "first_name": "No", "last_name": "Name", "national_id": "63-999999Z63",
            "phone": "0771999999", "income_source": "informal", "net_salary": 500},
            format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("business_name", response.content.decode())

    def test_a_business_owner_is_never_on_an_employers_payroll(self):
        trader = self.make_trader(employer="Delta Beverages", employee_no="E1")
        stored = Borrower.objects.get(pk=trader["id"])
        self.assertEqual((stored.employer, stored.employee_no), (None, None))

    def test_negative_figures_are_refused(self):
        response = self.officer.post("/api/borrowers", {
            "first_name": "Neg", "last_name": "Costs", "national_id": "63-888888N63",
            "phone": "0771888888", **TRADER, "monthly_expenses": "-5"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_affordability_uses_the_business_limit(self):
        self.admin.patch(f"/api/products/{self.product['id']}",
                         {"max_instalment_to_business_pct": 15}, format="json")
        trader = self.make_trader()
        # About 197 a month out of 1000: inside 40% but above the 15% business limit.
        quote = self.quote(trader)
        self.assertEqual(quote["income_basis"], "net business income")
        self.assertEqual(Decimal(quote["affordability_limit_pct"]), Decimal("15"))
        self.assertFalse(quote["affordable"])
        response = self.apply(trader)
        self.assertEqual(response.status_code, 400)
        self.assertIn("net business income", response.content.decode())

    def test_a_business_owner_who_can_afford_it_gets_the_loan(self):
        trader = self.make_trader()
        response = self.apply(trader)
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_loan_with_no_income_on_file_is_refused(self):
        nobody = self.make_borrower(net_salary=0)
        response = self.apply(nobody)
        self.assertEqual(response.status_code, 400)
        self.assertIn("net monthly income", response.content.decode())

    def test_the_scorecard_scores_the_business_not_employment(self):
        trader = self.make_trader()
        factors = {f["factor"]: f for f in self.quote(trader)["scorecard"]["factors"]}
        self.assertNotIn("Employment", factors)
        business = factors["Business"]
        # trading 4 years (6) + registered (3) + worksheet (3), no guarantor
        self.assertEqual(business["points"], 12)
        self.assertIn("Chari Hardware", business["reason"])
        self.assertIn("net business income", factors["Affordability"]["reason"])

    def test_employed_borrowers_are_scored_as_before(self):
        employed = self.make_borrower()
        factors = {f["factor"] for f in self.quote(employed)["scorecard"]["factors"]}
        self.assertIn("Employment", factors)
        self.assertEqual(self.quote(employed)["income_basis"], "net salary")

    def test_the_statement_names_the_business(self):
        from core.models import Loan
        from core.services.statements import loan_statement

        loan = self.make_loan(self.make_trader())
        data = loan_statement(Loan.objects.get(pk=loan["id"]))
        self.assertEqual(data["business"], "Chari Hardware")
        pdf = self.officer.get(f"/api/loans/{loan['id']}/statement?fmt=pdf")
        self.assertEqual(pdf.status_code, 200)

    def test_an_online_applicant_can_say_they_run_a_business(self):
        body = {"first_name": "Ruvimbo", "last_name": "Chari", "national_id": "63-245781K42",
                "phone": "0772418903", "income_source": "self_employed",
                "business_name": "Chari Hardware", "net_salary": "1450", "payday": "25",
                "product_id": self.product["id"], "amount": "500", "term_months": "6",
                "consent": True}
        response = APIClient().post("/api/public/apply", body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        made = OnlineApplication.objects.get()
        self.assertEqual((made.income_source, made.business_name),
                         (IncomeSource.SELF_EMPLOYED, "Chari Hardware"))

        accepted = self.officer.post(f"/api/online-applications/{made.id}/accept")
        self.assertEqual(accepted.status_code, 200, accepted.content)
        borrower = Borrower.objects.get(national_id="63-245781K42")
        self.assertEqual((borrower.income_source, borrower.business_name, borrower.employer),
                         (IncomeSource.SELF_EMPLOYED, "Chari Hardware", None))

    def test_an_online_business_applicant_must_name_the_business(self):
        response = APIClient().post("/api/public/apply", {
            "first_name": "A", "last_name": "B", "national_id": "63-111111B63",
            "phone": "0772111111", "income_source": "farmer", "net_salary": "300",
            "product_id": self.product["id"], "amount": "500", "term_months": "6",
            "consent": True}, format="json")
        self.assertEqual(response.status_code, 400)
