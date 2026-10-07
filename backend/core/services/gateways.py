"""Actually delivering a message.

The outbox used to be marked sent in one UPDATE that contacted nobody. This is
what makes "sent" mean sent.

Five backends, chosen per channel by setting. None of them is a vendor SDK
(`twilio` speaks Twilio's REST API with the standard library; see TwilioBackend):

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
import base64
import hashlib
import hmac
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


# ---------------------------------------------------------------- Twilio
def international(number: str | None) -> str | None:
    """A phone number in the +<country><number> form providers insist on.

    Staff type numbers the way people say them: "0771 234 567", "263771234567",
    "+263 77 123 4567". A leading 0 is the national trunk prefix and gives way to
    the default country code; anything left that is not 8-15 digits is not a
    phone number, and None says so.
    """
    if not number:
        return None
    raw = str(number).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith("+"):
        pass
    elif raw.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = (getattr(settings, "MESSAGE_DEFAULT_COUNTRY_CODE", "263") or "") + digits[1:]
    elif len(digits) <= 9:
        # A number with neither a trunk 0 nor a country code: assume local.
        digits = (getattr(settings, "MESSAGE_DEFAULT_COUNTRY_CODE", "263") or "") + digits
    if not 8 <= len(digits) <= 15:
        return None
    return f"+{digits}"


# Twilio error codes that mean this message can never be sent as it stands. The
# rest of the 4xx range (and 429, rate limiting) is worth another go.
_TWILIO_PERMANENT = {
    21211,  # invalid To number
    21212,  # invalid From number
    21408,  # no permission to send to that country
    21610,  # the recipient replied STOP
    21612,  # cannot route between these numbers
    21614,  # not a mobile number
    63003,  # not a WhatsApp number, or the channel cannot reach it
    63016,  # WhatsApp: free text outside the 24-hour window
    63024,  # WhatsApp: invalid recipient
}


class TwilioBackend(Backend):
    """Twilio's Messages API, for SMS or WhatsApp. Stdlib only, like HttpBackend.

    WhatsApp differs from SMS in two ways handled here: both numbers carry a
    "whatsapp:" prefix, and a message to someone who has not written to you in the
    last 24 hours must be an approved template (a Content SID with numbered
    variables) rather than free text. A kind with a template configured is sent as
    the template; one without is sent as text, which only arrives inside that
    window - and when it does not, refresh_deliveries() sees Twilio report it
    undelivered and the outbox resends it by SMS.
    """
    name = "twilio"

    def __init__(self, whatsapp: bool = False):
        self.whatsapp = whatsapp

    def config(self) -> dict:
        config = dict(getattr(settings, "TWILIO", {}) or {})
        problems = []
        if not config.get("account_sid"):
            problems.append("TWILIO_ACCOUNT_SID")
        if not (config.get("auth_token")
                or (config.get("api_key_sid") and config.get("api_key_secret"))):
            problems.append("TWILIO_AUTH_TOKEN (or TWILIO_API_KEY_SID and TWILIO_API_KEY_SECRET)")
        if self.whatsapp and not config.get("whatsapp_from"):
            problems.append("TWILIO_WHATSAPP_FROM")
        if (not self.whatsapp and not config.get("sms_from")
                and not config.get("messaging_service_sid")):
            problems.append("TWILIO_SMS_FROM or TWILIO_MESSAGING_SERVICE_SID")
        if problems:
            # Stop the run: every message would fail the same way.
            raise RuntimeError(f"Twilio is selected but not configured: set {', '.join(problems)} "
                               "in backend/.env.")
        return config

    def _auth(self, config) -> str:
        user, secret = ((config["api_key_sid"], config["api_key_secret"])
                        if config.get("api_key_sid") and config.get("api_key_secret")
                        else (config["account_sid"], config["auth_token"]))
        token = base64.b64encode(f"{user}:{secret}".encode()).decode()
        return f"Basic {token}"

    def _url(self, config, suffix: str = "") -> str:
        base = config.get("api_base", "https://api.twilio.com").rstrip("/")
        return f"{base}/2010-04-01/Accounts/{config['account_sid']}/Messages{suffix}.json"

    def payload(self, notification, config) -> dict:
        to = international(notification.to_address)
        if not to:
            raise ValueError(f"{notification.to_address!r} is not a phone number")
        if self.whatsapp:
            data = {"To": f"whatsapp:{to}",
                    "From": f"whatsapp:{international(config['whatsapp_from']) or config['whatsapp_from']}"}
            values = notification.template_vars or {}
            # A promise reminder is stored as a reminder but worded as a promise.
            kind = values.get("_template") or notification.kind
            template = (config.get("whatsapp_templates") or {}).get(kind)
            if template:
                from .templates import whatsapp_variables

                data["ContentSid"] = template
                data["ContentVariables"] = json.dumps(whatsapp_variables(kind, values))
            else:
                data["Body"] = notification.body
            return data
        data = {"To": to, "Body": notification.body}
        if config.get("messaging_service_sid"):
            data["MessagingServiceSid"] = config["messaging_service_sid"]
        else:
            sender = config["sms_from"].strip()
            # An alphanumeric sender id ("ZINMAD") is used as it is; a number is
            # put in international form like the recipient.
            is_number = not any(ch.isalpha() for ch in sender)
            data["From"] = (international(sender) or sender) if is_number else sender
        return data

    def _request(self, config, url: str, data: dict | None = None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(
            url, data=body, method="POST" if data is not None else "GET",
            headers={"Authorization": self._auth(config), "Accept": "application/json",
                     **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
        with urllib.request.urlopen(request, timeout=config.get("timeout", 20)) as response:
            return json.loads(response.read().decode("utf-8", "replace") or "{}")

    def send(self, notification) -> Delivery:
        config = self.config()
        try:
            data = self.payload(notification, config)
        except ValueError as exc:
            return Delivery(ok=False, provider=self.name, permanent=True, error=str(exc))
        try:
            answer = self._request(config, self._url(config), data)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(raw)
            except ValueError:
                detail = {"message": raw[:300]}
            code = detail.get("code")
            message = f"Twilio {exc.code}{f' ({code})' if code else ''}: {detail.get('message', '')}"
            if exc.code in (401, 403) and code not in _TWILIO_PERMANENT:
                # The credentials, not the message: stop before every message fails.
                raise RuntimeError(f"{message}. Check TWILIO_ACCOUNT_SID and the auth token.")
            permanent = code in _TWILIO_PERMANENT or (400 <= exc.code < 500 and exc.code != 429)
            return Delivery(ok=False, provider=self.name, permanent=permanent, error=message)
        except Exception as exc:
            return Delivery(ok=False, provider=self.name, error=f"{type(exc).__name__}: {exc}")
        return Delivery(ok=True, provider=self.name, message_id=answer.get("sid"))

    def status(self, message_id: str) -> dict:
        """What Twilio says became of a message: status, error code and text."""
        config = self.config()
        answer = self._request(config, self._url(config, f"/{message_id}"))
        return {"status": answer.get("status"), "error_code": answer.get("error_code"),
                "error_message": answer.get("error_message")}


# ---------------------------------------------------------------- Meta
# Meta's WhatsApp error codes that mean this message will never go as it stands.
# Throttling (130429, 131056) and Meta's own trouble (131000, 131016) are retried.
_META_PERMANENT = {
    100,     # a parameter is wrong (often the template's variables)
    131008,  # a required parameter is missing
    131009,  # a parameter value is not valid
    131026,  # cannot be delivered: not on WhatsApp, or an old app
    131030,  # a test number may only send to numbers on its allowed list
    131047,  # free text outside the 24-hour window
    131051,  # unsupported message type
    132000,  # the template's variable count does not match
    132001,  # no such template, or not in that language
    132005,  # the template's text is too long once filled in
    132007,  # the template breaks a WhatsApp policy
    132012,  # a variable is in the wrong format
}
# Meta's codes that mean the access token or the account, not the message.
_META_CREDENTIALS = {0, 190, 10, 200, 3, 131005, 131031}


class MetaWhatsAppBackend(Backend):
    """WhatsApp through Meta's Cloud API, with no provider in between.

    Messages go from the business phone number registered in Meta's WhatsApp
    Manager (META_WHATSAPP_PHONE_NUMBER_ID) with a system-user access token. As
    with any WhatsApp sender, a message to someone who has not written in the last
    24 hours must be an approved template: META_WHATSAPP_TEMPLATES names one per
    kind, and its body variables are filled in the order templates.py gives.

    Meta has no "what happened to this message" lookup. It reports delivered, read
    and failed by calling a webhook (views/messaging.meta_webhook), which needs this
    server reachable from the internet over HTTPS. Without it, messages Meta
    refuses outright still fall back to SMS; ones that fail later are not seen.
    """
    name = "meta"

    def config(self) -> dict:
        config = dict(getattr(settings, "META_WHATSAPP", {}) or {})
        missing = [name for name, key in (("META_WHATSAPP_TOKEN", "token"),
                                          ("META_WHATSAPP_PHONE_NUMBER_ID", "phone_number_id"))
                   if not config.get(key)]
        if missing:
            raise RuntimeError(f"Meta WhatsApp is selected but not configured: set "
                               f"{' and '.join(missing)} in backend/.env.")
        return config

    def payload(self, notification, config) -> dict:
        to = international(notification.to_address)
        if not to:
            raise ValueError(f"{notification.to_address!r} is not a phone number")
        values = notification.template_vars or {}
        kind = values.get("_template") or notification.kind
        template = (config.get("templates") or {}).get(kind)
        base = {"messaging_product": "whatsapp", "recipient_type": "individual",
                "to": to.lstrip("+")}
        if not template:
            return {**base, "type": "text",
                    "text": {"preview_url": False, "body": notification.body}}
        from .templates import whatsapp_variables

        parameters = [{"type": "text", "text": value}
                      for _, value in sorted(whatsapp_variables(kind, values).items(),
                                             key=lambda item: int(item[0]))]
        return {**base, "type": "template", "template": {
            "name": template, "language": {"code": config.get("language") or "en"},
            "components": [{"type": "body", "parameters": parameters}] if parameters else []}}

    def send(self, notification) -> Delivery:
        config = self.config()
        try:
            data = self.payload(notification, config)
        except ValueError as exc:
            return Delivery(ok=False, provider=self.name, permanent=True, error=str(exc))
        url = (f"{config.get('api_base', 'https://graph.facebook.com').rstrip('/')}/"
               f"{config.get('version', 'v23.0')}/{config['phone_number_id']}/messages")
        request = urllib.request.Request(
            url, data=json.dumps(data).encode(), method="POST",
            headers={"Authorization": f"Bearer {config['token']}",
                     "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=config.get("timeout", 20)) as response:
                answer = json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                error = json.loads(raw).get("error", {})
            except ValueError:
                error = {"message": raw[:300]}
            code = error.get("code")
            detail = error.get("error_data", {}).get("details") or error.get("message", "")
            message = f"Meta {exc.code}{f' ({code})' if code is not None else ''}: {detail}"
            if exc.code in (401, 403) or code in _META_CREDENTIALS:
                raise RuntimeError(f"{message}. Check META_WHATSAPP_TOKEN and the phone number id.")
            permanent = code in _META_PERMANENT or (400 <= exc.code < 500 and exc.code != 429
                                                    and code not in (130429, 131056))
            return Delivery(ok=False, provider=self.name, permanent=permanent, error=message)
        except Exception as exc:
            return Delivery(ok=False, provider=self.name, error=f"{type(exc).__name__}: {exc}")
        messages = answer.get("messages") or [{}]
        return Delivery(ok=True, provider=self.name, message_id=messages[0].get("id"))


def verify_meta_signature(body: bytes, header: str | None) -> bool:
    """Meta signs each webhook call with the app secret (X-Hub-Signature-256)."""
    secret = (getattr(settings, "META_WHATSAPP", {}) or {}).get("app_secret") or ""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


BACKENDS = {
    "console": ConsoleBackend,
    "file": FileBackend,
    "smtp": SmtpBackend,
    "http": HttpBackend,
    "twilio": TwilioBackend,
}


def backend_for(channel: str) -> Backend:
    """The configured backend for a channel.

    Each channel is set separately, because an institution typically has an
    aggregator for SMS, a mail server for email, and WhatsApp through one of them.
    """
    if channel == NotificationChannel.WHATSAPP:
        name = settings.MESSAGE_WHATSAPP_BACKEND
        if name == "twilio":
            return TwilioBackend(whatsapp=True)
        if name == "meta":
            return MetaWhatsAppBackend()
        if name in ("console", "file"):
            return BACKENDS[name]()
        raise RuntimeError(f"MESSAGE_WHATSAPP_BACKEND {name!r} cannot send WhatsApp. "
                           "Choose meta, twilio, console, file or off.")
    name = (settings.MESSAGE_EMAIL_BACKEND if channel == NotificationChannel.EMAIL
            else settings.MESSAGE_SMS_BACKEND)
    try:
        return BACKENDS[name]()
    except KeyError:
        raise RuntimeError(
            f"Unknown message backend {name!r}. Choose one of: "
            f"{', '.join(sorted(BACKENDS))}.")


def whatsapp_enabled() -> bool:
    """False when WhatsApp is switched off, so WhatsApp-preferring borrowers get SMS."""
    return (getattr(settings, "MESSAGE_WHATSAPP_BACKEND", "console") or "off") != "off"


def describe() -> dict:
    """What the system would do with a message right now.

    Shown on the Messages page, because "is this actually going anywhere?" is the
    first thing anyone asks about an outbox.
    """
    sms = settings.MESSAGE_SMS_BACKEND
    email = settings.MESSAGE_EMAIL_BACKEND
    whatsapp = getattr(settings, "MESSAGE_WHATSAPP_BACKEND", "console")
    pretend = {"console", "file"}
    twilio = getattr(settings, "TWILIO", {}) or {}
    meta = getattr(settings, "META_WHATSAPP", {}) or {}
    # The template names (Meta) or Content SIDs (Twilio) of whichever is sending WhatsApp.
    templates = ((meta.get("templates") if whatsapp == "meta" else twilio.get("whatsapp_templates"))
                 or {})
    return {
        "sms_backend": sms,
        "email_backend": email,
        "whatsapp_backend": whatsapp,
        "sms_delivers": sms not in pretend,
        "email_delivers": email not in pretend,
        "whatsapp_delivers": whatsapp not in pretend | {"off"},
        "max_attempts": settings.MESSAGE_MAX_ATTEMPTS,
        "http_configured": bool((getattr(settings, "MESSAGE_HTTP", {}) or {}).get("url")),
        "twilio_configured": bool(twilio.get("account_sid")),
        "meta_configured": bool(meta.get("token") and meta.get("phone_number_id")),
        "meta_webhook_ready": bool(meta.get("verify_token") and meta.get("app_secret")),
        "whatsapp_templates": sorted(templates.keys()),
        "whatsapp_template_guide": _template_guide(templates),
    }


def _template_guide(configured: dict) -> list[dict]:
    """What to submit to Meta for each kind, and whether it has been set up here."""
    from .templates import TEMPLATES, WHATSAPP_VARIABLES, whatsapp_wording

    return [{"kind": kind, "label": TEMPLATES[kind]["label"], "text": whatsapp_wording(kind),
             "variables": names, "content_sid": configured.get(kind),
             "suggested_name": f"lms_{kind}"}
            for kind, names in WHATSAPP_VARIABLES.items()]
