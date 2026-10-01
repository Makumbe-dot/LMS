"""Forget revocations for refresh tokens that have expired anyway.

    python manage.py prune_tokens

A revoked-token row only matters until the token it names would have expired on
its own. Nothing breaks if this never runs — the table just grows by one row per
sign-out and per refresh — so it belongs in the nightly batch rather than anywhere
load-bearing.
"""
from django.core.management.base import BaseCommand

from core.services.tokens import prune


class Command(BaseCommand):
    help = "Delete revoked-token records whose tokens have already expired."

    def handle(self, *args, **options):
        deleted = prune()
        self.stdout.write(self.style.SUCCESS(
            f"Pruned {deleted} expired revocation(s)."))
