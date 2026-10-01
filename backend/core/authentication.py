"""JWT authentication that honours revocation.

simplejwt's own `JWTAuthentication` accepts any token whose signature verifies and
whose user row is active. That leaves two holes this closes:

  * A token issued before the user's sessions were revoked keeps working until it
    expires. The `tv` claim is compared against `User.token_version`, so bumping
    the version rejects every outstanding token for that user on the next request.

  * A refresh token presented as if it were an access token would authenticate,
    because both are signed with the same key and carry the same claims. The token
    type is checked explicitly.

Neither check costs a query: `get_user` already loads the row.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken

from .services.tokens import VERSION_CLAIM


class RevocableJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        if validated_token.get("token_type") not in (None, "access"):
            # A refresh token is a credential for /api/auth/refresh, not a bearer
            # token for the API.
            raise InvalidToken("Use an access token, not a refresh token.")

        user = super().get_user(validated_token)

        presented = validated_token.get(VERSION_CLAIM)
        current = getattr(user, "token_version", 0)
        # A token minted before this claim existed has no version. Treating it as
        # stale would sign everyone out on deploy, so `None` is accepted and only a
        # MISMATCH is rejected.
        if presented is not None and presented != current:
            raise AuthenticationFailed(
                "This session has been signed out. Please sign in again.", code="session_revoked")
        return user
