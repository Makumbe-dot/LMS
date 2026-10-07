"""Paying a disbursed loan out to the borrower: mobile money, or a bank file.

A loan disbursed by bank transfer or mobile money gets a Payout for the amount the
borrower actually receives (the principal less fees deducted at disbursement).

* Mobile money goes through a provider's API, configured in settings rather than
  coded (PAYOUT_HTTP, like the SMS gateway): EcoCash, OneMoney or an aggregator
  each publish a "send money" call that takes a number, an amount and a reference.
  Until one is configured the payout is only logged, and says so.
* Bank payouts are gathered into a CSV for the bank's bulk-payment upload; the file
  marks them sent. Staff mark each paid (with the bank's reference) or failed.

Nothing here touches the ledger: the disbursement was posted when the loan was
disbursed. A failed payout is put right by paying another way.
"""
import csv
import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import Loan, OrganisationSetting, PaymentMethod, Payout, PayoutStatus

log = logging.getLogger(__name__)

PAID_OUT = (PaymentMethod.BANK_TRANSFER, PaymentMethod.MOBILE_MONEY)


def create_for(loan: Loan, method: str, user=None) -> Payout | None:
    """The payout for a loan just disbursed, when it is paid by bank or wallet."""
    if method not in PAID_OUT:
        return None
    borrower = loan.borrower
    net = Decimal(loan.principal) - Decimal(loan.upfront_fees or 0)
    if method == PaymentMethod.MOBILE_MONEY:
        account, bank, branch = (borrower.mobile_wallet or borrower.phone), "", ""
        name = borrower.full_name
    else:
        account = borrower.bank_account_no
        bank, branch = borrower.bank_name, borrower.bank_branch
        name = borrower.bank_account_name or borrower.full_name
    return Payout.objects.create(
        loan=loan, amount=net, currency=loan.currency or OrganisationSetting.load().currency,
        method=method, payee_name=name[:160], account=(account or "")[:60], bank_name=bank,
        bank_branch=branch, updated_by=user,
        error="" if account else "No account on file: add the borrower's bank details, then pay.")


# ---------------------------------------------------------------- mobile money
def _config() -> dict:
    return dict(getattr(settings, "PAYOUT_HTTP", {}) or {})


def mobile_money_live() -> bool:
    return bool(_config().get("url"))


def _send_one(payout: Payout, config: dict) -> tuple[bool, str, str]:
    """(ok, provider reference, error) for one wallet payout."""
    from .gateways import _dig, international

    to = international(payout.account)
    if not to:
        return False, "", f"{payout.account!r} is not a mobile number"
    reference = f"{payout.loan.loan_no}-P{payout.id}"
    if not config.get("url"):
        log.info("[payout to %s] %s %s %s", to, payout.currency, payout.amount, reference)
        return True, f"console-{payout.id}", ""
    number = to.lstrip("+") if config.get("number_format", "plain") == "plain" else to
    payload = {config.get("to_field", "msisdn"): number,
               config.get("amount_field", "amount"): f"{payout.amount:.2f}",
               config.get("reference_field", "reference"): reference,
               **(config.get("extra") or {})}
    as_json = (config.get("format") or "json").lower() == "json"
    request = urllib.request.Request(
        config["url"], method="POST",
        data=json.dumps(payload).encode() if as_json else urllib.parse.urlencode(payload).encode(),
        headers={"Content-Type": "application/json" if as_json else "application/x-www-form-urlencoded",
                 "Accept": "application/json", **(config.get("headers") or {})})
    try:
        with urllib.request.urlopen(request, timeout=config.get("timeout", 30)) as response:
            raw = response.read().decode("utf-8", "replace")
            return True, _dig(raw, config.get("id_path")) or reference, ""
    except urllib.error.HTTPError as exc:
        return False, "", f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}"
    except Exception as exc:  # the provider's trouble; staff decide what next
        return False, "", f"{type(exc).__name__}: {exc}"


