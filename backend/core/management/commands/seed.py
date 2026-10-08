"""Seed SQL Server with users, products, borrowers and a realistic loan book.

    python manage.py seed              idempotent: skips if users already exist
    python manage.py seed --reset      wipes the loan book first (keeps the schema)
"""
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    RIGHT_PRESETS,
    BureauEnquiry,
    ExchangeRate,
    RevaluationLine,
    RevaluationRun,
    AccountingPeriod,
    AuditLog,
    Borrower,
    CapitalTransaction,
    FacilityTransaction,
    FundingFacility,
    BorrowerDocument,
    BorrowerGroup,
    Branch,
    Charge,
    ChargeBasis,
    Collateral,
    CollateralType,
    GroupMember,
    GroupStatus,
    Guarantor,
    Instalment,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Loan,
    LoanCharge,
    LoanNote,
    LoanProduct,
    MemberRole,
    Notification,
    OrganisationSetting,
    PaymentMethod,
    ProductCharge,
    ProvisionRun,
    ProvisionRunLine,
    RateMethod,
    RepaymentFrequency,
    Role,
    SavingsAccount,
    SavingsProduct,
    SavingsTransaction,
    Sequence,
    Transaction,
    User,
)
from core.services import loans as svc
from core.services.amortisation import add_months, instalment_amount, monthly_equivalent, q
from core.services import funding as funding_svc
from core.services import fx
from core.services import groups as group_svc
from core.services import provisioning as provisioning_svc
from core.services import savings as savings_svc
from core.services.ledger import (
    balance_sheet,
    ensure_chart_of_accounts,
    reconciliation,
    trial_balance,
)
from core.services.notifications import generate_reminders
from core.services.penalties import accrue_penalties
from core.services.repayments import post_repayment

CHARGES = [
    dict(code="STAMP", name="Stamp duty", basis=ChargeBasis.PERCENT_OF_PRINCIPAL,
         value=Decimal("0.25"), description="Statutory duty on the loan agreement"),
    dict(code="PROC", name="Processing charge", basis=ChargeBasis.FIXED, value=Decimal("5"),
         description="Flat charge for capturing and vetting the application"),
]

SAVINGS_PRODUCTS = [
    dict(code="SAV-ORD", name="Ordinary Savings",
         description="Instant-access savings, interest credited monthly",
         interest_rate_pct_pa=Decimal("4"), min_balance=Decimal("5"),
         monthly_fee=Decimal("0.50"), allow_withdrawals=True),
    dict(code="SAV-FIX", name="Contractual Savings",
         description="Locked savings, no withdrawals until the contract ends",
         interest_rate_pct_pa=Decimal("7.5"), min_balance=Decimal("0"),
         monthly_fee=Decimal("0"), allow_withdrawals=False),
]

GROUP_NAMES = ["Simukai Women's Group", "Tapiwa Traders", "Chiedza Farmers"]

BRANCHES = [
    dict(code="HQ", name="Head Office", address="12 Samora Machel Ave, Harare", phone="0242700100"),
    dict(code="BYO", name="Bulawayo Branch", address="45 Fife Street, Bulawayo", phone="0292880200"),
    dict(code="MUT", name="Mutare Branch", address="8 Herbert Chitepo St, Mutare", phone="0202160300"),
]

# Everyone but the administrator holds the rights ticked for them: here, the sets
# the old loan officer, teller and viewer roles carried.
USERS = [
    ("admin", "Administrator", "admin123", Role.ADMIN, []),
    ("officer", "Tendai Moyo", "officer123", Role.USER, RIGHT_PRESETS["loan_officer"]),
    ("officer2", "Rudo Chikwanha", "officer123", Role.USER, RIGHT_PRESETS["loan_officer"]),
    ("teller", "Blessing Ncube", "teller123", Role.USER, RIGHT_PRESETS["teller"]),
    ("viewer", "Board Viewer", "viewer123", Role.USER, RIGHT_PRESETS["viewer"]),
]

