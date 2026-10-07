"""Twilio, for SMS and WhatsApp, and falling back to SMS when WhatsApp cannot deliver.

Nothing here reaches Twilio: urlopen is replaced by a fake that records what it was
asked and answers the way Twilio's REST API does.
"""
import io
import json
import unittest.mock as mock
import urllib.error
import urllib.parse
from datetime import date, timedelta

from django.test import override_settings

from core.models import Borrower, Notification, NotificationChannel, NotificationStatus
from core.services import gateways
from core.services import notifications as notify
from core.services import templates

from .test_messaging import MessagingBase

TWILIO = {
    "account_sid": "ACtest", "auth_token": "token", "api_key_sid": "", "api_key_secret": "",
    "sms_from": "ZINMAD", "messaging_service_sid": "", "whatsapp_from": "+14155238886",
    "whatsapp_templates": {"reminder": "HXreminder"}, "timeout": 5,
    "api_base": "https://twilio.example",
}


class FakeTwilio:
    """Answers POST /Messages.json with a sid and GET /Messages/<sid>.json with a status."""

    def __init__(self, statuses=None, refuse=None):
        self.posts, self.gets = [], []
        self.statuses = statuses or {}
        self.refuse = refuse  # (http code, twilio code) to refuse every POST with
        self.count = 0

    def __call__(self, request, timeout=None):
        if request.method == "POST":
            form = dict(urllib.parse.parse_qsl(request.data.decode()))
            self.posts.append({"url": request.full_url, "form": form,
                               "auth": request.headers.get("Authorization")})
            if self.refuse:
                status, code = self.refuse
                raise urllib.error.HTTPError(
                    request.full_url, status, "refused", {},
                    io.BytesIO(json.dumps({"code": code, "message": "refused"}).encode()))
            self.count += 1
            return Answer({"sid": f"SM{self.count}", "status": "queued"})
        sid = request.full_url.rsplit("/", 1)[-1].removesuffix(".json")
        self.gets.append(sid)
        status, code = self.statuses.get(sid, ("delivered", None))
        return Answer({"sid": sid, "status": status, "error_code": code,
                       "error_message": "outside the window" if code else None})


class Answer:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(TWILIO=TWILIO, MESSAGE_SMS_BACKEND="twilio", MESSAGE_WHATSAPP_BACKEND="twilio",
                   MESSAGE_DEFAULT_COUNTRY_CODE="263")
