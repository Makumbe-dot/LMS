"""The amortisation engine. Pure arithmetic, so no database is needed."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from core.services.amortisation import (
    FLAT,
    add_months,
    annual_percentage_rate,
    build_schedule,
    monthly_instalment,
    total_interest,
)


class AmortisationTests(SimpleTestCase):
    def test_instalment_matches_annuity_formula(self):
        # 1000 at 5%/month over 6 months: PMT = 197.02
        self.assertEqual(monthly_instalment(Decimal("1000"), Decimal("5"), 6), Decimal("197.02"))

    def test_zero_rate_splits_evenly(self):
        self.assertEqual(monthly_instalment(Decimal("1200"), Decimal("0"), 12), Decimal("100.00"))

    def test_schedule_closes_to_zero_and_sums_to_principal(self):
        rows = build_schedule(Decimal("2500"), Decimal("7.5"), 9, date(2026, 1, 31))
        self.assertEqual(rows[-1].closing_balance, Decimal("0.00"))
        self.assertEqual(sum(r.principal_due for r in rows), Decimal("2500.00"))
        self.assertTrue(all(r.opening_balance - r.principal_due == r.closing_balance for r in rows))
        self.assertGreater(total_interest(rows), 0)

    def test_due_dates_clamp_to_month_end(self):
        rows = build_schedule(Decimal("1000"), Decimal("5"), 4, date(2026, 1, 31))
        self.assertEqual(
            [r.due_date for r in rows],
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)])

    def test_add_months_backwards(self):
        self.assertEqual(add_months(date(2026, 3, 31), -1), date(2026, 2, 28))


class AnnualPercentageRateTests(SimpleTestCase):
    """The disclosure figure. If it is wrong, the agreement understates what a loan costs."""

    def test_one_payment_a_year_later_is_exactly_the_simple_rate(self):
        # 1000 out, 1100 back 365 days later: 10% a year, and nothing to argue about.
        apr = annual_percentage_rate(Decimal("1000"), [(date(2027, 1, 1), Decimal("1100"))],
                                     date(2026, 1, 1))
        self.assertEqual(apr, Decimal("10.00"))

    def test_fees_taken_off_the_top_raise_the_rate(self):
        start = date(2026, 1, 1)
        rows = build_schedule(Decimal("1000"), Decimal("5"), 6, date(2026, 2, 1))
        payments = [(r.due_date, r.instalment) for r in rows]
        without_fees = annual_percentage_rate(Decimal("1000"), payments, start)
        with_fees = annual_percentage_rate(Decimal("960"), payments, start)
        # 5% a month compounds to roughly 80% a year; the fees push it further still.
        self.assertGreater(without_fees, Decimal("75"))
        self.assertGreater(with_fees, without_fees)

    def test_a_flat_rate_loan_discloses_more_than_reducing_at_the_same_quoted_rate(self):
        start = date(2026, 1, 1)
        flat = build_schedule(Decimal("1000"), Decimal("5"), 12, date(2026, 2, 1), FLAT)
        reducing = build_schedule(Decimal("1000"), Decimal("5"), 12, date(2026, 2, 1))
        self.assertGreater(
            annual_percentage_rate(Decimal("1000"), [(r.due_date, r.instalment) for r in flat],
                                   start),
            annual_percentage_rate(Decimal("1000"),
                                   [(r.due_date, r.instalment) for r in reducing], start))

    def test_there_is_no_rate_when_nothing_is_repaid_beyond_the_advance(self):
        self.assertIsNone(annual_percentage_rate(
            Decimal("1000"), [(date(2026, 6, 1), Decimal("1000"))], date(2026, 1, 1)))
        self.assertIsNone(annual_percentage_rate(Decimal("0"), [], date(2026, 1, 1)))