def send_mobile_money(ids: list[int] | None, user) -> dict:
    """Hand pending wallet payouts to the provider, one at a time."""
    config = _config()
    queue = Payout.objects.select_related("loan").filter(
        status=PayoutStatus.PENDING, method=PaymentMethod.MOBILE_MONEY)
    if ids:
        queue = queue.filter(id__in=ids)
    sent = failed = 0
    for payout in queue:
        ok, ref, error = _send_one(payout, config)
        payout.updated_by = user
        if ok:
            payout.status, payout.sent_at, payout.provider_reference, payout.error = (
                PayoutStatus.SENT, timezone.now(), ref, "")
            if not config.get("url"):
                payout.error = "Logged only: no mobile-money provider is configured (PAYOUT_HTTP_URL)."
            sent += 1
        else:
            payout.status, payout.error = PayoutStatus.FAILED, error
            failed += 1
        payout.save()
    return {"sent": sent, "failed": failed, "live": bool(config.get("url"))}


# ---------------------------------------------------------------- bank file
BANK_COLUMNS = ["Beneficiary name", "Bank", "Branch", "Account number", "Amount", "Currency",
                "Reference", "Loan"]


@transaction.atomic
def bank_file(ids: list[int] | None, user) -> tuple[str, bytes, int]:
    """A CSV of pending bank payouts for the bank's bulk upload. Marks them sent,
    so the same payment cannot go into two files."""
    queue = list(Payout.objects.select_for_update().select_related("loan").filter(
        status=PayoutStatus.PENDING, method=PaymentMethod.BANK_TRANSFER).exclude(account=""))
    if ids:
        queue = [p for p in queue if p.id in set(ids)]
    if not queue:
        raise BusinessRuleError("No bank payouts with account details are waiting")
    batch = f"BANK-{timezone.localtime():%Y%m%d-%H%M%S}"
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(BANK_COLUMNS)
    for p in queue:
        writer.writerow([p.payee_name, p.bank_name, p.bank_branch, p.account, f"{p.amount:.2f}",
                         p.currency, f"{p.loan.loan_no}-P{p.id}", p.loan.loan_no])
        p.status, p.batch, p.sent_at, p.updated_by = PayoutStatus.SENT, batch, timezone.now(), user
        p.save(update_fields=["status", "batch", "sent_at", "updated_by"])
    return f"{batch}.csv", out.getvalue().encode("utf-8-sig"), len(queue)


# ---------------------------------------------------------------- outcomes
def mark_paid(payout: Payout, user, reference: str = "") -> Payout:
    if payout.status == PayoutStatus.PAID:
        raise BusinessRuleError("Already marked paid")
    payout.status, payout.paid_at, payout.updated_by = PayoutStatus.PAID, timezone.now(), user
    if reference:
        payout.provider_reference = reference[:120]
    payout.error = ""
    payout.save()
    return payout


def mark_failed(payout: Payout, user, reason: str) -> Payout:
    if payout.status == PayoutStatus.PAID:
        raise BusinessRuleError("It is marked paid; a paid payout cannot fail")
    if not (reason or "").strip():
        raise BusinessRuleError("Say what went wrong")
    payout.status, payout.error, payout.updated_by = PayoutStatus.FAILED, reason.strip(), user
    payout.save()
    return payout


def retry(payout: Payout, user) -> Payout:
    """Put a failed payout back in the queue, e.g. after fixing the account number."""
    if payout.status != PayoutStatus.FAILED:
        raise BusinessRuleError("Only a failed payout can be tried again")
    borrower = payout.loan.borrower
    if payout.method == PaymentMethod.MOBILE_MONEY:
        payout.account = (borrower.mobile_wallet or borrower.phone)[:60]
    else:
        payout.account = borrower.bank_account_no[:60]
        payout.bank_name, payout.bank_branch = borrower.bank_name, borrower.bank_branch
    payout.status, payout.error, payout.updated_by = PayoutStatus.PENDING, "", user
    payout.save()
    return payout
