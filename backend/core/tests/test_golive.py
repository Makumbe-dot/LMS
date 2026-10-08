"""The go-live checklist, and the sign-in page keeping demo passwords to itself."""
from django.test import override_settings
from rest_framework.test import APIClient

from core.models import User
from core.services import golive

from .test_components import LedgerBase


class GoLiveTests(LedgerBase):
    def item(self, key):
        return next(i for i in golive.checks() if i["key"] == key)

    def test_a_demo_user_with_its_demo_password_fails_the_check(self):
        User.objects.create_user("viewer", "viewer123", full_name="Viewer")
        self.assertEqual(self.item("demo_logins")["status"], golive.FAIL)
        self.assertIn("viewer", self.item("demo_logins")["detail"])

    def test_a_changed_password_passes(self):
        user = User.objects.create_user("viewer", "viewer123", full_name="Viewer")
        user.set_password("a much better passphrase 2026")
        user.save()
        self.assertNotIn("viewer", self.item("demo_logins")["detail"])

    @override_settings(DEBUG=True)
    def test_development_mode_fails(self):
        self.assertEqual(self.item("debug")["status"], golive.FAIL)

    def test_the_checklist_is_for_administrators(self):
        self.assertEqual(self.admin.get("/api/go-live").status_code, 200)
        self.assertEqual(self.teller.get("/api/go-live").status_code, 403)
        body = self.admin.get("/api/go-live").json()
        self.assertIn("items", body)
        self.assertEqual(body["pass"] + body["warn"] + body["fail"], len(body["items"]))

    def test_the_sign_in_page_is_told_not_to_list_demo_logins_by_default(self):
        self.assertFalse(APIClient().get("/api/health").json()["demo_logins"])
        with override_settings(SHOW_DEMO_LOGINS=True):
            self.assertTrue(APIClient().get("/api/health").json()["demo_logins"])
