"""The functionality added on top of the core lifecycle: flat-rate products,
early settlement, bulk repayment import, the messaging outbox, IFRS 9
provisioning, branches, settings, documents, search, pagination and account
lockout."""
import tempfile
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient, APITestCase

from core.models import (
    Branch,
    LoanStatus,
    Notification,
    NotificationStatus,
    OrganisationSetting,
    RateMethod,
    Role,
    User,
)
from core.services.amortisation import build_schedule, monthly_instalment


def dec(value) -> Decimal:
    return Decimal(str(value))


# ---------------------------------------------------------------- flat rate
class FlatRateTests(SimpleTestCase):
    def test_flat_instalment_spreads_principal_and_interest_evenly(self):
        # 1200 at 2%/month flat over 6 months: interest = 1200 * 0.02 * 6 = 144
        # instalment = (1200 + 144) / 6 = 224
        self.assertEqual(monthly_instalment(dec(1200), dec(2), 6, "flat"), dec("224.00"))

    def test_flat_schedule_closes_and_totals_are_exact(self):
        rows = build_schedule(dec(1000), dec("1.5"), 7, date(2026, 1, 31), "flat")
        self.assertEqual(rows[-1].closing_balance, dec("0.00"))
        self.assertEqual(sum(r.principal_due for r in rows), dec("1000.00"))
        # 1000 * 1.5% * 7 = 105.00 exactly, despite 105/7 not dividing evenly in cents
        self.assertEqual(sum(r.interest_due for r in rows), dec("105.00"))

    def test_flat_costs_more_than_reducing_at_the_same_rate(self):
        flat = build_schedule(dec(1000), dec(5), 6, date(2026, 1, 1), "flat")
        reducing = build_schedule(dec(1000), dec(5), 6, date(2026, 1, 1), "reducing")
        self.assertGreater(sum(r.interest_due for r in flat),
                           sum(r.interest_due for r in reducing))

    def test_flat_interest_is_level_across_instalments(self):
        rows = build_schedule(dec(1200), dec(2), 6, date(2026, 1, 1), "flat")
        self.assertEqual({r.interest_due for r in rows[:-1]}, {dec("24.00")})


