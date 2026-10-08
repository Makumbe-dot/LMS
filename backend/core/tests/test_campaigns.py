"""Bulk messages, and test mode sending everything to one number."""
import unittest.mock as mock
from datetime import date, timedelta

from django.test import override_settings

from core.models import Borrower, Campaign, Notification, NotificationChannel, NotificationStatus
from core.services import campaigns
from core.services import notifications as notify

from .test_messaging import MessagingBase


def run_now(ids):
    """The background sender, run in the test's own thread and transaction."""
    notify.send(ids)


@override_settings(MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="console",
                   MESSAGE_EMAIL_BACKEND="console")
class CampaignTests(MessagingBase):
    def post(self, **body):
        payload = {"audience": {"who": "active"}, "channel": "sms",
                   "text": "Dear {first_name}, loan {loan_no} has {outstanding} to pay."}
        payload.update(body)
        with mock.patch("core.services.campaigns._send_in_background", run_now), \
                self.captureOnCommitCallbacks(execute=True):
            return self.admin.post("/api/communications/campaigns", payload, format="json")

    def test_the_preview_counts_the_audience_and_fills_in_the_text(self):
        response = self.admin.post("/api/communications/campaigns/preview", {
            "audience": {"who": "active"}, "channel": "sms",
            "text": "Dear {first_name}, loan {loan_no}"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["total"], body["sms"]), (1, 1))
        self.assertEqual(body["samples"][0]["text"], f"Dear Test, loan {self.loan['loan_no']}")

    def test_a_campaign_sent_now_is_delivered_through_the_outbox(self):
        response = self.post(name="Easter reminder")
        self.assertEqual(response.status_code, 201, response.content)
        message = Notification.objects.get(kind="bulk")
        self.assertEqual((message.status, message.channel), (NotificationStatus.SENT, NotificationChannel.SMS))
        self.assertIn(self.loan["loan_no"], message.body)
        self.assertEqual(response.json()["queued"], 1)

    def test_a_later_date_waits_for_that_days_run(self):
        later = (date.today() + timedelta(days=3)).isoformat()
        self.post(scheduled_for=later)
        self.assertEqual(Notification.objects.get(kind="bulk").status, NotificationStatus.QUEUED)

    def test_email_skips_borrowers_without_an_address(self):
        response = self.post(channel="email")
        self.assertEqual((response.json()["queued"], response.json()["skipped"]), (0, 1))

    def test_unknown_placeholders_are_refused(self):
        self.assertEqual(self.post(text="Hello {nickname}").status_code, 400)

    def test_blacklisted_borrowers_are_never_bulk_messaged(self):
        Borrower.objects.all().update(is_blacklisted=True)
        self.assertEqual(self.post().status_code, 400)

    def test_the_arrears_audience_is_only_those_behind(self):
        self.assertEqual(len(campaigns.audience({"who": "arrears"})),
                         len([1 for b, loan in campaigns.audience({"who": "active"})
                              if loan and notify.arrears(loan, date.today())[0] > 0]))

    def test_the_list_shows_how_each_campaign_went(self):
        self.post(name="Notice")
        body = self.admin.get("/api/communications/campaigns").json()
        self.assertEqual(body["campaigns"][0]["name"], "Notice")
        self.assertEqual(body["campaigns"][0]["sent"], 1)
        self.assertIn("arrears", body["audiences"])

    def test_a_teller_cannot_send_bulk_messages(self):
        response = self.teller.post("/api/communications/campaigns",
                                    {"audience": {"who": "all"}, "channel": "sms", "text": "Hi"},
                                    format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Campaign.objects.exists())


@override_settings(MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="console",
                   MESSAGE_EMAIL_BACKEND="console", MESSAGE_TEST_RECIPIENT="0780062362")
class TestModeTests(MessagingBase):
    def test_every_sms_goes_to_the_test_number_marked_with_whom_it_was_for(self):
        message = self.queue(to_address="0771234567")
        with mock.patch("core.services.gateways.ConsoleBackend.send", side_effect=notify_delivery):
            notify.send()
        message.refresh_from_db()
        self.assertEqual(message.to_address, "0771234567")  # the borrower's own number is kept
        self.assertEqual(message.test_redirect, "0780062362")
        self.assertEqual(CAPTURED["to"], "0780062362")
        self.assertTrue(CAPTURED["body"].startswith("TEST for Test Borrower, 0771234567: "))

    def test_whatsapp_goes_to_the_test_number_too(self):
        message = self.queue(channel=NotificationChannel.WHATSAPP)
        notify.send()
        message.refresh_from_db()
        self.assertEqual(message.test_redirect, "0780062362")

    def test_email_with_no_test_address_falls_back_to_an_sms_to_the_test_number(self):
        message = self.queue(channel=NotificationChannel.EMAIL, to_address="real@example.com")
        notify.send()
        sms = Notification.objects.get(fallback_of=message)
        self.assertEqual((sms.channel, sms.test_redirect), (NotificationChannel.SMS, "0780062362"))

    @override_settings(MESSAGE_TEST_EMAIL="me@example.org")
    def test_email_goes_to_the_test_address_when_one_is_set(self):
        message = self.queue(channel=NotificationChannel.EMAIL, to_address="real@example.com")
        notify.send()
        message.refresh_from_db()
        self.assertEqual((message.status, message.test_redirect), (NotificationStatus.SENT, "me@example.org"))

    def test_the_whatsapp_button_opens_the_test_number(self):
        body = self.officer.post(f"/api/loans/{self.loan['id']}/whatsapp").json()
        self.assertTrue(body["url"].startswith("https://wa.me/263780062362?"))


CAPTURED = {}


def notify_delivery(message):
    from core.services.gateways import Delivery

    CAPTURED["to"], CAPTURED["body"] = message.to_address, message.body
    return Delivery(ok=True, provider="console", message_id="console-x")
