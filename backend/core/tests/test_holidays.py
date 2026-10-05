"""The holiday calendar: nothing falls due on a day the offices are shut.

The rules that matter: a due date on a closed day moves to the next working day;
only that one instalment moves, never the ones after it; the amounts do not
change; and a holiday declared after loans are on the book reaches their unpaid
future instalments, but never one already due.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase

from core.models import Instalment, Loan
from core.services.amortisation import MONTHLY, WEEKLY, build_schedule
from core.services.workdays import WorkingCalendar, format_weekdays, parse_weekdays

from .test_components import LedgerBase

WEEKEND = {5, 6}


class WorkingCalendarTests(SimpleTestCase):
    def test_a_working_day_stays_where_it_is(self):
        cal = WorkingCalendar(closed_weekdays=WEEKEND)
        self.assertEqual(cal.next_working(date(2026, 3, 25)), date(2026, 3, 25))  # Wednesday

    def test_a_weekend_moves_to_the_monday(self):
        cal = WorkingCalendar(closed_weekdays=WEEKEND)
        self.assertEqual(cal.next_working(date(2026, 4, 25)), date(2026, 4, 27))

    def test_a_holiday_next_to_a_weekend_moves_past_both(self):
        # Good Friday 2026 is 3 April, Easter Monday the 6th.
        cal = WorkingCalendar(closed_weekdays=WEEKEND,
                              dates={date(2026, 4, 3), date(2026, 4, 6)})
        self.assertEqual(cal.next_working(date(2026, 4, 3)), date(2026, 4, 7))

    def test_an_annual_holiday_is_shut_every_year(self):
        cal = WorkingCalendar(annual={(12, 25)})
        self.assertFalse(cal.is_working(date(2031, 12, 25)))
        self.assertEqual(cal.next_working(date(2031, 12, 25)), date(2031, 12, 26))

    def test_an_empty_calendar_is_falsy(self):
        self.assertFalse(WorkingCalendar())
        self.assertTrue(WorkingCalendar(closed_weekdays={6}))

    def test_a_calendar_closed_every_day_says_so_rather_than_looping(self):
        with self.assertRaises(ValueError):
            WorkingCalendar(closed_weekdays=set(range(7))).next_working(date(2026, 1, 1))

    def test_weekday_names_are_read_loosely_and_stored_one_way(self):
        self.assertEqual(format_weekdays(parse_weekdays(" Sunday, sat ,")), "sat,sun")
        with self.assertRaises(ValueError):
            parse_weekdays("funday")


class ScheduleOnTheCalendarTests(SimpleTestCase):
    def test_only_the_due_dates_move_and_the_amounts_do_not(self):
        plain = build_schedule(Decimal("1000"), Decimal("5"), 6, date(2026, 3, 25))
        cal = WorkingCalendar(closed_weekdays=WEEKEND)
        moved = build_schedule(Decimal("1000"), Decimal("5"), 6, date(2026, 3, 25),
                               calendar=cal)
        for a, b in zip(plain, moved):
            self.assertEqual((a.principal_due, a.interest_due, a.closing_balance),
                             (b.principal_due, b.interest_due, b.closing_balance))
        self.assertEqual([r.due_date for r in moved], [
            date(2026, 3, 25), date(2026, 4, 27),  # 25 April is a Saturday
            date(2026, 5, 25), date(2026, 6, 25),
            date(2026, 7, 27),  # 25 July is a Saturday
            date(2026, 8, 25)])

    def test_a_holiday_moves_one_instalment_not_the_rest(self):
        cal = WorkingCalendar(dates={date(2026, 3, 13)})
        rows = build_schedule(Decimal("1000"), Decimal("5"), 4, date(2026, 3, 6),
                              frequency=WEEKLY, calendar=cal)
        self.assertEqual([r.due_date for r in rows], [
            date(2026, 3, 6), date(2026, 3, 14), date(2026, 3, 20), date(2026, 3, 27)])

    def test_no_calendar_is_the_schedule_as_it_always_was(self):
        rows = build_schedule(Decimal("1000"), Decimal("5"), 3, date(2026, 4, 25), "reducing",
                              MONTHLY, WorkingCalendar())
        self.assertEqual(rows[0].due_date, date(2026, 4, 25))


class HolidayApiTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def quote_dates(self):
        response = self.officer.post("/api/loans/quote", {
            "product_id": self.product["id"], "principal": 1000, "term_months": 6,
            "borrower_id": self.borrower["id"], "disbursement_date": "2026-03-01",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return [date.fromisoformat(r["due_date"]) for r in response.json()["schedule"]]

    def add_holiday(self, on, name="Holiday", client=None, **extra):
        return (client or self.admin).post("/api/holidays", {
            "date": on.isoformat(), "name": name, **extra}, format="json")

    def test_closed_weekdays_move_a_quote_off_the_weekend(self):
        self.assertEqual(self.quote_dates()[1], date(2026, 4, 25))
        response = self.admin.patch("/api/settings", {"closed_weekdays": "Sunday, sat"},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["closed_weekdays"], "sat,sun")
        self.assertEqual(self.quote_dates()[1], date(2026, 4, 27))

    def test_the_settings_refuse_an_unreadable_day_or_a_week_with_no_working_day(self):
        for value in ("funday", "mon,tue,wed,thu,fri,sat,sun"):
            response = self.admin.patch("/api/settings", {"closed_weekdays": value},
                                        format="json")
            self.assertEqual(response.status_code, 400, value)

    def test_a_disbursed_loan_falls_due_after_the_holiday_and_keeps_its_anchor(self):
        self.assertEqual(self.add_holiday(date(2026, 3, 25)).status_code, 201)
        loan = self.disbursed_loan(self.product, self.borrower)
        dates = [date.fromisoformat(r["due_date"]) for r in loan["schedule"]]
        self.assertEqual(dates[0], date(2026, 3, 26))
        self.assertEqual(dates[1], date(2026, 4, 25))
        # The agreed day stays on the loan, so the schedule can always be rebuilt from it.
        self.assertEqual(loan["first_instalment_date"], "2026-03-25")

    def test_an_annual_holiday_applies_in_later_years(self):
        self.add_holiday(date(2020, 4, 25), "Anniversary", recurs_annually=True)
        self.assertEqual(self.quote_dates()[1], date(2026, 4, 26))

    def test_only_an_admin_keeps_the_calendar_but_everyone_reads_it(self):
        self.assertEqual(self.add_holiday(date(2026, 12, 25), client=self.officer).status_code,
                         403)
        created = self.add_holiday(date(2026, 12, 25), "Christmas").json()
        listed = self.teller.get("/api/holidays").json()
        self.assertEqual([h["name"] for h in listed], ["Christmas"])
        self.assertEqual(self.add_holiday(date(2026, 12, 25)).status_code, 400)  # one per day
        self.assertEqual(self.officer.delete(f"/api/holidays/{created['id']}").status_code, 403)
        self.assertEqual(self.admin.delete(f"/api/holidays/{created['id']}").status_code, 204)
        self.assertEqual(self.teller.get("/api/holidays").json(), [])

    def test_a_holiday_declared_later_moves_unpaid_future_instalments_only(self):
        old = self.disbursed_loan(self.product, self.borrower)  # March 2026: all past due
        other = self.make_borrower(national_id="63-654321B63")
        new = self.disbursed_loan(self.product, other, disbursement_date=date.today().isoformat())
        past = date.fromisoformat(old["schedule"][0]["due_date"])
        future = date.fromisoformat(new["schedule"][1]["due_date"])

        self.assertEqual(self.add_holiday(past).json()["instalments_moved"], 0)
        self.assertEqual(Instalment.objects.get(loan_id=old["id"], number=1).due_date, past)

        self.assertEqual(self.add_holiday(future).json()["instalments_moved"], 1)
        self.assertEqual(Instalment.objects.get(loan_id=new["id"], number=2).due_date,
                         future + timedelta(days=1))
        self.assertEqual(Instalment.objects.get(loan_id=new["id"], number=3).due_date,
                         date.fromisoformat(new["schedule"][2]["due_date"]))

    def test_a_holiday_on_the_last_instalment_moves_the_maturity_date(self):
        loan = self.disbursed_loan(self.product, self.borrower,
                                   disbursement_date=date.today().isoformat())
        last = date.fromisoformat(loan["maturity_date"])
        self.add_holiday(last)
        self.assertEqual(Loan.objects.get(pk=loan["id"]).maturity_date, last + timedelta(days=1))

    def test_closing_the_weekend_moves_the_running_book_off_it(self):
        loan = self.disbursed_loan(self.product, self.borrower,
                                   disbursement_date=date.today().isoformat())
        on_weekend = [r for r in loan["schedule"]
                      if date.fromisoformat(r["due_date"]).weekday() >= 5]
        response = self.admin.patch("/api/settings", {"closed_weekdays": "sat,sun"},
                                    format="json")
        self.assertEqual(response.json()["instalments_moved"], len(on_weekend))
        self.assertFalse(any(i.due_date.weekday() >= 5
                             for i in Instalment.objects.filter(loan_id=loan["id"])))

    def test_the_agreement_says_a_closed_day_moves_the_instalment(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        clause = "falls due on the next working day instead"
        self.assertNotIn(clause, self.officer.get(f"/api/loans/{loan['id']}/agreement")
                         .content.decode())
        self.add_holiday(date(2026, 12, 25), "Christmas")
        self.assertIn(clause, self.officer.get(f"/api/loans/{loan['id']}/agreement")
                      .content.decode())
