"""Payments a provider says it received, matched to loans and posted.

A payment arrives one of two ways: a mobile-money or bank notification posted to
the webhook, signed by the provider, or a line of a statement uploaded by staff.
Either way it is kept exactly as received, once per provider reference, so a
notification delivered twice is never posted twice.

Then it is matched. What the payer typed as the account is tried as a loan
number, a borrower number and a national ID; failing those, the paying phone is
tried against borrowers' phones. A match that names exactly one active loan, in
the payment's currency, for no more than it owes, in an open period, is posted
there and then as a repayment. Anything else waits on the Incoming payments page
with the reason, and the loan it nearly matched when there was one, for someone
with the cash right to assign it or reject it.
"""
import csv
import hashlib
import hmac
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..audit import audit
from ..exceptions import BusinessRuleError
from ..models import (
    Borrower,
    InboundPayment,
    InboundStatus,
    Loan,
    LoanStatus,
    PaymentMethod,
    User,
)
from . import fx
from .amortisation import q
from .notifications import queue_receipt
from .repayments import post_repayment


# ---------------------------------------------------------------- receiving
def verify_signature(provider: str, body: bytes, signature: str | None) -> None:
    """Refuse a notification not signed with this provider's secret."""
    secret = (getattr(settings, "INBOUND_PAYMENT_SECRETS", {}) or {}).get(provider)
    if not secret:
        raise BusinessRuleError(f"No secret is configured for provider '{provider}'")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    given = (signature or "").strip().lower().removeprefix("sha256=")
    if not hmac.compare_digest(expected, given):
        raise BusinessRuleError("The signature does not match")


def _dig(data, path: str | None):
    if not path:
        return None
    value = data
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
    return value


def _paths(provider: str) -> dict:
    base = dict(getattr(settings, "INBOUND_PAYMENT_PATHS", {}) or {})
    base.update((getattr(settings, "INBOUND_PAYMENT_PROVIDER_PATHS", {}) or {}).get(provider, {}))
    return base


