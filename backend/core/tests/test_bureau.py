"""The credit bureau check: the register of enquiries and how the scorecard reads it."""
import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from core.models import Borrower, BureauEnquiry, LoanProduct
from core.services import bureau as bureau_svc
from core.services.scoring import score_application

from .test_components import LedgerBase, dec


class BureauTests(LedgerBase):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def factor(self, card, name):
        return next((f for f in card["factors"] if f["factor"] == name), None)

    # ------------------------------------------------------------------ none
    def test_without_a_bureau_the_page_shows_no_button_and_a_check_is_refused(self):
        status = self.officer.get("/api/bureau/status").json()
        self.assertFalse(status["configured"])
        response = self.officer.post(f"/api/borrowers/{self.borrower['id']}/bureau")
        self.assertEqual(response.status_code, 400)
        self.assertIn("No credit bureau is configured", response.json()["detail"])
        self.assertEqual(BureauEnquiry.objects.count(), 0)

    def test_without_a_report_the_scorecard_says_so(self):
        borrower = Borrower.objects.get(pk=self.borrower["id"])
        product = LoanProduct.objects.get(pk=self.product["id"])
        card = score_application(borrower, product, dec(1000), 6, dec("197.02"))
        history = self.factor(card, "Repayment history")
        self.assertEqual(history["max"], 30)
        self.assertIn("no bureau report", history["reason"])
        self.assertIsNone(self.factor(card, "Credit bureau"))
        self.assertEqual(sum(f["max"] for f in card["factors"]), 100)

    # ------------------------------------------------------------------ demo
    @override_settings(BUREAU_BACKEND="demo")
    def test_a_demo_enquiry_is_recorded_and_repeatable(self):
        url = f"/api/borrowers/{self.borrower['id']}/bureau"
        first = self.officer.post(url)
        self.assertEqual(first.status_code, 201, first.content)
        second = self.officer.post(url)
        self.assertEqual(first.json()["score"], second.json()["score"])
        self.assertEqual(first.json()["provider"], "demo")
        self.assertEqual(first.json()["status"], "ok")

        listing = self.officer.get(url).json()
        self.assertTrue(listing["configured"])
        self.assertTrue(listing["demo"])
        self.assertEqual(len(listing["rows"]), 2)
        self.assertEqual(listing["latest_id"], second.json()["id"])

    @override_settings(BUREAU_BACKEND="demo")
    def test_a_viewer_may_read_but_not_run_an_enquiry(self):
        viewer = self.client_for("teller", "teller123")
        url = f"/api/borrowers/{self.borrower['id']}/bureau"
        self.assertEqual(viewer.post(url).status_code, 403)
        self.assertEqual(viewer.get(url).status_code, 200)

    @override_settings(BUREAU_BACKEND="demo")
    def test_a_fresh_report_splits_the_history_points_with_the_bureau(self):
        self.officer.post(f"/api/borrowers/{self.borrower['id']}/bureau")
        borrower = Borrower.objects.get(pk=self.borrower["id"])
        product = LoanProduct.objects.get(pk=self.product["id"])
        card = score_application(borrower, product, dec(1000), 6, dec("197.02"))

        self.assertEqual(self.factor(card, "Repayment history")["max"], 15)
        bureau = self.factor(card, "Credit bureau")
        self.assertEqual(bureau["max"], 15)
        self.assertIn("Bureau report of", bureau["reason"])
        self.assertEqual(sum(f["max"] for f in card["factors"]), 100)
        self.assertLessEqual(card["score"], 100)

    @override_settings(BUREAU_BACKEND="demo", BUREAU_VALID_DAYS=30)
    def test_a_stale_report_is_not_read(self):
        row = bureau_svc.enquire(Borrower.objects.get(pk=self.borrower["id"]), None)
        row.enquired_at = timezone.now() - timedelta(days=31)
        row.save(update_fields=["enquired_at"])
        self.assertIsNone(bureau_svc.latest(row.borrower))

    def test_a_default_on_record_scores_nothing_for_the_bureau(self):
        borrower = Borrower.objects.get(pk=self.borrower["id"])
        BureauEnquiry.objects.create(borrower=borrower, provider="http", status="ok",
                                     score=700, defaults=1)
        product = LoanProduct.objects.get(pk=self.product["id"])
        card = score_application(borrower, product, dec(1000), 6, dec("197.02"))
        self.assertEqual(self.factor(card, "Credit bureau")["points"], 0)

    # ------------------------------------------------------------------ http
    @override_settings(BUREAU_BACKEND="http", BUREAU_HTTP={
        "url": "https://bureau.example.com/enquiry", "method": "POST",
        "id_field": "idNumber", "timeout": 5, "extra": {"subscriber": "SIMBA"},
        "headers": {"Authorization": "Bearer t"},
        "paths": {"score": "result.score", "score_max": "", "open_accounts": "result.accounts.open",
                  "accounts_in_arrears": "result.accounts.arrears", "defaults": "result.defaults",
                  "worst_days_in_arrears": "result.worstDpd", "total_exposure": "result.exposure",
                  "reference": "id"},
    })
    def test_the_http_backend_reads_the_configured_paths_and_keeps_only_them(self):
        answer = json.dumps({"id": "REF-9", "result": {
            "score": 612, "accounts": {"open": 3, "arrears": 1}, "defaults": 0, "worstDpd": 45,
            "exposure": "1234.5", "otherLenderAccountNumbers": ["secret"]}}).encode()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return answer

        sent = {}

        def fake_urlopen(request, timeout):
            sent["body"] = json.loads(request.data)
            sent["headers"] = dict(request.header_items())
            return FakeResponse()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            response = self.officer.post(f"/api/borrowers/{self.borrower['id']}/bureau")

        self.assertEqual(response.status_code, 201, response.content)
        row = response.json()
        self.assertEqual(row["score"], 612)
        self.assertEqual(row["open_accounts"], 3)
        self.assertEqual(row["worst_days_in_arrears"], 45)
        self.assertEqual(row["total_exposure"], "1234.50")
        self.assertEqual(row["reference"], "REF-9")
        self.assertEqual(sent["body"]["idNumber"], self.borrower["national_id"])
        self.assertEqual(sent["body"]["subscriber"], "SIMBA")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer t")
        stored = BureauEnquiry.objects.get(pk=row["id"])
        self.assertNotIn("secret", stored.detail or "")

    @override_settings(BUREAU_BACKEND="http", BUREAU_HTTP={"url": ""})
    def test_a_misconfigured_bureau_is_a_failed_enquiry_on_the_register(self):
        response = self.officer.post(f"/api/borrowers/{self.borrower['id']}/bureau")
        self.assertEqual(response.status_code, 502)
        row = response.json()
        self.assertEqual(row["status"], "failed")
        self.assertIn("BUREAU_HTTP_URL", row["error"])
        self.assertEqual(BureauEnquiry.objects.filter(status="failed").count(), 1)
        # A failed attempt is never read as a report.
        self.assertIsNone(bureau_svc.latest(Borrower.objects.get(pk=self.borrower["id"])))
