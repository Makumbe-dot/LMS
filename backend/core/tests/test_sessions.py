"""Sessions: renewing them, ending them, and making sure ending one works.

Before this, login handed out a refresh token that nothing could use, there was no
way to sign out, and an access token stayed valid for eight hours no matter what —
so disabling an employee's account did not stop them posting.

A signed JWT cannot be recalled, so there are two mechanisms and they answer
different questions. `RevokedToken` ends ONE session by its jti. `token_version`
ends EVERY session for a user at once, immediately, access tokens included. The
tests below are mostly about the boundary between them.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AuditLog, RevokedToken, Role, User
from core.services import tokens

from .test_components import LedgerBase


class SessionBase(LedgerBase):
    def sign_in(self, username="teller", password="teller123"):
        client = APIClient()
        response = client.post("/api/auth/login",
                               {"username": username, "password": password}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        client.credentials(HTTP_AUTHORIZATION="Bearer " + body["access_token"])
        return client, body

    def as_token(self, client, access):
        client.credentials(HTTP_AUTHORIZATION="Bearer " + access)
        return client


class RefreshTests(SessionBase):
    def test_login_hands_out_a_pair_that_both_work(self):
        client, body = self.sign_in()
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertEqual(client.get("/api/auth/me").status_code, 200)

        fresh = APIClient().post("/api/auth/refresh",
                                 {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(fresh.status_code, 200, fresh.content)
        self.assertEqual(fresh.json()["user"]["username"], "teller")
        self.assertEqual(
            self.as_token(APIClient(), fresh.json()["access_token"]).get("/api/auth/me")
            .status_code, 200)

    def test_a_refresh_token_works_only_once(self):
        """Rotation. A replayed refresh token is a stale client or a stolen one."""
        _client, body = self.sign_in()
        first = APIClient().post("/api/auth/refresh",
                                 {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(first.status_code, 200)

        replay = APIClient().post("/api/auth/refresh",
                                  {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(replay.status_code, 401, replay.content)
        self.assertIn("signed out", replay.json()["detail"])

        # And the one it was exchanged for still works.
        again = APIClient().post("/api/auth/refresh",
                                 {"refresh_token": first.json()["refresh_token"]}, format="json")
        self.assertEqual(again.status_code, 200)

    def test_refreshing_needs_no_access_token(self):
        """The whole point: callable once the access token has expired."""
        _client, body = self.sign_in()
        anonymous = APIClient()  # no Authorization header at all
        response = anonymous.post("/api/auth/refresh",
                                  {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def test_rubbish_and_missing_tokens_are_refused_not_crashed_on(self):
        self.assertEqual(APIClient().post("/api/auth/refresh",
                                          {"refresh_token": "not-a-jwt"},
                                          format="json").status_code, 401)
        self.assertEqual(APIClient().post("/api/auth/refresh", {}, format="json").status_code,
                         400)

    def test_an_access_token_cannot_be_presented_as_a_refresh_token_or_vice_versa(self):
        client, body = self.sign_in()
        # A refresh token is not a bearer credential for the API.
        rejected = self.as_token(APIClient(), body["refresh_token"]).get("/api/auth/me")
        self.assertEqual(rejected.status_code, 401, rejected.content)
        # And an access token is not accepted where a refresh token belongs.
        self.assertEqual(APIClient().post("/api/auth/refresh",
                                          {"refresh_token": body["access_token"]},
                                          format="json").status_code, 401)


class SignOutTests(SessionBase):
    def test_signing_out_retires_the_refresh_token(self):
        client, body = self.sign_in()
        response = client.post("/api/auth/logout",
                               {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["refresh_token_retired"])
        self.assertTrue(RevokedToken.objects.filter(reason="signed out").exists())

        self.assertEqual(APIClient().post("/api/auth/refresh",
                                          {"refresh_token": body["refresh_token"]},
                                          format="json").status_code, 401)
        self.assertTrue(AuditLog.objects.filter(action="logout").exists())

    def test_signing_out_twice_is_not_an_error(self):
        """A sign-out that fails leaves the user signed in, which is worse."""
        client, body = self.sign_in()
        client.post("/api/auth/logout", {"refresh_token": body["refresh_token"]}, format="json")
        again = client.post("/api/auth/logout",
                            {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertFalse(again.json()["refresh_token_retired"])

    def test_signing_out_on_one_device_leaves_the_others_signed_in(self):
        laptop, laptop_body = self.sign_in()
        phone, phone_body = self.sign_in()

        laptop.post("/api/auth/logout", {"refresh_token": laptop_body["refresh_token"]},
                    format="json")

        self.assertEqual(phone.get("/api/auth/me").status_code, 200)
        self.assertEqual(APIClient().post("/api/auth/refresh",
                                          {"refresh_token": phone_body["refresh_token"]},
                                          format="json").status_code, 200)

    def test_signing_out_everywhere_kills_every_session_immediately(self):
        """Not at the end of the access token's life — on the next request."""
        laptop, laptop_body = self.sign_in()
        phone, phone_body = self.sign_in()
        self.assertEqual(phone.get("/api/auth/me").status_code, 200)

        response = laptop.post("/api/auth/sign-out-everywhere")
        self.assertEqual(response.status_code, 200, response.content)

        # The phone's ACCESS token, which has not expired, stops working at once.
        self.assertEqual(phone.get("/api/auth/me").status_code, 401)
        self.assertEqual(APIClient().post("/api/auth/refresh",
                                          {"refresh_token": phone_body["refresh_token"]},
                                          format="json").status_code, 401)
        # Including the device that asked.
        self.assertEqual(laptop.get("/api/auth/me").status_code, 401)
        self.assertTrue(AuditLog.objects.filter(action="sign_out_everywhere").exists())

        # Signing in again works, with the new version.
        fresh, _ = self.sign_in()
        self.assertEqual(fresh.get("/api/auth/me").status_code, 200)


