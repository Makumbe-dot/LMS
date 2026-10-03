"""Bank and mobile-money reconciliation.

None of what this catches breaks a trial balance - the books agree with
themselves - so the tests are about pairing the bank's record with the ledger's
honestly: exact amounts only, one entry per line, cash never on a bank
statement, and the ambiguous left for a person rather than guessed.
"""
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import JournalEntry, StatementLine, Transaction, TxnType
from core.services import funding as funding_svc

from .test_components import LedgerBase, dec

BANK_CSV = (
    "date,description,reference,amount\r\n"
    "2026-03-01,Transfer to borrower,TRF001,-960.00\r\n"
    "26/03/2026,Payroll credit ZESA,PAY-0325,197.02\r\n"
    "2026-03-28,Monthly service fee,,-2.50\r\n"
)


class BankRecBase(LedgerBase):
    def setUp(self):
        super().setUp()
        funding_svc.inject_capital(None, dec("20000"), "Shareholders", date(2026, 1, 5),
                                   "bank_transfer", "CAP-1")
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)  # bank transfer, 960 net

    def repay(self, amount, method, on, reference=None):
        response = self.teller.post(f"/api/loans/{self.loan['id']}/repayments", {
            "amount": amount, "method": method, "txn_date": on, "reference": reference},
            format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def upload(self, content=BANK_CSV, channel="bank_transfer", client=None, **extra):
        payload = {"file": SimpleUploadedFile("statement.csv", content.encode(),
                                              content_type="text/csv"),
                   "account_name": "CBZ current account", "channel": channel, **extra}
        return (client or self.admin).post("/api/bank-statements", payload, format="multipart")

    def lines(self, statement):
        return {line["line_no"]: line for line in statement["lines"]}


class MatchingTests(BankRecBase):
    def setUp(self):
        super().setUp()
        self.repay("197.02", "salary_deduction", "2026-03-25", "PAY-0325")
        self.repay("50.00", "mobile_money", "2026-03-26", "MP-7781")
        self.repay("30.00", "cash", "2026-03-27")

    def test_upload_matches_what_it_can_and_leaves_the_bank_charge(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.content)
        statement = response.json()
        lines = self.lines(statement)
        self.assertEqual(lines[2]["status"], "matched")  # the disbursement
        self.assertEqual(lines[3]["status"], "matched")  # payroll, a day late, DD/MM date
        self.assertTrue(lines[3]["auto_matched"])
        self.assertEqual(lines[4]["status"], "unmatched")  # nobody booked the fee
        self.assertEqual(statement["summary"]["unmatched"], 1)
        self.assertEqual(statement["summary"]["unmatched_amount"], "-2.50")
        self.assertFalse(statement["summary"]["reconciled"])

    def test_cash_and_mobile_money_never_appear_on_a_bank_statement(self):
        statement = self.upload().json()
        outstanding = self.admin.get(
            f"/api/bank-statements/{statement['id']}/outstanding").json()
        methods = {row["method"] for row in outstanding}
        self.assertNotIn("cash", methods)
        self.assertNotIn("mobile_money", methods)

    def test_a_mobile_money_statement_finds_the_wallet_payment(self):
        content = "date,description,reference,amount\r\n2026-03-26,Received,MP-7781,50.00\r\n"
        statement = self.upload(content, channel="mobile_money").json()
        self.assertEqual(self.lines(statement)[2]["status"], "matched")

    def test_a_match_must_be_exact(self):
        statement = self.upload().json()
        fee = self.lines(statement)[4]
        repayment = Transaction.objects.get(txn_type=TxnType.REPAYMENT,
                                            reference="PAY-0325").journal_entry
        # Already matched, so refused for that reason first...
        response = self.admin.post(f"/api/bank-statements/lines/{fee['id']}/match",
                                   {"entry_id": repayment.id}, format="json")
        self.assertEqual(response.status_code, 400)
        # ...and an unmatched entry of a different amount is refused for the amount.
        self.repay("120.00", "bank_transfer", "2026-03-28", "TRF-9")
        other = Transaction.objects.get(reference="TRF-9").journal_entry
        response = self.admin.post(f"/api/bank-statements/lines/{fee['id']}/match",
                                   {"entry_id": other.id}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be exact", response.json()["detail"])

    def test_an_entry_is_matched_to_one_line_only(self):
        self.upload()
        second = self.upload().json()
        self.assertEqual(second["summary"]["matched"], 0)

    def test_a_bank_charge_becomes_a_journal_and_then_a_match(self):
        statement = self.upload().json()
        fee = self.lines(statement)[4]
        response = self.admin.post(f"/api/bank-statements/lines/{fee['id']}/journal",
                                   {"account_code": "6600"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        journal = response.json()
        self.assertEqual(journal["status"], "draft")
        debit = next(l for l in journal["lines"] if l["account_code"] == "6600")
        self.assertEqual(dec(debit["debit"]), dec("2.50"))

        self.admin.post(f"/api/journals/{journal['id']}/post")
        statement = self.admin.post(f"/api/bank-statements/{statement['id']}/auto-match").json()
        self.assertTrue(statement["summary"]["reconciled"])

    def test_a_line_can_be_set_aside_with_a_reason_and_brought_back(self):
        statement = self.upload().json()
        fee = self.lines(statement)[4]
        response = self.admin.post(f"/api/bank-statements/lines/{fee['id']}/ignore",
                                   {"reason": "Refunded by the bank next day"}, format="json")
        self.assertEqual(self.lines(response.json())[4]["status"], "ignored")
        self.assertTrue(response.json()["summary"]["reconciled"])
        response = self.admin.post(f"/api/bank-statements/lines/{fee['id']}/unmatch")
        self.assertEqual(self.lines(response.json())[4]["status"], "unmatched")

    def test_only_an_administrator_reconciles(self):
        self.assertEqual(self.upload(client=self.officer).status_code, 403)


class AmbiguityTests(BankRecBase):
    def test_two_identical_amounts_are_told_apart_by_reference(self):
        self.repay("100.00", "bank_transfer", "2026-03-25", "EMP-A")
        self.repay("100.00", "bank_transfer", "2026-03-25", "EMP-B")
        content = ("date,description,reference,amount\r\n"
                   "2026-03-25,Credit from employer,EMP-B,100.00\r\n")
        statement = self.upload(content).json()
        line = self.lines(statement)[2]
        self.assertEqual(line["status"], "matched")
        entry = JournalEntry.objects.get(entry_no=line["entry_no"])
        self.assertEqual(entry.transaction.reference, "EMP-B")

    def test_two_identical_amounts_with_nothing_to_tell_them_apart_are_left_alone(self):
        self.repay("100.00", "bank_transfer", "2026-03-25")
        self.repay("100.00", "bank_transfer", "2026-03-25")
        content = "date,description,amount\r\n2026-03-25,Credit,100.00\r\n"
        statement = self.upload(content).json()
        line = self.lines(statement)[2]
        self.assertEqual(line["status"], "unmatched")
        candidates = self.admin.get(
            f"/api/bank-statements/lines/{line['id']}/candidates").json()
        self.assertEqual(len(candidates), 2)


class FileFormatTests(BankRecBase):
    def test_money_in_and_money_out_columns(self):
        content = ("Transaction Date,Details,Money In,Money Out\r\n"
                   "01/03/2026,Transfer to borrower,,960.00\r\n")
        statement = self.upload(content).json()
        line = self.lines(statement)[2]
        self.assertEqual(dec(line["amount"]), dec("-960.00"))
        self.assertEqual(line["status"], "matched")

    def test_a_statement_that_does_not_add_up_says_so(self):
        statement = self.upload(opening_balance="1000.00", closing_balance="500.00").json()
        self.assertFalse(statement["summary"]["adds_up"])
        statement = self.upload(opening_balance="1000.00", closing_balance="234.52").json()
        self.assertTrue(statement["summary"]["adds_up"])

    def test_an_unreadable_date_names_the_row(self):
        content = "date,amount\r\n2026-03-01,10\r\nyesterday,20\r\n"
        response = self.upload(content)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Row 3", response.json()["detail"])

    def test_deleting_a_statement_frees_its_entries(self):
        statement = self.upload().json()
        self.admin.delete(f"/api/bank-statements/{statement['id']}")
        self.assertFalse(StatementLine.objects.exists())
        again = self.upload().json()
        self.assertEqual(again["summary"]["matched"], 1)