PRODUCTS = [
    dict(code="SAL-STD", name="Salary Advance",
         description="Short-term salary-based loan for employed borrowers",
         interest_rate_pct=Decimal("8"), min_amount=Decimal("100"), max_amount=Decimal("2000"),
         min_term_months=1, max_term_months=6, admin_fee_pct=Decimal("3"),
         insurance_fee_pct=Decimal("1"), penalty_rate_pct_per_day=Decimal("0.5"), grace_days=3),
    dict(code="SAL-TERM", name="Salary Term Loan",
         description="Medium-term loan repaid by payroll deduction",
         interest_rate_pct=Decimal("5"), min_amount=Decimal("500"), max_amount=Decimal("10000"),
         min_term_months=6, max_term_months=24, admin_fee_pct=Decimal("2.5"),
         insurance_fee_pct=Decimal("1.5"), penalty_rate_pct_per_day=Decimal("0.3"), grace_days=5),
    # Flat rate, to exercise the second amortisation method
    dict(code="SCHOOL", name="School Fees Loan",
         description="Seasonal loan for school fees, 3 to 4 month terms, flat rate",
         interest_rate_pct=Decimal("4"), rate_method=RateMethod.FLAT,
         min_amount=Decimal("200"), max_amount=Decimal("3000"),
         min_term_months=3, max_term_months=4, admin_fee_pct=Decimal("2"),
         insurance_fee_pct=Decimal("1"), penalty_rate_pct_per_day=Decimal("0.5"), grace_days=3),
    # In another currency, to exercise the rate table and the revaluation run. The
    # ledger stays in USD; this book is carried at the booked rate (see services/fx.py).
    dict(code="ZWG-SAL", name="ZWG Salary Advance", currency="ZWG",
         description="Short-term salary loan in local currency, repaid monthly",
         interest_rate_pct=Decimal("10"), min_amount=Decimal("2000"), max_amount=Decimal("60000"),
         min_term_months=1, max_term_months=6, admin_fee_pct=Decimal("3"),
         insurance_fee_pct=Decimal("1"), penalty_rate_pct_per_day=Decimal("0.5"), grace_days=3),
    # Weekly, to exercise the repayment frequencies. Terms are in weeks.
    dict(code="GRP-WK", name="Group Business Loan",
         description="Working capital for group members, repaid weekly at the group meeting",
         interest_rate_pct=Decimal("6"), repayment_frequency=RepaymentFrequency.WEEKLY,
         min_amount=Decimal("100"), max_amount=Decimal("1500"),
         min_term_months=8, max_term_months=26, admin_fee_pct=Decimal("2"),
         insurance_fee_pct=Decimal("1"), penalty_rate_pct_per_day=Decimal("0.5"), grace_days=2),
]

FIRST = ["Tatenda", "Chipo", "Farai", "Nyasha", "Kudzai", "Rumbidzai", "Tinashe", "Vimbai",
         "Simbarashe", "Ropafadzo", "Takudzwa", "Anesu", "Munashe", "Tapiwa", "Shamiso",
         "Tawanda", "Panashe", "Nomsa", "Sipho", "Thandiwe"]
LAST = ["Moyo", "Ncube", "Sibanda", "Dube", "Chikwanha", "Mutasa", "Mhlanga", "Makoni", "Gumbo",
        "Zhou", "Mapfumo", "Chirwa", "Banda", "Mlambo", "Nyathi", "Marufu", "Chigumba",
        "Madziva", "Hove", "Mudzingwa"]
EMPLOYERS = ["Ministry of Education", "City of Harare", "ZESA Holdings", "Econet Wireless",
             "Delta Beverages", "OK Zimbabwe", "CBZ Bank", "NetOne", "ZIMRA",
             "Harare City Council"]
PURPOSES = ["School fees", "Medical expenses", "Home improvement", "Business stock",
            "Funeral expenses", "Rent", "Vehicle repair"]

STREETS = ["Samora Machel Ave", "Borrowdale Rd", "Chiremba Rd", "Seke Rd", "Bulawayo Rd"]
JOBS = ["Teacher", "Clerk", "Nurse", "Technician", "Officer", "Driver", "Accountant"]


