"""Collections: assigning overdue loans, the work queue, and promises to pay."""
from datetime import date, datetime

from django.utils import timezone

from core.models import (
    RIGHT_PRESETS,
    LoanNote,
    Notification,
    Right,
    User,
)
from core.services import collections, notifications

from .fixtures import LoanFixtures

AS_OF = date(2026, 4, 10)  # instalment 1 of a loan paid out on 1 March fell due on 25 March


def made_on(note_id: int, day: date) -> None:
    """Back-date a note: promises are judged from the day they were made."""
    LoanNote.objects.filter(pk=note_id).update(
        created_at=timezone.make_aware(datetime(day.year, day.month, day.day, 9)))


class CollectionsTests(LoanFixtures):
    def setUp(self):
        super().setUp()
        self.collector = User.objects.create_user("rudo", "rudo-pass-1", full_name="Rudo",
                                                  rights=RIGHT_PRESETS["collector"])
        self.rudo = self.client_for("rudo", "rudo-pass-1")
        self.late = self.make_loan()
        self.current = self.make_loan()
        self.repay(self.current, "197.02", "2026-03-25")

    def assign(self, loan_ids, collector_id, client=None):
        return (client or self.officer).post("/api/collections/assign", {
            "loan_ids": loan_ids, "collector_id": collector_id}, format="json")

    def queue(self, client=None, **params):
        query = "&".join(f"{k}={v}" for k, v in {"as_of": AS_OF.isoformat(), **params}.items())
        return (client or self.officer).get(f"/api/collections/queue?{query}").json()

    def promise(self, loan, amount, by, client=None):
        response = (client or self.rudo).post(f"/api/loans/{loan['id']}/notes", {
            "body": "Will pay on Friday", "promised_amount": amount, "promised_date": by,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    # ---- the queue and assignment
    def test_the_queue_holds_only_loans_in_arrears(self):
        rows = self.queue()
        self.assertEqual([r["loan_no"] for r in rows], [self.late["loan_no"]])
        self.assertEqual(rows[0]["days_in_arrears"], 16)
        self.assertIsNone(rows[0]["collector"])

    def test_assigning_gives_the_collector_their_queue(self):
        response = self.assign([self.late["id"]], self.collector.id)
        self.assertEqual(response.json()["changed"], 1)
        self.assertEqual(len(self.queue(self.rudo, collector="me")), 1)
        self.assertEqual(self.queue(collector="none"), [])
        loan = self.admin.get(f"/api/loans/{self.late['id']}").json()
        self.assertEqual(loan["collector_name"], "Rudo")

        self.assign([self.late["id"]], None)
        self.assertEqual(len(self.queue(collector="none")), 1)

    def test_assigning_needs_the_supervise_right(self):
        self.assertEqual(self.assign([self.late["id"]], self.collector.id, self.rudo).status_code,
                         403)

    def test_only_a_holder_of_the_collections_right_can_be_given_loans(self):
        clerk = User.objects.create_user("clerk", "clerk-pass1", full_name="Clerk")
        response = self.assign([self.late["id"]], clerk.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn(Right.COLLECTIONS.label, response.json()["detail"])
        names = [c["full_name"] for c in self.officer.get("/api/collections/collectors").json()]
        self.assertIn("Rudo", names)
        self.assertNotIn("Clerk", names)

    def test_a_follow_up_due_today_comes_first(self):
        deeper = self.make_loan(disbursed="2026-01-15")
        self.rudo.post(f"/api/loans/{self.late['id']}/notes", {
            "body": "Call back", "next_action_date": "2026-04-09"}, format="json")
        rows = self.queue()
        self.assertEqual(rows[0]["loan_no"], self.late["loan_no"])
        self.assertTrue(rows[0]["action_due"])
        self.assertEqual(rows[1]["loan_no"], deeper["loan_no"])

    def test_notes_need_the_collections_right(self):
        clerk = User.objects.create_user("clerk", "clerk-pass1", full_name="Clerk",
                                         rights=["cash"])
        response = self.client_for("clerk", "clerk-pass1").post(
            f"/api/loans/{self.late['id']}/notes", {"body": "x"}, format="json")
        self.assertEqual(response.status_code, 403)

    # ---- promises
    def test_a_promise_paid_in_time_is_kept(self):
        note = self.promise(self.late, "150.00", "2026-04-17")
        made_on(note["id"], date(2026, 4, 10))
        self.repay(self.late, "150.00", "2026-04-15")
        state = collections.judge(LoanNote.objects.all(), date(2026, 4, 20))[note["id"]]
        self.assertEqual(state["state"], "kept")

    def test_a_promise_short_by_the_day_is_broken(self):
        note = self.promise(self.late, "150.00", "2026-04-17")
        made_on(note["id"], date(2026, 4, 10))
        self.repay(self.late, "100.00", "2026-04-15")
        self.assertEqual(collections.judge(LoanNote.objects.all(), date(2026, 4, 16))[note["id"]]
                         ["state"], "pending")
        self.assertEqual(collections.judge(LoanNote.objects.all(), date(2026, 4, 18))[note["id"]]
                         ["state"], "broken")

    def test_a_payment_before_the_promise_was_made_does_not_keep_it(self):
        note = self.promise(self.late, "50.00", "2026-04-17")
        made_on(note["id"], date(2026, 4, 12))
        self.repay(self.late, "50.00", "2026-04-11")
        self.assertEqual(collections.judge(LoanNote.objects.all(), date(2026, 4, 20))[note["id"]]
                         ["state"], "broken")

    def test_the_queue_shows_the_open_promise(self):
        note = self.promise(self.late, "150.00", "2026-04-17")
        made_on(note["id"], date(2026, 4, 9))
        row = self.queue()[0]
        self.assertEqual(row["promise"]["amount"], "150.00")
        self.assertEqual(row["promise"]["state"], "pending")

    def test_the_borrower_is_reminded_the_day_before_a_promise(self):
        self.promise(self.late, "150.00", "2026-04-17")
        notifications.generate_reminders(as_of=date(2026, 4, 16), days_before=0)
        notifications.generate_reminders(as_of=date(2026, 4, 16), days_before=0)
        messages = Notification.objects.filter(dedupe_key__startswith="promise:")
        self.assertEqual(messages.count(), 1)
        self.assertIn("17 Apr 2026", messages.get().body)

    # ---- results
    def test_performance_counts_what_came_in_and_the_promises(self):
        self.assign([self.late["id"]], self.collector.id)
        kept = self.promise(self.late, "100.00", "2026-04-17")
        made_on(kept["id"], date(2026, 4, 10))
        self.repay(self.late, "100.00", "2026-04-15")
        broken = self.promise(self.late, "500.00", "2026-04-20")
        made_on(broken["id"], date(2026, 4, 16))

        rows = self.officer.get("/api/collections/performance?start=2026-04-01&end=2026-04-30"
                                ).json()
        row = next(r for r in rows if r["collector"] == "Rudo")
        self.assertEqual(row["collected"], "100.00")
        self.assertEqual(row["promises_made"], 2)
        self.assertEqual(row["loans_held"], 1)
        # The promise dated 20 April is judged against today, long after it.
        self.assertEqual((row["promises_kept"], row["promises_broken"]), (1, 1))
        self.assertEqual(row["kept_rate_pct"], 50.0)