class RevocationTests(SessionBase):
    def test_disabling_an_account_stops_it_posting_at_once(self):
        """The case the eight-hour token made impossible."""
        teller, body = self.sign_in()
        self.assertEqual(teller.get("/api/auth/me").status_code, 200)
        user = User.objects.get(username="teller")

        response = self.admin.patch(f"/api/users/{user.id}", {"is_active": False},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(teller.get("/api/auth/me").status_code, 401,
                         "a disabled user must stop working now, not in thirty minutes")
        refreshed = APIClient().post("/api/auth/refresh",
                                     {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(refreshed.status_code, 403)
        self.assertIn("disabled", refreshed.json()["detail"])

    def test_changing_someones_role_ends_their_sessions(self):
        """Otherwise a demoted user keeps the permissions they just lost."""
        teller, _ = self.sign_in()
        user = User.objects.get(username="teller")
        self.admin.patch(f"/api/users/{user.id}", {"role": Role.VIEWER}, format="json")

        self.assertEqual(teller.get("/api/auth/me").status_code, 401)
        entry = AuditLog.objects.filter(action="update", entity="user").first()
        self.assertIn("sessions ended", entry.detail)

    def test_an_admin_resetting_a_password_ends_that_users_sessions(self):
        teller, _ = self.sign_in()
        user = User.objects.get(username="teller")
        self.admin.patch(f"/api/users/{user.id}", {"password": "brand-new-one"}, format="json")
        self.assertEqual(teller.get("/api/auth/me").status_code, 401)

    def test_unlocking_an_account_does_not_end_its_sessions(self):
        """Unlocking is a favour, not a security event."""
        teller, _ = self.sign_in()
        user = User.objects.get(username="teller")
        user.locked_until = timezone.now() + timedelta(minutes=5)
        user.save(update_fields=["locked_until"])

        self.admin.patch(f"/api/users/{user.id}", {"unlock": True}, format="json")
        self.assertEqual(teller.get("/api/auth/me").status_code, 200)

    def test_changing_your_own_password_keeps_you_signed_in_here(self):
        teller, _ = self.sign_in()
        response = teller.post("/api/auth/change-password",
                               {"current_password": "teller123",
                                "new_password": "a-better-one"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("access_token", response.json(),
                      "the caller's own token was just revoked; hand back a new pair")

        # The old token is dead, the new one works.
        self.assertEqual(teller.get("/api/auth/me").status_code, 401)
        self.assertEqual(
            self.as_token(APIClient(), response.json()["access_token"])
            .get("/api/auth/me").status_code, 200)

    def test_changing_your_password_ends_your_other_sessions(self):
        laptop, _ = self.sign_in()
        phone, _ = self.sign_in()
        laptop.post("/api/auth/change-password",
                    {"current_password": "teller123", "new_password": "a-better-one"},
                    format="json")
        self.assertEqual(phone.get("/api/auth/me").status_code, 401,
                         "the usual reason to change a password is that the old one leaked")

    def test_a_locked_account_cannot_refresh(self):
        _teller, body = self.sign_in()
        user = User.objects.get(username="teller")
        user.locked_until = timezone.now() + timedelta(minutes=10)
        user.save(update_fields=["locked_until"])

        response = APIClient().post("/api/auth/refresh",
                                    {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertIn("locked", response.json()["detail"])


class TokenHousekeepingTests(SessionBase):
    def test_pruning_forgets_revocations_that_no_longer_matter(self):
        _client, body = self.sign_in()
        APIClient().post("/api/auth/refresh", {"refresh_token": body["refresh_token"]},
                         format="json")
        self.assertEqual(RevokedToken.objects.count(), 1)

        # Nothing has expired yet.
        self.assertEqual(tokens.prune(), 0)
        self.assertEqual(RevokedToken.objects.count(), 1)

        # Once the token it names would have expired anyway, the row is noise.
        RevokedToken.objects.update(expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(tokens.prune(), 1)
        self.assertEqual(RevokedToken.objects.count(), 0)

    def test_a_token_with_no_version_claim_is_still_accepted(self):
        """A token minted before the claim existed must not sign everyone out on deploy."""
        from rest_framework_simplejwt.tokens import RefreshToken

        user = User.objects.get(username="teller")
        legacy = RefreshToken.for_user(user)  # no tv claim
        client = self.as_token(APIClient(), str(legacy.access_token))
        self.assertEqual(client.get("/api/auth/me").status_code, 200)

    def test_an_expired_access_token_is_refused_but_the_refresh_token_still_renews(self):
        """Exactly what the client's silent-renewal path depends on.

        The access token's expiry is pushed into the past directly rather than by
        overriding the lifetime setting, so the refresh token issued alongside it
        keeps its real, unexpired lifetime — which is the situation being tested.
        """
        _client, body = self.sign_in()
        token = tokens.parse_refresh(body["refresh_token"])
        expired = token.access_token
        expired.set_exp(from_time=timezone.now() - timedelta(hours=2),
                        lifetime=timedelta(minutes=1))

        stale = self.as_token(APIClient(), str(expired))
        self.assertEqual(stale.get("/api/auth/me").status_code, 401, "already expired")

        renewed = APIClient().post("/api/auth/refresh",
                                   {"refresh_token": body["refresh_token"]}, format="json")
        self.assertEqual(renewed.status_code, 200, renewed.content)
        self.assertEqual(
            self.as_token(APIClient(), renewed.json()["access_token"])
            .get("/api/auth/me").status_code, 200)


class ThrottleAndAuditTests(SessionBase):
    def test_every_role_can_refresh_and_sign_out(self):
        for username, password in (("admin", "admin123"), ("officer", "officer123"),
                                   ("teller", "teller123")):
            client, body = self.sign_in(username, password)
            self.assertEqual(
                APIClient().post("/api/auth/refresh",
                                 {"refresh_token": body["refresh_token"]},
                                 format="json").status_code, 200, username)
            self.assertEqual(client.post("/api/auth/logout", {}, format="json").status_code,
                             200, username)

    def test_signing_out_is_audited_even_without_a_token(self):
        client, _ = self.sign_in()
        AuditLog.objects.filter(action="logout").delete()
        client.post("/api/auth/logout", {}, format="json")
        entry = AuditLog.objects.get(action="logout")
        self.assertIn("no usable refresh token", entry.detail)
