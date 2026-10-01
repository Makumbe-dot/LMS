"""Two definitions of arrears exist. These tests are what keeps them the same.

`loans.arrears()` walks a loan's instalments in Python, because the services that
allocate a repayment need the in-memory objects. `services.arrears` says the same
thing in SQL, because a report that loads every schedule does not scale. If those
two ever disagree, the dashboard and a repayment will be working from different
numbers and nothing else will notice.

So: a parity test over a book exercised through the whole lifecycle, at several
dates — and query-count assertions, so a quiet regression to the per-loan path
fails the suite instead of merely getting slower. A benchmark nobody runs is not a
control; an assertion in the suite is.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import Instalment, Loan, LoanStatus
from core.services import arrears as arrears_svc
from core.services import groups as group_svc
from core.services import reports as rpt
from core.services.loans import arrears as walk_arrears

from .test_components import LedgerBase, dec

ZERO = Decimal("0")


class ArrearsParityBase(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()

    def busy_book(self):
        """A book with every shape of arrears the system can produce.

        Paid up, partly paid, penalised, charged, waived, reversed, settled,
        written off, rescheduled and untouched — because parity on a book of
        pristine loans proves nothing.
        """
        loans = []
        for index in range(7):
            borrower = self.make_borrower(national_id=f"63-3000{index:02d}A63")
            loans.append(self.disbursed_loan(self.product, borrower, principal=1500,
                                             disbursement_date="2026-02-01"))

        # 0: untouched. 1: one instalment paid.
        self.teller.post(f"/api/loans/{loans[1]['id']}/repayments",
                         {"amount": "280.00", "txn_date": "2026-03-10"}, format="json")
        # 2: penalised, so penalty_due is part of the overdue balance.
        self.officer.post(f"/api/loans/{loans[2]['id']}/accrue-penalties?as_of=2026-06-05")
        # 3: a charge added to the balance, which is the column the old hand-rolled
        #    filter in views/loans.py omitted.
        self.officer.post(f"/api/loans/{loans[3]['id']}/charges",
                          {"name": "Restructure fee", "amount": "25.00",
                           "collection": "balance"}, format="json")
        # 4: penalised then waived, so penalty_due comes back down to penalty_paid.
        self.officer.post(f"/api/loans/{loans[4]['id']}/accrue-penalties?as_of=2026-06-05")
        detail = self.teller.get(f"/api/loans/{loans[4]['id']}").json()
        self.admin.post(f"/api/loans/{loans[4]['id']}/waive-penalties",
                        {"amount": detail["penalties_outstanding"], "narration": "goodwill"},
                        format="json")
        # 5: a repayment then its reversal, so paid columns go up and back down.
        paid = self.teller.post(f"/api/loans/{loans[5]['id']}/repayments",
                                {"amount": "280.00", "txn_date": "2026-03-10"},
                                format="json").json()
        self.officer.post(f"/api/loans/{loans[5]['id']}/transactions/{paid['id']}/reverse",
                          {"narration": "wrong account"}, format="json")
        # 6: rescheduled, which deletes and rebuilds the schedule.
        self.admin.post(f"/api/loans/{loans[6]['id']}/reschedule", {"new_term_months": 12},
                        format="json")
        return loans


class ParityTests(ArrearsParityBase):
    """The SQL definition and the Python one must agree, loan for loan."""

    def assert_parity(self, as_of):
        walked = {}
        for loan in Loan.objects.prefetch_related("instalments"):
            walked[loan.id] = walk_arrears(loan, as_of)

        by_loan = arrears_svc.by_loan(
            as_of, statuses=[s.value for s in LoanStatus], loan_ids=list(walked))

        self.assertEqual(set(by_loan), set(walked), "the two see different loans")
        for loan_id, (amount, days) in walked.items():
            row = by_loan[loan_id]
            self.assertEqual(row["arrears_amount"], amount,
                             f"loan {loan_id} amount differs at {as_of}")
            self.assertEqual(row["days_in_arrears"], days,
                             f"loan {loan_id} days differ at {as_of}")

    def test_the_two_definitions_agree_across_a_busy_book(self):
        self.busy_book()
        # Before anything was due, mid-book, and well past maturity.
        for as_of in (date(2026, 2, 15), date(2026, 4, 20), date(2026, 7, 1),
                      date(2027, 6, 1)):
            with self.subTest(as_of=as_of):
                self.assert_parity(as_of)

    def test_they_agree_on_a_loan_with_no_schedule(self):
        borrower = self.make_borrower(national_id="63-400001A63")
        self.officer.post("/api/loans", {
            "borrower_id": borrower["id"], "product_id": self.product["id"],
            "principal": 500, "term_months": 3}, format="json")
        # A pending application has no instalments at all: the subquery returns
        # NULL, Coalesce gives 0, and Python's empty loop gives 0.
        self.assert_parity(date.today())

    def test_they_agree_when_an_instalment_falls_due_exactly_on_as_of(self):
        loans = self.busy_book()
        first = (Instalment.objects.filter(loan_id=loans[0]["id"])
                 .order_by("number").first())
        # due_date < as_of in both, so the instalment due today is excluded by both.
        self.assert_parity(first.due_date)
        self.assert_parity(first.due_date + timedelta(days=1))

    def test_a_charge_only_balance_is_counted_by_both(self):
        """The eight-column definition, exercised on the two columns a copy omitted."""
        loans = self.busy_book()
        instalment = (Instalment.objects.filter(loan_id=loans[3]["id"], charge_due__gt=0)
                      .order_by("number").first())
        self.assertIsNotNone(instalment, "the fixture no longer adds a charge to a balance")
        # Pay everything except the charge, leaving charge_due as the only balance.
        Instalment.objects.filter(pk=instalment.pk).update(
            principal_paid=instalment.principal_due,
            interest_paid=instalment.interest_due,
            penalty_paid=instalment.penalty_due,
        )
        as_of = instalment.due_date + timedelta(days=1)
        self.assert_parity(as_of)

        row = arrears_svc.by_loan(as_of, loan_ids=[loans[3]["id"]])[loans[3]["id"]]
        self.assertEqual(row["arrears_amount"], instalment.charge_due,
                         "a charge is the whole overdue balance here")

    def test_the_in_arrears_filter_returns_what_the_sum_says(self):
        self.busy_book()
        as_of = date.today()
        expected = {row["id"] for row in arrears_svc.rows(as_of)
                    if row["arrears_amount"] > 0}
        filtered = set(Loan.objects.filter(status=LoanStatus.ACTIVE)
                       .filter(arrears_svc.is_overdue(as_of))
                       .values_list("id", flat=True))
        self.assertEqual(filtered, expected,
                         "the Exists filter and the Sum annotation disagree")


class BucketTests(ArrearsParityBase):
    def test_the_buckets_partition_the_active_book(self):
        self.busy_book()
        book = arrears_svc.totals(date(2026, 7, 1))
        active = Loan.objects.filter(status=LoanStatus.ACTIVE).count()

        self.assertEqual(sum(book["bucket_loans"].values()), active,
                         "every active loan lands in exactly one bucket")
        self.assertEqual(sum(book["buckets"].values(), ZERO), book["principal_outstanding"],
                         "the bucket amounts must add up to the principal outstanding")

    def test_the_ageing_report_and_the_dashboard_chart_are_the_same_numbers(self):
        self.busy_book()
        as_of = date(2026, 7, 1)
        chart = rpt.dashboard(as_of)["arrears_buckets"]
        table = {row["bucket"]: row["principal_outstanding"]
                 for row in rpt.arrears_ageing(as_of)}
        self.assertEqual(chart, table)

    def test_par_matches_the_loans_the_par_report_lists(self):
        self.busy_book()
        as_of = date(2026, 7, 1)
        book = arrears_svc.totals(as_of)
        listed = rpt.portfolio_at_risk(as_of)

        over_30 = [r for r in listed if r["days_in_arrears"] > 30]
        self.assertEqual(book["par_loans"], len(over_30))
        self.assertEqual(book["par_amount"],
                         sum((r["principal_outstanding"] for r in over_30), ZERO))

    def test_par_is_ordered_worst_first(self):
        self.busy_book()
        days = [r["days_in_arrears"] for r in rpt.portfolio_at_risk(date(2026, 7, 1))]
        self.assertEqual(days, sorted(days, reverse=True),
                         "PAR must be ordered by days in arrears, descending")


class QueryCountTests(ArrearsParityBase):
    """The reports must not go back to a query per loan.

    These bounds are deliberately loose — they are not a benchmark, they are a
    tripwire. The point is that the count does not GROW WITH THE BOOK, so each test
    doubles the number of loans and asserts the count is unchanged.
    """

    def counts_for(self, call):
        with CaptureQueriesContext(connection) as first:
            call()
        before = len(first)

        for index in range(7, 14):
            borrower = self.make_borrower(national_id=f"63-5000{index:02d}A63")
            self.disbursed_loan(self.product, borrower, principal=1500,
                                disbursement_date="2026-02-01")

        with CaptureQueriesContext(connection) as second:
            call()
        return before, len(second)

    def test_the_dashboard_does_not_query_per_loan(self):
        self.busy_book()
        before, after = self.counts_for(lambda: rpt.dashboard(date(2026, 7, 1)))
        self.assertEqual(before, after,
                         f"dashboard queries grew from {before} to {after} when the book "
                         f"doubled — it is back to a query per loan")

    def test_par_does_not_query_per_loan(self):
        self.busy_book()
        before, after = self.counts_for(lambda: rpt.portfolio_at_risk(date(2026, 7, 1)))
        self.assertEqual(before, after, f"PAR queries grew from {before} to {after}")

    def test_the_ecl_report_does_not_query_per_loan(self):
        self.busy_book()
        before, after = self.counts_for(lambda: rpt.ecl_report(date(2026, 7, 1)))
        self.assertEqual(before, after, f"ECL queries grew from {before} to {after}")

    def test_officer_performance_does_not_query_per_loan(self):
        self.busy_book()
        before, after = self.counts_for(rpt.officer_performance)
        self.assertEqual(before, after,
                         f"officer performance queries grew from {before} to {after}")

    def test_the_loan_list_does_not_query_per_loan(self):
        self.busy_book()
        before, after = self.counts_for(
            lambda: self.officer.get("/api/loans?page_size=100"))
        self.assertEqual(before, after, f"the loan list grew from {before} to {after}")

    def test_the_dashboard_fetches_no_instalment_rows(self):
        """The whole point: a hundred thousand instalment rows never leave the server."""
        self.busy_book()
        with CaptureQueriesContext(connection) as captured:
            rpt.dashboard(date(2026, 7, 1))
        selects = [q["sql"] for q in captured.captured_queries]
        bare = [s for s in selects
                if "instalments" in s.lower() and "sum" not in s.lower()
                and "count" not in s.lower() and "min" not in s.lower()
                and "top 1" not in s.lower()]
        self.assertEqual(bare, [],
                         "the dashboard is fetching instalment rows again rather than "
                         "aggregating them in the database")


class GroupStandingTests(ArrearsParityBase):
    def test_group_standing_agrees_with_walking_the_schedules(self):
        loans = self.busy_book()
        from core.models import Borrower, BorrowerGroup, GroupMember

        group = BorrowerGroup.objects.create(
            group_no="GRP-TEST", name="Parity group", branch=self.branch)
        borrower_ids = list(Loan.objects.filter(
            id__in=[l["id"] for l in loans]).values_list("borrower_id", flat=True))
        for borrower_id in borrower_ids:
            GroupMember.objects.create(group=group,
                                       borrower=Borrower.objects.get(pk=borrower_id))

        as_of = date(2026, 7, 1)
        state = group_svc.standing(group, as_of)

        expected_amount = ZERO
        expected_worst = 0
        for loan in Loan.objects.filter(borrower_id__in=borrower_ids,
                                        status=LoanStatus.ACTIVE).prefetch_related("instalments"):
            amount, days = walk_arrears(loan, as_of)
            expected_amount += amount
            expected_worst = max(expected_worst, days)

        self.assertEqual(state["arrears_amount"], dec(expected_amount))
        self.assertEqual(state["worst_days_in_arrears"], expected_worst)

    def test_group_performance_does_not_query_per_member_loan(self):
        self.busy_book()
        before, after = QueryCountTests.counts_for(self, group_svc.performance)
        self.assertEqual(before, after,
                         f"group performance queries grew from {before} to {after}")
