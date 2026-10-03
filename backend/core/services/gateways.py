"""Actually delivering a message.

The outbox used to be marked sent in one UPDATE that contacted nobody. This is
what makes "sent" mean sent.

Four backends, chosen per channel by setting. None of them is a vendor SDK:

  * `console` — writes to the log. The default, because a development machine that
    silently texts real borrowers from seeded data is a worse failure than one that
    sends nothing.
  * `file` — appends to a file. Useful for a demo, and for handing an aggregator a
    batch by hand.
  * `smtp` — Django's own email backend, for the email channel. Real delivery with
    no third-party account.
  * `http` — a generic form/JSON POST, configured entirely by settings: URL,
    method, auth header, and which field names carry the recipient and the text.
    Africa's Talking, Infobip, Twilio and most African SMS aggregators all accept
    some shape of this, so the integration is configuration rather than code, and
    adding a provider does not mean adding a dependency.

A backend returns a `Delivery`. It must not raise for an ordinary failure — a
rejected number, a 500 from the provider — because a batch has to continue past one
bad message. It may raise for a misconfiguration, which should stop the run.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.mail import send_mail

from ..models import NotificationChannel

log = logging.getLogger(__name__)


@dataclass
class Delivery:
    """The outcome of one attempt."""
    ok: bool
    provider: str
    message_id: str | None = None
    error: str | None = None
    # True when the failure is the message's fault (a malformed number) rather
    # than the provider's (a timeout). A permanent failure is not retried, because
    # retrying it three times just delays the operator finding out.
    permanent: bool = False


class Backend:
    name = "backend"

    def send(self, notification) -> Delivery:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleBackend(Backend):
    """Log the message. The default, so nothing is sent by accident."""
    name = "console"

    def send(self, notification) -> Delivery:
        log.info("[%s to %s] %s", notification.channel, notification.to_address,
                 notification.body)
        return Delivery(ok=True, provider=self.name, message_id=f"console-{notification.id}")


class FileBackend(Backend):
    """Append the message to a file, one JSON object per line."""
    name = "file"

    def send(self, notification) -> Delivery:
        path = Path(getattr(settings, "MESSAGE_FILE_PATH", None)
                    or Path(settings.BASE_DIR) / "logs" / "messages.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": notification.id,
                "channel": notification.channel,
                "to": notification.to_address,
                "subject": notification.subject,
                "body": notification.body,
                "kind": notification.kind,
            }, ensure_ascii=False) + "\n")
        return Delivery(ok=True, provider=self.name, message_id=f"file-{notification.id}")


class SmtpBackend(Backend):
    """Real email, through whatever EMAIL_BACKEND is configured."""
    name = "smtp"

    def send(self, notification) -> Delivery:
        if "@" not in (notification.to_address or ""):
            # Permanent: no number of retries will put an @ in it.
            return Delivery(ok=False, provider=self.name, permanent=True,
                            error=f"{notification.to_address!r} is not an email address")
        try:
            sent = send_mail(
                subject=notification.subject or f"{notification.get_kind_display()}",
                message=notification.body,
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[notification.to_address],
                fail_silently=False,
            )
        except Exception as exc:  # the provider's problem; worth retrying
            return Delivery(ok=False, provider=self.name, error=f"{type(exc).__name__}: {exc}")
        if not sent:
            return Delivery(ok=False, provider=self.name, error="the mail backend sent nothing")
        return Delivery(ok=True, provider=self.name, message_id=f"smtp-{notification.id}")


class HttpBackend(Backend):
    """A generic HTTP gateway, configured rather than coded.

    Settings (all under MESSAGE_HTTP):
        url          the endpoint
        method       POST by default
        format       "form" or "json"
        to_field     which field carries the recipient
        body_field   which field carries the text
        extra        any fixed fields the provider wants (api key, sender id, ...)
        headers      any fixed headers (Authorization, ...)
        id_path      dotted path to the provider's message id in a JSON response
        timeout      seconds, 20 by default

    Deliberately stdlib: a vendor SDK would be a dependency, a lock-in and a
    second HTTP stack to keep patched, to send a form POST.
    """
    name = "http"

    def config(self) -> dict:
        config = dict(getattr(settings, "MESSAGE_HTTP", {}) or {})
        if not config.get("url"):
            # A misconfiguration, not a message failure: stop the run rather than
            # marking a queue failed three times over.
            raise RuntimeError(
                "MESSAGE_SMS_BACKEND is 'http' but MESSAGE_HTTP['url'] is not set. "
                "Set MESSAGE_HTTP_URL in backend/.env, or use the console backend.")
        return config

    def send(self, notification) -> Delivery:
        config = self.config()
        payload = {
            config.get("to_field", "to"): notification.to_address,
            config.get("body_field", "message"): notification.body,
            **(config.get("extra") or {}),
        }
        as_json = (config.get("format") or "form").lower() == "json"
        data = (json.dumps(payload).encode() if as_json
                else urllib.parse.urlencode(payload).encode())
        headers = {
            "Content-Type": "application/json" if as_json
            else "application/x-www-form-urlencoded",
            "Accept": "application/json",
            **(config.get("headers") or {}),
        }
        request = urllib.request.Request(config["url"], data=data, headers=headers,
                                        method=config.get("method", "POST"))
        try:
            with urllib.request.urlopen(request, timeout=config.get("timeout", 20)) as response:
                raw = response.read().decode("utf-8", "replace")
                return Delivery(ok=True, provider=self.name,
                                message_id=_dig(raw, config.get("id_path")))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            # 4xx is the message or the credentials; 5xx is the provider having a
            # bad day and is worth another go.
            return Delivery(ok=False, provider=self.name, permanent=400 <= exc.code < 500,
                            error=f"HTTP {exc.code}: {detail}")
        except Exception as exc:
            return Delivery(ok=False, provider=self.name, error=f"{type(exc).__name__}: {exc}")


def _dig(raw: str, path: str | None) -> str | None:
    """Pull the provider's message id out of a JSON response, if it gave one."""
    if not path:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    for key in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(key)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return str(value) if value is not None else None


BACKENDS = {
    "console": ConsoleBackend,
    "file": FileBackend,
    "smtp": SmtpBackend,
    "http": HttpBackend,
}


def backend_for(channel: str) -> Backend:
    """The configured backend for a channel.

    SMS and email are set separately, because an institution typically has an
    aggregator for one and a mail server for the other.
    """
    name = (settings.MESSAGE_EMAIL_BACKEND if channel == NotificationChannel.EMAIL
            else settings.MESSAGE_SMS_BACKEND)
    try:
        return BACKENDS[name]()
    except KeyError:
        raise RuntimeError(
            f"Unknown message backend {name!r}. Choose one of: "
            f"{', '.join(sorted(BACKENDS))}.")


def describe() -> dict:
    """What the system would do with a message right now.

    Shown on the Messages page, because "is this actually going anywhere?" is the
    first thing anyone asks about an outbox.
    """
    sms = settings.MESSAGE_SMS_BACKEND
    email = settings.MESSAGE_EMAIL_BACKEND
    pretend = {"console", "file"}
    return {
        "sms_backend": sms,
        "email_backend": email,
        "sms_delivers": sms not in pretend,
        "email_delivers": email not in pretend,
        "max_attempts": settings.MESSAGE_MAX_ATTEMPTS,
        "http_configured": bool((getattr(settings, "MESSAGE_HTTP", {}) or {}).get("url")),
    }
