"""Turn two-factor sign-in off for one user, from the server.

    python manage.py reset_mfa admin

For the administrator who has lost their phone and is the only one who could
reset it through the Users page. Anyone who can run this already has the
database, so it is not a weaker door than the one it replaces. It is audited
like the Users-page reset.
"""
from django.core.management.base import BaseCommand, CommandError

from core.audit import audit
from core.models import User


class Command(BaseCommand):
    help = "Turn two-factor sign-in off for a user who has lost their authenticator."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **options):
        user = User.objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No user called {username}")
        if not user.mfa_enabled and not user.mfa_secret:
            self.stdout.write(f"{username} does not use two-factor sign-in; nothing to do.")
            return
        user.mfa_enabled = False
        user.mfa_secret = None
        user.mfa_last_step = None
        user.save(update_fields=["mfa_enabled", "mfa_secret", "mfa_last_step"])
        audit(None, "update", "user", user.id, f"['two-factor reset'] from the server for {username}")
        self.stdout.write(self.style.SUCCESS(
            f"Two-factor sign-in is off for {username}. They sign in with their password and "
            f"can set it up again on My account."))
