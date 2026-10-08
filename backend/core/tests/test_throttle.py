"""Rate limits on the doors that take guesses: staff sign-in, the portal, the webhook.

The suite runs with a no-op cache (settings.py), so these switch a real one on.
"""
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from core.models import Role, User

REAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                          "LOCATION": "throttle-tests"}}


@override_settings(CACHES=REAL_CACHE)
class ThrottleTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user("admin", "admin123", full_name="Admin", role=Role.ADMIN)

    def setUp(self):
        cache.clear()

    def login(self, password, address="10.0.0.1", username="admin"):
        return APIClient().post("/api/auth/login", {"username": username, "password": password},
                                format="json", REMOTE_ADDR=address)

    def test_twenty_sign_ins_a_minute_from_one_address(self):
        # Guessing at names nobody holds, so the account lockout stays out of it.
        for n in range(20):
            self.assertNotEqual(self.login("wrong", username=f"nobody{n}").status_code, 429)
        # The twenty-first is refused, even with the right password.
        self.assertEqual(self.login("admin123").status_code, 429)
        # Another address is counted on its own.
        self.assertEqual(self.login("admin123", address="10.0.0.2").status_code, 200)

    def test_the_portal_sign_in_is_limited_too(self):
        statuses = [APIClient().post("/api/portal/login", {}, format="json",
                                     REMOTE_ADDR="10.0.0.3").status_code for _ in range(11)]
        self.assertNotIn(429, statuses[:10])
        self.assertEqual(statuses[10], 429)
