"""The dashboard's collection rate for the month in progress: measured against what
has fallen due so far, and blind to money paid after the as-at date."""
from datetime import timedelta

from core.models import Instalment
from core.services import reports as rpt

from .fixtures import LoanFixtures


class MonthToDateRateTests(LoanFixtures):
    def setUp(self):
        super().setUp()
        self.loan = self.make_loan()
        self.first = Instalment.objects.get(loan_id=self.loan["id"], number=1)
        self.due = self.first.due_date
        self.assertGreater(self.due.day, 1, "the test needs a day before the due date in its month")

    def test_before_anything_falls_due_there_is_no_rate_rather_than_zero(self):
        board = rpt.dashboard(self.due - timedelta(days=1))
        self.assertIsNone(board["collection_rate_pct"])
        self.assertEqual(board["due_to_date"], 0)
        self.assertGreater(board["due_this_month"], 0)  # the whole month is still reported
        latest = board["monthly_series"][-1]
        self.assertTrue(latest["partial"])
        self.assertIsNone(latest["collection_rate_pct"])

    def test_paid_on_the_day_it_falls_due_is_a_full_rate(self):
        amount = self.first.principal_due + self.first.interest_due
        self.repay(self.loan, str(amount), self.due.isoformat())
        board = rpt.dashboard(self.due)
        self.assertEqual(board["collection_rate_pct"], 100)
        self.assertEqual(board["monthly_series"][-1]["collection_rate_pct"], 100)

    def test_a_snapshot_does_not_count_what_was_paid_after_it(self):
        self.repay(self.loan, "100", self.due.isoformat())
        board = rpt.dashboard(self.due - timedelta(days=1))
        self.assertEqual(board["collected_this_month"], 0)
        self.assertEqual(board["monthly_series"][-1]["collected"], 0)

    def test_settled_months_keep_their_whole_month_rate(self):
        amount = self.first.principal_due + self.first.interest_due
        self.repay(self.loan, str(amount), self.due.isoformat())
        later = rpt.dashboard(self.due + timedelta(days=40))
        month = self.due.strftime("%Y-%m")
        point = next(p for p in later["monthly_series"] if p["month"] == month)
        self.assertFalse(point["partial"])
        self.assertEqual(point["collection_rate_pct"], 100)
