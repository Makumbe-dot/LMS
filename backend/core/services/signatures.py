"""Signing a loan agreement with a one-time code.

Staff (or the borrower, in the portal) ask for a code; it is texted to the
borrower's phone on file and nowhere else. Entering it signs the agreement: the
signature records when, from where, and a fingerprint of the terms as they stood.
A code is six digits, lives ten minutes, survives five wrong guesses, works once,
and only the latest one sent for a loan counts. Only its keyed hash is kept.

The fingerprint covers what the borrower agrees to: amount, currency, product,
rate and method, frequency and term, the fees and charges, the guarantors and the
security. A change to any of them after signing leaves the signature standing on
the record but no longer current; when the organisation requires a signature,
disbursement wants a current one.
"""
import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..audit import audit
from ..exceptions import BusinessRuleError
from ..models import (
    Loan,
    LoanSignature,
    LoanStatus,
    NotificationChannel,
    NotificationKind,
    OrganisationSetting,
    SignatureStatus,
    User,
)
from . import notifications, templates

CODE_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_SECONDS = 60
SIGNABLE = (LoanStatus.PENDING, LoanStatus.APPROVED, LoanStatus.ACTIVE)


def fingerprint(loan: Loan) -> str:
    terms = {
        "loan_no": loan.loan_no, "borrower": loan.borrower_id, "product": loan.product_id,
        "principal": str(loan.principal), "currency": loan.currency or "",
        "rate": str(loan.interest_rate_pct), "method": loan.rate_method,
        "frequency": loan.repayment_frequency, "term": loan.term_months,
        "fees": [str(loan.admin_fee), str(loan.insurance_fee), str(loan.other_charges)],
        "charges": sorted([c.charge_id, str(c.amount)] for c in loan.charges.all()),
        "guarantors": sorted(g.id for g in loan.guarantors.all()),
        "security": sorted(c.id for c in loan.collateral.filter(status="pledged")),
    }
    return hashlib.sha256(json.dumps(terms, sort_keys=True).encode()).hexdigest()


