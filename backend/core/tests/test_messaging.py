"""Delivering messages, and being honest about whether anything was delivered.

The outbox used to be marked sent by a single UPDATE that contacted nobody, so the
system reported arrears notices as delivered that no borrower had received. These
tests are mostly about the difference between "sent" and "we tried".
"""
import json
import urllib.error
from datetime import date, timedelta

from django.core import mail
from django.test import override_settings

from core.models import AuditLog, Notification, NotificationChannel, NotificationStatus
from core.services import gateways
from core.services import notifications as notify

from .test_components import LedgerBase


class MessagingBase(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)
        # Disbursing queues a payout confirmation; each test starts from an empty outbox.
        self.payout_confirmation = Notification.objects.filter(kind="welcome").first()
        Notification.objects.all().delete()

    def queue(self, **overrides):
        from core.models import Borrower

        fields = {
            "borrower": Borrower.objects.get(pk=self.borrower["id"]),
            "kind": "reminder",
            "channel": NotificationChannel.SMS,
            "to_address": "0771234567",
            "body": "Your instalment is due on Friday.",
            "scheduled_for": date.today(),
            "dedupe_key": f"test:{Notification.objects.count()}",
        }
        fields.update(overrides)
        return Notification.objects.create(**fields)


# ------------------------------------------------------------------ the default
@override_settings(MESSAGE_SMS_BACKEND="console", MESSAGE_EMAIL_BACKEND="console")
class ConsoleBackendTests(MessagingBase):
    def test_the_console_backend_marks_sent_but_says_it_does_not_deliver(self):
        """A development machine must not text real-looking numbers from seed data.

        So console is the default — but it must not let anyone believe a borrower
        was contacted, which is what `describe()` is for.
        """
        self.queue()
        result = notify.send()

        self.assertEqual(result["sent"], 1)
        self.assertFalse(result["gateway"]["sms_delivers"])
        self.assertEqual(Notification.objects.get().provider, "console")

    def test_a_message_records_its_attempt_and_provider(self):
        message = self.queue()
        notify.send()
        message.refresh_from_db()

        self.assertEqual(message.status, NotificationStatus.SENT)
        self.assertEqual(message.attempts, 1)
        self.assertIsNotNone(message.sent_at)
        self.assertIsNotNone(message.last_attempt_at)
        self.assertIsNotNone(message.provider_message_id)
        self.assertIsNone(message.error)

    def test_only_messages_that_are_due_are_attempted(self):
        self.queue(scheduled_for=date.today())
        later = self.queue(scheduled_for=date.today() + timedelta(days=5))

        result = notify.send()

        self.assertEqual(result["attempted"], 1)
        later.refresh_from_db()
        self.assertEqual(later.status, NotificationStatus.QUEUED)

    def test_a_cancelled_message_is_never_attempted(self):
        message = self.queue(status=NotificationStatus.CANCELLED)
        self.assertEqual(notify.send()["attempted"], 0)
        message.refresh_from_db()
        self.assertEqual(message.status, NotificationStatus.CANCELLED)

    def test_sending_twice_does_not_send_twice(self):
        self.queue()
        self.assertEqual(notify.send()["sent"], 1)
        self.assertEqual(notify.send()["attempted"], 0,
                         "a sent message must not be picked up again")

    def test_the_limit_caps_a_first_run_on_a_big_queue(self):
        for _ in range(5):
            self.queue()
        self.assertEqual(notify.send(limit=2)["attempted"], 2)
        self.assertEqual(Notification.objects.filter(status=NotificationStatus.QUEUED).count(), 3)


# ------------------------------------------------------------------ retries
class _Flaky(gateways.Backend):
    """Fails the way a gateway having a bad day fails: temporarily."""
    name = "flaky"

    def send(self, notification):
        return gateways.Delivery(ok=False, provider=self.name, error="connection reset")


class _Rejecting(gateways.Backend):
    """Fails the way a wrong phone number fails: permanently."""
    name = "rejecting"

    def send(self, notification):
        return gateways.Delivery(ok=False, provider=self.name, permanent=True,
                                 error="invalid recipient")


