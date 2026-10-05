"""Credit-life claims.

The rules that matter: while a claim is open the loan is neither penalised nor
chased; a payout is an ordinary repayment, so the ledger stays tied; what the
payout leaves can be written off in the same step; a rejected claim puts the loan
back as it was; and one balance is never claimed twice.
"""
from datetime import date, timedelta
from decimal import Decimal

from core.models import InsuranceClaim, Loan, Notification, Transaction, TxnType
from core.services import ledger as gl
from core.services.notifications import generate_reminders
from core.services.penalties import accrue_penalties

from .test_components import LedgerBase, dec


class ClaimTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        # Disbursed in March, nothing paid: well in arrears by now.
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def lodge(self, client=None, **overrides):
        payload = {"loan_no": self.loan["loan_no"], "cause": "death",
                   "event_date": (date.today() - timedelta(days=3)).isoformat()}
        payload.update(overrides)
        return (client or self.officer).post("/api/claims", payload, format="json")

    def outstanding(self):
        return dec(self.officer.get(f"/api/loans/{self.loan['id']}").json()["total_outstanding"])

    def test_an_officer_lodges_a_claim_for_the_balance(self):
        owed = self.outstanding()
        response = self.lodge()
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "lodged")
        self.assertEqual(dec(body["amount_claimed"]), owed)
        self.assertTrue(body["claim_no"].startswith("CLM-"))
        self.assertEqual(self.teller.post("/api/claims", {}, format="json").status_code, 403)

    def test_one_balance_is_never_claimed_twice(self):
        self.lodge()
        again = self.lodge()
        self.assertEqual(again.status_code, 400)
        self.assertIn("already has a claim open", again.json()["detail"])

    def test_an_event_in_the_future_or_before_the_loan_is_refused(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.assertEqual(self.lodge(event_date=tomorrow).status_code, 400)
        self.assertEqual(self.lodge(event_date="2026-01-01").status_code, 400)

    def test_an_open_claim_stops_penalties_and_reminders(self):
        self.lodge()
        accrue_penalties(date.today())
        self.assertFalse(Transaction.objects.filter(
            loan_id=self.loan["id"], txn_type=TxnType.PENALTY).exists())
        # The single-loan path refuses too.
        accrue_penalties(date.today(), Loan.objects.get(pk=self.loan["id"]))
        self.assertFalse(Transaction.objects.filter(
            loan_id=self.loan["id"], txn_type=TxnType.PENALTY).exists())
        generate_reminders(date.today())
        self.assertFalse(Notification.objects.filter(loan_id=self.loan["id"]).exists())

    def test_a_full_payout_settles_the_loan_and_the_ledger_ties(self):
        claim = self.lodge().json()
        response = self.admin.post(f"/api/claims/{claim['id']}/pay", {
            "amount": claim["amount_claimed"], "reference": "INS-991"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["insurer_reference"]), ("paid", "INS-991"))
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).status, "closed")
        txn = Transaction.objects.get(pk=body["transaction"])
        self.assertEqual((txn.txn_type, txn.method), (TxnType.REPAYMENT, "bank_transfer"))
        rec = {row["code"]: row for row in gl.reconciliation()["rows"]}
        self.assertTrue(rec["1100"]["agrees"], rec["1100"])
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_a_partial_payout_can_write_off_what_it_leaves(self):
        claim = self.lodge().json()
        owed = dec(claim["amount_claimed"])
        response = self.admin.post(f"/api/claims/{claim['id']}/pay", {
            "amount": "300", "write_off_remainder": True}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(dec(response.json()["remainder_written_off"]), owed - Decimal("300"))
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).status, "written_off")
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_a_partial_payout_can_leave_the_rest_on_the_loan(self):
        claim = self.lodge().json()
        owed = dec(claim["amount_claimed"])
        self.admin.post(f"/api/claims/{claim['id']}/pay", {"amount": "300"}, format="json")
        self.assertEqual(Loan.objects.get(pk=self.loan["id"]).status, "active")
        self.assertEqual(self.outstanding(), owed - Decimal("300"))

    def test_a_payout_above_the_balance_is_refused(self):
        claim = self.lodge().json()
        response = self.admin.post(f"/api/claims/{claim['id']}/pay", {"amount": "999999"},
                                   format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(InsuranceClaim.objects.get(pk=claim["id"]).status, "lodged")

    def test_a_rejected_claim_puts_the_loan_back_to_normal(self):
        claim = self.lodge().json()
        url = f"/api/claims/{claim['id']}/reject"
        self.assertEqual(self.officer.post(url, {"note": "x"}, format="json").status_code, 403)
        self.assertEqual(self.admin.post(url, {"note": ""}, format="json").status_code, 400)
        response = self.admin.post(url, {"note": "Pre-existing condition excluded"},
                                   format="json")
        self.assertEqual(response.json()["status"], "rejected")
        accrue_penalties(date.today())
        self.assertTrue(Transaction.objects.filter(
            loan_id=self.loan["id"], txn_type=TxnType.PENALTY).exists())
        # A decided claim cannot be decided again.
        self.assertEqual(self.admin.post(f"/api/claims/{claim['id']}/pay", {"amount": "10"},
                                         format="json").status_code, 400)
        # And the loan can be claimed on afresh.
        self.assertEqual(self.lodge().status_code, 201)

    def test_the_list_counts_open_claims(self):
        self.lodge()
        listed = self.officer.get("/api/claims?status=lodged").json()
        self.assertEqual(listed["open"], 1)
        self.assertEqual(listed["results"][0]["loan_no"], self.loan["loan_no"])