# ---------------------------------------------------------------- shared setup
class FeatureTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branch = Branch.objects.create(code="HQ", name="Head Office")
        User.objects.create_user("admin", "admin123", full_name="Admin", role=Role.ADMIN)
        User.objects.create_user("officer", "officer123", full_name="Officer",
                                 role=Role.LOAN_OFFICER)
        User.objects.create_user("teller", "teller123", full_name="Teller", role=Role.TELLER)

    def client_for(self, username, password):
        client = APIClient()
        response = client.post("/api/auth/login", {"username": username, "password": password},
                               format="json")
        self.assertEqual(response.status_code, 200, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access_token"])
        return client

    def setUp(self):
        self.admin = self.client_for("admin", "admin123")
        self.officer = self.client_for("officer", "officer123")
        self.teller = self.client_for("teller", "teller123")

    def make_product(self, **overrides):
        payload = {
            "code": "T-SAL", "name": "Test Salary", "interest_rate_pct": 5,
            "rate_method": "reducing", "min_amount": 100, "max_amount": 5000,
            "min_term_months": 1, "max_term_months": 12, "admin_fee_pct": 3,
            "insurance_fee_pct": 1, "penalty_rate_pct_per_day": 0.5, "grace_days": 3,
            "max_instalment_to_salary_pct": 40,
        }
        payload.update(overrides)
        response = self.admin.post("/api/products", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def make_borrower(self, national_id="63-123456A63", **overrides):
        payload = {
            "first_name": "Test", "last_name": "Borrower", "national_id": national_id,
            "phone": "0771234567", "net_salary": 1500, "payday": 25, "kyc_verified": True,
            "employer": "Test Employer", "employee_no": "EMP1",
            "branch": self.branch.id,
        }
        payload.update(overrides)
        response = self.officer.post("/api/borrowers", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def disbursed_loan(self, product, borrower, principal=1000, term=6,
                       disbursement_date="2026-03-01"):
        response = self.officer.post("/api/loans", {
            "borrower_id": borrower["id"], "product_id": product["id"],
            "principal": principal, "term_months": term,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        loan = response.json()
        self.admin.post(f"/api/loans/{loan['id']}/approve")
        response = self.officer.post(f"/api/loans/{loan['id']}/disburse",
                                     {"disbursement_date": disbursement_date}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


# ---------------------------------------------------------------- settlement
class EarlySettlementTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def test_quote_rebates_interest_not_yet_earned(self):
        lid = self.loan["id"]
        response = self.teller.get(f"/api/loans/{lid}/settlement-quote?as_of=2026-04-10")
        self.assertEqual(response.status_code, 200, response.content)
        quote = response.json()

        # Instalments 1 and 2 (25 Mar, 25 Apr)... only the first has fallen due by 10 April.
        self.assertEqual(dec(quote["principal_outstanding"]), dec("1000.00"))
        self.assertEqual(dec(quote["interest_accrued"]), dec("50.00"))
        self.assertGreater(dec(quote["interest_rebate"]), 0)
        self.assertEqual(
            dec(quote["settlement_amount"]),
            dec(quote["principal_outstanding"]) + dec(quote["interest_accrued"])
            + dec(quote["penalties_outstanding"]))
        # settling early is cheaper than running to term
        self.assertLess(dec(quote["settlement_amount"]), dec(quote["total_outstanding"]))

    def test_settling_closes_the_loan_and_rebates_the_unearned_interest(self):
        lid = self.loan["id"]
        quote = self.teller.get(f"/api/loans/{lid}/settlement-quote?as_of=2026-04-10").json()
        response = self.teller.post(f"/api/loans/{lid}/settle", {
            "txn_date": "2026-04-10", "method": "bank_transfer", "reference": "SETTLE1",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        detail = response.json()

        self.assertEqual(detail["status"], "closed")
        self.assertEqual(dec(detail["total_outstanding"]), dec("0"))
        types = [t["txn_type"] for t in detail["transactions"]]
        self.assertIn("waiver", types)   # the rebate
        self.assertIn("repayment", types)
        paid = [t for t in detail["transactions"] if t["txn_type"] == "repayment"][-1]
        self.assertEqual(dec(paid["amount"]), dec(quote["settlement_amount"]))

    def test_a_wrong_confirmation_amount_is_refused(self):
        lid = self.loan["id"]
        response = self.teller.post(f"/api/loans/{lid}/settle",
                                    {"amount": "1.00", "txn_date": "2026-04-10"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be exactly", response.json()["detail"])

    def test_only_active_loans_can_be_settled(self):
        borrower = self.make_borrower(national_id="63-000002C63")
        response = self.officer.post("/api/loans", {
            "borrower_id": borrower["id"], "product_id": self.product["id"],
            "principal": 500, "term_months": 3,
        }, format="json")
        pending_id = response.json()["id"]
        self.assertEqual(
            self.teller.get(f"/api/loans/{pending_id}/settlement-quote").status_code, 400)


# ---------------------------------------------------------------- bulk import
class BulkImportTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def upload(self, text, **extra):
        payload = {"file": SimpleUploadedFile("batch.csv", text.encode("utf-8"),
                                              content_type="text/csv")}
        payload.update(extra)
        return self.teller.post("/api/imports/repayments", payload, format="multipart")

    def test_dry_run_reports_without_posting(self):
        csv_text = ("loan_no,amount,date,method\n"
                    f"{self.loan['loan_no']},197.02,2026-03-25,salary_deduction\n")
        response = self.upload(csv_text)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertFalse(body["committed"])
        self.assertEqual(body["valid_rows"], 1)
        self.assertEqual(dec(body["total_amount"]), dec("197.02"))

        after = self.teller.get(f"/api/loans/{self.loan['id']}").json()
        self.assertEqual(dec(after["total_paid"]), dec("0"))

    def test_commit_posts_every_valid_row(self):
        csv_text = ("loan_no,amount,date,method,reference\n"
                    f"{self.loan['loan_no']},197.02,2026-03-25,salary_deduction,PAY1\n"
                    f"{self.loan['loan_no']},100.00,2026-04-25,salary_deduction,PAY2\n")
        response = self.upload(csv_text, commit="true")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["posted_rows"], 2)
        self.assertEqual(dec(body["total_amount"]), dec("297.02"))

        after = self.teller.get(f"/api/loans/{self.loan['id']}").json()
        self.assertEqual(dec(after["total_paid"]), dec("297.02"))

    def test_bad_rows_are_reported_and_block_the_batch(self):
        csv_text = ("loan_no,amount\n"
                    f"{self.loan['loan_no']},50.00\n"
                    "LN-999999,25.00\n"
                    f"{self.loan['loan_no']},not-a-number\n")
        preview = self.upload(csv_text).json()
        self.assertEqual(preview["valid_rows"], 1)
        self.assertEqual(preview["invalid_rows"], 2)
        errors = " ".join(r["error"] or "" for r in preview["rows"])
        self.assertIn("No loan numbered LN-999999", errors)
        self.assertIn("not a positive number", errors)

        blocked = self.upload(csv_text, commit="true")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("failed validation", blocked.json()["detail"])

        partial = self.upload(csv_text, commit="true", allow_partial="true")
        self.assertEqual(partial.status_code, 201, partial.content)
        self.assertEqual(partial.json()["posted_rows"], 1)

    def test_two_rows_cannot_jointly_overpay_one_loan(self):
        outstanding = self.teller.get(f"/api/loans/{self.loan['id']}").json()["total_outstanding"]
        csv_text = ("loan_no,amount\n"
                    f"{self.loan['loan_no']},{outstanding}\n"
                    f"{self.loan['loan_no']},10.00\n")
        preview = self.upload(csv_text).json()
        self.assertEqual(preview["valid_rows"], 1)
        self.assertIn("after earlier rows in this file", preview["rows"][1]["error"])

    def test_a_missing_column_is_rejected_clearly(self):
        response = self.upload("loan_no,value\nLN-1,10\n")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing column", response.json()["detail"])

    def test_a_viewer_cannot_import(self):
        User.objects.create_user("viewer", "viewer123", full_name="Viewer", role=Role.VIEWER)
        viewer = self.client_for("viewer", "viewer123")
        response = viewer.post("/api/imports/repayments", {
            "file": SimpleUploadedFile("b.csv", b"loan_no,amount\nLN-1,1\n",
                                       content_type="text/csv"),
        }, format="multipart")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------- notifications
class NotificationTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def test_generate_queues_reminders_and_is_idempotent(self):
        response = self.officer.post("/api/notifications/generate?as_of=2026-03-23")
        self.assertEqual(response.status_code, 200, response.content)
        first = response.json()
        self.assertGreaterEqual(first["reminders_queued"], 1)

        again = self.officer.post("/api/notifications/generate?as_of=2026-03-23").json()
        self.assertEqual(again["total_queued"], 0)

    def test_arrears_notices_are_queued_for_overdue_loans(self):
        result = self.officer.post("/api/notifications/generate?as_of=2026-05-05").json()
        self.assertGreaterEqual(result["arrears_notices_queued"], 1)
        body = Notification.objects.filter(kind="arrears").first().body
        self.assertIn("in arrears", body)

    def test_a_repayment_queues_a_receipt(self):
        self.teller.post(f"/api/loans/{self.loan['id']}/repayments",
                         {"amount": 197.02, "txn_date": "2026-03-25"}, format="json")
        receipt = Notification.objects.filter(kind="receipt").first()
        self.assertIsNotNone(receipt)
        self.assertIn("we have received", receipt.body)

    def test_messages_can_be_listed_sent_and_cancelled(self):
        # 23 March queues a reminder for the instalment due on the 25th;
        # 5 May queues an arrears notice, because that instalment went unpaid.
        self.officer.post("/api/notifications/generate?as_of=2026-03-23")
        self.officer.post("/api/notifications/generate?as_of=2026-05-05")
        listing = self.officer.get("/api/notifications?status=queued").json()
        self.assertGreaterEqual(listing["count"], 2)

        ids = [row["id"] for row in listing["results"]][:1]
        cancelled = self.officer.post("/api/notifications/cancel", {"ids": ids}, format="json")
        self.assertEqual(cancelled.json()["cancelled"], 1)

        sent = self.officer.post("/api/notifications/send", {"as_of": "2026-05-05"},
                                 format="json")
        self.assertGreaterEqual(sent.json()["sent"], 1)
        # a cancelled message is never sent afterwards
        self.assertFalse(
            Notification.objects.filter(id__in=ids, status=NotificationStatus.SENT).exists())


# ---------------------------------------------------------------- IFRS 9
class ECLTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def test_a_current_loan_sits_in_stage_1(self):
        report = self.admin.get("/api/reports/ecl?as_of=2026-03-02").json()
        row = next(r for r in report["rows"] if r["loan_id"] == self.loan["id"])
        self.assertEqual(row["stage"], "1")
        self.assertEqual(dec(row["provision"]),
                         (dec(row["exposure"]) * dec(1) / 100).quantize(dec("0.01")))

    def test_staging_follows_days_past_due(self):
        # instalment 1 fell due 2026-03-25; by 2026-07-01 that is ~98 days
        report = self.admin.get("/api/reports/ecl?as_of=2026-07-01").json()
        row = next(r for r in report["rows"] if r["loan_id"] == self.loan["id"])
        self.assertEqual(row["stage"], "3")
        self.assertEqual(dec(row["provision_rate_pct"]), dec(60))
        self.assertEqual(report["total_provision"], row["provision"])

    def test_the_rates_come_from_organisation_settings(self):
        self.admin.patch("/api/settings", {"ecl_stage1_pct": "2.5"}, format="json")
        report = self.admin.get("/api/reports/ecl?as_of=2026-03-02").json()
        row = next(r for r in report["rows"] if r["loan_id"] == self.loan["id"])
        self.assertEqual(dec(row["provision_rate_pct"]), dec("2.5"))

    def test_csv_export(self):
        response = self.admin.get("/api/reports/ecl?fmt=csv")
        self.assertTrue(response["content-type"].startswith("text/csv"))


# ---------------------------------------------------------------- performance
class PerformanceReportTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def test_officer_report_attributes_the_portfolio(self):
        rows = self.admin.get("/api/reports/officer-performance").json()
        officer = next(r for r in rows if r["name"] == "Officer")
        self.assertEqual(officer["active_loans"], 1)
        self.assertEqual(officer["loans_originated"], 1)
        self.assertEqual(dec(officer["total_disbursed"]), dec("1000.00"))

    def test_product_report_carries_the_rate_method(self):
        rows = self.admin.get("/api/reports/product-performance").json()
        self.assertEqual(rows[0]["name"], "Test Salary")
        self.assertEqual(rows[0]["rate_method"], "reducing")

    def test_branch_report_groups_by_branch(self):
        rows = self.admin.get("/api/reports/branch-performance").json()
        self.assertEqual(rows[0]["name"], "Head Office")
        self.assertEqual(rows[0]["active_loans"], 1)


# ---------------------------------------------------------------- payroll
class PayrollTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def test_deduction_schedule_lists_the_instalments_due(self):
        rows = self.officer.get(
            "/api/reports/payroll?start=2026-03-01&end=2026-03-31").json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["employee_no"], "EMP1")
        self.assertEqual(rows[0]["employer"], "Test Employer")
        self.assertEqual(dec(rows[0]["deduct"]), dec("197.02"))

    def test_filtering_by_employer(self):
        rows = self.officer.get(
            "/api/reports/payroll?start=2026-03-01&end=2026-03-31&employer=Nobody Ltd").json()
        self.assertEqual(rows, [])

    def test_employer_list(self):
        rows = self.officer.get("/api/reports/employers").json()
        self.assertEqual(rows[0]["employer"], "Test Employer")
        self.assertEqual(rows[0]["active_loans"], 1)

    def test_csv_export_is_named_for_the_employer(self):
        response = self.officer.get(
            "/api/reports/payroll?start=2026-03-01&end=2026-03-31"
            "&employer=Test Employer&fmt=csv")
        self.assertIn("payroll_Test_Employer_202603.csv", response["content-disposition"])


# ---------------------------------------------------------------- org / admin
class OrganisationTests(FeatureTestBase):
    def test_branches_are_admin_only_to_create(self):
        self.assertEqual(
            self.officer.post("/api/branches", {"code": "X", "name": "X"},
                              format="json").status_code, 403)
        response = self.admin.post("/api/branches", {"code": "BYO", "name": "Bulawayo"},
                                   format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(len(self.officer.get("/api/branches").json()), 2)

    def test_settings_are_readable_by_all_and_writable_by_admin(self):
        self.assertEqual(self.teller.get("/api/settings").status_code, 200)
        self.assertEqual(
            self.teller.patch("/api/settings", {"currency": "ZWL"}, format="json").status_code, 403)
        response = self.admin.patch("/api/settings", {"currency": "ZWL", "name": "Simba"},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(OrganisationSetting.load().currency, "ZWL")

    def test_settings_stay_a_single_row(self):
        self.admin.patch("/api/settings", {"name": "One"}, format="json")
        self.admin.patch("/api/settings", {"name": "Two"}, format="json")
        self.assertEqual(OrganisationSetting.objects.count(), 1)

    def test_search_spans_borrowers_and_loans(self):
        product = self.make_product()
        borrower = self.make_borrower()
        loan = self.disbursed_loan(product, borrower)

        results = self.officer.get("/api/search?q=Borrower").json()
        self.assertGreaterEqual(len(results["borrowers"]), 1)

        results = self.officer.get(f"/api/search?q={loan['loan_no']}").json()
        self.assertEqual(results["loans"][0]["id"], loan["id"])

    def test_search_ignores_one_character_terms(self):
        self.assertEqual(self.officer.get("/api/search?q=a").json()["borrowers"], [])


class PasswordAndLockoutTests(FeatureTestBase):
    def test_a_user_can_change_their_own_password(self):
        response = self.teller.post("/api/auth/change-password", {
            "current_password": "teller123", "new_password": "newpass456",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.client_for("teller", "newpass456")  # asserts the new password works

    def test_the_current_password_must_be_right(self):
        response = self.teller.post("/api/auth/change-password", {
            "current_password": "wrong", "new_password": "newpass456",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not correct", response.json()["detail"])

    @override_settings(LOGIN_MAX_ATTEMPTS=3, LOGIN_LOCKOUT_MINUTES=15)
    def test_repeated_bad_passwords_lock_the_account(self):
        client = APIClient()
        for _ in range(3):
            response = client.post("/api/auth/login",
                                   {"username": "teller", "password": "nope"}, format="json")
            self.assertEqual(response.status_code, 401)

        locked = client.post("/api/auth/login",
                             {"username": "teller", "password": "teller123"}, format="json")
        self.assertEqual(locked.status_code, 403)
        self.assertIn("Too many failed sign-in attempts", locked.json()["detail"])

        # an admin can release it
        teller = User.objects.get(username="teller")
        self.admin.patch(f"/api/users/{teller.id}", {"unlock": True}, format="json")
        self.client_for("teller", "teller123")

    def test_a_good_sign_in_clears_the_failure_count(self):
        client = APIClient()
        client.post("/api/auth/login", {"username": "teller", "password": "nope"}, format="json")
        self.assertEqual(User.objects.get(username="teller").failed_login_attempts, 1)
        self.client_for("teller", "teller123")
        self.assertEqual(User.objects.get(username="teller").failed_login_attempts, 0)

    def test_an_admin_cannot_change_their_own_role(self):
        admin_user = User.objects.get(username="admin")
        response = self.admin.patch(f"/api/users/{admin_user.id}", {"role": "viewer"},
                                    format="json")
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------- documents
@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="lms-test-media-"))
class DocumentTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.borrower = self.make_borrower()

    def pdf(self, name="id.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4 fake", content_type="application/pdf")

    def test_upload_list_download_and_delete(self):
        bid = self.borrower["id"]
        response = self.officer.post(f"/api/borrowers/{bid}/documents",
                                     {"file": self.pdf(), "doc_type": "id", "note": "Passport"},
                                     format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        document = response.json()
        self.assertEqual(document["doc_type"], "id")
        self.assertEqual(document["original_name"], "id.pdf")

        listing = self.officer.get(f"/api/borrowers/{bid}/documents").json()
        self.assertEqual(len(listing), 1)

        download = self.officer.get(document["download_url"])
        self.assertEqual(download.status_code, 200)
        self.assertIn("id.pdf", download["content-disposition"])

        # Deleting works even while Windows still holds the downloaded file open:
        # the record goes, and the stray file is left for housekeeping.
        deleted = self.officer.delete(f"/api/borrowers/{bid}/documents/{document['id']}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.officer.get(f"/api/borrowers/{bid}/documents").json(), [])

    def test_an_unsupported_file_type_is_refused(self):
        bid = self.borrower["id"]
        response = self.officer.post(f"/api/borrowers/{bid}/documents", {
            "file": SimpleUploadedFile("run.exe", b"MZ", content_type="application/x-msdownload"),
        }, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not an accepted file type", response.json()["detail"])

    @override_settings(MAX_UPLOAD_BYTES=10)
    def test_an_oversized_file_is_refused(self):
        bid = self.borrower["id"]
        response = self.officer.post(f"/api/borrowers/{bid}/documents",
                                     {"file": self.pdf()}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("larger than", response.json()["detail"])

    def test_a_teller_cannot_upload(self):
        bid = self.borrower["id"]
        response = self.teller.post(f"/api/borrowers/{bid}/documents", {"file": self.pdf()},
                                    format="multipart")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------- notes
class LoanNoteTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower)

    def test_a_follow_up_can_be_recorded_and_resolved(self):
        lid = self.loan["id"]
        response = self.teller.post(f"/api/loans/{lid}/notes", {
            "body": "Borrower promises to pay at month end.",
            "next_action_date": "2026-04-30", "promised_amount": "197.02",
            "promised_date": "2026-04-28",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        note = response.json()
        self.assertEqual(note["author_name"], "Teller")
        self.assertFalse(note["resolved"])

        detail = self.teller.get(f"/api/loans/{lid}").json()
        self.assertEqual(len(detail["notes"]), 1)

        updated = self.teller.patch(f"/api/loans/{lid}/notes/{note['id']}", {"resolved": True},
                                    format="json")
        self.assertTrue(updated.json()["resolved"])

        self.assertEqual(
            self.teller.delete(f"/api/loans/{lid}/notes/{note['id']}").status_code, 204)

    def test_a_viewer_cannot_add_notes(self):
        User.objects.create_user("viewer", "viewer123", full_name="Viewer", role=Role.VIEWER)
        viewer = self.client_for("viewer", "viewer123")
        response = viewer.post(f"/api/loans/{self.loan['id']}/notes", {"body": "x"},
                               format="json")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------- listings
class ListingTests(FeatureTestBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        for index in range(7):
            self.make_borrower(national_id=f"63-10000{index}Z63")

    def test_borrowers_are_paginated(self):
        page1 = self.officer.get("/api/borrowers?page=1&page_size=3").json()
        self.assertEqual(page1["count"], 7)
        self.assertEqual(page1["num_pages"], 3)
        self.assertEqual(len(page1["results"]), 3)

        page3 = self.officer.get("/api/borrowers?page=3&page_size=3").json()
        self.assertEqual(len(page3["results"]), 1)

        # a page beyond the end clamps to the last page rather than erroring
        beyond = self.officer.get("/api/borrowers?page=99&page_size=3").json()
        self.assertEqual(beyond["page"], 3)

    def test_loans_are_paginated_and_filterable(self):
        borrowers = self.officer.get("/api/borrowers?page_size=100").json()["results"]
        self.disbursed_loan(self.product, borrowers[0])

        listing = self.officer.get("/api/loans?page_size=5").json()
        self.assertIn("results", listing)
        self.assertGreaterEqual(listing["count"], 1)

        filtered = self.officer.get(f"/api/loans?branch_id={self.branch.id}").json()
        self.assertGreaterEqual(filtered["count"], 1)

        none_match = self.officer.get("/api/loans?status=written_off").json()
        self.assertEqual(none_match["count"], 0)

    def test_borrowers_can_be_filtered_by_kyc(self):
        self.make_borrower(national_id="63-777777K63", kyc_verified=False)
        unverified = self.officer.get("/api/borrowers?kyc=0").json()
        self.assertEqual(unverified["count"], 1)

    def test_audit_log_filters_and_paginates(self):
        listing = self.admin.get("/api/reports/audit?action=create&page_size=5").json()
        self.assertIn("results", listing)
        self.assertTrue(all("create" in row["action"] for row in listing["results"]))