def _hash(signature_id: int, code: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{signature_id}:{code}".encode(), hashlib.sha256).hexdigest()


def current(loan: Loan) -> LoanSignature | None:
    """The signature that holds for the loan's terms as they are now, if any."""
    mark = fingerprint(loan)
    return (loan.signatures.filter(status=SignatureStatus.SIGNED, fingerprint=mark)
            .order_by("-signed_at").first())


def state(loan: Loan) -> dict:
    """What the loan page and the portal show."""
    signed = current(loan)
    last = loan.signatures.order_by("-sent_at", "-id").first()
    return {
        "required": OrganisationSetting.load().require_signature,
        "signed": signed is not None,
        "signed_at": signed.signed_at if signed else None,
        "channel": signed.channel if signed else None,
        "phone": signed.phone if signed else None,
        "fingerprint": signed.fingerprint if signed else None,
        "stale": signed is None and loan.signatures.filter(status=SignatureStatus.SIGNED).exists(),
        "pending": bool(last and last.status == SignatureStatus.PENDING
                        and last.expires_at > timezone.now()),
        "expires_at": last.expires_at if last and last.status == SignatureStatus.PENDING else None,
    }


@transaction.atomic
def send_code(loan: Loan, requested_by: User | None, channel: str = "counter") -> LoanSignature:
    if loan.status not in SIGNABLE:
        raise BusinessRuleError(f"A {loan.get_status_display().lower()} loan cannot be signed")
    phone = loan.borrower.phone
    if not phone:
        raise BusinessRuleError("The borrower has no phone number on file")
    latest = (LoanSignature.objects.select_for_update().filter(loan=loan)
              .order_by("-sent_at", "-id").first())
    now = timezone.now()
    if (latest and latest.status == SignatureStatus.PENDING
            and (now - latest.sent_at).total_seconds() < RESEND_SECONDS):
        raise BusinessRuleError("A code was sent less than a minute ago; wait before sending "
                                "another")
    loan.signatures.filter(status=SignatureStatus.PENDING).update(
        status=SignatureStatus.SUPERSEDED)
    signature = LoanSignature.objects.create(
        loan=loan, phone=phone, code_hash="-", fingerprint=fingerprint(loan),
        expires_at=now + timedelta(minutes=CODE_MINUTES), channel=channel,
        requested_by=requested_by)
    code = f"{secrets.randbelow(10 ** 6):06d}"
    signature.code_hash = _hash(signature.id, code)
    signature.save(update_fields=["code_hash"])

    body = templates.render("signing_code", loan.borrower, loan_no=loan.loan_no, code=code,
                            minutes=CODE_MINUTES)
    message = notifications._queue(
        borrower=loan.borrower, loan=loan, kind=NotificationKind.SIGNING_CODE,
        channel=NotificationChannel.SMS, to_address=phone, body=body,
        scheduled_for=now.date(), dedupe_key=f"signing:{signature.id}")
    audit(requested_by, "signing_code_sent", "loan", loan.id,
          f"{loan.loan_no} to {phone} ({channel})")
    # A code cannot wait for the nightly send: hand it to the gateway after commit.
    if message is not None:
        transaction.on_commit(lambda: notifications.send(ids=[message.id]))
    return signature


def verify(loan: Loan, code: str, *, channel: str = "counter", witnessed_by: User | None = None,
           ip: str | None = None, user_agent: str | None = None) -> LoanSignature:
    """Sign with the code, or say why not. A wrong guess is counted and kept even
    though the call fails, so the five-guess limit cannot be rolled back."""
    signature, problem = _check(loan, code, channel, witnessed_by, ip, user_agent)
    if problem:
        raise BusinessRuleError(problem)
    return signature


@transaction.atomic
def _check(loan, code, channel, witnessed_by, ip, user_agent):
    signature = (LoanSignature.objects.select_for_update()
                 .filter(loan=loan, status=SignatureStatus.PENDING)
                 .order_by("-sent_at", "-id").first())
    if signature is None:
        return None, "No code is waiting for this loan; send one first"
    now = timezone.now()
    if signature.expires_at <= now or signature.attempts >= MAX_ATTEMPTS:
        signature.status = SignatureStatus.EXPIRED
        signature.save(update_fields=["status"])
        return None, "The code has expired; send a new one"
    if signature.fingerprint != fingerprint(loan):
        signature.status = SignatureStatus.EXPIRED
        signature.save(update_fields=["status"])
        return None, ("The loan's terms changed after the code was sent; send a new one so "
                      "the borrower signs what is now on the agreement")
    given = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not hmac.compare_digest(_hash(signature.id, given), signature.code_hash):
        signature.attempts += 1
        left = MAX_ATTEMPTS - signature.attempts
        if left <= 0:
            signature.status = SignatureStatus.EXPIRED
        signature.save(update_fields=["attempts", "status"])
        return None, "That code is not right" + (
            f"; {left} more {'try' if left == 1 else 'tries'}" if left > 0
            else "; send a new one")
    signature.status = SignatureStatus.SIGNED
    signature.signed_at = now
    signature.channel = channel
    signature.witnessed_by = witnessed_by
    signature.ip_address = (ip or "")[:64] or None
    signature.user_agent = (user_agent or "")[:255] or None
    signature.save(update_fields=["status", "signed_at", "channel", "witnessed_by",
                                  "ip_address", "user_agent"])
    audit(witnessed_by, "agreement_signed", "loan", loan.id,
          f"{loan.loan_no} by {signature.phone} ({channel}), terms {signature.fingerprint[:12]}")
    return signature, None


def assert_signed_for_disbursement(loan: Loan) -> None:
    if OrganisationSetting.load().require_signature and current(loan) is None:
        raise BusinessRuleError(
            "The borrower has not signed this loan's agreement on its current terms. Send a "
            "signing code from the Agreement section first.")
