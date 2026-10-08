"""AML screening: lists in, borrowers matched, reviewed, and loans held until they are."""
from django.core.files.uploadedfile import SimpleUploadedFile

from core.models import Borrower, ScreeningHit, ScreeningStatus
from core.services import screening

from .fixtures import LoanFixtures

UN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST>
  <INDIVIDUALS>
    <INDIVIDUAL>
      <DATAID>1</DATAID>
      <FIRST_NAME>TAPIWA</FIRST_NAME>
      <SECOND_NAME>ROBERT</SECOND_NAME>
      <THIRD_NAME>MOYO</THIRD_NAME>
      <REFERENCE_NUMBER>QDi.999</REFERENCE_NUMBER>
      <INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>T. R. Moyo</ALIAS_NAME></INDIVIDUAL_ALIAS>
      <INDIVIDUAL_DATE_OF_BIRTH><YEAR>1975</YEAR></INDIVIDUAL_DATE_OF_BIRTH>
      <INDIVIDUAL_DOCUMENT><NUMBER>63-999999X63</NUMBER></INDIVIDUAL_DOCUMENT>
    </INDIVIDUAL>
  </INDIVIDUALS>
  <ENTITIES>
    <ENTITY><FIRST_NAME>ACME FRONT COMPANY</FIRST_NAME><REFERENCE_NUMBER>QDe.1</REFERENCE_NUMBER></ENTITY>
  </ENTITIES>
</CONSOLIDATED_LIST>"""


class ScoringTests(LoanFixtures):
    def test_word_order_case_and_accents_do_not_matter(self):
        a = screening.normalise("Moyo, Tápiwa")
        b = screening.normalise("TAPIWA MOYO")
        self.assertEqual(screening.score(a, b), 100)

    def test_a_two_word_name_inside_a_longer_listed_name_is_a_hit(self):
        self.assertGreaterEqual(screening.score(screening.normalise("Tapiwa Moyo"),
                                                screening.normalise("Tapiwa Robert Moyo")),
                                screening.THRESHOLD)

    def test_a_shared_surname_alone_is_not(self):
        self.assertLess(screening.score(screening.normalise("Chipo Moyo"),
                                        screening.normalise("Tapiwa Robert Moyo")),
                        screening.THRESHOLD)


class ScreeningTests(LoanFixtures):
    def borrower(self, first, last, **extra):
        return Borrower.objects.create(borrower_no=f"BRW-T{Borrower.objects.count()}",
                                       first_name=first, last_name=last,
                                       national_id=extra.pop("national_id", f"99-{first}{last}"),
                                       phone="0771000000", **extra)

    def load_un(self):
        response = self.admin.post("/api/screening/lists",
                                   {"file": SimpleUploadedFile("un.xml", UN_XML, "text/xml")},
                                   format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_loading_the_un_list_screens_everyone_and_finds_the_match(self):
        match = self.borrower("Tapiwa", "Moyo")
        self.borrower("Chipo", "Banda")
        result = self.load_un()
        self.assertEqual(result["loaded"], 2)
        self.assertEqual(list(ScreeningHit.objects.values_list("borrower_id", flat=True)), [match.id])

    def test_a_different_year_of_birth_lowers_the_score_below_a_hit(self):
        from datetime import date

        self.borrower("Tapiwa", "Moyo", date_of_birth=date(1999, 1, 1))
        self.load_un()
        self.assertFalse(ScreeningHit.objects.exists())

    def test_a_matching_id_number_is_a_hit_whatever_the_name(self):
        self.borrower("Someone", "Else", national_id="63-999999X63")
        self.load_un()
        self.assertEqual(ScreeningHit.objects.get().reason, "ID number matches")

    def test_a_new_borrower_is_screened_when_created(self):
        self.load_un()
        response = self.officer.post("/api/borrowers", {
            "first_name": "Tapiwa", "last_name": "Moyo", "national_id": "63-123123T63",
            "phone": "0771234567", "net_salary": 1000, "payday": 25,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(ScreeningHit.objects.filter(borrower_id=response.json()["id"]).exists())

    def test_an_open_match_holds_approval_and_clearing_it_releases_it(self):
        self.load_un()
        borrower_id = self.make_borrower(first_name="Tapiwa", last_name="Moyo",
                                         national_id="63-456456T63")["id"]
        loan = self.officer.post("/api/loans", {"borrower_id": borrower_id,
                                                "product_id": self.product["id"],
                                                "principal": 500, "term_months": 6},
                                 format="json").json()
        refused = self.admin.post(f"/api/loans/{loan['id']}/approve")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("sanctions", str(refused.json()).lower())

        hit = ScreeningHit.objects.get(borrower_id=borrower_id)
        self.assertEqual(self.admin.post(f"/api/screening/hits/{hit.id}/review",
                                         {"decision": "cleared"}, format="json").status_code, 400)
        response = self.admin.post(f"/api/screening/hits/{hit.id}/review",
                                   {"decision": "cleared", "note": "Different ID and age; spoke to HR"},
                                   format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.admin.post(f"/api/loans/{loan['id']}/approve").status_code, 200)

    def test_a_confirmed_match_blacklists_the_borrower(self):
        person = self.borrower("Tapiwa", "Moyo")
        self.load_un()
        hit = ScreeningHit.objects.get(borrower=person)
        self.admin.post(f"/api/screening/hits/{hit.id}/review",
                        {"decision": "confirmed", "note": "Same ID number"}, format="json")
        person.refresh_from_db()
        self.assertTrue(person.is_blacklisted)
        with self.assertRaisesMessage(Exception, "confirmed match"):
            screening.assert_clear(person, "approve this loan")

    def test_a_reviewed_hit_survives_the_list_being_loaded_again(self):
        person = self.borrower("Tapiwa", "Moyo")
        self.load_un()
        hit = ScreeningHit.objects.get(borrower=person)
        screening.review(hit, None, ScreeningStatus.CLEARED, "Checked")
        self.load_un()
        self.assertEqual(ScreeningHit.objects.get(borrower=person).status, ScreeningStatus.CLEARED)

    def test_a_csv_list_with_its_own_name(self):
        csv = b"name,aliases,date_of_birth,id_number,reference\nRudo Chari,R. Chari,1980,,LOCAL-1\n"
        self.borrower("Rudo", "Chari")
        response = self.admin.post("/api/screening/lists",
                                   {"file": SimpleUploadedFile("pep.csv", csv, "text/csv"),
                                    "source": "Local PEP list"}, format="multipart")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(ScreeningHit.objects.get().source, "Local PEP list")

    def test_only_administrators_load_lists(self):
        response = self.officer.post("/api/screening/lists",
                                     {"file": SimpleUploadedFile("un.xml", UN_XML, "text/xml")},
                                     format="multipart")
        self.assertEqual(response.status_code, 403)
