"""Bringing a running loan book over from another system.

A lender going live has loans already out: disbursed months ago, partly repaid,
some behind. The rules asserted here are the ones that make a migrated book
indistinguishable from one that had always been here - arrears fall out of the
schedule, the ledger agrees with the book on day one, no cash moves - and the two
that protect it afterwards: penalties are not charged twice for the old system's
months, and a file run twice does not import anything twice.
"""
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Borrower, Loan, Transaction, TxnType
from core.services import funding as funding_svc
from core.services import ledger as gl
from core.services import loans as loan_svc
from core.services import periods as periods_svc
from core.services.amortisation import build_schedule
from core.services.penalties import accrue_penalties

from .test_components import LedgerBase, dec

HEADER = ("national_id,first_name,last_name,phone,employer,net_salary,payday,branch_code,"
          "product_code,principal,term,disbursement_date,first_instalment_date,amount_paid,"
          "penalties_outstanding,external_ref")
CUTOVER = "2026-06-30"


def row(national_id="63-555555E63", amount_paid="472.84", penalties="0", ref="OLD-1",
        first="Chipo", **overrides):
    values = {
        "national_id": national_id, "first_name": first, "last_name": "Dube",
        "phone": "0772000000", "employer": "ZESA Holdings", "net_salary": "900", "payday": "25",
        "branch_code": "HQ", "product_code": "T-SAL", "principal": "1200", "term": "6",
        "disbursement_date": "2026-01-10", "first_instalment_date": "2026-01-25",
        "amount_paid": amount_paid, "penalties_outstanding": penalties, "external_ref": ref,
    }
    values.update(overrides)
    return ",".join(values[h] for h in HEADER.split(","))


class LoanBookBase(LedgerBase):
    def setUp(self):
        super().setUp()
        self.make_product()  # T-SAL: 5% a month, reducing, monthly

    def upload(self, *rows, commit=False, client=None, cutover=CUTOVER):
        content = "\r\n".join([HEADER, *rows]).encode()
        payload = {"file": SimpleUploadedFile("book.csv", content, content_type="text/csv"),
                   "commit": "true" if commit else "false", "cutover_date": cutover}
        return (client or self.admin).post("/api/imports/loan-book", payload, format="multipart")

    def imported(self, *rows):
        response = self.upload(*rows, commit=True)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()


class CheckingTests(LoanBookBase):
    def test_a_dry_run_reports_what_would_come_over_and_writes_nothing(self):
        response = self.upload(row())
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["valid_rows"], 1)
        self.assertEqual(body["new_borrowers"], 1)
        line = body["rows"][0]
        self.assertTrue(line["ok"], line)
        expected = build_schedule(dec(1200), dec(5), 6, date(2026, 1, 25))[1].closing_balance
        self.assertEqual(dec(line["principal_outstanding"]), expected)
        self.assertFalse(Loan.objects.exists())
        self.assertFalse(Borrower.objects.filter(national_id="63-555555E63").exists())

    def test_only_an_administrator_may_migrate(self):
        self.assertEqual(self.upload(row(), client=self.officer).status_code, 403)

    def test_a_new_borrower_needs_a_name_and_a_phone(self):
        body = self.upload(row(first="")).json()
        self.assertIn("first_name", body["rows"][0]["error"])

    def test_a_fully_repaid_loan_is_not_brought_over(self):
        body = self.upload(row(amount_paid="2000")).json()
        self.assertIn("nothing outstanding", body["rows"][0]["error"])

    def test_a_borrower_with_an_open_loan_here_is_refused(self):
        borrower = self.make_borrower(national_id="63-555555E63")
        product = self.make_product(code="T-TWO", name="Another")
        self.officer.post("/api/loans", {"borrower_id": borrower["id"],
                                         "product_id": product["id"], "principal": 500,
                                         "term_months": 6}, format="json")
        body = self.upload(row()).json()
        self.assertIn("already has an open loan", body["rows"][0]["error"])

    def test_one_bad_row_stops_the_whole_file(self):
        response = self.upload(row(), row(national_id="63-666666F63", ref="OLD-2",
                                          product_code="NOPE"), commit=True)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("all or nothing", response.json()["detail"])
        self.assertFalse(Loan.objects.exists())

    def test_a_closed_cutover_date_is_refused(self):
        periods_svc.close_period(2026, 6, None, force=True)
        response = self.upload(row())
        self.assertEqual(response.status_code, 409, response.content)