class Command(BaseCommand):
    help = "Load demo users, products, borrowers and a loan book into SQL Server."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                           help="delete existing rows before seeding")

    def handle(self, *args, **options):
        random.seed(7)

        if options["reset"]:
            self.stdout.write("Clearing the loan book...")
            with transaction.atomic():
                # Before the User delete, so the closed_by/reopened_by SET_NULLs
                # never run. A stale closed period left over from a previous demo
                # would make this command fail on its first back-dated repayment,
                # which is a baffling way to learn the tool.
                AccountingPeriod.objects.all().delete()
                AuditLog.objects.all().delete()
                Notification.objects.all().delete()
                FacilityTransaction.objects.all().delete()
                CapitalTransaction.objects.all().delete()
                FundingFacility.objects.all().delete()
                LoanNote.objects.all().delete()
                ProvisionRunLine.objects.all().delete()
                ProvisionRun.objects.all().delete()
                RevaluationLine.objects.all().delete()
                RevaluationRun.objects.all().delete()
                ExchangeRate.objects.all().delete()
                BureauEnquiry.objects.all().delete()
                JournalLine.objects.all().delete()
                JournalEntry.objects.all().delete()
                LedgerAccount.objects.all().delete()
                Collateral.objects.all().delete()
                SavingsTransaction.objects.all().delete()
                SavingsAccount.objects.all().delete()
                SavingsProduct.objects.all().delete()
                GroupMember.objects.all().delete()
                BorrowerGroup.objects.all().delete()
                LoanCharge.objects.all().delete()
                ProductCharge.objects.all().delete()
                Charge.objects.all().delete()
                Transaction.objects.all().delete()
                Instalment.objects.all().delete()
                Loan.objects.all().delete()
                BorrowerDocument.objects.all().delete()
                Guarantor.objects.all().delete()
                Borrower.objects.all().delete()
                LoanProduct.objects.all().delete()
                Sequence.objects.all().delete()
                User.objects.all().delete()
                Branch.objects.all().delete()
                OrganisationSetting.objects.all().delete()

        if User.objects.exists():
            self.stdout.write(self.style.WARNING(
                "Database already seeded; use --reset to start over."))
            return

        config = OrganisationSetting.load()
        config.name = "Simba Microfinance"
        config.currency = "USD"
        config.address = "12 Samora Machel Avenue, Harare"
        config.phone = "0242 700 100"
        config.email = "info@example.com"
        config.save()

        # The chart of accounts must exist before the first transaction, because
        # every transaction raises its journal entry as it is created.
        ensure_chart_of_accounts()

        branches = [Branch.objects.create(**b) for b in BRANCHES]

        users = {}
        for index, (username, name, password, role, rights) in enumerate(USERS):
            users[username] = User.objects.create_user(
                username=username, password=password, full_name=name, role=role,
                rights=[r.value for r in rights],
                branch=branches[index % len(branches)],
                is_staff=(role == Role.ADMIN), is_superuser=(role == Role.ADMIN))
        # The ZWG has been easing against the dollar for two years; one rate a
        # quarter, so a loan disbursed a year ago is booked at a rate the
        # revaluation run can move.
        for months_back, rate in [(24, "0.040000"), (18, "0.038500"), (12, "0.037000"),
                                  (6, "0.036000"), (3, "0.035200"), (0, "0.034800")]:
            ExchangeRate.objects.create(code="ZWG", rate_date=add_months(date.today(), -months_back),
                                        rate=Decimal(rate), note="Reserve Bank mid-rate")
        products = [LoanProduct.objects.create(**p) for p in PRODUCTS]

        # The charges catalogue, attached to the two salary products.
        catalogue = [Charge.objects.create(**c) for c in CHARGES]
        for product in products[:2]:
            for charge in catalogue:
                ProductCharge.objects.create(product=product, charge=charge)

        savings_products = [SavingsProduct.objects.create(**p) for p in SAVINGS_PRODUCTS]

        borrowers = []
        for i in range(40):
            fn, ln = random.choice(FIRST), random.choice(LAST)
            letter = random.choice("ABCDEFGHJKLMNPQRSTVWXYZ")
            borrower = Borrower.objects.create(
                borrower_no=svc.next_number("BRW"), first_name=fn, last_name=ln,
                national_id=(f"{random.randint(10, 79)}-{random.randint(100000, 999999)}"
                             f"{letter}{random.randint(10, 79)}"),
                date_of_birth=date(random.randint(1965, 2000), random.randint(1, 12),
                                   random.randint(1, 28)),
                gender=random.choice(["M", "F"]),
                phone=f"07{random.choice([1, 7, 8])}{random.randint(1000000, 9999999)}",
                email=f"{fn.lower()}.{ln.lower()}{i}@example.com",
                address=f"{random.randint(1, 900)} {random.choice(STREETS)}, Harare",
                employer=random.choice(EMPLOYERS), employee_no=f"EMP{random.randint(1000, 99999)}",
                job_title=random.choice(JOBS),
                net_salary=Decimal(random.choice([350, 450, 600, 800, 1000, 1200, 1500, 2000])),
                payday=random.choice([20, 25, 28]),
                branch=branches[i % len(branches)],
                kyc_verified=i % 8 != 7, is_blacklisted=(i == 39),
            )
            Guarantor.objects.create(
                borrower=borrower,
                full_name=f"{random.choice(FIRST)} {random.choice(LAST)}",
                national_id=(f"{random.randint(10, 79)}-{random.randint(100000, 999999)}"
                             f"X{random.randint(10, 79)}"),
                phone=f"077{random.randint(1000000, 9999999)}",
                relationship_to_borrower=random.choice(["Spouse", "Sibling", "Colleague",
                                                        "Parent"]),
                employer=random.choice(EMPLOYERS),
            )
            borrowers.append(borrower)

        today = date.today()
        officer, admin, teller = users["officer"], users["admin"], users["teller"]
        n_pending = n_active = 0

        # The money has to be in the bank before the first disbursement leaves it.
        # Without this, account 1000 finishes the seed thousands of dollars negative
        # and no balance sheet can be drawn: the loans were real but nothing said
        # where the cash came from.
        with transaction.atomic():
            funding_svc.inject_capital(
                admin, Decimal("15000"), "Founding shareholders", add_months(today, -18),
                PaymentMethod.BANK_TRANSFER, "CAP0001", "Initial share capital")
            facility = funding_svc.open_facility(
                admin, funder_name="CBZ Bank Wholesale", name="Wholesale on-lending line",
                facility_limit=Decimal("50000"), interest_rate_pct_pa=Decimal("12"),
                start_date=add_months(today, -15), maturity_date=add_months(today, 21),
                is_revolving=True, repayment_terms="Quarterly interest, principal at maturity")
            funding_svc.drawdown(
                facility, admin, Decimal("20000"), add_months(today, -14),
                PaymentMethod.BANK_TRANSFER, "DRW0001", "First drawdown")

        for idx, borrower in enumerate(borrowers):
            if not borrower.kyc_verified or borrower.is_blacklisted or idx % 7 == 6:
                continue
            product = random.choice(products)
            term = random.randint(product.min_term_months, product.max_term_months)
            # principal capped by affordability
            max_inst = (borrower.net_salary * product.max_instalment_to_salary_pct / 100
                        * Decimal("0.95"))
            if product.currency:
                # The salary is in USD; the instalment will be in the product's currency.
                max_inst = max_inst / fx.rate_on(product.currency, today)
            per_thousand = monthly_equivalent(
                instalment_amount(Decimal(1000), product.interest_rate_pct, term,
                                  product.rate_method, product.repayment_frequency),
                product.repayment_frequency)
            max_p = min(product.max_amount, max_inst / per_thousand * 1000)
            if max_p < product.min_amount:
                continue
            principal = Decimal(
                int(random.uniform(float(product.min_amount), float(max_p)) / 50) * 50)
            pending = idx % 6 == 0
            months_ago = 0 if pending else random.randint(1, 14)
            app_date = (today - timedelta(days=random.randint(0, 5)) if pending
                        else add_months(today, -months_ago) - timedelta(days=random.randint(0, 10)))

            with transaction.atomic():
                loan = svc.apply(borrower, product, principal, term, random.choice(PURPOSES),
                                 officer, app_date)
                if pending:
                    n_pending += 1
                    if idx % 12 == 0:
                        svc.approve(loan, admin)  # approved, awaiting disbursement
                    continue
                svc.approve(loan, admin)
                loan.approved_at = datetime.combine(app_date + timedelta(days=1),
                                                    datetime.min.time(), tzinfo=timezone.utc)
                loan.save(update_fields=["approved_at"])
                if idx % 11 == 10:
                    svc.reject(loan, admin, "Insufficient affordability after other deductions")
                    continue
                disb = app_date + timedelta(days=random.randint(1, 4))
                svc.disburse(loan, admin, disb, None, PaymentMethod.BANK_TRANSFER,
                             f"TRF{random.randint(100000, 999999)}")
                n_active += 1

                # repayment behaviour profile
                profile = random.choices(["good", "late", "delinquent"], weights=[7, 2, 1])[0]
                for ins in list(svc.sched(loan)):
                    if ins.due_date > today:
                        break
                    if profile == "delinquent" and ins.number > 1:
                        break
                    if profile == "late" and random.random() < 0.25:
                        continue  # missed this one
                    pay_date = ins.due_date + timedelta(
                        days=0 if profile == "good" else random.randint(1, 20))
                    if pay_date > today:
                        continue
                    if loan.status != "active":
                        break
                    amt = min(ins.total_due, loan.total_outstanding)
                    if amt > 0:
                        post_repayment(loan, teller, amt, pay_date,
                                       PaymentMethod.SALARY_DEDUCTION,
                                       f"PAY{random.randint(10000, 99999)}", "Payroll deduction")

        with transaction.atomic():
            result = accrue_penalties(today)

        # A few collections follow-ups on the loans that are actually behind
        with transaction.atomic():
            behind = [l for l in Loan.objects.filter(status="active")
                      .select_related("borrower").prefetch_related("instalments")
                      if svc.arrears(l, today)[0] > 0]
            for loan in behind[:6]:
                LoanNote.objects.create(
                    loan=loan, author=users["officer"],
                    body=random.choice([
                        "Called the borrower; says the employer paid salaries late this month.",
                        "Spoke to HR at the employer, deduction was missed on the payroll run.",
                        "Borrower promises to pay at the next payday.",
                        "No answer on the phone; sent an SMS and will try again.",
                    ]),
                    next_action_date=today + timedelta(days=random.randint(2, 10)),
                    promised_amount=svc.arrears(loan, today)[0],
                    promised_date=today + timedelta(days=random.randint(3, 20)),
                )

        # Security against the largest active loans. The largest four rather than
        # everything over a threshold: the random principals shift with the run date,
        # and a fixed cut-off can silently match nothing and leave the demo with no
        # collateral at all.
        with transaction.atomic():
            secured = 0
            for loan in Loan.objects.filter(status="active").order_by("-principal")[:4]:
                Collateral.objects.create(
                    loan=loan, recorded_by=officer,
                    type=random.choice([CollateralType.VEHICLE, CollateralType.EQUIPMENT,
                                        CollateralType.PROPERTY]),
                    description=random.choice([
                        "Toyota Hilux double cab, white",
                        "Residential stand, Glen Lorne",
                        "Generator set, 15 kVA",
                        "Delivery motorcycle",
                    ]),
                    estimated_value=(loan.principal * Decimal("1.4")).quantize(Decimal("0.01")),
                    valuation_date=loan.disbursement_date,
                    reference=f"REF{random.randint(100000, 999999)}",
                )
                secured += 1

        # Joint-liability groups, drawn from borrowers who are not already in one
        with transaction.atomic():
            eligible = [b for b in borrowers if not b.is_blacklisted][:15]
            group_objects = []
            for index, group_name in enumerate(GROUP_NAMES):
                group = group_svc.create_group(
                    name=group_name, branch_id=branches[index % len(branches)].id,
                    officer=officer, status=GroupStatus.ACTIVE,
                    meeting_day=random.choice(["Monday", "Wednesday", "Friday"]),
                    meeting_place=random.choice(["Community hall", "Church grounds",
                                                 "Market square"]),
                    formed_on=add_months(today, -random.randint(6, 30)))
                for position, borrower in enumerate(eligible[index * 5:(index + 1) * 5]):
                    group_svc.add_member(
                        group, borrower,
                        MemberRole.LEADER if position == 0
                        else MemberRole.TREASURER if position == 1
                        else MemberRole.MEMBER)
                group_objects.append(group)

            # Point each member's running loan at the group it is guaranteed by.
            for group in group_objects:
                member_ids = list(group.members.values_list("borrower_id", flat=True))
                Loan.objects.filter(borrower_id__in=member_ids).update(group=group)

        # Savings accounts, with a few months of deposits behind them
        with transaction.atomic():
            savers = 0
            for index, borrower in enumerate(borrowers[:22]):
                if borrower.is_blacklisted:
                    continue
                product = savings_products[index % len(savings_products)]
                account = savings_svc.open_account(
                    borrower, product, teller,
                    opening_deposit=Decimal(random.choice([20, 50, 100])),
                    opened_on=add_months(today, -random.randint(3, 18)),
                    method=PaymentMethod.CASH, reference=f"DEP{random.randint(10000, 99999)}")
                savers += 1
                for month_back in range(random.randint(1, 5), 0, -1):
                    savings_svc.deposit(
                        account, teller, Decimal(random.choice([10, 20, 25, 40, 60])),
                        add_months(today, -month_back), PaymentMethod.CASH,
                        f"DEP{random.randint(10000, 99999)}", "Monthly saving")
                if product.allow_withdrawals and index % 4 == 0:
                    drawable = account.available_balance
                    if drawable > 15:
                        savings_svc.withdraw(
                            account, teller, (drawable / 2).quantize(Decimal("0.01")),
                            today - timedelta(days=random.randint(5, 40)), PaymentMethod.CASH,
                            f"WDL{random.randint(10000, 99999)}", "Withdrawal")

        with transaction.atomic():
            savings_run = savings_svc.accrue_interest(today)

        with transaction.atomic():
            messages = generate_reminders(today)

        # Catches up every month since the drawdown, so 5300 and 2110 carry real
        # numbers rather than one month's worth. Then pay the funder all but the
        # latest month, which is what quarterly-interest terms look like in practice
        # and leaves 2110 holding a live accrual rather than a year of arrears.
        with transaction.atomic():
            borrowing_interest = funding_svc.accrue_interest(today)
            monthly = q(facility.principal_outstanding * facility.interest_rate_pct_pa / 100 / 12)
            facility.refresh_from_db()
            to_pay = max(Decimal("0"), facility.interest_accrued - monthly)
            if to_pay > 0:
                funding_svc.pay_interest(facility, admin, to_pay, today,
                                         PaymentMethod.BANK_TRANSFER, "INT0001",
                                         "Interest settled with the funder")

        # Book the expected credit loss, so the demo book opens with account 1900
        # agreeing with the provision carried on every loan.
        with transaction.atomic():
            provision = provisioning_svc.run_provision(users["admin"], today)

        balance = trial_balance()
        sheet = balance_sheet()
        ties = reconciliation()

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(USERS)} users, {len(BRANCHES)} branches, {len(PRODUCTS)} products, "
            f"{len(borrowers)} borrowers, {n_active} disbursed loans, "
            f"{n_pending} pending applications, {secured} secured loans."))
        self.stdout.write(
            f"Groups: {BorrowerGroup.objects.count()} with "
            f"{GroupMember.objects.count()} members. "
            f"Savings: {savers} accounts, balance "
            f"{savings_svc.portfolio()['total_balance']}.")
        self.stdout.write(f"Penalty accrual: {result}")
        self.stdout.write(f"Savings interest: {savings_run}")
        self.stdout.write(f"Messages queued: {messages}")
        self.stdout.write(f"Borrowing interest: {borrowing_interest}")
        self.stdout.write(
            f"Funding: {FundingFacility.objects.count()} facility drawn "
            f"{facility.principal_outstanding} of {facility.facility_limit}, capital "
            f"{funding_svc.capital_summary()['net_capital']}, cash "
            f"{funding_svc.cash_balance()}")
        self.stdout.write(
            f"Provision: {provision.run_no} for {provision.period_end}, required "
            f"{provision.provision_required}, movement {provision.movement}")
        self.stdout.write(
            f"Ledger: {JournalEntry.objects.count()} entries, "
            f"Dr {balance['total_debit']} / Cr {balance['total_credit']}, "
            + ("balanced" if balance["balanced"] else self.style.ERROR("OUT OF BALANCE")))
        self.stdout.write(
            f"Balance sheet: assets {sheet['total_assets']} = liabilities "
            f"{sheet['total_liabilities']} + equity {sheet['total_equity']} "
            + ("" if sheet["balanced"]
               else self.style.ERROR(f"OUT BY {sheet['difference']}")))
        self.stdout.write(
            "Sub-ledger ties: "
            + ("all agree" if ties["agrees"] else self.style.ERROR(
                "; ".join(f"{r['code']} ledger {r['ledger']} vs book {r['book']}"
                          for r in ties["breaks"]))))
        self.stdout.write("Logins:  admin/admin123  officer/officer123  teller/teller123  "
                          "viewer/viewer123")
        # Said out loud because it is otherwise a confusing surprise: these are set
        # with set_password, which does not run AUTH_PASSWORD_VALIDATORS, so they
        # are shorter than the policy the API now enforces. The first password
        # anyone chooses through the UI will need ten characters.
        self.stdout.write(self.style.WARNING(
            "These demo passwords are below the password policy the API enforces "
            "(10 characters, not common, not numeric, not like the username). They work "
            "because seeding sets them directly; a password changed through the app will "
            "have to meet it."))
