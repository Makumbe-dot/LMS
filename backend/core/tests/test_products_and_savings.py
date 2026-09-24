"""Savings, joint-liability groups, the charges catalogue, top-ups and the
printable loan agreement."""
from datetime import date, timedelta
from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from core.models import (
    BorrowerGroup,
    Branch,
    Charge,
    GroupMember,
    JournalEntry,
    Loan,
    LoanStatus,
    OrganisationSetting,
    ProductCharge,
    Role,
    SavingsAccount,
    SavingsProduct,
    SavingsStatus,
    User,
)
from core.services import ledger as gl
from core.services import savings as savings_svc


def dec(value) -> Decimal:
    return Decimal(str(value))


class Base(APITestCase):
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
        gl.ensure_chart_of_accounts()
        self.admin = self.client_for("admin", "admin123")
        self.officer = self.client_for("officer", "officer123")
        self.teller = self.client_for("teller", "teller123")

    def make_product(self, **overrides):
        payload = {
            "code": "T-SAL", "name": "Test Salary", "interest_rate_pct": 5,
            "rate_method": "reducing", "min_amount": 100, "max_amount": 20000,
            "min_term_months": 1, "max_term_months": 24, "admin_fee_pct": 3,
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
            "phone": "0771234567", "net_salary": 4000, "payday": 25, "kyc_verified": True,
            "employer": "Test Employer", "employee_no": "EMP1", "branch": self.branch.id,
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


# ---------------------------------------------------------------- savings
class SavingsTests(Base):
    def setUp(self):
        super().setUp()
        self.product = SavingsProduct.objects.create(
            code="SAV-ORD", name="Ordinary Savings", interest_rate_pct_pa=dec(12),
            min_balance=dec(5), monthly_fee=dec("0.50"))
        self.borrower = self.make_borrower()

    def open(self, **overrides):
        payload = {"borrower_id": self.borrower["id"], "product_id": self.product.id}
        payload.update(overrides)
        response = self.teller.post("/api/savings/accounts", payload, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_opening_an_account_with_a_deposit(self):
        account = self.open(opening_deposit="100.00")
        self.assertTrue(account["account_no"].startswith("SAV-"))
        self.assertEqual(dec(account["balance"]), dec("100.00"))
        self.assertEqual(account["status"], "active")

    def test_one_account_per_product_per_member(self):
        self.open()
        response = self.teller.post("/api/savings/accounts", {
            "borrower_id": self.borrower["id"], "product_id": self.product.id,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already has", response.json()["detail"])

    def test_deposit_and_withdrawal_move_the_balance(self):
        account = self.open(opening_deposit="100.00")
        aid = account["id"]

        self.teller.post(f"/api/savings/accounts/{aid}/deposit",
                         {"amount": "50.00", "method": "cash"}, format="json")
        self.teller.post(f"/api/savings/accounts/{aid}/withdraw",
                         {"amount": "30.00", "method": "cash"}, format="json")

        detail = self.teller.get(f"/api/savings/accounts/{aid}").json()
        self.assertEqual(dec(detail["balance"]), dec("120.00"))
        # each movement records the balance that followed it
        self.assertEqual([dec(t["balance_after"]) for t in detail["transactions"]],
                         [dec("100.00"), dec("150.00"), dec("120.00")])

    def test_the_minimum_balance_is_protected(self):
        account = self.open(opening_deposit="100.00")
        response = self.teller.post(f"/api/savings/accounts/{account['id']}/withdraw",
                                    {"amount": "100.00"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("exceeds the 95.00 available", response.json()["detail"])

    def test_a_locked_product_refuses_withdrawals(self):
        locked = SavingsProduct.objects.create(code="SAV-FIX", name="Contractual",
                                               allow_withdrawals=False)
        account = self.open(product_id=locked.id, opening_deposit="100.00")
        response = self.teller.post(f"/api/savings/accounts/{account['id']}/withdraw",
                                    {"amount": "10.00"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not allow withdrawals", response.json()["detail"])

    def test_a_deposit_can_be_reversed(self):
        account = self.open(opening_deposit="100.00")
        aid = account["id"]
        deposit = self.teller.post(f"/api/savings/accounts/{aid}/deposit",
                                   {"amount": "40.00"}, format="json").json()

        response = self.teller.post(
            f"/api/savings/accounts/{aid}/transactions/{deposit['id']}/reverse",
            {"narration": "Counted wrong"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(dec(self.teller.get(f"/api/savings/accounts/{aid}").json()["balance"]),
                         dec("100.00"))

    def test_interest_and_the_monthly_fee(self):
        account = self.open(opening_deposit="1200.00")
        result = savings_svc.accrue_interest(date(2026, 4, 30))
        # 12% a year on 1200 is 12.00 a month; the fee is 0.50
        self.assertEqual(result["interest_credited"], dec("12.00"))
        self.assertEqual(result["fees_taken"], dec("0.50"))

        detail = self.teller.get(f"/api/savings/accounts/{account['id']}").json()
        self.assertEqual(dec(detail["balance"]), dec("1211.50"))

    def test_interest_is_credited_once_a_month(self):
        self.open(opening_deposit="1200.00")
        savings_svc.accrue_interest(date(2026, 4, 30))
        again = savings_svc.accrue_interest(date(2026, 5, 10))  # same month window
        self.assertEqual(again["accounts_credited"], 0)
        next_month = savings_svc.accrue_interest(date(2026, 6, 1))
        self.assertEqual(next_month["accounts_credited"], 1)

    def test_closing_pays_out_the_balance(self):
        account = self.open(opening_deposit="100.00")
        response = self.admin.post(f"/api/savings/accounts/{account['id']}/close",
                                   {"narration": "Member left"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        closed = response.json()
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(dec(closed["balance"]), dec("0.00"))

    def test_savings_hit_the_ledger_as_a_liability(self):
        account = self.open(opening_deposit="100.00")
        self.teller.post(f"/api/savings/accounts/{account['id']}/withdraw",
                         {"amount": "30.00"}, format="json")

        balance = gl.trial_balance()
        self.assertTrue(balance["balanced"], balance)
        by_code = {row["code"]: row for row in balance["rows"]}
        # Members' money is owed to them, not earned by the lender.
        self.assertEqual(by_code["2000"]["balance"], dec("70.00"))
        self.assertEqual(by_code["2000"]["side"], "credit")
        self.assertEqual(
            by_code["2000"]["balance"],
            sum((a.balance for a in SavingsAccount.objects.all()), Decimal("0")))

    def test_the_portfolio_summary(self):
        self.open(opening_deposit="100.00")
        summary = self.officer.get("/api/savings/portfolio").json()
        self.assertEqual(summary["accounts"], 1)
        self.assertEqual(summary["active_accounts"], 1)
        self.assertEqual(dec(summary["total_balance"]), dec("100.00"))

    def test_a_viewer_cannot_post_to_savings(self):
        account = self.open(opening_deposit="10.00")
        User.objects.create_user("viewer", "viewer123", full_name="Viewer", role=Role.VIEWER)
        viewer = self.client_for("viewer", "viewer123")
        response = viewer.post(f"/api/savings/accounts/{account['id']}/deposit",
                               {"amount": "5.00"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_dormancy(self):
        account = self.open(opening_deposit="50.00")
        SavingsAccount.objects.filter(pk=account["id"]).update(opened_on=date(2024, 1, 1))
        account_obj = SavingsAccount.objects.get(pk=account["id"])
        account_obj.transactions.all().update(txn_date=date(2024, 1, 1))

        result = savings_svc.mark_dormant(6, date(2026, 9, 1))
        self.assertEqual(result["marked_dormant"], 1)
        self.assertEqual(SavingsAccount.objects.get(pk=account["id"]).status,
                         SavingsStatus.DORMANT)

        # a deposit wakes it up again
        self.teller.post(f"/api/savings/accounts/{account['id']}/deposit",
                         {"amount": "10.00"}, format="json")
        self.assertEqual(SavingsAccount.objects.get(pk=account["id"]).status,
                         SavingsStatus.ACTIVE)


# ---------------------------------------------------------------- groups
class GroupTests(Base):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        # A literal outside the GRP- sequence, so creating a group through the API
        # in these tests cannot collide with this one.
        self.group = BorrowerGroup.objects.create(group_no="GRP-90001", name="Simukai",
                                                  branch=self.branch, status="active")
        # The seeded loans below sit months in the past, so the joint-liability
        # block would fire everywhere. The two tests that exercise the rule turn
        # it on themselves.
        config = OrganisationSetting.load()
        config.group_arrears_block_days = 0
        config.save()

    def add(self, borrower, role="member"):
        return self.officer.post(f"/api/groups/{self.group.id}/members",
                                 {"borrower_id": borrower["id"], "role": role}, format="json")

    def test_a_group_can_be_created_and_staffed(self):
        response = self.officer.post("/api/groups", {
            "name": "Chiedza Farmers", "branch": self.branch.id, "meeting_day": "Friday",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        group = response.json()
        self.assertTrue(group["group_no"].startswith("GRP-"))
        self.assertEqual(group["officer_name"], "Officer")

    def test_members_join_once_and_only_one_group(self):
        first = self.make_borrower()
        self.assertEqual(self.add(first, "leader").status_code, 201)

        again = self.add(first)
        self.assertEqual(again.status_code, 400)
        self.assertIn("already in this group", again.json()["detail"])

        other = BorrowerGroup.objects.create(group_no="GRP-00002", name="Other")
        response = self.officer.post(f"/api/groups/{other.id}/members",
                                     {"borrower_id": first["id"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("one group at a time", response.json()["detail"])

    def test_a_member_with_a_running_loan_cannot_leave(self):
        borrower = self.make_borrower()
        member = self.add(borrower).json()
        self.disbursed_loan(self.product, borrower)

        response = self.officer.delete(f"/api/groups/{self.group.id}/members/{member['id']}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("settle it before leaving", response.json()["detail"])

    def test_standing_aggregates_the_members_exposure(self):
        first = self.make_borrower()
        second = self.make_borrower(national_id="63-222222B63")
        self.add(first, "leader")
        self.add(second)
        self.disbursed_loan(self.product, first, principal=1000)
        self.disbursed_loan(self.product, second, principal=2000)

        standing = self.officer.get(f"/api/groups/{self.group.id}/standing").json()
        self.assertEqual(standing["members"], 2)
        self.assertEqual(standing["active_loans"], 2)
        self.assertGreater(dec(standing["total_outstanding"]), dec(3000))

    def test_joint_liability_blocks_a_new_loan_while_a_member_is_behind(self):
        config = OrganisationSetting.load()
        config.group_arrears_block_days = 30
        config.save()

        behind = self.make_borrower()
        fresh = self.make_borrower(national_id="63-333333C63")
        self.add(behind, "leader")
        self.add(fresh)
        # A loan disbursed long ago and never paid: deeply in arrears by now.
        self.disbursed_loan(self.product, behind, principal=1000,
                            disbursement_date="2025-01-02")

        response = self.officer.post("/api/loans", {
            "borrower_id": fresh["id"], "product_id": self.product["id"],
            "principal": 500, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("joint liability", response.json()["detail"])

    def test_the_rule_can_be_switched_off(self):
        # setUp already switched it off; this asserts that a group deep in
        # arrears is then no obstacle.
        behind = self.make_borrower()
        fresh = self.make_borrower(national_id="63-444444D63")
        self.add(behind)
        self.add(fresh)
        self.disbursed_loan(self.product, behind, disbursement_date="2025-01-02")

        response = self.officer.post("/api/loans", {
            "borrower_id": fresh["id"], "product_id": self.product["id"],
            "principal": 500, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)

    def test_group_performance_lists_the_worst_first(self):
        borrower = self.make_borrower()
        self.add(borrower, "leader")
        self.disbursed_loan(self.product, borrower, disbursement_date="2025-01-02")

        rows = self.officer.get("/api/groups/performance").json()
        self.assertEqual(rows[0]["name"], "Simukai")
        self.assertGreater(rows[0]["worst_days_in_arrears"], 30)

    def test_a_blacklisted_borrower_cannot_join(self):
        borrower = self.make_borrower(national_id="63-555555E63", is_blacklisted=True)
        response = self.add(borrower)
        self.assertEqual(response.status_code, 400)
        self.assertIn("blacklisted", response.json()["detail"])


# ---------------------------------------------------------------- charges
class ChargeTests(Base):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.stamp = Charge.objects.create(code="STAMP", name="Stamp duty", basis="percent",
                                           value=dec("0.25"))
        self.proc = Charge.objects.create(code="PROC", name="Processing", basis="fixed",
                                          value=dec(5))

    def attach(self, *charges):
        return self.admin.put(f"/api/products/{self.product['id']}/charges",
                              {"charge_ids": [c.id for c in charges]}, format="json")

    def test_charges_attach_to_a_product_and_show_in_the_quote(self):
        self.assertEqual(self.attach(self.stamp, self.proc).status_code, 200)
        borrower = self.make_borrower()

        quote = self.officer.post("/api/loans/quote", {
            "product_id": self.product["id"], "principal": 1000, "term_months": 6,
            "borrower_id": borrower["id"],
        }, format="json").json()

        # 0.25% of 1000 = 2.50, plus a flat 5.00
        self.assertEqual(dec(quote["other_charges"]), dec("7.50"))
        self.assertEqual([c["code"] for c in quote["charges"]], ["PROC", "STAMP"])
        # net = 1000 - 30 admin - 10 credit life - 7.50 charges
        self.assertEqual(dec(quote["net_disbursed"]), dec("952.50"))

    def test_charges_are_frozen_onto_the_loan_at_disbursement(self):
        self.attach(self.stamp, self.proc)
        borrower = self.make_borrower()
        loan = self.disbursed_loan(self.product, borrower)

        self.assertEqual(dec(loan["other_charges"]), dec("7.50"))
        self.assertEqual(len(loan["charges"]), 2)
        self.assertEqual(sum(dec(c["amount"]) for c in loan["charges"]), dec("7.50"))

        # renaming the catalogue entry does not rewrite what was charged
        self.admin.patch(f"/api/charges/{self.stamp.id}", {"name": "Duty (revised)"},
                         format="json")
        detail = self.officer.get(f"/api/loans/{loan['id']}").json()
        self.assertIn("Stamp duty", [c["name"] for c in detail["charges"]])

    def test_the_ledger_takes_the_whole_fee_to_income(self):
        self.attach(self.stamp, self.proc)
        borrower = self.make_borrower()
        self.disbursed_loan(self.product, borrower)

        entry = JournalEntry.objects.get(source="disbursement")
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["4100"].credit, dec("47.50"))   # 30 + 10 + 7.50
        self.assertEqual(lines["1000"].credit, dec("952.50"))
        self.assertEqual(entry.total_debit, entry.total_credit)

    def test_a_manual_charge_is_collected_at_the_counter(self):
        borrower = self.make_borrower()
        loan = self.disbursed_loan(self.product, borrower)

        response = self.officer.post(f"/api/loans/{loan['id']}/charges", {
            "name": "Statement reprint", "amount": "2.00",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)

        entry = JournalEntry.objects.get(source="charge")
        lines = {line.account.code: line for line in entry.lines.all()}
        self.assertEqual(lines["1000"].debit, dec("2.00"))
        self.assertEqual(lines["4100"].credit, dec("2.00"))
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_a_used_charge_cannot_be_deleted(self):
        self.attach(self.proc)
        borrower = self.make_borrower()
        self.disbursed_loan(self.product, borrower)

        response = self.admin.delete(f"/api/charges/{self.proc.id}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("deactivate it instead", response.json()["detail"])

    def test_only_an_admin_manages_the_catalogue(self):
        self.assertEqual(self.officer.get("/api/charges").status_code, 200)
        self.assertEqual(
            self.officer.post("/api/charges", {"code": "X", "name": "X", "basis": "fixed",
                                               "value": "1"}, format="json").status_code, 403)

    def test_replacing_the_set_removes_what_is_no_longer_attached(self):
        self.attach(self.stamp, self.proc)
        self.attach(self.stamp)
        attached = self.officer.get(f"/api/products/{self.product['id']}/charges").json()
        self.assertEqual([c["code"] for c in attached], ["STAMP"])
        self.assertEqual(ProductCharge.objects.filter(product_id=self.product["id"]).count(), 1)


# ---------------------------------------------------------------- top-up
class TopUpTests(Base):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()
        self.loan = self.disbursed_loan(self.product, self.borrower, principal=1000, term=6)

    def test_the_quote_shows_what_the_borrower_would_receive(self):
        response = self.officer.post(f"/api/loans/{self.loan['id']}/top-up-quote", {
            "product_id": self.product["id"], "principal": 3000, "term_months": 12,
            "application_date": "2026-04-10",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        quote = response.json()

        self.assertEqual(quote["existing_loan_no"], self.loan["loan_no"])
        self.assertTrue(quote["sufficient"])
        # cash out = net advanced less the settlement of the old loan
        self.assertEqual(
            dec(quote["cash_to_borrower"]),
            dec(quote["net_disbursed"]) - dec(quote["settlement_amount"]))

    def test_a_top_up_too_small_to_settle_is_refused(self):
        response = self.officer.post(f"/api/loans/{self.loan['id']}/top-up", {
            "product_id": self.product["id"], "principal": 200, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not enough to settle", response.json()["detail"])

    def test_disbursing_the_top_up_settles_the_old_loan(self):
        applied = self.officer.post(f"/api/loans/{self.loan['id']}/top-up", {
            "product_id": self.product["id"], "principal": 3000, "term_months": 12,
            "application_date": "2026-04-10",
        }, format="json")
        self.assertEqual(applied.status_code, 201, applied.content)
        new_loan = applied.json()
        self.assertEqual(new_loan["refinanced_from_id"], self.loan["id"])

        # The old loan is untouched until the new one is actually paid out.
        self.assertEqual(self.officer.get(f"/api/loans/{self.loan['id']}").json()["status"],
                         "active")

        self.admin.post(f"/api/loans/{new_loan['id']}/approve")
        response = self.officer.post(f"/api/loans/{new_loan['id']}/disburse",
                                     {"disbursement_date": "2026-04-10"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

        old = self.officer.get(f"/api/loans/{self.loan['id']}").json()
        self.assertEqual(old["status"], "closed")
        self.assertEqual(dec(old["total_outstanding"]), dec(0))
        self.assertEqual(Loan.objects.get(pk=new_loan["id"]).status, LoanStatus.ACTIVE)
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_the_borrower_may_hold_the_old_loan_and_the_top_up_application_together(self):
        self.officer.post(f"/api/loans/{self.loan['id']}/top-up", {
            "product_id": self.product["id"], "principal": 3000, "term_months": 12,
        }, format="json")
        # but not a third, unrelated application
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.product["id"],
            "principal": 500, "term_months": 6,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already has an open loan", response.json()["detail"])

    def test_only_an_active_loan_can_be_topped_up(self):
        self.admin.post(f"/api/loans/{self.loan['id']}/write-off", {"narration": "gone"},
                        format="json")
        response = self.officer.post(f"/api/loans/{self.loan['id']}/top-up-quote", {
            "product_id": self.product["id"], "principal": 3000, "term_months": 12,
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("only an active", response.json()["detail"].lower())


# ---------------------------------------------------------------- agreement
class AgreementTests(Base):
    def setUp(self):
        super().setUp()
        self.product = self.make_product()
        self.borrower = self.make_borrower()

    def test_the_agreement_renders_the_terms_and_the_schedule(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        response = self.officer.get(f"/api/loans/{loan['id']}/agreement")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        self.assertIn(loan["loan_no"], html)
        self.assertIn("Test Borrower", html)
        self.assertIn("63-123456A63", html)
        self.assertIn("LOAN AGREEMENT", html.upper())
        self.assertIn("Repayment schedule", html)
        self.assertIn("2026-03-25", html)          # the first instalment
        self.assertIn("Borrower", html)            # the signature block

    def test_an_undisbursed_application_shows_the_offered_schedule(self):
        response = self.officer.post("/api/loans", {
            "borrower_id": self.borrower["id"], "product_id": self.product["id"],
            "principal": 1000, "term_months": 6,
        }, format="json")
        loan = response.json()
        html = self.officer.get(f"/api/loans/{loan['id']}/agreement").content.decode()
        self.assertIn("Repayment schedule", html)
        self.assertIn(loan["loan_no"], html)

    def test_guarantors_and_security_appear_when_present(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.officer.post(f"/api/borrowers/{self.borrower['id']}/guarantors", {
            "full_name": "Guarantor One", "national_id": "63-999999Z63", "phone": "0779999999",
        }, format="json")
        self.officer.post(f"/api/loans/{loan['id']}/collateral", {
            "type": "vehicle", "description": "Toyota Hilux", "estimated_value": "5000.00",
        }, format="json")

        html = self.officer.get(f"/api/loans/{loan['id']}/agreement").content.decode()
        self.assertIn("Guarantors", html)
        self.assertIn("Guarantor One", html)
        self.assertIn("Security", html)
        self.assertIn("Toyota Hilux", html)

    def test_it_needs_a_signed_in_user(self):
        loan = self.disbursed_loan(self.product, self.borrower)
        self.assertEqual(APIClient().get(f"/api/loans/{loan['id']}/agreement").status_code, 401)
