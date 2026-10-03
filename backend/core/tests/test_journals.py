"""Manual journals: operating expenses, other income, assets and opening balances.

Before these existed the ledger knew only what the loan book, savings, funding and
capital told it, so the income statement reported lending income as if it were
profit. The rules asserted here are the ones that keep a hand-written entry from
damaging the books it is added to: four eyes, no control accounts, cash guarded,
closed months refused, reversal rather than deletion.
"""
from datetime import date
from decimal import Decimal

from core.models import JournalEntry, ManualJournal, Role, User
from core.services import funding as funding_svc
from core.services import ledger as gl
from core.services import periods as periods_svc

from .test_components import LedgerBase, dec


class JournalBase(LedgerBase):
    def setUp(self):
        super().setUp()
        funding_svc.inject_capital(None, dec("10000"), "Shareholders", date(2026, 1, 5))

    def account_id(self, code):
        return next(a["id"] for a in self.admin.get("/api/ledger/accounts").json()
                    if a["code"] == code)

    def expense(self, client=None, amount="1200.00", account="6000", entry_date="2026-03-31",
                **extra):
        payload = {
            "entry_date": entry_date, "narration": "March salaries", "reference": "PAY-0326",
            "lines": [
                {"account_code": account, "debit": amount, "description": "Salaries"},
                {"account_code": "1000", "credit": amount, "description": "Paid from the bank"},
            ],
        }
        payload.update(extra)
        return (client or self.teller).post("/api/journals", payload, format="json")

    def prepared(self, **kwargs):
        response = self.expense(**kwargs)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()


