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

from .models import (
    CapitalTransaction,
    FacilityTransaction,
    JournalEntry,
    SavingsTransaction,
    Transaction,
)


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


# ---------------------------------------------------------------- the till guard
# Here rather than in each service for the reason the ledger hook is: every way of
# taking or paying out cash writes one of these two rows, and the next one someone
# adds should not have to remember. Does nothing unless the setting is on.
@receiver(pre_save, sender=Transaction, dispatch_uid="core.guard_transaction_till")
def guard_transaction_till(sender, instance, raw=False, **kwargs):
    if raw or not instance._state.adding:
        return
    from .services.tills import assert_till_open

    assert_till_open(instance.posted_by_id, instance.method,
                     f"This {instance.get_txn_type_display().lower()}",
                     instance.loan.currency)


@receiver(pre_save, sender=SavingsTransaction, dispatch_uid="core.guard_savings_till")
def guard_savings_till(sender, instance, raw=False, **kwargs):
    if raw or not instance._state.adding:
        return
    from .services.tills import assert_till_open

    assert_till_open(instance.posted_by_id, instance.method,
                     f"This savings {instance.get_txn_type_display().lower()}",
                     instance.account.currency)


# ---------------------------------------------------------------- exchange rates
# Every posting on a foreign-currency loan carries the spot rate of its date and
# the loan's booked rate at the time, so the ledger can convert it now and again
# on a Rebuild. Here, for the reason the ledger hook is: the next service that
# writes a Transaction should not have to remember. A base-currency loan gets 1.
# Savings and facility movements are stamped the same way, against the account's
# or the facility's booked rate.
@receiver(pre_save, sender=Transaction, dispatch_uid="core.stamp_transaction_rates")
def stamp_transaction_rates(sender, instance, raw=False, **kwargs):
    if raw or not instance._state.adding:
        return
    from .services import fx

    loan = instance.loan
    if instance.book_rate is None:
        instance.book_rate = loan.fx_rate or fx.ONE
    if instance.fx_rate is None:
        instance.fx_rate = (fx.rate_on(loan.currency, instance.txn_date)
                            if fx.is_foreign(loan) else fx.ONE)


def _stamp_rates(instance, holder, model) -> None:
    """The same stamp for a savings or facility movement. The booked rate is read
    from the row rather than the instance in hand, which a revaluation run since
    it was loaded may have left behind."""
    from .services import fx

    if not fx.is_foreign(holder):
        instance.book_rate = instance.book_rate or fx.ONE
        instance.fx_rate = instance.fx_rate or fx.ONE
        return
    if instance.book_rate is None:
        instance.book_rate = (model.objects.filter(pk=holder.pk)
                              .values_list("fx_rate", flat=True).first() or fx.ONE)
    if instance.fx_rate is None:
        instance.fx_rate = fx.rate_on(holder.currency, instance.txn_date)


@receiver(pre_save, sender=SavingsTransaction, dispatch_uid="core.stamp_savings_rates")
def stamp_savings_rates(sender, instance, raw=False, **kwargs):
    if raw or not instance._state.adding:
        return
    from .models import SavingsAccount

    _stamp_rates(instance, instance.account, SavingsAccount)


@receiver(pre_save, sender=FacilityTransaction, dispatch_uid="core.stamp_facility_rates")
def stamp_facility_rates(sender, instance, raw=False, **kwargs):
    if raw or not instance._state.adding:
        return
    from .models import FundingFacility

    _stamp_rates(instance, instance.facility, FundingFacility)


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


@receiver(post_save, sender=FacilityTransaction, dispatch_uid="core.post_facility_to_ledger")
def post_facility_to_ledger(sender, instance, created, **kwargs):
    if not created:
        return
    from .services.ledger import post_facility_transaction

    post_facility_transaction(instance)


@receiver(post_save, sender=CapitalTransaction, dispatch_uid="core.post_capital_to_ledger")
def post_capital_to_ledger(sender, instance, created, **kwargs):
    if not created:
        return
    from .services.ledger import post_capital_transaction

    post_capital_transaction(instance)
