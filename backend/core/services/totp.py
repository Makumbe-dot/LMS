"""Time-based one-time codes (RFC 6238), the six digits an authenticator app shows.

Written out rather than pulled in as a dependency: it is twenty lines of HMAC,
and every authenticator app - Google, Microsoft, Authy, 1Password - speaks this
exact variant (SHA-1, six digits, thirty-second steps).

A code is accepted for the step it belongs to and one step either side, so a
phone whose clock is half a minute out still works. Each step can be used once:
the caller records the last step accepted and refuses it, or any earlier one,
again - a code read over someone's shoulder is worthless after it has been typed.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1


def new_secret() -> str:
    """160 random bits, base32 - the form authenticator apps take as a setup key."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret: str, code: str, last_step: int | None = None,
           now: float | None = None) -> int | None:
    """The step the code belongs to, or None if it is wrong, stale or already used."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """The otpauth:// link an authenticator app reads from a QR code."""
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")
