"""The amortisation engine. Pure arithmetic, so no database is needed."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from core.services.amortisation import (
    add_months,
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
