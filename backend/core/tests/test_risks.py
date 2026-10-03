"""The risk register: owners maintain their own risks, the residual rating moves
only through a review, and administrators hand risks out, close and reopen them."""
from datetime import date, timedelta

from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient, APITestCase

from core.models import AuditLog, Risk, RiskStatus, Role, User
from core.services.amortisation import add_months
from core.services.risks import BANDS, rating, score_range


class RatingBandTests(SimpleTestCase):
    def test_the_band_boundaries_are_where_the_register_says(self):
        self.assertEqual(rating(1, 4), "low")
        self.assertEqual(rating(1, 5), "medium")
        self.assertEqual(rating(3, 3), "medium")
        self.assertEqual(rating(2, 5), "high")
        self.assertEqual(rating(4, 4), "high")
        self.assertEqual(rating(4, 5), "critical")
        self.assertEqual(rating(5, 5), "critical")

    def test_filtering_by_band_finds_exactly_the_scores_the_band_names(self):
        # The list filters on a score range in SQL and the serializer bands in
        # Python; if the two disagreed, "show me the high risks" would miss some.
        for likelihood in range(1, 6):
            for impact in range(1, 6):
                band = rating(likelihood, impact)
                low, high = score_range(band)
                self.assertTrue(low <= likelihood * impact <= high,
                                f"{likelihood}x{impact} is {band} but outside {low}-{high}")

    def test_the_bands_cover_every_score_once(self):
        covered = []
        for band, _ in BANDS:
            low, high = score_range(band)
            covered += range(low, high + 1)
        self.assertEqual(sorted(covered), list(range(1, 26)))


class RiskTestBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = User.objects.create_user("admin", "admin123", full_name="Admin",
                                                  role=Role.ADMIN)
        cls.officer_user = User.objects.create_user("officer", "officer123",
                                                    full_name="Officer", role=Role.LOAN_OFFICER)
        cls.teller_user = User.objects.create_user("teller", "teller123", full_name="Teller",
                                                   role=Role.TELLER)
        cls.viewer_user = User.objects.create_user("viewer", "viewer123", full_name="Viewer",
                                                   role=Role.VIEWER)

    def client_for(self, username, password):
        client = APIClient()
        response = client.post("/api/auth/login", {"username": username, "password": password},
                               format="json")
        self.assertEqual(response.status_code, 200, response.content)
        client.credentials(HTTP_AUTHORIZATION="Bearer " + response.json()["access_token"])
        return client

    def setUp(self):
        self.today = date.today()
        self.admin = self.client_for("admin", "admin123")
        self.officer = self.client_for("officer", "officer123")
        self.teller = self.client_for("teller", "teller123")
        self.viewer = self.client_for("viewer", "viewer123")

    def raise_risk(self, client=None, expect=201, **overrides):
        payload = {
            "title": "Cash shortages at the counter", "category": "operational",
            "inherent_likelihood": 4, "inherent_impact": 4,
            "controls": "Dual count at close of day",
            "residual_likelihood": 2, "residual_impact": 3,
            "treatment": "treat", "review_every_months": 3,
        }
        payload.update(overrides)
        response = (client or self.officer).post("/api/risks", payload, format="json")
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def review(self, client, risk, expect=201, **overrides):
        payload = {"residual_likelihood": 1, "residual_impact": 3,
                   "note": "Counts reconciled daily for a quarter with no shortage"}
        payload.update(overrides)
        response = client.post(f"/api/risks/{risk['id']}/reviews", payload, format="json")
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()


