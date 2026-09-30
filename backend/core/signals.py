"""Signal wiring.

The ledger hangs off Transaction rather than off each service that posts one.
That is deliberate: "every money movement is accounted for" is an invariant that
should not depend on a future caller remembering to add a line. Posting is
idempotent per transaction, and runs inside the caller's atomic block.

The period guards come first in this file on purpose: they are pre_save, so they
refuse a posting into a closed month before the row reaches SQL Server and before
any post_save hook below can run. Anything else that wants a receiver here belongs
underneath them.
"""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import JournalEntry, SavingsTransaction, Transaction


# ---------------------------------------------------------------- period guards
def _guard_posting_date(instance, raw: bool, on, what: str) -> None:
    """Refuse a new dated posting that falls in a closed month.

    pre_save rather than Model.clean(), because nothing in this codebase calls
    full_clean() and a guard a caller can skip by using objects.create() is not a
    guard. pre_save rather than post_save, because a post_save refusal from a
    caller without an atomic block would leave the row written.

    Skipped for `raw` saves so loaddata can restore a book that has closed months,
    and for updates (`_state.adding` false) so an existing posting can still be
    marked reversed. bulk_create bypasses pre_save entirely; see the note in
    services/periods.py.
    """
    if raw or not instance._state.adding or on is None:
        return
    from .services.periods import assert_open

    assert_open(on, what)


@receiver(pre_save, sender=Transaction, dispatch_uid="core.guard_transaction_period")
def guard_transaction_period(sender, instance, raw=False, **kwargs):
    _guard_posting_date(instance, raw, instance.txn_date,
                        f"This {instance.get_txn_type_display().lower()}")


@receiver(pre_save, sender=SavingsTransaction, dispatch_uid="core.guard_savings_period")
def guard_savings_period(sender, instance, raw=False, **kwargs):
    _guard_posting_date(instance, raw, instance.txn_date,
                        f"This savings {instance.get_txn_type_display().lower()}")


@receiver(pre_save, sender=JournalEntry, dispatch_uid="core.guard_journal_period")
def guard_journal_period(sender, instance, raw=False, **kwargs):
    # The backstop for entries raised directly rather than off a Transaction: the
    # provision run, and anything else that posts without moving cash.
    _guard_posting_date(instance, raw, instance.entry_date, "This journal entry")


# ---------------------------------------------------------------- the ledger
@receiver(post_save, sender=Transaction, dispatch_uid="core.post_transaction_to_ledger")
def post_transaction_to_ledger(sender, instance, created, **kwargs):
    if not created:
        return
    from .services.ledger import post_transaction

    post_transaction(instance)


@receiver(post_save, sender=SavingsTransaction, dispatch_uid="core.post_savings_to_ledger")
def post_savings_to_ledger(sender, instance, created, **kwargs):
    if not created:
        return
    from .services.ledger import post_savings_transaction

    post_savings_transaction(instance)