class PreparingAndPostingTests(JournalBase):
    def test_a_teller_prepares_and_nothing_reaches_the_ledger_until_it_is_posted(self):
        journal = self.prepared()
        self.assertEqual(journal["status"], "draft")
        self.assertTrue(journal["journal_no"].startswith("MJ-"))
        self.assertIsNone(journal["entry_no"])
        self.assertFalse(JournalEntry.objects.filter(source="manual_journal").exists())

    def test_posting_debits_the_expense_and_credits_the_bank(self):
        journal = self.prepared()
        response = self.admin.post(f"/api/journals/{journal['id']}/post")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "posted")

        entry = JournalEntry.objects.get(source="manual_journal")
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["6000"].debit, dec("1200.00"))
        self.assertEqual(lines["1000"].credit, dec("1200.00"))
        self.assertEqual(funding_svc.cash_balance(), dec("8800.00"))

    def test_the_income_statement_finally_shows_what_it_cost_to_run_the_place(self):
        journal = self.prepared()
        self.admin.post(f"/api/journals/{journal['id']}/post")
        statement = gl.income_statement(date(2026, 3, 1), date(2026, 3, 31))
        staff = next(r for r in statement["expense"] if r["code"] == "6000")
        self.assertEqual(staff["balance"], dec("1200.00"))
        self.assertEqual(statement["surplus"], dec("-1200.00"))

    def test_only_an_administrator_posts(self):
        journal = self.prepared()
        self.assertEqual(self.teller.post(f"/api/journals/{journal['id']}/post").status_code, 403)
        self.assertEqual(self.officer.post(f"/api/journals/{journal['id']}/post").status_code,
                         403)

    def test_a_viewer_can_read_but_not_prepare(self):
        User.objects.create_user("viewer", "viewer123", full_name="Viewer", role=Role.VIEWER)
        viewer = self.client_for("viewer", "viewer123")
        self.assertEqual(self.expense(client=viewer).status_code, 403)
        self.assertEqual(viewer.get("/api/journals").status_code, 200)

    def test_a_posted_journal_cannot_be_posted_twice(self):
        journal = self.prepared()
        self.admin.post(f"/api/journals/{journal['id']}/post")
        response = self.admin.post(f"/api/journals/{journal['id']}/post")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(JournalEntry.objects.filter(source="manual_journal").count(), 1)

    def test_a_rejected_journal_never_posts(self):
        journal = self.prepared()
        response = self.admin.post(f"/api/journals/{journal['id']}/reject",
                                   {"reason": "No supporting payslips"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["rejected_reason"], "No supporting payslips")
        self.assertEqual(self.admin.post(f"/api/journals/{journal['id']}/post").status_code, 400)


class LineRuleTests(JournalBase):
    def test_an_unbalanced_journal_is_refused_with_the_difference(self):
        response = self.teller.post("/api/journals", {
            "entry_date": "2026-03-31", "narration": "Rent", "lines": [
                {"account_code": "6100", "debit": "500"},
                {"account_code": "1000", "credit": "450"},
            ]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("out by 50.00", response.json()["detail"])

    def test_a_single_line_is_not_a_journal(self):
        response = self.teller.post("/api/journals", {
            "narration": "Rent", "lines": [{"account_code": "6100", "debit": "500"}]},
            format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least two lines", response.json()["detail"])

    def test_a_line_with_both_sides_is_refused(self):
        response = self.teller.post("/api/journals", {
            "narration": "Rent", "lines": [
                {"account_code": "6100", "debit": "500", "credit": "500"},
                {"account_code": "1000", "credit": "0"},
            ]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not both", response.json()["detail"])

    def test_a_control_account_is_refused_and_the_right_place_is_named(self):
        # A journal to loans receivable would open a reconciliation break that
        # nothing could explain.
        response = self.expense(account="1100")
        self.assertEqual(response.status_code, 400)
        self.assertIn("the loan itself", response.json()["detail"])

        response = self.expense(account="2000")
        self.assertEqual(response.status_code, 400)
        self.assertIn("savings account", response.json()["detail"])

    def test_every_reconciled_account_is_a_control_account(self):
        # If reconciliation learns a new identity and CONTROL_ACCOUNTS does not, a
        # journal could quietly break it. This keeps the two lists one list.
        reconciled = {row["code"] for row in gl.reconciliation()["rows"]}
        self.assertEqual(reconciled, set(gl.CONTROL_ACCOUNTS))

    def test_journals_cannot_break_the_reconciliation(self):
        journal = self.prepared()
        self.admin.post(f"/api/journals/{journal['id']}/post")
        self.assertTrue(gl.reconciliation()["agrees"])
        self.assertTrue(gl.trial_balance()["balanced"])


class SafeguardTests(JournalBase):
    def test_paying_out_more_than_the_bank_holds_is_refused_at_posting(self):
        journal = self.prepared(amount="15000.00")
        response = self.admin.post(f"/api/journals/{journal['id']}/post")
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds the 10000.00 in the bank", response.json()["detail"])

    def test_money_coming_in_needs_no_cash(self):
        response = self.teller.post("/api/journals", {
            "entry_date": "2026-03-31", "narration": "Sale of an old generator", "lines": [
                {"account_code": "1000", "debit": "300"},
                {"account_code": "4900", "credit": "300"},
            ]}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        response = self.admin.post(f"/api/journals/{response.json()['id']}/post")
        self.assertEqual(response.status_code, 200, response.content)

    def test_a_journal_dated_in_a_closed_month_is_refused_when_prepared(self):
        periods_svc.close_period(2026, 2, None)
        response = self.expense(entry_date="2026-02-27")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("2026-03-01", response.json()["detail"])

    def test_closing_a_month_warns_about_journals_still_awaiting_approval(self):
        self.prepared(entry_date="2026-03-31")
        checks = periods_svc.preflight(2026, 3)["checks"]
        waiting = next(c for c in checks if c["key"] == "no_journals_awaiting_approval")
        self.assertFalse(waiting["passed"])
        self.assertFalse(waiting["blocking"])


class ReversalAndWithdrawalTests(JournalBase):
    def test_a_reversal_mirrors_the_journal_and_the_pair_nets_to_nothing(self):
        journal = self.prepared()
        self.admin.post(f"/api/journals/{journal['id']}/post")
        response = self.admin.post(f"/api/journals/{journal['id']}/reverse",
                                   {"reason": "Paid twice in error"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["status"], "reversed")
        self.assertIsNotNone(body["reversal_entry_no"])
        self.assertEqual(funding_svc.cash_balance(), dec("10000.00"))
        balances = {r["code"]: r for r in gl.trial_balance()["rows"]}
        self.assertEqual(balances["6000"]["balance"], dec("0.00"))

    def test_a_posted_journal_cannot_be_withdrawn(self):
        journal = self.prepared()
        self.admin.post(f"/api/journals/{journal['id']}/post")
        response = self.admin.delete(f"/api/journals/{journal['id']}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Reverse a posted journal", response.json()["detail"])

    def test_the_preparer_can_withdraw_a_draft_and_someone_else_cannot(self):
        journal = self.prepared()
        self.assertEqual(self.officer.delete(f"/api/journals/{journal['id']}").status_code, 400)
        self.assertEqual(self.teller.delete(f"/api/journals/{journal['id']}").status_code, 204)
        self.assertFalse(ManualJournal.objects.filter(pk=journal["id"]).exists())

    def test_rebuild_reposts_journals_after_the_journal_is_wiped(self):
        journal = self.prepared()
        self.admin.post(f"/api/journals/{journal['id']}/post")
        self.admin.post(f"/api/journals/{journal['id']}/reverse",
                        {"reason": "Wrong month"}, format="json")
        JournalEntry.objects.all().delete()

        result = gl.backfill()
        self.assertEqual(result["manual_journals_reposted"], 2)
        self.assertEqual(funding_svc.cash_balance(), dec("10000.00"))
        self.assertTrue(gl.trial_balance()["balanced"])


class ListingTests(JournalBase):
    def test_the_list_filters_by_status_and_exports(self):
        first = self.prepared()
        self.prepared(narration="Office rent", account="6100", amount="400.00")
        self.admin.post(f"/api/journals/{first['id']}/post")

        drafts = self.admin.get("/api/journals?status=draft").json()
        self.assertEqual(drafts["count"], 1)
        self.assertEqual(drafts["awaiting_approval"], 1)
        self.assertEqual(drafts["results"][0]["total"], "400.00")

        response = self.admin.get("/api/journals?fmt=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("6100", response.content.decode())