class RaisingTests(RiskTestBase):
    def test_whoever_raises_a_risk_owns_it(self):
        risk = self.raise_risk(self.officer)
        self.assertEqual(risk["risk_no"], "RSK-00001")
        self.assertEqual(risk["owner_id"], self.officer_user.id)
        self.assertEqual(risk["raised_by_name"], "Officer")
        self.assertTrue(risk["can_edit"])

    def test_raising_a_risk_starts_its_history_and_its_review_clock(self):
        risk = self.raise_risk()
        self.assertEqual(risk["last_reviewed_on"], self.today.isoformat())
        self.assertEqual(risk["next_review_on"], add_months(self.today, 3).isoformat())
        self.assertEqual(len(risk["reviews"]), 1)
        self.assertEqual(risk["reviews"][0]["residual_score"], 6)
        self.assertIn("Raised", risk["reviews"][0]["note"])

    def test_scores_and_ratings_come_from_the_server(self):
        risk = self.raise_risk(inherent_likelihood=5, inherent_impact=4,
                               residual_likelihood=2, residual_impact=4)
        self.assertEqual((risk["inherent_score"], risk["inherent_rating"]), (20, "critical"))
        self.assertEqual((risk["residual_score"], risk["residual_rating"]), (8, "medium"))

    def test_a_viewer_can_read_the_register_but_not_raise_a_risk(self):
        self.raise_risk()
        self.assertEqual(self.viewer.get("/api/risks").status_code, 200)
        self.raise_risk(self.viewer, expect=403)

    def test_only_an_admin_can_raise_a_risk_for_someone_else(self):
        self.raise_risk(self.officer, expect=403, owner=self.teller_user.id)
        risk = self.raise_risk(self.admin, owner=self.teller_user.id)
        self.assertEqual(risk["owner_id"], self.teller_user.id)

    def test_a_disabled_account_cannot_be_made_an_owner(self):
        self.teller_user.is_active = False
        self.teller_user.save()
        self.raise_risk(self.admin, expect=400, owner=self.teller_user.id)

    def test_the_residual_rating_cannot_exceed_the_inherent_one(self):
        # Controls reduce a risk; a residual above inherent means one rating is wrong.
        self.raise_risk(expect=400, inherent_likelihood=3, residual_likelihood=4)
        self.raise_risk(expect=400, inherent_impact=2, residual_impact=3)

    def test_ratings_are_on_a_one_to_five_scale(self):
        self.raise_risk(expect=400, inherent_likelihood=6)
        self.raise_risk(expect=400, residual_impact=0)

    def test_the_review_interval_is_one_of_the_offered_ones(self):
        self.raise_risk(expect=400, review_every_months=2)

    def test_the_database_refuses_a_residual_above_inherent_too(self):
        # The API checks it, but so does SQL Server, so an edit in SSMS or the
        # Django admin cannot slip one in either.
        risk = self.raise_risk()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Risk.objects.filter(pk=risk["id"]).update(residual_likelihood=5)


