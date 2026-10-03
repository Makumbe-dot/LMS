"""Manual journals: what a person posts because nothing else in the system will.

Salaries, rent, airtime, a bank charge, a new laptop, the opening balances of a
book brought over from another system. Without this the ledger knew only what
the loan book, savings, funding and capital told it, so the income statement
showed lending income as if it were profit and the cash account never paid a
salary.

Three rules carry the weight:

  * Four eyes. Anyone who handles money may prepare a journal; only an
    administrator may post one, so a journal prepared by anyone else always passes
    through a second pair of hands. It mirrors loan approval, where an
    administrator may also approve their own, for the same reason: an expense
    payment is the easiest way for money to leave a lender unnoticed.

  * Control accounts are off limits. 1100, 2000 and the rest are reconciled
    against a sub-ledger, and a journal to one would open a break nothing could
    explain. The refusal names where that posting belongs instead
    (`ledger.CONTROL_ACCOUNTS`).

  * Cash is guarded, as it is everywhere else: a journal that pays out more than
    the bank holds on its date is refused.

A posted journal is never edited or deleted. It is reversed, by a mirror entry
dated when the reversal is made, which leaves both in the record.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    LedgerAccount,
    ManualJournal,
    ManualJournalLine,
    ManualJournalStatus,
    Role,
    User,
)
from . import ledger, periods
from .amortisation import q
from .loans import next_number

ZERO = Decimal("0")
SOURCE = "manual_journal"
REVERSAL_SOURCE = "manual_journal_reversal"


# ---------------------------------------------------------------- validation
def _amount(value, line_no: int, side: str) -> Decimal:
    if value in (None, ""):
        return ZERO
    try:
        amount = q(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        raise BusinessRuleError(f"Line {line_no}: {side} '{value}' is not an amount")
    if amount < 0:
        raise BusinessRuleError(f"Line {line_no}: {side} cannot be negative")
    return amount


def clean_lines(lines) -> list[dict]:
    """Check a journal's lines and return them with their accounts resolved.

    Each line names its account by `account_id` or `account_code` and carries a
    debit or a credit, never both. The whole must balance, touch at least two
    lines, and avoid every control account.
    """
    if not lines or len(lines) < 2:
        raise BusinessRuleError("A journal needs at least two lines: something debited and "
                                "something credited")

    wanted_ids = {line.get("account_id") for line in lines if line.get("account_id")}
    wanted_codes = {str(line.get("account_code")) for line in lines if line.get("account_code")}
    accounts = {a.id: a for a in LedgerAccount.objects.filter(id__in=wanted_ids)}
    by_code = {a.code: a for a in LedgerAccount.objects.filter(code__in=wanted_codes)}

    cleaned = []
    for index, line in enumerate(lines, start=1):
        account = (accounts.get(line.get("account_id")) if line.get("account_id")
                   else by_code.get(str(line.get("account_code") or "")))
        if account is None:
            raise BusinessRuleError(f"Line {index}: choose an account from the chart of accounts")
        if not account.is_active:
            raise BusinessRuleError(f"Line {index}: {account.code} {account.name} is inactive")
        if account.code in ledger.CONTROL_ACCOUNTS:
            raise BusinessRuleError(
                f"Line {index}: {account.code} {account.name} is reconciled against its own "
                f"records and cannot take a manual journal. Post it through "
                f"{ledger.CONTROL_ACCOUNTS[account.code]}.")
        debit = _amount(line.get("debit"), index, "debit")
        credit = _amount(line.get("credit"), index, "credit")
        if (debit > 0) == (credit > 0):
            raise BusinessRuleError(f"Line {index}: give a debit or a credit, not both and not "
                                    f"neither")
        cleaned.append({"account": account, "debit": debit, "credit": credit,
                        "description": (line.get("description") or "").strip() or None})

    debits = sum((line["debit"] for line in cleaned), ZERO)
    credits = sum((line["credit"] for line in cleaned), ZERO)
    if debits != credits:
        raise BusinessRuleError(f"The journal does not balance: debits {debits}, credits "
                                f"{credits}, out by {q(abs(debits - credits))}")
    return cleaned


def _cash_out(lines) -> Decimal:
    """Net amount the lines take out of account 1000; zero when they put money in."""
    bank = ledger.CODES["bank"]
    net = sum(((line["credit"] - line["debit"]) for line in lines
               if line["account"].code == bank), ZERO)
    return q(max(net, ZERO))


def _ledger_lines(journal: ManualJournal, mirror: bool = False):
    rows = []
    for line in journal.lines.select_related("account").order_by("id"):
        debit, credit = (line.credit, line.debit) if mirror else (line.debit, line.credit)
        text = line.description or journal.narration[:200]
        rows.append((line.account.code, debit, credit, f"Reversal: {text}"[:200] if mirror else text))
    return rows


# ---------------------------------------------------------------- lifecycle
@db_transaction.atomic
def prepare(user: User | None, *, entry_date: date | None, narration: str, lines,
            reference: str | None = None, branch_id: int | None = None) -> ManualJournal:
    """Capture a journal for approval. Nothing reaches the ledger yet."""
    narration = (narration or "").strip()
    if not narration:
        raise BusinessRuleError("Say what the journal is for")
    entry_date = entry_date or date.today()
    # Refused now rather than at approval: whoever prepares it should hear that the
    # month is closed, not the administrator a week later.
    periods.assert_open(entry_date, "This journal")
    cleaned = clean_lines(lines)

    journal = ManualJournal.objects.create(
        journal_no=next_number("MJ"), entry_date=entry_date, narration=narration,
        reference=(reference or "").strip() or None, branch_id=branch_id, prepared_by=user,
    )
    ManualJournalLine.objects.bulk_create([
        ManualJournalLine(journal=journal, account=line["account"], debit=line["debit"],
                          credit=line["credit"], description=line["description"])
        for line in cleaned
    ])
    return journal


@db_transaction.atomic
def post(journal: ManualJournal, user: User) -> ManualJournal:
    """Approve a journal and raise its ledger entry."""
    if journal.status != ManualJournalStatus.DRAFT:
        raise BusinessRuleError(f"{journal.journal_no} is {journal.get_status_display().lower()}; "
                                f"only a journal awaiting approval can be posted")
    if not user.has_role(Role.ADMIN):
        # Which is also the four-eyes rule: a journal prepared by anyone else always
        # passes through a second pair of hands before it reaches the ledger.
        raise BusinessRuleError("Only an administrator can post a journal")

    periods.assert_open(journal.entry_date, f"Journal {journal.journal_no}")
    # Re-validated at posting: an account may have been deactivated, or turned into
    # something the rules now refuse, since the journal was prepared.
    cleaned = clean_lines([{"account_id": line.account_id, "debit": line.debit,
                            "credit": line.credit, "description": line.description}
                           for line in journal.lines.all()])
    outflow = _cash_out(cleaned)
    if outflow > 0:
        from .funding import assert_cash

        assert_cash(outflow, f"Journal {journal.journal_no}", on=journal.entry_date)

    entry = ledger.post_manual_entry(
        _ledger_lines(journal), journal.entry_date,
        f"{journal.journal_no}: {journal.narration}", SOURCE,
        branch_id=journal.branch_id, posted_by=user, strict=True)
    journal.journal_entry = entry
    journal.status = ManualJournalStatus.POSTED
    journal.posted_by = user
    journal.posted_at = timezone.now()
    journal.save(update_fields=["journal_entry", "status", "posted_by", "posted_at"])
    return journal


@db_transaction.atomic
def reject(journal: ManualJournal, user: User, reason: str) -> ManualJournal:
    if journal.status != ManualJournalStatus.DRAFT:
        raise BusinessRuleError(f"{journal.journal_no} is {journal.get_status_display().lower()}; "
                                f"only a journal awaiting approval can be rejected")
    journal.status = ManualJournalStatus.REJECTED
    journal.rejected_reason = reason
    journal.posted_by = user
    journal.posted_at = timezone.now()
    journal.save(update_fields=["status", "rejected_reason", "posted_by", "posted_at"])
    return journal


def withdraw(journal: ManualJournal, user: User) -> None:
    """Delete a draft. Its preparer or an administrator; never once posted."""
    if journal.status != ManualJournalStatus.DRAFT:
        raise BusinessRuleError(f"{journal.journal_no} is {journal.get_status_display().lower()}; "
                                f"only a draft can be withdrawn. Reverse a posted journal instead.")
    if journal.prepared_by_id != user.id and not user.has_role(Role.ADMIN):
        raise BusinessRuleError("Only whoever prepared a journal, or an administrator, can "
                                "withdraw it")
    journal.delete()


@db_transaction.atomic
def reverse(journal: ManualJournal, user: User, reason: str,
            on: date | None = None) -> ManualJournal:
    """Undo a posted journal with its mirror, dated when the reversal is made.

    Dated today rather than on the original's date, so a reversal never needs a
    closed month reopened and the record shows when the correction was made.
    """
    if journal.status != ManualJournalStatus.POSTED:
        raise BusinessRuleError(f"{journal.journal_no} is {journal.get_status_display().lower()}; "
                                f"only a posted journal can be reversed")
    on = on or date.today()
    if on < journal.entry_date:
        raise BusinessRuleError("A reversal cannot be dated before the journal it reverses")
    periods.assert_open(on, f"The reversal of {journal.journal_no}")

    mirrored = _ledger_lines(journal, mirror=True)
    outflow = q(max(sum((c - d for code, d, c, _ in mirrored if code == ledger.CODES["bank"]),
                        ZERO), ZERO))
    if outflow > 0:
        from .funding import assert_cash

        assert_cash(outflow, f"The reversal of {journal.journal_no}", on=on)

    entry = ledger.post_manual_entry(
        mirrored, on, f"Reversal of {journal.journal_no}: {reason}", REVERSAL_SOURCE,
        branch_id=journal.branch_id, posted_by=user, strict=True)
    journal.reversal_entry = entry
    journal.status = ManualJournalStatus.REVERSED
    journal.reversed_by = user
    journal.reversed_at = timezone.now()
    journal.reversal_reason = reason
    journal.save(update_fields=["reversal_entry", "status", "reversed_by", "reversed_at",
                                "reversal_reason"])
    return journal


# ---------------------------------------------------------------- rebuild
def repost_journals() -> dict:
    """Re-raise the entries of journals that have lost them, for `ledger.backfill`.

    A manual journal stands behind no transaction, so the sweeps in backfill cannot
    find it; without this, a JournalEntry wipe followed by Rebuild would silently
    drop every salary and rent payment from the books. Runs inside
    allow_closed_posting for the reason backfill does: the journal already happened.
    """
    with periods.allow_closed_posting("manual journal repost"):
        reposted = 0
        for journal in ManualJournal.objects.filter(
                status__in=[ManualJournalStatus.POSTED, ManualJournalStatus.REVERSED],
                journal_entry__isnull=True):
            journal.journal_entry = ledger.post_manual_entry(
                _ledger_lines(journal), journal.entry_date,
                f"{journal.journal_no}: {journal.narration} (re-posted)", SOURCE,
                branch_id=journal.branch_id, posted_by=journal.posted_by, strict=False)
            if journal.journal_entry:
                journal.save(update_fields=["journal_entry"])
                reposted += 1
        for journal in ManualJournal.objects.filter(status=ManualJournalStatus.REVERSED,
                                                    reversal_entry__isnull=True):
            on = (journal.reversed_at.date() if journal.reversed_at else journal.entry_date)
            journal.reversal_entry = ledger.post_manual_entry(
                _ledger_lines(journal, mirror=True), on,
                f"Reversal of {journal.journal_no} (re-posted)", REVERSAL_SOURCE,
                branch_id=journal.branch_id, posted_by=journal.reversed_by, strict=False)
            if journal.reversal_entry:
                journal.save(update_fields=["reversal_entry"])
                reposted += 1
    return {"reposted": reposted}


def awaiting_approval(start: date, end: date) -> int:
    """Drafts dated inside a window: a month closed over them can never post them."""
    return ManualJournal.objects.filter(status=ManualJournalStatus.DRAFT,
                                        entry_date__range=(start, end)).count()
