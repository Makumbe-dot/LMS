"""Access rights: an administrator ticks what each user may do.

There are no fixed roles below administrator. A user with no rights reads; each
right opens its own set of actions and nothing else; the approval limit is the
user's own when one is set; and the four-eyes rules hold for anyone who is not an
administrator, whatever they have been granted.
"""
import importlib

from django.apps import apps
from rest_framework.test import APITestCase

from core.models import RIGHT_PRESETS, AuditLog, Right, Role, User

from .test_journals import JournalBase
# The module, not the class: a test class imported by name runs twice.
from . import test_loan_lifecycle as lifecycle


class RightsBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user("admin", "admin123", full_name="Admin", role=Role.ADMIN)
        User.objects.create_user("officer", "officer123", full_name="Officer",
                                 rights=RIGHT_PRESETS["loan_officer"])
        User.objects.create_user("clerk", "clerk-pass1", full_name="Clerk")

    client_for = lifecycle.LoanLifecycleTests.client_for
    make_product = lifecycle.LoanLifecycleTests.make_product
    make_borrower = lifecycle.LoanLifecycleTests.make_borrower

    def setUp(self):
        self.admin = self.client_for("admin", "admin123")
        self.officer = self.client_for("officer", "officer123")
        self.clerk = self.client_for("clerk", "clerk-pass1")
        self.clerk_id = User.objects.get(username="clerk").id

    def grant(self, *rights, **extra):
        response = self.admin.patch(f"/api/users/{self.clerk_id}",
                                    {"rights": [r.value for r in rights], **extra},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def new_borrower(self, client, national_id="63-555555C63"):
        return client.post("/api/borrowers", {
            "first_name": "New", "last_name": "Borrower", "national_id": national_id,
            "phone": "0775555555", "net_salary": 1500, "payday": 25, "kyc_verified": True,
        }, format="json")


class GrantingRightsTests(RightsBase):
    def test_a_user_with_no_rights_reads_but_changes_nothing(self):
        self.assertEqual(self.clerk.get("/api/borrowers").status_code, 200)
        self.assertEqual(self.clerk.get("/api/reports/dashboard").status_code, 200)
        self.assertEqual(self.new_borrower(self.clerk).status_code, 403)

    def test_a_right_takes_effect_at_once_without_signing_in_again(self):
        self.assertEqual(self.new_borrower(self.clerk).status_code, 403)
        self.grant(Right.BORROWERS)
        self.assertEqual(self.new_borrower(self.clerk).status_code, 201)

        self.grant()
        response = self.new_borrower(self.clerk, national_id="63-555556C63")
        self.assertEqual(response.status_code, 403)
        self.assertIn(Right.BORROWERS.label, response.json()["detail"])

    def test_one_right_opens_its_own_actions_and_no_others(self):
        self.grant(Right.BORROWERS)
        self.assertEqual(self.new_borrower(self.clerk).status_code, 201)
        # Borrowers, not products and not the ledger.
        self.assertEqual(self.clerk.post("/api/products", {}, format="json").status_code, 403)
        self.assertEqual(self.clerk.post("/api/ledger/rebuild").status_code, 403)

    def test_administrator_only_pages_are_never_granted(self):
        self.grant(*Right)
        self.assertEqual(self.clerk.get("/api/users").status_code, 403)
        self.assertEqual(self.clerk.get("/api/reports/audit").status_code, 403)

    def test_an_administrator_holds_every_right(self):
        me = self.admin.get("/api/auth/me").json()
        self.assertEqual(me["rights"], [r.value for r in Right])

    def test_the_user_record_lists_rights_in_catalogue_order(self):
        body = self.grant(Right.SETUP, Right.BORROWERS, Right.BORROWERS)
        self.assertEqual(body["rights"], ["borrowers", "setup"])

    def test_an_unknown_right_is_refused(self):
        response = self.admin.patch(f"/api/users/{self.clerk_id}", {"rights": ["everything"]},
                                    format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_new_user_is_created_with_the_rights_ticked(self):
        response = self.admin.post("/api/users", {
            "username": "cashier", "full_name": "Cashier", "password": "Zvakanaka-2026",
            "rights": ["cash"],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["role"], "user")
        self.assertEqual(response.json()["rights"], ["cash"])

    def test_the_audit_log_says_what_was_granted_and_removed(self):
        self.grant(Right.CASH, Right.LOANS)
        self.grant(Right.CASH, Right.APPROVE)
        entry = AuditLog.objects.filter(action="update", entity="user").first()
        self.assertIn("granted: approve", entry.detail)
        self.assertIn("removed: loans", entry.detail)

    def test_the_catalogue_lists_every_right_and_the_old_role_sets(self):
        body = self.admin.get("/api/users/rights").json()
        self.assertEqual([r["code"] for r in body["rights"]], [r.value for r in Right])
        self.assertTrue(all(r["description"] for r in body["rights"]))
        presets = {p["code"]: p["rights"] for p in body["presets"]}
        self.assertEqual(presets["teller"], ["cash"])


class ApprovalTests(RightsBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def apply(self, principal=1000):
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.product["id"],
            "principal": principal, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def test_approving_needs_the_approve_right(self):
        loan_id = self.apply()
        self.grant(Right.LOANS, Right.DISBURSE)
        self.assertEqual(self.clerk.post(f"/api/loans/{loan_id}/approve").status_code, 403)
        self.grant(Right.APPROVE)
        self.assertEqual(self.clerk.post(f"/api/loans/{loan_id}/approve").status_code, 200)

    def test_a_users_own_limit_replaces_the_organisations(self):
        loan_id = self.apply(principal=1000)
        self.grant(Right.APPROVE, approval_limit="500.00")
        response = self.clerk.post(f"/api/loans/{loan_id}/approve")
        self.assertEqual(response.status_code, 400)
        self.assertIn("500", response.json()["detail"])

        self.grant(Right.APPROVE, approval_limit="1500.00")
        self.assertEqual(self.clerk.post(f"/api/loans/{loan_id}/approve").status_code, 200)

    def test_without_a_limit_of_their_own_the_organisations_applies(self):
        loan_id = self.apply(principal=2500)
        self.grant(Right.APPROVE)
        response = self.clerk.post(f"/api/loans/{loan_id}/approve")
        self.assertEqual(response.status_code, 400)
        self.assertIn("2000", response.json()["detail"])

    def test_every_right_still_cannot_approve_a_loan_one_originated(self):
        self.grant(*Right)
        response = self.clerk.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.product["id"],
            "principal": 1000, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        response = self.clerk.post(f"/api/loans/{response.json()['id']}/approve")
        self.assertEqual(response.status_code, 400)
        self.assertIn("originated", response.json()["detail"])


class JournalRightsTests(JournalBase):
    def setUp(self):
        super().setUp()
        User.objects.create_user("accountant", "accountant-1", full_name="Accountant",
                                 rights=[Right.CASH, Right.ACCOUNTING])
        self.accountant = self.client_for("accountant", "accountant-1")

    def test_the_accounting_right_posts_someone_elses_journal(self):
        journal = self.prepared()
        response = self.accountant.post(f"/api/journals/{journal['id']}/post")
        self.assertEqual(response.status_code, 200, response.content)

    def test_the_accounting_right_cannot_post_its_own_journal(self):
        journal = self.prepared(client=self.accountant)
        response = self.accountant.post(f"/api/journals/{journal['id']}/post")
        self.assertEqual(response.status_code, 400)
        self.assertIn("prepared", response.json()["detail"])
        # A second pair of hands can.
        self.assertEqual(self.admin.post(f"/api/journals/{journal['id']}/post").status_code, 200)


class MigrationTests(APITestCase):
    """Migration 0028 turns each old role into the rights it carried."""

    def test_old_roles_become_users_with_the_same_rights(self):
        migration = importlib.import_module("core.migrations.0028_user_access_rights")
        officer = User.objects.create_user("o", "x", full_name="O")
        teller = User.objects.create_user("t", "x", full_name="T")
        viewer = User.objects.create_user("v", "x", full_name="V")
        admin = User.objects.create_user("a", "x", full_name="A", role=Role.ADMIN)
        User.objects.filter(pk=officer.pk).update(role="loan_officer")
        User.objects.filter(pk=teller.pk).update(role="teller")
        User.objects.filter(pk=viewer.pk).update(role="viewer")

        migration.roles_to_rights(apps, None)

        for user, preset in [(officer, "loan_officer"), (teller, "teller"), (viewer, "viewer")]:
            user.refresh_from_db()
            self.assertEqual(user.role, Role.USER)
            self.assertEqual(user.rights, [r.value for r in RIGHT_PRESETS[preset]])
        admin.refresh_from_db()
        self.assertEqual(admin.role, Role.ADMIN)

        migration.rights_to_roles(apps, None)
        self.assertEqual([User.objects.get(pk=u.pk).role for u in (officer, teller, viewer)],
                         ["loan_officer", "teller", "viewer"])