def _amount(value) -> Decimal:
    try:
        amount = q(Decimal(str(value).replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        raise BusinessRuleError(f"'{value}' is not an amount")
    if amount <= 0:
        raise BusinessRuleError("The amount must be more than zero")
    return amount


def _date(value) -> date:
    if not value:
        return date.today()
    text = str(value).strip()
    try:  # 2026-03-10, or a timestamp: 2026-03-10T09:15:00Z
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise BusinessRuleError(f"'{value}' is not a date")


def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def record(provider: str, fields: dict, *, method=PaymentMethod.MOBILE_MONEY,
           raw: str | None = None) -> tuple[InboundPayment, bool]:
    """Keep one payment. Returns it and whether it is new; a provider reference
    already held returns the one already kept, untouched."""
    external_id = _text(fields.get("id"), 100)
    if not external_id:
        raise BusinessRuleError("The payment has no reference from the provider")
    existing = InboundPayment.objects.filter(provider=provider, external_id=external_id).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            payment = InboundPayment.objects.create(
                provider=provider, external_id=external_id, method=method,
                amount=_amount(fields.get("amount")),
                currency=(_text(fields.get("currency"), 8) or "").upper() or None,
                paid_on=_date(fields.get("date")),
                payer_phone=_text(fields.get("phone"), 30),
                payer_name=_text(fields.get("name"), 120),
                account_ref=_text(fields.get("reference"), 100),
                raw=raw)
    except IntegrityError:  # the same notification, twice at once
        return InboundPayment.objects.get(provider=provider, external_id=external_id), False
    return payment, True


def receive_notification(provider: str, body: bytes, signature: str | None) -> dict:
    """The webhook: verify, keep, and match. Answers the same for a repeat."""
    verify_signature(provider, body, signature)
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BusinessRuleError("The body is not JSON")
    paths = _paths(provider)
    fields = {name: _dig(data, path) for name, path in paths.items()}
    payment, created = record(provider, fields, raw=body.decode("utf-8")[:20000])
    if created:
        match_and_post(payment)
        payment.refresh_from_db()
    return {"id": payment.id, "status": payment.status, "duplicate": not created}


# ---------------------------------------------------------------- statements
COLUMNS = {
    "id": ("id", "reference_no", "transaction_id", "txn_id", "receipt"),
    "date": ("date", "paid_on", "txn_date", "value_date"),
    "amount": ("amount", "credit", "paid"),
    "reference": ("reference", "account", "account_ref", "narration", "description"),
    "phone": ("phone", "msisdn", "payer_phone", "mobile"),
    "name": ("name", "payer", "payer_name"),
    "currency": ("currency",),
}


def parse_statement(file_bytes: bytes) -> list[dict]:
    """A CSV of received payments. The heading names are matched loosely."""
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BusinessRuleError("The file has no heading row")
    headings = {re.sub(r"[^a-z_]", "", h.strip().lower().replace(" ", "_")): h
                for h in reader.fieldnames if h}
    mapping = {}
    for field, names in COLUMNS.items():
        for name in names:
            if name in headings:
                mapping[field] = headings[name]
                break
    missing = [f for f in ("id", "amount") if f not in mapping]
    if missing:
        raise BusinessRuleError("The file needs columns for: " + ", ".join(missing))
    rows = []
    for line, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        rows.append({"line": line, **{f: row.get(h) for f, h in mapping.items()}})
    return rows


def import_statement(provider: str, method: str, file_bytes: bytes, user: User) -> dict:
    """Keep and match every line of a statement. A line already held is skipped,
    so the same statement uploaded twice posts nothing the second time."""
    if method not in (PaymentMethod.MOBILE_MONEY, PaymentMethod.BANK_TRANSFER):
        raise BusinessRuleError("A statement is of mobile money or bank transfers")
    rows = parse_statement(file_bytes)
    result = {"lines": len(rows), "new": 0, "duplicates": 0, "posted": 0, "waiting": 0,
              "errors": []}
    for row in rows:
        try:
            payment, created = record(provider, row, method=method,
                                      raw=json.dumps({k: v for k, v in row.items()
                                                      if k != "line"}))
        except BusinessRuleError as exc:
            result["errors"].append({"line": row["line"], "error": str(exc)})
            continue
        if not created:
            result["duplicates"] += 1
            continue
        result["new"] += 1
        match_and_post(payment)
        payment.refresh_from_db()
        result["posted" if payment.status == InboundStatus.POSTED else "waiting"] += 1
    audit(user, "import_payments", "system", None,
          f"{provider}: {result['new']} new, {result['posted']} posted, "
          f"{result['waiting']} waiting, {result['duplicates']} already held")
    return result


# ---------------------------------------------------------------- matching
def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def _active(loans):
    return [loan for loan in loans if loan.status == LoanStatus.ACTIVE]


def find_loan(payment: InboundPayment) -> tuple[Loan | None, str | None, str]:
    """The loan this payment is for, how it was found, and if none, why not."""
    ref = (payment.account_ref or "").strip().upper()
    compact = re.sub(r"\s", "", ref)
    if compact:
        candidates = [compact]
        number = _digits(compact)
        if number and (compact.isdigit() or compact.startswith("LN")):
            candidates.append(f"LN-{int(number):06d}")
        loan = Loan.objects.filter(loan_no__in=candidates).select_related("borrower").first()
        if loan:
            return loan, "loan_no", ""
        borrower = (Borrower.objects.filter(borrower_no__iexact=compact).first()
                    or Borrower.objects.filter(national_id__iexact=compact).first())
        if borrower:
            how = "borrower_no" if borrower.borrower_no.upper() == compact else "national_id"
            active = _active(borrower.loans.all())
            if len(active) == 1:
                return active[0], how, ""
            return None, None, (f"{borrower.borrower_no} has {len(active)} active loans; "
                                f"choose one")
    phone = _digits(payment.payer_phone)[-9:]
    if len(phone) == 9:
        borrowers = [b for b in Borrower.objects.filter(phone__endswith=phone[-7:])
                     if _digits(b.phone)[-9:] == phone]
        active = [loan for b in borrowers for loan in _active(b.loans.all())]
        if len(active) == 1:
            return active[0], "phone", ""
        if len(active) > 1:
            return None, None, f"The paying phone has {len(active)} active loans; choose one"
    if compact:
        return None, None, f"No loan or borrower matches '{payment.account_ref}'"
    return None, None, "The payer gave no account and the phone matches no borrower"


def _post(payment: InboundPayment, loan: Loan, user: User | None, how: str) -> None:
    """Post to this loan, or leave it waiting with the reason. Never raises for a
    payment that cannot go on this loan: the money arrived, and someone must see it."""
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    payment.loan = loan
    loan_currency = loan.currency or fx.base_currency()
    try:
        if payment.currency and payment.currency != loan_currency:
            raise BusinessRuleError(
                f"Paid in {payment.currency}; {loan.loan_no} is in {loan_currency}")
        with transaction.atomic():
            txn = post_repayment(
                loan, user, payment.amount, payment.paid_on, payment.method,
                f"{payment.provider}:{payment.external_id}"[:100],
                f"{payment.get_method_display()} from {payment.payer_phone or 'unknown'}"
                + (f" ({payment.payer_name})" if payment.payer_name else ""))
    except BusinessRuleError as exc:
        payment.status = InboundStatus.UNMATCHED
        payment.reason = f"{loan.loan_no}: {exc}"[:255]
        payment.save(update_fields=["loan", "status", "reason"])
        return
    payment.transaction = txn
    payment.status = InboundStatus.POSTED
    payment.reason = None
    payment.matched_by = how
    payment.resolved_by = user
    payment.resolved_at = timezone.now()
    payment.save(update_fields=["loan", "transaction", "status", "reason", "matched_by",
                                "resolved_by", "resolved_at"])
    audit(user, "inbound_payment", "loan", loan.id,
          f"{loan.loan_no} {txn.amount} from {payment.provider} {payment.external_id} "
          f"(matched by {how})")
    queue_receipt(loan, txn.amount, txn.id, txn.txn_date)


@transaction.atomic
def match_and_post(payment: InboundPayment) -> InboundPayment:
    loan, how, reason = find_loan(payment)
    if loan is None:
        payment.reason = reason[:255]
        payment.save(update_fields=["reason"])
        return payment
    _post(payment, loan, None, how)
    return payment


def _waiting(payment_id: int) -> InboundPayment:
    payment = InboundPayment.objects.select_for_update().filter(pk=payment_id).first()
    if payment is None:
        raise BusinessRuleError("Payment not found")
    if payment.status != InboundStatus.UNMATCHED:
        raise BusinessRuleError(f"This payment is already {payment.get_status_display().lower()}")
    return payment


@transaction.atomic
def assign(payment_id: int, loan: Loan, user: User) -> InboundPayment:
    """Staff choose the loan. If it still cannot be posted there, the reason says why."""
    payment = _waiting(payment_id)
    _post(payment, loan, user, "staff")
    payment.refresh_from_db()
    if payment.status != InboundStatus.POSTED:
        raise BusinessRuleError(payment.reason or "The payment could not be posted")
    return payment


@transaction.atomic
def reject(payment_id: int, user: User, reason: str) -> InboundPayment:
    """Not ours to post: refunded, sent to the wrong institution, a test. Kept, so
    the statement still reconciles, and never posted."""
    if not (reason or "").strip():
        raise BusinessRuleError("Say why the payment is rejected")
    payment = _waiting(payment_id)
    payment.status = InboundStatus.REJECTED
    payment.reason = reason.strip()[:255]
    payment.resolved_by = user
    payment.resolved_at = timezone.now()
    payment.save(update_fields=["status", "reason", "resolved_by", "resolved_at"])
    audit(user, "reject_inbound_payment", "system", payment.id,
          f"{payment.provider} {payment.external_id} {payment.amount}: {payment.reason}")
    return payment


@transaction.atomic
def retry_waiting() -> dict:
    """Match the waiting payments again: a borrower added since, or a period
    reopened, may let one through. Run nightly, and from the page."""
    posted = 0
    for payment in InboundPayment.objects.filter(status=InboundStatus.UNMATCHED):
        loan, how, reason = find_loan(payment)
        if loan is None:
            continue
        _post(payment, loan, None, how)
        payment.refresh_from_db()
        posted += payment.status == InboundStatus.POSTED
    return {"posted": posted,
            "still_waiting": InboundPayment.objects.filter(status=InboundStatus.UNMATCHED).count()}
