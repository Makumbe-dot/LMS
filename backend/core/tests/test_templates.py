"""Borrower message wording: the institution's own, or the built-in default."""
from datetime import date

from core.models import Notification, NotificationKind
from core.services import notifications, templates

from .fixtures import LoanFixtures


class MessageTemplateTests(LoanFixtures):
    def save(self, client=None, **texts):
        return (client or self.admin).put("/api/message-templates", {"templates": texts},
                                          format="json")

    def test_the_built_in_wording_is_used_until_one_is_saved(self):
        loan = self.make_loan()
        self.repay(loan, 100, "2026-03-10")
        receipt = Notification.objects.get(kind=NotificationKind.RECEIPT)
        self.assertTrue(receipt.body.startswith("Dear Test, we have received"))

    def test_saved_wording_is_what_the_borrower_reads(self):
        response = self.save(receipt="Hi {first_name}, thanks for {amount} on {loan_no}.")
        self.assertEqual(response.status_code, 200, response.content)
        loan = self.make_loan()
        self.repay(loan, 100, "2026-03-10")
        receipt = Notification.objects.get(kind=NotificationKind.RECEIPT)
        self.assertEqual(receipt.body, f"Hi Test, thanks for USD 100.00 on {loan['loan_no']}.")

    def test_reminders_and_arrears_notices_use_it_too(self):
        self.save(reminder="{loan_no}: {amount} due {due_date}",
                  arrears="{loan_no} overdue {days} days")
        loan = self.make_loan()
        notifications.generate_reminders(as_of=date(2026, 3, 23), days_before=3)
        reminder = Notification.objects.get(kind=NotificationKind.REMINDER)
        self.assertEqual(reminder.body, f"{loan['loan_no']}: USD 197.02 due 25 Mar 2026")
        notifications.generate_reminders(as_of=date(2026, 4, 6), days_before=0)
        notice = Notification.objects.get(kind=NotificationKind.ARREARS)
        self.assertEqual(notice.body, f"{loan['loan_no']} overdue 12 days")

    def test_an_unknown_placeholder_is_refused_when_saved(self):
        response = self.save(receipt="Thanks {first_name}, your PIN is {pin}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("{pin}", response.json()["detail"])

    def test_a_blank_box_restores_the_built_in_wording(self):
        self.save(receipt="Custom {amount}")
        self.save(receipt="")
        receipt = next(t for t in self.admin.get("/api/message-templates").json()
                       if t["kind"] == "receipt")
        self.assertEqual(receipt["template"], "")

    def test_only_an_administrator_changes_the_wording(self):
        self.assertEqual(self.officer.get("/api/message-templates").status_code, 200)
        self.assertEqual(self.save(client=self.officer, receipt="x").status_code, 403)

    def test_rendering_never_fails_on_wording_saved_under_older_rules(self):
        from core.models import OrganisationSetting

        row = OrganisationSetting.load()
        row.message_templates = {"receipt": "Hi {gone}"}
        row.save()
        body = templates.render("receipt", None, row, loan_no="LN-1", amount="1", balance="2")
        self.assertTrue(body.startswith("Dear"))
