"""Signal wiring.

The ledger hangs off Transaction rather than off each service that posts one.
That is deliberate: "every money movement is accounted for" is an invariant that
should not depend on a future caller remembering to add a line. Posting is
idempotent per transaction, and runs inside the caller's atomic block.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import SavingsTransaction, Transaction


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
