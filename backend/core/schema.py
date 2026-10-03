"""Telling drf-spectacular about this project's own authentication.

Without this it emits "could not resolve authenticator" for every endpoint and
leaves the generated schema with no security scheme at all — so a generated client
would not know to send a bearer token, which is the first thing it needs to know.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class RevocableJWTScheme(OpenApiAuthenticationExtension):
    target_class = "core.authentication.RevocableJWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Obtain a pair from `POST /api/auth/login`, then send the access token as "
                "`Authorization: Bearer <access_token>`.\n\n"
                "Access tokens last 30 minutes. Exchange the refresh token at "
                "`POST /api/auth/refresh` for a new pair — a refresh token is good for "
                "exactly ONE use, so store the one you get back.\n\n"
                "A 401 after a successful refresh means the session is over: the account "
                "was disabled, the role changed, or someone signed out everywhere."
            ),
        }