class ImportedBookTests(LoanBookBase):
    def setUp(self):
        super().setUp()
        self.result = self.imported(row(penalties="15.00"))
        self.loan = Loan.objects.get(external_ref="OLD-1")

    def test_the_loan_arrives_active_with_its_history_laid_over_the_schedule(self):
        self.assertEqual(self.loan.status, "active")
        self.assertEqual(self.loan.disbursement_date, date(2026, 1, 10))
        rows = list(self.loan.instalments.order_by("number"))
        self.assertEqual([r.status for r in rows[:2]], ["paid", "paid"])
        self.assertTrue(all(r.principal_paid == 0 for r in rows[2:]))
        self.assertEqual(self.loan.penalties_outstanding, dec("15.00"))

    def test_arrears_fall_out_of_the_schedule_like_any_other_loan(self):
        loan = Loan.objects.prefetch_related("instalments").get(pk=self.loan.pk)
        amount, days = loan_svc.arrears(loan, date(2026, 6, 30))
        # March to June missed: four instalments, the oldest due 25 March.
        self.assertEqual(days, (date(2026, 6, 30) - date(2026, 3, 25)).days)
        self.assertGreater(amount, dec("900"))

    def test_the_ledger_agrees_with_the_book_and_no_cash_moved(self):
        rec = {r["code"]: r for r in gl.reconciliation()["rows"]}
        self.assertTrue(rec["1100"]["agrees"], rec["1100"])
        self.assertTrue(rec["1300"]["agrees"], rec["1300"])
        self.assertEqual(funding_svc.cash_balance(), dec("0.00"))
        balances = {r["code"]: r for r in gl.trial_balance()["rows"]}
        self.assertEqual(balances["3900"]["balance"],
                         self.loan.principal_outstanding + self.loan.penalties_outstanding)
        opening = Transaction.objects.get(loan=self.loan, txn_type=TxnType.OPENING_BALANCE)
        self.assertEqual(opening.txn_date, date(2026, 6, 30))

    def test_penalties_are_charged_only_from_the_cutover_not_for_the_old_months(self):
        loan = Loan.objects.prefetch_related("instalments").get(pk=self.loan.pk)
        unpaid = sum((i.principal_due - i.principal_paid + i.interest_due - i.interest_paid
                      for i in loan.instalments.all() if i.due_date < date(2026, 6, 30)),
                     Decimal("0"))
        # Ten days later, with no instalment falling due in between: ten days of
        # penalty at 0.5% a day, not the months since March.
        result = accrue_penalties(date(2026, 7, 10), loan)
        self.assertEqual(result["total_penalties"], (unpaid * dec("0.005") * 10).quantize(dec("0.01")))

    def test_running_the_same_file_again_imports_nothing(self):
        body = self.upload(row(penalties="15.00")).json()
        self.assertIn("already been imported", body["rows"][0]["error"])
        self.assertEqual(Loan.objects.filter(external_ref="OLD-1").count(), 1)

    def test_a_payroll_return_quoting_the_old_number_finds_the_loan(self):
        content = b"loan_no,amount,date\r\nOLD-1,100.00,2026-07-25\r\n"
        response = self.teller.post("/api/imports/repayments", {
            "file": SimpleUploadedFile("pay.csv", content, content_type="text/csv"),
            "commit": "true"}, format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["postings"][0]["loan_no"], self.loan.loan_no)

    def test_the_loan_can_be_found_by_its_old_number(self):
        results = self.officer.get("/api/loans?q=OLD-1").json()["results"]
        self.assertEqual([r["loan_no"] for r in results], [self.loan.loan_no])
