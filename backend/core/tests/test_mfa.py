"""Two-factor sign-in with an authenticator app.

The codes are checked against the RFC's own test vectors, because a TOTP that is
subtly wrong still produces six digits - it just never agrees with anyone's phone.
The sign-in tests are about the gaps a second factor exists to close: a code
works once, guessing codes counts towards the lockout, ending sessions also ends
a half-finished sign-in, and turning it off takes both factors.
"""
import base64
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase
from rest_framework.test import APIClient

from core.models import AuditLog, User
from core.services import totp

from .test_components import LedgerBase

# RFC 6238 appendix B, SHA-1: the 20-byte ASCII seed "12345678901234567890".
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


class TotpTests(SimpleTestCase):
    def test_the_rfc_test_vectors(self):
        for unix_time, eight_digits in [(59, "94287082"), (1111111109, "07081804"),
                                        (1111111111, "14050471"), (1234567890, "89005924"),
                                        (2000000000, "69279037")]:
            step = totp.current_step(unix_time)
            self.assertEqual(totp.code_at(RFC_SECRET, step), eight_digits[-6:], unix_time)

    def test_a_phone_half_a_minute_out_still_works(self):
        now = 1_700_000_000
        early = totp.code_at(RFC_SECRET, totp.current_step(now) - 1)
        self.assertIsNotNone(totp.verify(RFC_SECRET, early, now=now))

    def test_a_code_works_once(self):
        now = 1_700_000_000
        code = totp.code_at(RFC_SECRET, totp.current_step(now))
        step = totp.verify(RFC_SECRET, code, now=now)
        self.assertIsNotNone(step)
        self.assertIsNone(totp.verify(RFC_SECRET, code, last_step=step, now=now))

    def test_spaces_in_a_typed_code_are_forgiven_and_letters_are_not_a_code(self):
        now = 1_700_000_000
        code = totp.code_at(RFC_SECRET, totp.current_step(now))
        self.assertIsNotNone(totp.verify(RFC_SECRET, f"{code[:3]} {code[3:]}", now=now))
        self.assertIsNone(totp.verify(RFC_SECRET, "abcdef", now=now))

    def test_the_setup_link_is_what_authenticator_apps_read(self):
        uri = totp.provisioning_uri("ABC", "admin", "Simba Finance")
        self.assertTrue(uri.startswith("otpauth://totp/Simba%20Finance%3Aadmin?secret=ABC"))


class TwoFactorSignInTests(LedgerBase):
    def enrol(self, client, username):
        secret = client.post("/api/auth/mfa/setup").json()["secret"]
        code = totp.code_at(secret, totp.current_step())
        response = client.post("/api/auth/mfa/enable", {"code": code}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["mfa_enabled"])
        return secret

    def next_code(self, username):
        """A code the server has not seen: the step after the last one accepted."""
        user = User.objects.get(username=username)
        return totp.code_at(user.mfa_secret, user.mfa_last_step + 1)

    def password_step(self, username="officer", password="officer123"):
        response = APIClient().post("/api/auth/login", {"username": username,
                                                        "password": password}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_with_two_factor_on_a_password_alone_does_not_sign_in(self):
        self.enrol(self.officer, "officer")
        body = self.password_step()
        self.assertTrue(body["mfa_required"])
        self.assertNotIn("access_token", body)

        response = APIClient().post("/api/auth/login/verify", {
            "mfa_token": body["mfa_token"], "code": self.next_code("officer")}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access_token", response.json())
        self.assertTrue(AuditLog.objects.filter(action="login",
                                                detail="with an authenticator code").exists())

    def test_the_same_code_cannot_sign_in_twice(self):
        self.enrol(self.officer, "officer")
        code = self.next_code("officer")
        first = self.password_step()["mfa_token"]
        self.assertEqual(APIClient().post("/api/auth/login/verify", {
            "mfa_token": first, "code": code}, format="json").status_code, 200)
        second = self.password_step()["mfa_token"]
        self.assertEqual(APIClient().post("/api/auth/login/verify", {
            "mfa_token": second, "code": code}, format="json").status_code, 401)

    def test_guessing_codes_locks_the_account_like_guessing_passwords(self):
        self.enrol(self.officer, "officer")
        token = self.password_step()["mfa_token"]
        for _ in range(settings.LOGIN_MAX_ATTEMPTS):
            APIClient().post("/api/auth/login/verify", {"mfa_token": token, "code": "000000"},
                             format="json")
        self.assertTrue(User.objects.get(username="officer").is_locked)

    def test_a_tampered_or_stale_token_is_refused(self):
        self.enrol(self.officer, "officer")
        token = self.password_step()["mfa_token"]
        response = APIClient().post("/api/auth/login/verify", {
            "mfa_token": token[:-2] + "xx", "code": self.next_code("officer")}, format="json")
        self.assertEqual(response.status_code, 401)

        # Ending every session between the two steps ends the half-finished one too.
        self.officer.post("/api/auth/sign-out-everywhere")
        response = APIClient().post("/api/auth/login/verify", {
            "mfa_token": token, "code": self.next_code("officer")}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_an_abandoned_setup_changes_nothing(self):
        self.officer.post("/api/auth/mfa/setup")
        body = APIClient().post("/api/auth/login", {"username": "officer",
                                                    "password": "officer123"},
                                format="json").json()
        self.assertIn("access_token", body)

    def test_turning_it_off_takes_the_password_and_a_code(self):
        self.enrol(self.officer, "officer")
        response = self.officer.post("/api/auth/mfa/disable", {
            "password": "wrong-password", "code": self.next_code("officer")}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.officer.post("/api/auth/mfa/disable", {
            "password": "officer123", "code": "000000"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.officer.post("/api/auth/mfa/disable", {
            "password": "officer123", "code": self.next_code("officer")}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["mfa_enabled"])

    def test_an_administrator_resets_it_for_a_lost_phone(self):
        self.enrol(self.officer, "officer")
        officer = User.objects.get(username="officer")
        response = self.admin.patch(f"/api/users/{officer.id}", {"reset_mfa": True},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access_token", self.password_step())

    def test_the_server_command_resets_the_last_administrator(self):
        self.enrol(self.admin, "admin")
        out = StringIO()
        call_command("reset_mfa", "admin", stdout=out)
        self.assertIn("off for admin", out.getvalue())
        self.assertIn("access_token", self.password_step("admin", "admin123"))