class TwilioTests(MessagingBase):
    def send(self, fake):
        with mock.patch("urllib.request.urlopen", fake):
            return notify.send()

    def test_an_sms_goes_to_twilio_in_international_form_from_the_sender_id(self):
        self.queue()
        fake = FakeTwilio()
        result = self.send(fake)

        self.assertEqual(result["sent"], 1)
        post = fake.posts[0]
        self.assertEqual(post["url"], "https://twilio.example/2010-04-01/Accounts/ACtest/Messages.json")
        self.assertEqual(post["form"]["To"], "+263771234567")
        self.assertEqual(post["form"]["From"], "ZINMAD")
        self.assertEqual(post["form"]["Body"], "Your instalment is due on Friday.")
        self.assertTrue(post["auth"].startswith("Basic "))
        message = Notification.objects.get()
        self.assertEqual((message.provider, message.provider_message_id), ("twilio", "SM1"))

    def test_whatsapp_uses_the_approved_template_with_its_variables_in_order(self):
        values = templates.context(None, None, number=2, amount="USD 74.67", loan_no="LN-000030",
                                   due_date="20 Oct 2026")
        values["first_name"] = "Tawanda"
        self.queue(channel=NotificationChannel.WHATSAPP, template_vars=values)
        fake = FakeTwilio()
        self.send(fake)

        form = fake.posts[0]["form"]
        self.assertEqual(form["To"], "whatsapp:+263771234567")
        self.assertEqual(form["From"], "whatsapp:+14155238886")
        self.assertEqual(form["ContentSid"], "HXreminder")
        self.assertNotIn("Body", form)
        self.assertEqual(json.loads(form["ContentVariables"]), {
            "1": "Tawanda", "2": "2", "3": "USD 74.67", "4": "LN-000030", "5": "20 Oct 2026"})

    def test_a_kind_with_no_template_goes_as_text(self):
        self.queue(channel=NotificationChannel.WHATSAPP, kind="arrears", body="You are 3 days late.")
        fake = FakeTwilio()
        self.send(fake)
        self.assertEqual(fake.posts[0]["form"]["Body"], "You are 3 days late.")
        self.assertNotIn("ContentSid", fake.posts[0]["form"])

    def test_a_number_that_is_not_one_fails_without_asking_twilio(self):
        self.queue(to_address="12")
        fake = FakeTwilio()
        result = self.send(fake)
        self.assertEqual((result["failed"], fake.posts), (1, []))

    def test_whatsapp_refused_outright_is_resent_by_sms_in_the_same_run(self):
        self.queue(channel=NotificationChannel.WHATSAPP)
        refusing_whatsapp = FakeTwilio()

        def opener(request, timeout=None):
            form = dict(urllib.parse.parse_qsl(request.data.decode()))
            if form.get("To", "").startswith("whatsapp:"):
                refusing_whatsapp.refuse = (400, 63003)
            else:
                refusing_whatsapp.refuse = None
            return refusing_whatsapp(request, timeout)

        result = self.send(opener)

        self.assertEqual(result["fell_back_to_sms"], 1)
        original = Notification.objects.get(channel=NotificationChannel.WHATSAPP)
        sms = Notification.objects.get(channel=NotificationChannel.SMS)
        self.assertEqual(original.status, NotificationStatus.FAILED)
        self.assertIn("63003", original.error)
        self.assertEqual((sms.status, sms.fallback_of_id), (NotificationStatus.SENT, original.id))

    def test_whatsapp_reported_undelivered_later_is_resent_by_sms(self):
        """Twilio accepts a WhatsApp message and fails it afterwards (63016, outside
        the 24-hour window); the next run finds out and sends an SMS instead."""
        self.queue(channel=NotificationChannel.WHATSAPP)
        self.send(FakeTwilio())
        original = Notification.objects.get()
        self.assertEqual(original.status, NotificationStatus.SENT)

        later = FakeTwilio(statuses={original.provider_message_id: ("undelivered", 63016)})
        result = self.send(later)

        original.refresh_from_db()
        self.assertEqual(original.status, NotificationStatus.FAILED)
        self.assertEqual(original.delivery_status, "undelivered")
        self.assertIn("63016", original.error)
        sms = Notification.objects.get(fallback_of=original)
        self.assertEqual((sms.channel, sms.status), (NotificationChannel.SMS, NotificationStatus.SENT))
        self.assertEqual(result["fell_back_to_sms"], 1)

    def test_a_delivered_message_is_recorded_and_not_asked_about_again(self):
        self.queue()
        self.send(FakeTwilio())
        fake = FakeTwilio()
        self.send(fake)
        self.assertEqual(Notification.objects.get().delivery_status, "delivered")
        self.send(fake)
        self.assertEqual(len(fake.gets), 1)

    def test_an_sms_that_fails_has_nowhere_to_fall_back_to(self):
        self.queue()
        result = self.send(FakeTwilio(refuse=(400, 21211)))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(Notification.objects.count(), 1)

    def test_bad_credentials_stop_the_run_instead_of_failing_the_queue(self):
        self.queue()
        with self.assertRaises(RuntimeError):
            self.send(FakeTwilio(refuse=(401, 20003)))
        self.assertEqual(Notification.objects.get().status, NotificationStatus.QUEUED)

    def test_missing_settings_are_named(self):
        self.queue(channel=NotificationChannel.WHATSAPP)
        with override_settings(TWILIO={**TWILIO, "whatsapp_from": ""}):
            with self.assertRaisesMessage(RuntimeError, "TWILIO_WHATSAPP_FROM"):
                self.send(FakeTwilio())


