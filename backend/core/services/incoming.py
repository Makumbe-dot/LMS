"""Payments a mobile-money provider or bank tells us about as they arrive.

Without this a teller keys every EcoCash payment by hand from the provider's
statement. With it, the provider calls POST /api/payments/incoming/<provider> for
each payment, and a payment that names a loan is posted to it at once, through the
same waterfall a teller's posting goes through.

Trust: the body must carry an HMAC-SHA256 signature made with the provider's shared
secret. A provider with no secret configured is refused outright: an open endpoint
that posts repayments would let anyone clear a loan by saying it was paid.

Placing a payment, in order:

  1. the account the payer typed is a loan number (this system's, or the number a
     migrated loan had before);
  2. it is a borrower's national id or borrower number, and that borrower has
     exactly one active loan;
  3. the number the payer paid from belongs to a borrower with exactly one active
     loan.

Anything that cannot be placed, or that would be refused if posted (more than the
loan owes, a closed month), waits as unmatched with the reason, for a person to
place on a loan or dismiss. Nothing is ever posted on a guess between two loans.

Idempotent: a provider retries until it hears back, so the same reference from the
same provider is stored once and posted once; a repeat delivery is answered with
the first one's outcome.
"""
import hashlib
import hmac
import json
import re
import urllib.parse
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q

from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    IncomingPayment,
    IncomingPaymentStatus,
    Loan,
    LoanStatus,
    PaymentMethod,
    User,
)
from .amortisation import q
from .imports import _loan_for
from .repayments import post_repayment

FIELDS = ("reference", "amount", "account", "phone", "name", "date")


class Refused(Exception):
    """The delivery is not one we will store: unknown provider, bad signature,
    unreadable body. The view answers it with an error status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def provider_config(provider: str) -> dict:
    cfg = settings.INCOMING_PAYMENTS.get((provider or "").lower())
    if cfg is None:
        raise Refused(f"No payment provider called '{provider}' is configured", 404)
    if not cfg.get("secret"):
        raise Refused(f"Provider '{provider}' has no shared secret configured", 403)
    return cfg


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(cfg: dict, body: bytes, signature: str | None) -> None:
    given = (signature or "").strip()
    if given.lower().startswith("sha256="):
        given = given[7:]
    if not given or not hmac.compare_digest(given.lower(), sign(cfg["secret"], body)):
        raise Refused("The signature does not match the body", 403)


def _dig(data, path: str):
    for key in path.split("."):
        if isinstance(data, list):
            try:
                data = data[int(key)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def parse(cfg: dict, body: bytes) -> dict:
    """The provider's body, as the six values this system needs."""
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = {k: v[-1] for k, v in urllib.parse.parse_qs(text).items()}
    if not isinstance(data, dict):
        raise Refused("The body is neither a JSON object nor a form")

    values = {}
    for name in FIELDS:
        raw = _dig(data, cfg["fields"].get(name, name))
        values[name] = str(raw).strip() if raw not in (None, "") else None

    if not values["reference"]:
        raise Refused("The payment has no reference")
    try:
        amount = q(Decimal(values["amount"] or ""))
    except (InvalidOperation, ValueError):
        raise Refused(f"The amount '{values['amount']}' is not a number")
    if amount <= 0:
        raise Refused("The amount must be more than zero")
    values["amount"] = amount
    values["date"] = _date(values["date"])
    return values


def _date(raw: str | None) -> date:
    """The provider's date, in whichever common shape it came. Today if absent."""
    if not raw:
        return date.today()
    raw = raw.strip()
    if raw.isdigit() and len(raw) == 14:  # 20261005143000, as M-Pesa-style APIs send
        return datetime.strptime(raw, "%Y%m%d%H%M%S").date()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        raise Refused(f"The date '{raw}' is not readable")


def _digits(phone: str | None) -> str:
    """The last nine digits: 0771 234 567, +263 77 123 4567 and 263771234567 agree."""
    return re.sub(r"\D", "", phone or "")[-9:]


def _single_active_loan(borrowers) -> tuple[Loan | None, str | None]:
    loans = list(Loan.objects.filter(borrower__in=borrowers, status=LoanStatus.ACTIVE)
                 .values_list("loan_no", flat=True)[:2])
    if len(loans) == 1:
        return _loan_for(loans[0]), None
    if not loans:
        return None, "the payer has no active loan"
    return None, "the payer matches more than one active loan; choose which"


