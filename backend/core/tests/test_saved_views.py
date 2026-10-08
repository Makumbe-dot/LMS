"""Saved list filters: a user's own, by page and name."""
from core.models import SavedView, User

from .fixtures import LoanFixtures


class SavedViewTests(LoanFixtures):
    def save(self, client=None, **body):
        payload = {"page": "loans", "name": "My arrears", "params": {"in_arrears": True}, **body}
        return (client or self.officer).post("/api/saved-views", payload, format="json")

    def test_a_view_is_saved_and_listed_by_page(self):
        self.assertEqual(self.save().status_code, 201)
        self.save(page="borrowers", name="Unverified", params={"kyc": "0"})
        mine = self.officer.get("/api/saved-views?page=loans").json()
        self.assertEqual([(v["name"], v["params"]) for v in mine],
                         [("My arrears", {"in_arrears": True})])

    def test_the_same_name_replaces_the_filters(self):
        self.save()
        self.save(params={"status": "active"})
        self.assertEqual(SavedView.objects.get().params, {"status": "active"})

    def test_each_user_sees_and_deletes_only_their_own(self):
        view = self.save().json()
        self.assertEqual(self.teller.get("/api/saved-views").json(), [])
        self.assertEqual(self.teller.delete(f"/api/saved-views/{view['id']}").status_code, 404)
        self.assertEqual(self.officer.delete(f"/api/saved-views/{view['id']}").status_code, 204)

    def test_only_short_filter_values_on_known_pages(self):
        self.assertEqual(self.save(page="ledger").status_code, 400)
        self.assertEqual(self.save(params={"q": "x" * 500}).status_code, 400)
        self.assertEqual(self.save(params={"nested": {"a": 1}}).status_code, 400)
        self.assertEqual(self.save(name="  ").status_code, 400)
        self.assertEqual(User.objects.get(username="officer").saved_views.count(), 0)
