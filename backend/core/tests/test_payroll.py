"""Payroll returns: the employer's file checked against the schedule, then posted."""
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import PayrollRun, Transaction, TxnType, User

from .fixtures import LoanFixtures


class PayrollReturnTests(LoanFixtures):
    def setUp(self):
        super().setUp()
        self.full = self.make_loan(self.make_borrower(employee_no="E1"))
        self.short = self.make_loan(self.make_borrower(employee_no="E2"))
        self.missed = self.make_loan(self.make_borrower(employee_no="E3"))
        self.short_id = self.admin.get(
            f"/api/borrowers/{self.short['borrower_id']}").json()["national_id"]

    def upload(self, text, client=None, **extra):
        payload = {"file": SimpleUploadedFile("return.csv", text.encode(), "text/csv"),
                   "employer": "Test Employer", "start": "2026-03-01", "end": "2026-03-31",
                   "received_on": "2026-03-28", **extra}
        return (client or self.teller).post("/api/payroll/runs", payload, format="multipart")

    def standard(self):
        return ("Employee No,National ID,Name,Amount\n"
                "E1,,Full,197.02\n"
                f",{self.short_id},Short,100.00\n"
                "E9,,Stranger,50.00\n")

    def lines(self, body):
        return {(line["loan_no"] or line["name"]): line for line in body["lines"]}

    def repayments(self):
        return Transaction.objects.filter(txn_type=TxnType.REPAYMENT)

    def test_checking_sets_each_loan_against_the_schedule_and_posts_nothing(self):
        response = self.upload(self.standard())
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        lines = self.lines(body)
        self.assertEqual(lines[self.full["loan_no"]]["status"], "full")
        self.assertEqual(lines[self.short["loan_no"]]["status"], "short")
        self.assertEqual(Decimal(lines[self.short["loan_no"]]["shortfall"]), Decimal("97.02"))
        self.assertEqual(lines[self.missed["loan_no"]]["status"], "missed")
        self.assertEqual(lines["Stranger"]["status"], "unknown")
        self.assertEqual(Decimal(body["expected_total"]), Decimal("591.06"))
        self.assertEqual(Decimal(body["deducted_total"]), Decimal("347.02"))
        self.assertEqual(Decimal(body["shortfall"]), Decimal("294.04"))
        self.assertEqual(body["status"], "draft")
        self.assertFalse(self.repayments().exists())

    def test_posting_makes_salary_deduction_repayments_on_the_day_the_money_came(self):
        run = self.upload(self.standard()).json()
        response = self.teller.post(f"/api/payroll/runs/{run['id']}/post")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "posted")
        self.assertEqual(Decimal(response.json()["posted_total"]), Decimal("297.02"))
        txns = self.repayments()
        self.assertEqual(txns.count(), 2)
        self.assertEqual({t.method for t in txns}, {"salary_deduction"})
        self.assertEqual({t.txn_date.isoformat() for t in txns}, {"2026-03-28"})

        detail = self.admin.get(f"/api/loans/{self.full['id']}").json()
        self.assertEqual(detail["schedule"][0]["status"], "paid")

        self.assertEqual(self.teller.post(f"/api/payroll/runs/{run['id']}/post").status_code,
                         400)
        self.assertEqual(self.teller.delete(f"/api/payroll/runs/{run['id']}").status_code, 400)
        self.assertEqual(self.repayments().count(), 2)

    def test_a_deduction_above_what_is_owed_posts_what_is_owed(self):
        text = f"Loan No,Amount\n{self.full['loan_no']},99999.00\n"
        run = self.upload(text).json()
        line = self.lines(run)[self.full["loan_no"]]
        self.assertEqual(line["status"], "over")
        self.assertIn("refund", line["note"])
        self.teller.post(f"/api/payroll/runs/{run['id']}/post")
        detail = self.admin.get(f"/api/loans/{self.full['id']}").json()
        self.assertEqual(detail["status"], "closed")

    def test_a_draft_can_be_discarded(self):
        run = self.upload(self.standard()).json()
        self.assertEqual(self.teller.delete(f"/api/payroll/runs/{run['id']}").status_code, 204)
        self.assertFalse(PayrollRun.objects.exists())

    def test_the_shortfall_list_downloads(self):
        run = self.upload(self.standard()).json()
        response = self.teller.get(f"/api/payroll/runs/{run['id']}?fmt=csv")
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn("Deducted short", text)
        self.assertIn("Not deducted", text)

    def test_a_file_without_an_amount_or_a_key_is_refused(self):
        self.assertEqual(self.upload("Employee No,Name\nE1,x\n").status_code, 400)
        self.assertEqual(self.upload("Name,Amount\nx,1\n").status_code, 400)

    def test_checking_needs_the_cash_right(self):
        User.objects.create_user("viewer", "viewer-pass1", full_name="Viewer")
        viewer = self.client_for("viewer", "viewer-pass1")
        self.assertEqual(self.upload(self.standard(), client=viewer).status_code, 403)
        self.assertEqual(viewer.get("/api/payroll/runs").status_code, 200)