@override_settings(MESSAGE_MAX_ATTEMPTS=3)
class RetryTests(MessagingBase):
    def test_a_temporary_failure_stays_queued_for_the_next_run(self):
        message = self.queue()
        with override_settings(MESSAGE_SMS_BACKEND="flaky"):
            gateways.BACKENDS["flaky"] = _Flaky
            result = notify.send()

        self.assertEqual(result["retrying"], 1)
        self.assertEqual(result["failed"], 0)
        message.refresh_from_db()
        self.assertEqual(message.status, NotificationStatus.QUEUED,
                         "a gateway that is down comes back; the message must wait")
        self.assertEqual(message.attempts, 1)
        self.assertEqual(message.error, "connection reset")

    def test_it_gives_up_after_the_attempt_limit(self):
        message = self.queue()
        gateways.BACKENDS["flaky"] = _Flaky
        with override_settings(MESSAGE_SMS_BACKEND="flaky"):
            for _ in range(3):
                notify.send()

        message.refresh_from_db()
        self.assertEqual(message.attempts, 3)
        self.assertEqual(message.status, NotificationStatus.FAILED)
        self.assertEqual(notify.send()["attempted"], 0, "a failed message is not retried forever")

    def test_a_permanent_failure_is_not_retried_at_all(self):
        """Retrying a malformed number twice more only delays someone noticing."""
        message = self.queue()
        gateways.BACKENDS["rejecting"] = _Rejecting
        with override_settings(MESSAGE_SMS_BACKEND="rejecting"):
            result = notify.send()

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["retrying"], 0)
        message.refresh_from_db()
        self.assertEqual(message.status, NotificationStatus.FAILED)
        self.assertEqual(message.attempts, 1)

    def test_one_bad_message_does_not_hold_up_the_batch(self):
        good = self.queue(to_address="0771111111")
        bad = self.queue(to_address="not-a-number")

        class Selective(gateways.Backend):
            name = "selective"

            def send(self, notification):
                if notification.to_address == "not-a-number":
                    return gateways.Delivery(ok=False, provider=self.name, permanent=True,
                                             error="invalid recipient")
                return gateways.Delivery(ok=True, provider=self.name, message_id="x")

        gateways.BACKENDS["selective"] = Selective
        with override_settings(MESSAGE_SMS_BACKEND="selective"):
            result = notify.send()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["failed"], 1)
        good.refresh_from_db()
        bad.refresh_from_db()
        self.assertEqual(good.status, NotificationStatus.SENT)
        self.assertEqual(bad.status, NotificationStatus.FAILED)
        self.assertIn("invalid recipient", result["errors"][0])

    def test_a_misconfigured_gateway_stops_the_run_instead_of_failing_the_queue(self):
        """Marking a whole queue failed over a missing URL would lose the queue."""
        self.queue()
        with override_settings(MESSAGE_SMS_BACKEND="http", MESSAGE_HTTP={"url": ""}):
            with self.assertRaises(RuntimeError) as caught:
                notify.send()
        self.assertIn("MESSAGE_HTTP", str(caught.exception))
        self.assertEqual(Notification.objects.get().status, NotificationStatus.QUEUED)

    def test_an_unknown_backend_name_says_which_ones_exist(self):
        self.queue()
        with override_settings(MESSAGE_SMS_BACKEND="carrier-pigeon"):
            with self.assertRaises(RuntimeError) as caught:
                notify.send()
        self.assertIn("console", str(caught.exception))


# ------------------------------------------------------------------ email
@override_settings(MESSAGE_EMAIL_BACKEND="smtp",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailTests(MessagingBase):
    def test_an_email_is_actually_handed_to_the_mail_backend(self):
        self.queue(channel=NotificationChannel.EMAIL, to_address="borrower@example.com",
                   subject="Instalment due")
        result = notify.send()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["borrower@example.com"])
        self.assertEqual(mail.outbox[0].subject, "Instalment due")

    def test_an_address_with_no_at_sign_fails_permanently(self):
        message = self.queue(channel=NotificationChannel.EMAIL, to_address="0771234567")
        result = notify.send()

        self.assertEqual(result["failed"], 1)
        message.refresh_from_db()
        self.assertEqual(message.attempts, 1, "no number of retries will add an @")
        self.assertEqual(len(mail.outbox), 0)

    def test_sms_and_email_use_their_own_backends(self):
        self.queue(channel=NotificationChannel.SMS)
        self.queue(channel=NotificationChannel.EMAIL, to_address="b@example.com")
        notify.send()
        # Email went to the mail backend; the SMS did not.
        self.assertEqual(len(mail.outbox), 1)


