"""Issuing, renewing and revoking sessions.

A JWT is verified by its signature, so the server holds nothing it can call back.
That is the whole appeal — no database hit to authenticate — and also the whole
problem: disabling a user does not stop the token in their browser. Two mechanisms
cover the two things an operator actually wants:

  * `User.token_version`, carried as a claim and compared on every request. Bumping
    it ends EVERY session that user has, immediately, access tokens included. It
    costs no extra query, because the authentication layer already loads the user
    row to attach `request.user`.

  * `RevokedToken`, keyed on a refresh token's `jti`. Ends ONE session — signing
    out on this laptop without signing out the phone — and retires the old token
    when a refresh rotates it.

Access tokens are deliberately NOT checked against RevokedToken. Doing so would
mean a query per request to undo the one property JWTs are chosen for, and it
would still not be instant. Their thirty-minute lifetime is the bound on how long
a single revoked device keeps working; `token_version` is the escape hatch when
that is not good enough.
"""
import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db.models import F
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from ..models import RevokedToken, User

log = logging.getLogger(__name__)

VERSION_CLAIM = "tv"


def issue(user: User) -> dict:
    """A fresh token pair for this user, stamped with their current token version."""
    token = RefreshToken.for_user(user)
    token["role"] = user.role
    token["sub"] = user.username
    token[VERSION_CLAIM] = user.token_version
    # The access token is derived from the refresh token, so claims set above are
    # already on both.
    return {
        "access_token": str(token.access_token),
        "refresh_token": str(token),
        "token_type": "bearer",
    }


def is_revoked(jti: str | None) -> bool:
    return bool(jti) and RevokedToken.objects.filter(jti=jti).exists()


def revoke(token: RefreshToken, user: User, reason: str) -> bool:
    """Retire one refresh token. Idempotent, and never raises on a malformed one."""
    jti = token.get("jti")
    if not jti:
        return False
    expires = token.get("exp")
    expires_at = (datetime.fromtimestamp(expires, tz=dt_timezone.utc) if expires
                  else datetime.now(tz=dt_timezone.utc))
    _, created = RevokedToken.objects.get_or_create(
        jti=jti, defaults={"user": user, "expires_at": expires_at, "reason": reason[:80]})
    return created


def revoke_all(user: User, reason: str) -> int:
    """End every session this user has, immediately.

    Returns the new token version. Nothing is written per token: the version claim
    on an already-issued token simply stops matching.
    """
    User.objects.filter(pk=user.pk).update(token_version=F("token_version") + 1)
    user.refresh_from_db(fields=["token_version"])
    log.info("Revoked every session for %s: %s", user.username, reason)
    return user.token_version


def parse_refresh(raw: str) -> RefreshToken:
    """A verified refresh token, or TokenError."""
    return RefreshToken(raw)


def user_for(token) -> User | None:
    return User.objects.filter(pk=token.get(settings.SIMPLE_JWT["USER_ID_CLAIM"])).first()


def prune(now=None) -> int:
    """Forget revocations for tokens that have expired anyway."""
    from django.utils import timezone

    cutoff = now or timezone.now()
    deleted, _ = RevokedToken.objects.filter(expires_at__lt=cutoff).delete()
    return deleted


__all__ = ["VERSION_CLAIM", "TokenError", "issue", "is_revoked", "parse_refresh", "prune",
           "revoke", "revoke_all", "user_for"]