@override_settings(MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="console")
class PreferredChannelTests(MessagingBase):
    def overdue(self):
        Borrower.objects.filter(pk=self.borrower["id"]).update(preferred_channel="whatsapp")
        return notify.generate_reminders(as_of=date.today() + timedelta(days=400))

    def test_a_borrower_who_prefers_whatsapp_is_queued_on_whatsapp_with_the_values(self):
        self.overdue()
        message = Notification.objects.get(kind="arrears")
        self.assertEqual(message.channel, NotificationChannel.WHATSAPP)
        self.assertEqual(message.template_vars["first_name"], "Test")
        self.assertEqual(message.template_vars["loan_no"], self.loan["loan_no"])

    def test_switching_whatsapp_off_sends_their_messages_by_sms(self):
        with override_settings(MESSAGE_WHATSAPP_BACKEND="off"):
            self.overdue()
        self.assertEqual(Notification.objects.get(kind="arrears").channel, NotificationChannel.SMS)

    def test_the_preference_is_saved_on_the_borrower(self):
        response = self.officer.patch(f"/api/borrowers/{self.borrower['id']}",
                                      {"preferred_channel": "whatsapp"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["preferred_channel"], "whatsapp")

    def test_the_gateway_lists_the_template_wording_to_submit(self):
        guide = gateways.describe()["whatsapp_template_guide"]
        reminder = next(item for item in guide if item["kind"] == "reminder")
        self.assertEqual(reminder["text"],
                         "Dear {{1}}, instalment {{2}} of {{3}} on loan {{4}} is due on {{5}}. Thank you.")


# ------------------------------------------------------------------ Meta, directly
META = {"token": "EAAtoken", "phone_number_id": "1234567890",
        "templates": {"reminder": "lms_reminder"}, "language": "en", "version": "v23.0",
        "verify_token": "choose-me", "app_secret": "app-secret", "timeout": 5,
        "api_base": "https://graph.example"}


class FakeMeta:
    def __init__(self, refuse=None):
        self.posts, self.refuse, self.count = [], refuse, 0

    def __call__(self, request, timeout=None):
        self.posts.append({"url": request.full_url, "body": json.loads(request.data),
                           "auth": request.headers.get("Authorization")})
        if self.refuse:
            status, code = self.refuse
            raise urllib.error.HTTPError(request.full_url, status, "refused", {}, io.BytesIO(
                json.dumps({"error": {"message": "refused", "code": code}}).encode()))
        self.count += 1
        return Answer({"messaging_product": "whatsapp", "messages": [{"id": f"wamid.{self.count}"}]})


@override_settings(META_WHATSAPP=META, MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="meta")
class MetaTests(MessagingBase):
    def send(self, fake):
        with mock.patch("urllib.request.urlopen", fake):
            return notify.send()

    def test_a_template_goes_to_meta_with_its_body_variables_in_order(self):
        values = templates.context(None, None, number=2, amount="USD 74.67", loan_no="LN-000030",
                                   due_date="20 Oct 2026")
        values["first_name"] = "Tawanda"
        self.queue(channel=NotificationChannel.WHATSAPP, template_vars=values)
        fake = FakeMeta()
        result = self.send(fake)

        self.assertEqual(result["sent"], 1)
        post = fake.posts[0]
        self.assertEqual(post["url"], "https://graph.example/v23.0/1234567890/messages")
        self.assertEqual(post["auth"], "Bearer EAAtoken")
        body = post["body"]
        self.assertEqual((body["to"], body["type"]), ("263771234567", "template"))
        self.assertEqual(body["template"]["name"], "lms_reminder")
        self.assertEqual([p["text"] for p in body["template"]["components"][0]["parameters"]],
                         ["Tawanda", "2", "USD 74.67", "LN-000030", "20 Oct 2026"])
        self.assertEqual(Notification.objects.get().provider_message_id, "wamid.1")

    def test_a_kind_with_no_template_goes_as_text(self):
        self.queue(channel=NotificationChannel.WHATSAPP, kind="arrears", body="You are late.")
        fake = FakeMeta()
        self.send(fake)
        self.assertEqual(fake.posts[0]["body"]["text"]["body"], "You are late.")

    def test_refused_outright_is_resent_by_sms(self):
        self.queue(channel=NotificationChannel.WHATSAPP)
        result = self.send(FakeMeta(refuse=(400, 131026)))
        self.assertEqual(result["fell_back_to_sms"], 1)
        sms = Notification.objects.get(channel=NotificationChannel.SMS)
        self.assertEqual(sms.status, NotificationStatus.SENT)

    def test_an_expired_token_stops_the_run(self):
        self.queue(channel=NotificationChannel.WHATSAPP)
        with self.assertRaisesMessage(RuntimeError, "META_WHATSAPP_TOKEN"):
            self.send(FakeMeta(refuse=(401, 190)))
        self.assertEqual(Notification.objects.get().status, NotificationStatus.QUEUED)


@override_settings(META_WHATSAPP=META, MESSAGE_SMS_BACKEND="console", MESSAGE_WHATSAPP_BACKEND="meta")
class MetaWebhookTests(MessagingBase):
    URL = "/api/whatsapp/meta/webhook"

    def post(self, payload, secret="app-secret"):
        import hashlib
        import hmac

        from rest_framework.test import APIClient

        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return APIClient().post(self.URL, data=body, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=signature)

    def report(self, wamid, status, code=None):
        item = {"id": wamid, "status": status}
        if code:
            item["errors"] = [{"code": code, "title": "Message undeliverable"}]
        return {"entry": [{"changes": [{"value": {"statuses": [item]}}]}]}

    def test_meta_verifies_the_address_with_the_token(self):
        from rest_framework.test import APIClient

        ok = APIClient().get(self.URL, {"hub.mode": "subscribe", "hub.verify_token": "choose-me",
                                        "hub.challenge": "42"})
        self.assertEqual((ok.status_code, ok.content), (200, b"42"))
        wrong = APIClient().get(self.URL, {"hub.mode": "subscribe", "hub.verify_token": "nope",
                                           "hub.challenge": "42"})
        self.assertEqual(wrong.status_code, 403)

    def test_a_failed_report_marks_the_message_failed_and_queues_an_sms(self):
        message = self.queue(channel=NotificationChannel.WHATSAPP)
        with mock.patch("urllib.request.urlopen", FakeMeta()):
            notify.send()
        message.refresh_from_db()

        response = self.post(self.report(message.provider_message_id, "failed", 131047))
        self.assertEqual(response.status_code, 200, response.content)
        message.refresh_from_db()
        self.assertEqual((message.status, message.delivery_status), (NotificationStatus.FAILED, "failed"))
        self.assertIn("131047", message.error)
        self.assertEqual(Notification.objects.get(fallback_of=message).channel, NotificationChannel.SMS)

    def test_delivered_and_read_are_recorded(self):
        message = self.queue(channel=NotificationChannel.WHATSAPP)
        with mock.patch("urllib.request.urlopen", FakeMeta()):
            notify.send()
        message.refresh_from_db()
        self.post(self.report(message.provider_message_id, "read"))
        message.refresh_from_db()
        self.assertEqual((message.status, message.delivery_status), (NotificationStatus.SENT, "read"))

    def test_an_unsigned_or_wrongly_signed_report_is_refused(self):
        self.assertEqual(self.post(self.report("wamid.x", "read"), secret="wrong").status_code, 401)


class WhatsAppByHandTests(MessagingBase):
    def test_the_button_gives_a_wa_me_link_with_the_message_and_records_it(self):
        response = self.officer.post(f"/api/loans/{self.loan['id']}/whatsapp")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["url"].startswith("https://wa.me/263771234567?text="))
        self.assertIn(self.loan["loan_no"], body["text"])
        kept = Notification.objects.get(provider="by hand")
        self.assertEqual((kept.channel, kept.status), (NotificationChannel.WHATSAPP, NotificationStatus.SENT))

    def test_someone_without_the_right_cannot(self):
        from core.models import User

        User.objects.create_user("reader", "reader123", full_name="Reader", rights=[])
        reader = self.client_for("reader", "reader123")
        self.assertEqual(reader.post(f"/api/loans/{self.loan['id']}/whatsapp").status_code, 403)
