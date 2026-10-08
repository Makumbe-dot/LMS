"""The borrower portal: sign-in by one-time code, and what a borrower may see.

A borrower proves who they are with two things the institution holds (national ID
and phone number) and a code texted to that phone. Asking for a code answers the
same whether or not the two match, so the form cannot be used to find out who
borrows here. Codes last ten minutes, allow five guesses, work once; a borrower
can have three sent an hour.

A signed-in borrower holds a portal token: a signed reference to a PortalSession
row, sent as "Authorization: Portal <token>". It is not a JWT and the staff API's
authentication never accepts it; portal views accept nothing else. A session ends
after thirty idle minutes, twelve hours at most, or when the borrower signs out.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    NotificationChannel,
    NotificationKind,
    OrganisationSetting,
    PortalCode,
    PortalSession,
)
from . import notifications, templates

CODE_MINUTES = 10
MAX_ATTEMPTS = 5
CODES_PER_HOUR = 3
IDLE_MINUTES = 30
LONGEST_HOURS = 12
SALT = "lms.portal.session"
CHALLENGE_SALT = "lms.portal.challenge"


class PortalAuthError(Exception):
    """The request carries no usable portal session."""


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _hash(code_id: int, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"portal:{code_id}:{code}".encode(),
                    hashlib.sha256).hexdigest()


def assert_open() -> None:
    if not OrganisationSetting.load().portal_enabled:
        raise BusinessRuleError("The borrower portal is not open")


def _find(national_id: str, phone: str) -> Borrower | None:
    wanted = re.sub(r"[\s-]", "", (national_id or "")).upper()
    tail = _digits(phone)[-9:]
    if not wanted or len(tail) < 9:
        return None
    for borrower in Borrower.objects.filter(phone__endswith=tail[-7:]):
        if (re.sub(r"[\s-]", "", borrower.national_id).upper() == wanted
                and _digits(borrower.phone)[-9:] == tail and not borrower.is_blacklisted):
            return borrower
    return None


def start(national_id: str, phone: str, ip: str | None = None) -> str:
    """Text a code if the ID and phone belong together. Returns a challenge either
    way, and the same kind of challenge, so the answer gives nothing away."""
    assert_open()
    borrower = _find(national_id, phone)
    code_id = 0
    if borrower is not None:
        code_id = _send(borrower, ip)
    return signing.dumps({"c": code_id, "n": secrets.token_hex(4)}, salt=CHALLENGE_SALT)


@transaction.atomic
def _send(borrower: Borrower, ip: str | None) -> int:
    now = timezone.now()
    recent = PortalCode.objects.filter(borrower=borrower,
                                       sent_at__gte=now - timedelta(hours=1)).count()
    if recent >= CODES_PER_HOUR:
        return 0  # silently: the borrower's phone already has a code
    entry = PortalCode.objects.create(borrower=borrower, code_hash="-", ip_address=ip,
                                      expires_at=now + timedelta(minutes=CODE_MINUTES))
    code = f"{secrets.randbelow(10 ** 6):06d}"
    entry.code_hash = _hash(entry.id, code)
    entry.save(update_fields=["code_hash"])
    message = notifications._queue(
        borrower=borrower, loan=None, kind=NotificationKind.PORTAL_CODE,
        channel=NotificationChannel.SMS, to_address=borrower.phone,
        body=templates.render("portal_code", borrower, code=code, minutes=CODE_MINUTES),
        scheduled_for=now.date(), dedupe_key=f"portal:{entry.id}")
    if message is not None:
        transaction.on_commit(lambda: notifications.send(ids=[message.id]))
    return entry.id


def verify(challenge: str, code: str, ip: str | None = None,
           user_agent: str | None = None) -> tuple[str, Borrower]:
    """Exchange the challenge and the code for a portal token."""
    assert_open()
    try:
        code_id = signing.loads(challenge or "", salt=CHALLENGE_SALT,
                                max_age=CODE_MINUTES * 60 + 60)["c"]
    except (signing.BadSignature, KeyError, TypeError):
        raise BusinessRuleError("That code has expired; ask for a new one")
    result, problem = _check(code_id, code, ip, user_agent)
    if problem:
        raise BusinessRuleError(problem)
    return result


@transaction.atomic
def _check(code_id: int, code: str, ip, user_agent):
    """Commits a wrong guess before the caller raises, so guesses stay counted."""
    generic = "That code is not right"
    entry = (PortalCode.objects.select_for_update().select_related("borrower")
             .filter(pk=code_id).first()) if code_id else None
    if entry is None:
        return None, generic
    now = timezone.now()
    if entry.used_at or entry.expires_at <= now or entry.attempts >= MAX_ATTEMPTS:
        return None, "That code has expired; ask for a new one"
    if not hmac.compare_digest(_hash(entry.id, _digits(code)), entry.code_hash):
        entry.attempts += 1
        entry.save(update_fields=["attempts"])
        return None, generic
    entry.used_at = now
    entry.save(update_fields=["used_at"])
    session = PortalSession.objects.create(
        borrower=entry.borrower, expires_at=now + timedelta(minutes=IDLE_MINUTES),
        ip_address=(ip or "")[:64] or None, user_agent=(user_agent or "")[:255] or None)
    token = signing.dumps({"s": session.id}, salt=SALT)
    return (token, entry.borrower), None


def session_for(header: str | None) -> PortalSession:
    """The live session an Authorization header names, or PortalAuthError."""
    if not header or not header.startswith("Portal "):
        raise PortalAuthError("Sign in to the portal")
    try:
        session_id = signing.loads(header[len("Portal "):].strip(), salt=SALT,
                                   max_age=LONGEST_HOURS * 3600)["s"]
    except (signing.BadSignature, KeyError, TypeError):
        raise PortalAuthError("Your session has ended; sign in again")
    session = (PortalSession.objects.select_related("borrower")
               .filter(pk=session_id, ended_at__isnull=True).first())
    now = timezone.now()
    if session is None or session.expires_at <= now:
        raise PortalAuthError("Your session has ended; sign in again")
    if not OrganisationSetting.load().portal_enabled or session.borrower.is_blacklisted:
        raise PortalAuthError("The portal is closed")
    if (now - session.last_seen_at).total_seconds() > 60:
        session.last_seen_at = now
        session.expires_at = now + timedelta(minutes=IDLE_MINUTES)
        session.save(update_fields=["last_seen_at", "expires_at"])
    return session


def end(session: PortalSession) -> None:
    session.ended_at = timezone.now()
    session.save(update_fields=["ended_at"])