# ------------------------------------------------------------------ the http gateway
class HttpGatewayTests(MessagingBase):
    CONFIG = {
        "url": "https://gateway.example/send",
        "format": "form",
        "to_field": "recipient",
        "body_field": "text",
        "extra": {"from": "SIMBA", "apiKey": "secret"},
        "headers": {"Authorization": "Bearer abc"},
        "id_path": "SMSMessageData.Recipients.0.messageId",
        "timeout": 5,
    }

    def send_with(self, opener):
        import unittest.mock as mock

        self.queue()
        with override_settings(MESSAGE_SMS_BACKEND="http", MESSAGE_HTTP=self.CONFIG):
            with mock.patch("urllib.request.urlopen", opener):
                return notify.send()

    def test_it_posts_the_configured_fields_and_headers(self):
        import unittest.mock as mock

        captured = {}

        class Response:
            def read(self):
                return json.dumps({"SMSMessageData": {
                    "Recipients": [{"messageId": "ATXid_9", "status": "Success"}]}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["headers"] = dict(request.headers)
            captured["body"] = request.data.decode()
            captured["timeout"] = timeout
            return Response()

        result = self.send_with(mock.Mock(side_effect=opener))

        self.assertEqual(result["sent"], 1)
        self.assertEqual(captured["url"], "https://gateway.example/send")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["timeout"], 5)
        # The configured field names, not hard-coded ones — this is what lets a
        # different aggregator be an .env change rather than a code change.
        self.assertIn("recipient=0771234567", captured["body"])
        self.assertIn("text=", captured["body"])
        self.assertIn("from=SIMBA", captured["body"])
        # And the API key travels in the body, not the URL, so it stays out of logs.
        self.assertNotIn("secret", captured["url"])
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer abc")

        # The provider's own id is kept, so a delivery query can be tied back.
        self.assertEqual(Notification.objects.get().provider_message_id, "ATXid_9")

    def test_json_format_sends_json(self):
        import unittest.mock as mock

        captured = {}

        class Response:
            def read(self):
                return b'{"id": "abc123"}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            captured["body"] = request.data.decode()
            captured["type"] = dict(request.headers).get("Content-type")
            return Response()

        self.queue()
        with override_settings(MESSAGE_SMS_BACKEND="http",
                               MESSAGE_HTTP={**self.CONFIG, "format": "json",
                                             "id_path": "id"}):
            with mock.patch("urllib.request.urlopen", mock.Mock(side_effect=opener)):
                notify.send()

        self.assertEqual(captured["type"], "application/json")
        self.assertEqual(json.loads(captured["body"])["recipient"], "0771234567")
        self.assertEqual(Notification.objects.get().provider_message_id, "abc123")

    def test_a_4xx_is_permanent_and_a_5xx_is_worth_retrying(self):
        import unittest.mock as mock
        import io

        def failing(code):
            def opener(request, timeout=None):
                raise urllib.error.HTTPError(request.full_url, code, "nope", {},
                                             io.BytesIO(b'{"detail":"bad"}'))
            return mock.Mock(side_effect=opener)

        # 400: the message or the credentials. Retrying changes nothing.
        result = self.send_with(failing(400))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["retrying"], 0)

        Notification.objects.all().delete()

        # 503: the provider having a bad day. Worth another go.
        result = self.send_with(failing(503))
        self.assertEqual(result["retrying"], 1)
        self.assertEqual(result["failed"], 0)

    def test_a_timeout_is_worth_retrying(self):
        import unittest.mock as mock

        result = self.send_with(mock.Mock(side_effect=TimeoutError("timed out")))
        self.assertEqual(result["retrying"], 1)
        self.assertIn("TimeoutError", Notification.objects.get().error)

    def test_a_response_with_no_id_is_still_a_success(self):
        import unittest.mock as mock

        class Response:
            def read(self):
                return b'OK'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        result = self.send_with(mock.Mock(side_effect=lambda r, timeout=None: Response()))
        self.assertEqual(result["sent"], 1)
        self.assertIsNone(Notification.objects.get().provider_message_id)


# ------------------------------------------------------------------ the API
@override_settings(MESSAGE_SMS_BACKEND="console")
class MessagingApiTests(MessagingBase):
    def test_the_gateway_endpoint_says_whether_anything_is_delivered(self):
        body = self.officer.get("/api/notifications/gateway").json()
        self.assertEqual(body["sms_backend"], "console")
        self.assertFalse(body["sms_delivers"])
        self.assertFalse(body["http_configured"])

    def test_sending_through_the_api_records_the_outcome(self):
        self.queue()
        response = self.officer.post("/api/notifications/send", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["sent"], 1)
        self.assertTrue(AuditLog.objects.filter(action="send_notifications").exists())

    def test_marking_sent_by_hand_is_recorded_as_not_delivered(self):
        """The audit trail must not claim a delivery this system did not make."""
        message = self.queue()
        response = self.officer.post("/api/notifications/mark-sent", {"ids": [message.id]},
                                     format="json")
        self.assertEqual(response.status_code, 200, response.content)

        message.refresh_from_db()
        self.assertEqual(message.status, NotificationStatus.SENT)
        self.assertEqual(message.provider, "manual")
        self.assertIn("not delivered by this system", message.error)
        entry = AuditLog.objects.get(action="mark_notifications_sent")
        self.assertIn("not delivered", entry.detail)

    def test_a_teller_cannot_send_but_can_read(self):
        self.queue()
        self.assertEqual(self.teller.post("/api/notifications/send", {}, format="json")
                         .status_code, 403)
        self.assertEqual(self.teller.get("/api/notifications").status_code, 200)
        self.assertEqual(self.teller.get("/api/notifications/gateway").status_code, 200)

    def test_the_outbox_shows_the_attempt_count_and_the_provider(self):
        self.queue()
        notify.send()
        row = self.officer.get("/api/notifications").json()["results"][0]
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["provider"], "console")
        self.assertIsNotNone(row["last_attempt_at"])

    def test_the_command_reports_what_it_delivered(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("send_reminders", "--send", stdout=out)
        printed = out.getvalue()
        self.assertIn("Delivered", printed)
        # And warns that nothing actually left the building on the console backend.
        self.assertIn("Nothing actually left the building", printed)