def match(account: str | None, phone: str | None) -> tuple[Loan | None, str]:
    """The loan a payment belongs to, or why it could not be placed."""
    if account:
        loan = _loan_for(account)
        if loan is not None:
            return loan, ""
        borrowers = Borrower.objects.filter(
            Q(national_id__iexact=account) | Q(borrower_no__iexact=account))
        if borrowers.exists():
            loan, why = _single_active_loan(borrowers)
            return loan, why or ""
    digits = _digits(phone)
    if len(digits) == 9:
        borrowers = [b.id for b in Borrower.objects.filter(phone__endswith=digits[-4:])
                     if _digits(b.phone) == digits]
        if borrowers:
            loan, why = _single_active_loan(borrowers)
            return loan, why or ""
    if account:
        return None, f"no loan or borrower matches the account '{account}'"
    return None, "the payment names no account, and the phone matches no borrower"


def _post(payment: IncomingPayment, loan: Loan, user: User | None, method: str) -> None:
    """Post the payment to the loan, or leave it unmatched with the reason it was refused."""
    try:
        with transaction.atomic():
            txn = post_repayment(
                loan, user, payment.amount, payment.received_on, method, payment.reference,
                f"{payment.provider} payment {payment.reference}"
                + (f" from {payment.payer_phone}" if payment.payer_phone else ""))
    except BusinessRuleError as exc:
        payment.loan = loan
        payment.status = IncomingPaymentStatus.UNMATCHED
        payment.note = f"Not posted to {loan.loan_no}: {exc}"[:255]
        return
    payment.loan = loan
    payment.transaction = txn
    payment.status = IncomingPaymentStatus.POSTED
    payment.note = None

    from .notifications import queue_receipt
    queue_receipt(loan, txn.amount, txn.id, txn.txn_date)


def receive(provider: str, body: bytes, signature: str | None) -> tuple[IncomingPayment, bool]:
    """Store and, where it can be placed, post one delivery. Returns the payment and
    whether this delivery was new (False for a provider's retry)."""
    cfg = provider_config(provider)
    verify(cfg, body, signature)
    values = parse(cfg, body)
    name = provider.lower()

    existing = IncomingPayment.objects.filter(provider=name, reference=values["reference"]).first()
    if existing is not None:
        return existing, False

    try:
        with transaction.atomic():
            payment = IncomingPayment.objects.create(
                provider=name, reference=values["reference"], amount=values["amount"],
                received_on=values["date"], account=values["account"],
                payer_phone=values["phone"], payer_name=values["name"],
                payload=body.decode("utf-8", errors="replace"))
            loan, why = match(values["account"], values["phone"])
            if loan is None:
                payment.note = f"Not placed: {why}"[:255]
            else:
                _post(payment, loan, None, cfg["method"])
            payment.save()
    except IntegrityError:
        # The same delivery, arriving twice at once: the other one won.
        return IncomingPayment.objects.get(provider=name, reference=values["reference"]), False
    return payment, True


def assign(payment: IncomingPayment, loan: Loan, user: User) -> IncomingPayment:
    """A person places an unmatched payment on a loan."""
    if payment.status != IncomingPaymentStatus.UNMATCHED:
        raise BusinessRuleError(f"This payment is already {payment.get_status_display().lower()}")
    method = settings.INCOMING_PAYMENTS.get(payment.provider, {}).get(
        "method", PaymentMethod.MOBILE_MONEY)
    _post(payment, loan, user, method)
    if payment.status != IncomingPaymentStatus.POSTED:
        raise BusinessRuleError(payment.note)
    payment.resolved_by = user
    payment.resolved_at = datetime.now(timezone.utc)
    payment.save()
    return payment


def dismiss(payment: IncomingPayment, user: User, note: str) -> IncomingPayment:
    """Set an unmatched payment aside: refunded to the payer, or not ours."""
    if payment.status != IncomingPaymentStatus.UNMATCHED:
        raise BusinessRuleError(f"This payment is already {payment.get_status_display().lower()}")
    if not (note or "").strip():
        raise BusinessRuleError("Say why the payment is being dismissed")
    payment.status = IncomingPaymentStatus.DISMISSED
    payment.note = note.strip()[:255]
    payment.resolved_by = user
    payment.resolved_at = datetime.now(timezone.utc)
    payment.save()
    return payment