class OwnershipTests(RiskTestBase):
    def setUp(self):
        super().setUp()
        self.risk = self.raise_risk(self.admin, owner=self.teller_user.id)

    def test_the_owner_maintains_their_own_risk(self):
        response = self.teller.patch(f"/api/risks/{self.risk['id']}",
                                     {"controls": "Dual count and CCTV over the till"},
                                     format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["controls"], "Dual count and CCTV over the till")
        self.review(self.teller, self.risk)

    def test_someone_who_does_not_own_it_cannot_change_it_whatever_their_role(self):
        # A loan officer outranks a teller, but does not own this risk.
        response = self.officer.patch(f"/api/risks/{self.risk['id']}",
                                      {"controls": "Nothing"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.review(self.officer, self.risk, expect=403)

    def test_ownership_is_the_permission_even_for_a_viewer(self):
        # A board member or compliance officer with a read-only role can still be
        # made accountable for a risk, and then keeps it current.
        self.admin.patch(f"/api/risks/{self.risk['id']}", {"owner": self.viewer_user.id},
                         format="json")
        response = self.viewer.patch(f"/api/risks/{self.risk['id']}",
                                     {"action_plan": "Quarterly surprise cash counts"},
                                     format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.review(self.viewer, self.risk)

    def test_an_admin_can_change_any_risk(self):
        response = self.admin.patch(f"/api/risks/{self.risk['id']}",
                                    {"treatment": "transfer"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.review(self.admin, self.risk)

    def test_only_an_admin_can_hand_a_risk_to_someone_else(self):
        response = self.teller.patch(f"/api/risks/{self.risk['id']}",
                                     {"owner": self.officer_user.id}, format="json")
        self.assertEqual(response.status_code, 403)

        response = self.admin.patch(f"/api/risks/{self.risk['id']}",
                                    {"owner": self.officer_user.id}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        # The new owner can now maintain it, and the old one no longer can.
        self.review(self.officer, self.risk)
        self.review(self.teller, self.risk, expect=403)

    def test_can_edit_tells_each_user_where_they_may_act(self):
        mine = self.raise_risk(self.officer, title="Officer's own risk")
        rows = {r["id"]: r for r in self.officer.get("/api/risks").json()["results"]}
        self.assertTrue(rows[mine["id"]]["can_edit"])
        self.assertFalse(rows[self.risk["id"]]["can_edit"])


class ReviewTests(RiskTestBase):
    def setUp(self):
        super().setUp()
        self.risk = self.raise_risk()

    def test_a_review_rerates_the_risk_and_keeps_the_history(self):
        after = self.review(self.officer, self.risk, residual_likelihood=1, residual_impact=3)
        self.assertEqual((after["residual_likelihood"], after["residual_impact"]), (1, 3))
        self.assertEqual(after["residual_rating"], "low")
        self.assertEqual(after["next_review_on"], add_months(self.today, 3).isoformat())
        # Newest first: this review, then the rating the risk was raised with.
        self.assertEqual([r["residual_score"] for r in after["reviews"]], [3, 6])
        self.assertEqual(after["reviews"][0]["reviewed_by_name"], "Officer")

    def test_a_review_can_update_the_controls_and_plan_at_the_same_time(self):
        after = self.review(self.officer, self.risk, controls="Dual count and CCTV",
                            action_plan="Install a second camera",
                            action_due=(self.today + timedelta(days=30)).isoformat())
        self.assertEqual(after["controls"], "Dual count and CCTV")
        self.assertEqual(after["action_plan"], "Install a second camera")

    def test_the_residual_rating_changes_only_through_a_review(self):
        response = self.officer.patch(f"/api/risks/{self.risk['id']}",
                                      {"residual_likelihood": 1}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("recording a review", response.json()["detail"])
        self.assertEqual(Risk.objects.get(pk=self.risk["id"]).residual_likelihood, 2)

    def test_a_review_cannot_rate_above_the_inherent_rating(self):
        self.review(self.officer, self.risk, expect=400, residual_likelihood=5)

    def test_lowering_the_inherent_rating_below_the_residual_is_refused(self):
        response = self.officer.patch(f"/api/risks/{self.risk['id']}",
                                      {"inherent_likelihood": 1}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_a_review_needs_a_note_that_says_something(self):
        self.review(self.officer, self.risk, expect=400, note="ok")

    def test_a_review_cannot_be_dated_in_the_future(self):
        self.review(self.officer, self.risk, expect=400,
                    reviewed_on=(self.today + timedelta(days=1)).isoformat())

    def test_a_review_cannot_be_dated_before_the_last_one(self):
        self.review(self.officer, self.risk, expect=400,
                    reviewed_on=(self.today - timedelta(days=1)).isoformat())

    def test_the_next_review_must_come_after_this_one(self):
        self.review(self.officer, self.risk, expect=400,
                    next_review_on=self.today.isoformat())

    def test_shortening_the_interval_brings_the_next_review_forward(self):
        response = self.officer.patch(f"/api/risks/{self.risk['id']}",
                                      {"review_every_months": 1}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["next_review_on"],
                         add_months(self.today, 1).isoformat())

    def test_an_overdue_review_is_flagged_counted_and_filterable(self):
        Risk.objects.filter(pk=self.risk["id"]).update(
            next_review_on=self.today - timedelta(days=1))
        other = self.raise_risk(title="Not overdue")

        rows = self.officer.get("/api/risks?overdue=1").json()["results"]
        self.assertEqual([r["id"] for r in rows], [self.risk["id"]])
        self.assertTrue(rows[0]["review_overdue"])
        self.assertFalse(self.officer.get(f"/api/risks/{other['id']}").json()["review_overdue"])

        summary = self.officer.get("/api/risks/summary").json()
        self.assertEqual(summary["review_overdue"], 1)
        self.assertEqual(summary["mine_overdue"], 1)

        # Reviewing it is what clears it.
        self.review(self.officer, self.risk)
        self.assertEqual(self.officer.get("/api/risks/summary").json()["review_overdue"], 0)


class ClosureTests(RiskTestBase):
    def setUp(self):
        super().setUp()
        self.risk = self.raise_risk()

    def close(self, client, reason="The counter has been replaced by mobile money only"):
        return client.post(f"/api/risks/{self.risk['id']}/close", {"reason": reason},
                           format="json")

    def test_only_an_admin_closes_a_risk_and_says_why(self):
        self.assertEqual(self.close(self.officer).status_code, 403)
        self.assertEqual(self.close(self.admin, reason="done").status_code, 400)
        response = self.close(self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], RiskStatus.CLOSED)
        self.assertEqual(response.json()["closed_on"], self.today.isoformat())

    def test_a_closed_risk_cannot_be_changed_until_it_is_reopened(self):
        self.close(self.admin)
        response = self.officer.patch(f"/api/risks/{self.risk['id']}",
                                      {"controls": "x"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.review(self.officer, self.risk, expect=400)

        response = self.admin.post(f"/api/risks/{self.risk['id']}/reopen",
                                   {"reason": "Counter reopened at the Mutare branch"},
                                   format="json")
        self.assertEqual(response.status_code, 200, response.content)
        reopened = response.json()
        self.assertEqual(reopened["status"], RiskStatus.OPEN)
        # Its rating is stale by definition, so it is due for review straight away.
        self.assertEqual(reopened["next_review_on"], self.today.isoformat())
        self.review(self.officer, self.risk)

    def test_closed_risks_leave_the_register_unless_asked_for(self):
        self.close(self.admin)
        self.assertEqual(self.officer.get("/api/risks").json()["count"], 0)
        self.assertEqual(self.officer.get("/api/risks?status=closed").json()["count"], 1)
        self.assertEqual(self.officer.get("/api/risks?status=all").json()["count"], 1)
        summary = self.officer.get("/api/risks/summary").json()
        self.assertEqual((summary["open"], summary["closed"]), (0, 1))


class RegisterTests(RiskTestBase):
    def setUp(self):
        super().setUp()
        # residual 2x3=6 medium, 4x4=16 high, 1x2=2 low
        self.medium = self.raise_risk(self.officer, title="Medium one")
        self.high = self.raise_risk(self.admin, owner=self.teller_user.id, title="High one",
                                    category="fraud", inherent_likelihood=5,
                                    inherent_impact=5, residual_likelihood=4,
                                    residual_impact=4)
        self.low = self.raise_risk(self.officer, title="Low one", category="technology",
                                   residual_likelihood=1, residual_impact=2)

    def ids(self, path):
        response = self.officer.get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return [r["id"] for r in response.json()["results"]]

    def test_the_register_lists_the_worst_risks_first(self):
        self.assertEqual(self.ids("/api/risks"),
                         [self.high["id"], self.medium["id"], self.low["id"]])

    def test_filters_narrow_the_register(self):
        self.assertEqual(self.ids("/api/risks?rating=high"), [self.high["id"]])
        self.assertEqual(self.ids("/api/risks?owner=me"), [self.medium["id"], self.low["id"]])
        self.assertEqual(self.ids("/api/risks?category=technology"), [self.low["id"]])
        self.assertEqual(self.ids("/api/risks?q=High"), [self.high["id"]])

    def test_a_heat_map_cell_filters_on_the_rating_the_map_was_showing(self):
        self.assertEqual(self.ids("/api/risks?likelihood=4&impact=4"), [self.high["id"]])
        self.assertEqual(self.ids("/api/risks?basis=inherent&likelihood=5&impact=5"),
                         [self.high["id"]])
        self.assertEqual(self.ids("/api/risks?basis=inherent&likelihood=4&impact=4"),
                         [self.medium["id"], self.low["id"]])

    def test_the_summary_counts_the_register_and_draws_both_heat_maps(self):
        summary = self.officer.get("/api/risks/summary").json()
        self.assertEqual(summary["open"], 3)
        self.assertEqual(summary["mine"], 2)
        self.assertEqual(summary["by_rating"],
                         {"critical": 0, "high": 1, "medium": 1, "low": 1})
        residual = {(c["likelihood"], c["impact"]): c["count"]
                    for c in summary["heatmap"]["residual"]}
        self.assertEqual(residual, {(2, 3): 1, (4, 4): 1, (1, 2): 1})
        inherent = {(c["likelihood"], c["impact"]): c["count"]
                    for c in summary["heatmap"]["inherent"]}
        self.assertEqual(inherent, {(4, 4): 2, (5, 5): 1})

    def test_a_risk_whose_owner_is_disabled_counts_as_unowned(self):
        self.assertEqual(self.officer.get("/api/risks/summary").json()["unowned"], 0)
        response = self.admin.patch(f"/api/users/{self.teller_user.id}", {"is_active": False},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.officer.get("/api/risks/summary").json()["unowned"], 1)
        self.assertEqual(self.ids("/api/risks?owner=none"), [self.high["id"]])

    def test_a_risk_with_a_disabled_owner_can_still_be_edited(self):
        # Otherwise nothing about it could change until someone reassigned it.
        self.admin.patch(f"/api/users/{self.teller_user.id}", {"is_active": False},
                         format="json")
        response = self.admin.patch(f"/api/risks/{self.high['id']}",
                                    {"owner": self.teller_user.id, "treatment": "transfer"},
                                    format="json")
        self.assertEqual(response.status_code, 200, response.content)
        # ...but it cannot be handed TO a disabled account.
        response = self.admin.patch(f"/api/risks/{self.medium['id']}",
                                    {"owner": self.teller_user.id}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_the_register_exports_to_csv(self):
        response = self.officer.get("/api/risks?fmt=csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        lines = response.content.decode().splitlines()
        self.assertIn("residual_rating", lines[0])
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[1].startswith(self.high["risk_no"]))

    def test_every_change_is_in_the_audit_trail(self):
        risk = self.medium
        self.officer.patch(f"/api/risks/{risk['id']}", {"controls": "More"}, format="json")
        self.review(self.officer, risk)
        self.admin.patch(f"/api/risks/{risk['id']}", {"owner": self.teller_user.id},
                         format="json")
        self.admin.post(f"/api/risks/{risk['id']}/close",
                        {"reason": "Merged into RSK-00002"}, format="json")
        self.admin.post(f"/api/risks/{risk['id']}/reopen",
                        {"reason": "Split back out after review"}, format="json")
        actions = list(AuditLog.objects.filter(entity="risk", entity_id=risk["id"])
                       .order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["create", "update", "review", "reassign", "close", "reopen"])

    def test_the_register_does_not_query_per_risk(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.officer.get("/api/risks").status_code, 200)
            return len(queries)

        before = count()
        for index in range(3):
            self.raise_risk(self.officer, title=f"Another {index}")
        after = count()
        self.assertEqual(before, after,
                         f"the register's queries grew from {before} to {after} when it "
                         f"doubled — it is back to a query per risk")
