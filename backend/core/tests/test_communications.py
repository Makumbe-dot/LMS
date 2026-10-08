"""The Communications section: what goes out by itself, by which channel, how often."""
import io
from datetime import date, timedelta

from django.core import mail
from django.core.management import call_command
from django.test import override_settings

from core.models import Borrower, Notification, NotificationChannel, NotificationStatus
from core.services import communications as comms
from core.services import notifications as notify

from .test_messaging import MessagingBase

LATE = date.today() + timedelta(days=400)  # every instalment of the test loan is overdue by then


@override_settings(MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="console",
                   MESSAGE_EMAIL_BACKEND="smtp",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RulesTests(MessagingBase):
    def set_borrower(self, **fields):
        Borrower.objects.filter(pk=self.borrower["id"]).update(**fields)

    def test_a_blank_settings_row_means_everything_on_and_sent_automatically(self):
        current = comms.rules()
        self.assertTrue(current["auto_send"])
        self.assertTrue(all(current["kinds"].values()))
        self.assertEqual(current["arrears_every_days"], 1)

    def test_a_kind_switched_off_is_not_queued(self):
        comms.save_rules({"kinds": {"arrears": False}})
        notify.generate_reminders(as_of=LATE)
        self.assertFalse(Notification.objects.filter(kind="arrears").exists())

    def test_arrears_notices_come_at_most_every_n_days(self):
        comms.save_rules({"arrears_every_days": 7})
        for offset in range(10):
            notify.generate_reminders(as_of=LATE + timedelta(days=offset))
        days = sorted(n.scheduled_for for n in Notification.objects.filter(kind="arrears"))
        self.assertEqual(days, [LATE, LATE + timedelta(days=7)])

    def test_unknown_kinds_and_silly_numbers_are_refused(self):
        from core.exceptions import BusinessRuleError

        with self.assertRaises(BusinessRuleError):
            comms.save_rules({"kinds": {"birthday": True}})
        with self.assertRaises(BusinessRuleError):
            comms.save_rules({"arrears_every_days": 0})

    def test_a_borrower_who_prefers_email_gets_email_with_a_subject(self):
        self.set_borrower(preferred_channel="email", email="tendai@example.com")
        notify.generate_reminders(as_of=LATE)
        message = Notification.objects.get(kind="arrears")
        self.assertEqual((message.channel, message.to_address),
                         (NotificationChannel.EMAIL, "tendai@example.com"))
        self.assertIn("Arrears notice", message.subject)
        notify.send([message.id])
        self.assertEqual(mail.outbox[0].to, ["tendai@example.com"])

    def test_email_with_no_address_on_file_goes_by_sms(self):
        self.set_borrower(preferred_channel="email", email="")
        notify.generate_reminders(as_of=LATE)
        self.assertEqual(Notification.objects.get(kind="arrears").channel, NotificationChannel.SMS)

    def test_a_failed_email_is_resent_by_sms_to_the_phone(self):
        self.set_borrower(preferred_channel="email", email="not-an-address")
        message = self.queue(channel=NotificationChannel.EMAIL, to_address="not-an-address")
        result = notify.send()
        self.assertEqual(result["fell_back_to_sms"], 1)
        sms = Notification.objects.get(fallback_of=message)
        self.assertEqual((sms.channel, sms.to_address), (NotificationChannel.SMS, "0771234567"))

    def test_with_automatic_sending_off_the_daily_job_only_queues(self):
        comms.save_rules({"auto_send": False})
        out = io.StringIO()
        call_command("send_reminders", "--send", "--as-of", LATE.isoformat(), stdout=out)
        self.assertIn("Automatic sending is off", out.getvalue())
        self.assertFalse(Notification.objects.exclude(status=NotificationStatus.QUEUED).exists())

    def test_reminder_days_before_is_saved_on_the_settings_row(self):
        from core.models import OrganisationSetting

        comms.save_rules({"reminder_days_before": 5})
        self.assertEqual(OrganisationSetting.load().reminder_days_before, 5)


@override_settings(MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="console")
class ImmediateTests(MessagingBase):
    def test_a_receipt_goes_out_as_soon_as_the_repayment_commits(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                                        {"amount": "50", "method": "cash"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        receipt = Notification.objects.get(kind="receipt")
        self.assertEqual((receipt.status, receipt.provider), (NotificationStatus.SENT, "console"))

    def test_with_send_immediately_off_the_receipt_waits_in_the_outbox(self):
        comms.save_rules({"send_immediately": False})
        with self.captureOnCommitCallbacks(execute=True):
            self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                             {"amount": "50", "method": "cash"}, format="json")
        self.assertEqual(Notification.objects.get(kind="receipt").status, NotificationStatus.QUEUED)

    def test_a_payout_is_confirmed_to_the_borrower(self):
        message = self.payout_confirmation
        self.assertEqual(message.loan_id, self.loan["id"])
        self.assertIn(self.loan["loan_no"], message.body)
        self.assertIn("paid out", message.body)

    def test_switching_receipts_off_queues_none(self):
        comms.save_rules({"kinds": {"receipt": False}})
        self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                         {"amount": "50", "method": "cash"}, format="json")
        self.assertFalse(Notification.objects.filter(kind="receipt").exists())


@override_settings(MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="console",
                   MESSAGE_EMAIL_BACKEND="console")
class CommunicationsApiTests(MessagingBase):
    def test_the_overview_counts_by_channel_and_names_the_gateways(self):
        self.queue()
        notify.send()
        body = self.admin.get("/api/communications/overview").json()
        self.assertGreaterEqual(body["channels"]["sms"]["sent"], 1)
        self.assertIn("whatsapp_backend", body["gateway"])
        self.assertIn("sms", body["preference"])

    def test_the_rules_round_trip_through_the_api(self):
        response = self.admin.put("/api/communications/rules",
                                  {"kinds": {"promise": False}, "arrears_every_days": 3},
                                  format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["kinds"]["promise"])
        self.assertEqual(self.admin.get("/api/communications/rules").json()["arrears_every_days"], 3)

    def test_a_teller_may_read_the_rules_but_not_change_them(self):
        self.assertEqual(self.teller.get("/api/communications/rules").status_code, 200)
        self.assertEqual(self.teller.put("/api/communications/rules", {"auto_send": False},
                                         format="json").status_code, 403)

    def test_a_test_message_says_whether_it_really_went(self):
        response = self.admin.post("/api/communications/test",
                                   {"channel": "sms", "to": "0780062362"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["delivers"])  # console: logged, not sent

    def test_a_test_on_an_unconfigured_channel_says_what_is_missing(self):
        with override_settings(MESSAGE_WHATSAPP_BACKEND="meta",
                               META_WHATSAPP={"token": "", "phone_number_id": ""}):
            response = self.admin.post("/api/communications/test",
                                       {"channel": "whatsapp", "to": "0780062362"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("META_WHATSAPP_TOKEN", str(response.json()))

    def test_the_sidebar_counts_messages_that_failed_this_week(self):
        self.queue(status=NotificationStatus.FAILED)
        self.assertEqual(self.admin.get("/api/nav-summary").json()["messages_failed"], 1)
