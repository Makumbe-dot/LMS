"""Weekly and fortnightly loans, through the API.

Group lending repays at the group's meeting, usually weekly or fortnightly, while
the system began with monthly salary loans. The rules that matter: the schedule
falls on the right days, affordability is still measured against a month's
salary, and nothing downstream - the ledger, arrears, reschedule - cares which
frequency a loan has.
"""
from datetime import date, timedelta
from decimal import Decimal

from core.models import BorrowerGroup, GroupMember, Loan
from core.services import ledger as gl
from core.services.amortisation import q

from .test_components import LedgerBase, dec


class WeeklyLoanTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.weekly = self.make_product(code="T-GRP", name="Group weekly",
                                        repayment_frequency="weekly", min_term_months=4,
                                        max_term_months=52, max_amount=5000)
        self.borrower = self.make_borrower()

    def quote(self, **overrides):
        payload = {"product_id": self.weekly["id"], "principal": 1000, "term_months": 16,
                   "borrower_id": self.borrower["id"], "disbursement_date": "2026-03-02"}
        payload.update(overrides)
        response = self.officer.post("/api/loans/quote", payload, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def disburse_weekly(self, principal=1000, term=16, on="2026-03-02"):
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.weekly["id"],
            "principal": principal, "term_months": term, "application_date": on,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        loan = response.json()
        self.admin.post(f"/api/loans/{loan['id']}/approve")
        response = self.officer.post(f"/api/loans/{loan['id']}/disburse",
                                     {"disbursement_date": on}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_a_weekly_product_quotes_a_weekly_schedule(self):
        body = self.quote()
        self.assertEqual(body["repayment_frequency"], "weekly")
        dates = [date.fromisoformat(r["due_date"]) for r in body["schedule"]]
        self.assertEqual(len(dates), 16)
        self.assertEqual(dates[0], date(2026, 3, 9))  # a week after disbursement
        self.assertTrue(all(b - a == timedelta(weeks=1) for a, b in zip(dates, dates[1:])))

    def test_affordability_is_measured_on_a_month_of_weekly_instalments(self):
        body = self.quote()
        monthly = dec(body["monthly_equivalent"])
        self.assertEqual(monthly, q(dec(body["instalment_amount"]) * 52 / 12))
        self.assertEqual(dec(body["affordability_pct"]), q(monthly / dec("1500") * 100))

    def test_a_weekly_loan_that_only_looks_affordable_week_by_week_is_refused(self):
        # About 250 a week: 17% of salary if you forget there are four of them a month.
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.weekly["id"],
            "principal": 3600, "term_months": 16,
        }, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("a month", response.json()["detail"])

    def test_the_term_limit_is_stated_in_weeks(self):
        response = self.officer.post("/api/loans/quote", {
            "product_id": self.weekly["id"], "principal": 1000, "term_months": 60,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("4 and 52 weeks", response.json()["detail"])

    def test_the_loan_keeps_the_frequency_it_was_sold_with(self):
        loan = self.disburse_weekly()
        self.assertEqual(loan["repayment_frequency"], "weekly")
        # Changing the product later does not reach a loan already on the book.
        self.admin.patch(f"/api/products/{self.weekly['id']}",
                         {"repayment_frequency": "monthly"}, format="json")
        self.assertEqual(Loan.objects.get(pk=loan["id"]).repayment_frequency, "weekly")

    def test_a_weekly_loan_keeps_the_ledger_tied_to_the_book(self):
        loan = self.disburse_weekly()
        instalment = loan["instalment_amount"]
        for _ in range(3):
            response = self.teller.post(f"/api/loans/{loan['id']}/repayments", {
                "amount": instalment, "txn_date": "2026-03-30", "method": "cash"},
                format="json")
            self.assertEqual(response.status_code, 201, response.content)

        rec = {row["code"]: row for row in gl.reconciliation()["rows"]}
        self.assertTrue(rec["1100"]["agrees"], rec["1100"])
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_arrears_count_from_the_oldest_missed_week(self):
        loan = self.disburse_weekly()
        row = self.officer.get(f"/api/loans/{loan['id']}").json()
        self.assertEqual(row["first_instalment_date"], "2026-03-09")
        # Nothing paid: every week that has fallen due is in arrears, counted from
        # the first one, exactly as a monthly loan's would be.
        due = [r for r in row["schedule"] if date.fromisoformat(r["due_date"]) < date.today()]
        self.assertEqual(dec(row["arrears_amount"]), sum((dec(r["total_due"]) for r in due),
                                                         Decimal("0")))
        self.assertEqual(row["days_in_arrears"], (date.today() - date(2026, 3, 9)).days)

    def test_a_reschedule_stays_weekly(self):
        loan = self.disburse_weekly()
        response = self.admin.post(f"/api/loans/{loan['id']}/reschedule",
                                   {"new_term_months": 20}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        dates = [date.fromisoformat(r["due_date"]) for r in response.json()["schedule"]]
        self.assertEqual(len(dates), 20)
        self.assertTrue(all(b - a == timedelta(weeks=1) for a, b in zip(dates, dates[1:])))

    def test_the_agreement_says_every_week(self):
        loan = self.disburse_weekly()
        html = self.officer.get(f"/api/loans/{loan['id']}/agreement").content.decode()
        self.assertIn("16 weeks", html)
        self.assertIn("Weekly instalment", html)
        self.assertIn("every week, starting on Monday 9 March 2026", html)


class GroupMeetingDayTests(LedgerBase):
    """A group loan is repaid at the group's meeting, so it falls due on that day."""

    def setUp(self):
        super().setUp()
        self.weekly = self.make_product(code="T-GRP", name="Group weekly",
                                        repayment_frequency="weekly", min_term_months=4,
                                        max_term_months=52)
        self.fortnightly = self.make_product(code="T-FN", name="Group fortnightly",
                                             repayment_frequency="fortnightly",
                                             min_term_months=2, max_term_months=26)
        self.borrower = self.make_borrower()
        self.group = BorrowerGroup.objects.create(group_no="GRP-00001", name="Tashinga",
                                                  meeting_day="Friday")
        GroupMember.objects.create(group=self.group, borrower_id=self.borrower["id"])

    def test_the_first_weekly_instalment_falls_on_the_meeting_day(self):
        # Disbursed Monday 2 March; a week later is Monday 9th; the next Friday is the 13th.
        body = self.officer.post("/api/loans/quote", {
            "product_id": self.weekly["id"], "principal": 1000, "term_months": 8,
            "borrower_id": self.borrower["id"], "disbursement_date": "2026-03-02",
        }, format="json").json()
        dates = [date.fromisoformat(r["due_date"]) for r in body["schedule"]]
        self.assertEqual(dates[0], date(2026, 3, 13))
        self.assertTrue(all(d.weekday() == 4 for d in dates))

    def test_a_fortnightly_loan_meets_on_the_same_day_every_second_week(self):
        body = self.officer.post("/api/loans/quote", {
            "product_id": self.fortnightly["id"], "principal": 1000, "term_months": 6,
            "borrower_id": self.borrower["id"], "disbursement_date": "2026-03-02",
        }, format="json").json()
        dates = [date.fromisoformat(r["due_date"]) for r in body["schedule"]]
        self.assertEqual(dates[0], date(2026, 3, 20))  # 14 days on is the 16th; then Friday
        self.assertTrue(all(b - a == timedelta(weeks=2) for a, b in zip(dates, dates[1:])))

    def test_a_members_loan_is_booked_to_the_group(self):
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.weekly["id"],
            "principal": 1000, "term_months": 8,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["group_id"], self.group.id)

    def test_a_monthly_loan_ignores_the_meeting_day_and_follows_payday(self):
        monthly = self.make_product(code="T-MON", name="Monthly")
        body = self.officer.post("/api/loans/quote", {
            "product_id": monthly["id"], "principal": 1000, "term_months": 6,
            "borrower_id": self.borrower["id"], "disbursement_date": "2026-03-02",
        }, format="json").json()
        self.assertEqual(body["schedule"][0]["due_date"], "2026-03-25")  # payday 25

    def test_an_unreadable_meeting_day_falls_back_to_a_week_after_disbursement(self):
        self.group.meeting_day = "varies"
        self.group.save()
        body = self.officer.post("/api/loans/quote", {
            "product_id": self.weekly["id"], "principal": 1000, "term_months": 8,
            "borrower_id": self.borrower["id"], "disbursement_date": "2026-03-02",
        }, format="json").json()
        self.assertEqual(body["schedule"][0]["due_date"], "2026-03-09")
