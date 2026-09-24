"""Database schema for the Loan Management System.

Table names are set explicitly (users, borrowers, loans, ...) rather than left
to Django's app_model default, so the schema reads cleanly in SQL Server
Management Studio and in the reporting views under sql/.

Money is DECIMAL(14,2) and rates are DECIMAL(6,3); every calculation in
core/services uses Decimal, never float.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

ZERO = Decimal("0")

MONEY = {"max_digits": 14, "decimal_places": 2}
RATE = {"max_digits": 6, "decimal_places": 3}


class Role(models.TextChoices):
    ADMIN = "admin", "Administrator"
    LOAN_OFFICER = "loan_officer", "Loan officer"
    TELLER = "teller", "Teller"
    VIEWER = "viewer", "Viewer"


class RateMethod(models.TextChoices):
    REDUCING = "reducing", "Reducing balance"
    FLAT = "flat", "Flat rate"


class NotificationChannel(models.TextChoices):
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"


class NotificationStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class NotificationKind(models.TextChoices):
    REMINDER = "reminder", "Instalment reminder"
    ARREARS = "arrears", "Arrears notice"
    RECEIPT = "receipt", "Repayment receipt"
    WELCOME = "welcome", "Disbursement confirmation"


class DocumentType(models.TextChoices):
    ID = "id", "Identity document"
    PAYSLIP = "payslip", "Payslip"
    CONTRACT = "contract", "Employment contract"
    BANK_STATEMENT = "bank_statement", "Bank statement"
    AGREEMENT = "agreement", "Signed loan agreement"
    OTHER = "other", "Other"


class ECLStage(models.TextChoices):
    """IFRS 9 impairment stages, driven by days past due."""
    STAGE_1 = "1", "Stage 1 - performing"
    STAGE_2 = "2", "Stage 2 - significant increase in credit risk"
    STAGE_3 = "3", "Stage 3 - credit impaired"


class LoanStatus(models.TextChoices):
    PENDING = "pending", "Pending"              # application captured
    APPROVED = "approved", "Approved"           # approved, awaiting disbursement
    REJECTED = "rejected", "Rejected"
    ACTIVE = "active", "Active"                 # disbursed and running
    CLOSED = "closed", "Closed"                 # fully repaid
    WRITTEN_OFF = "written_off", "Written off"


class InstalmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PARTIAL = "partial", "Partially paid"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"


class TxnType(models.TextChoices):
    DISBURSEMENT = "disbursement", "Disbursement"
    REPAYMENT = "repayment", "Repayment"
    PENALTY = "penalty", "Penalty"
    FEE = "fee", "Fee"
    CHARGE = "charge", "Charge collected"
    CHARGE_ADDED = "charge_added", "Charge added to the balance"
    WAIVER = "waiver", "Waiver"
    WRITE_OFF = "write_off", "Write-off"
    RECOVERY = "recovery", "Recovery after write-off"
    REVERSAL = "reversal", "Reversal"


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    INCOME = "income", "Income"
    EXPENSE = "expense", "Expense"


class CollateralType(models.TextChoices):
    VEHICLE = "vehicle", "Vehicle"
    PROPERTY = "property", "Property"
    EQUIPMENT = "equipment", "Equipment"
    LIVESTOCK = "livestock", "Livestock"
    CASH_DEPOSIT = "cash_deposit", "Cash deposit"
    GUARANTEE = "guarantee", "Third-party guarantee"
    OTHER = "other", "Other"


class CollateralStatus(models.TextChoices):
    PLEDGED = "pledged", "Pledged"
    RELEASED = "released", "Released"
    REALISED = "realised", "Realised"


class CreditGrade(models.TextChoices):
    A = "A", "A - very strong"
    B = "B", "B - strong"
    C = "C", "C - acceptable"
    D = "D", "D - marginal"
    E = "E", "E - weak"


class ChargeBasis(models.TextChoices):
    PERCENT_OF_PRINCIPAL = "percent", "Percent of principal"
    FIXED = "fixed", "Fixed amount"


class ChargeTiming(models.TextChoices):
    DISBURSEMENT = "disbursement", "Deducted at disbursement"
    MANUAL = "manual", "Raised manually"


class ChargeCollection(models.TextChoices):
    """How a charge raised mid-term is recovered."""
    COUNTER = "counter", "Collected at the counter"
    BALANCE = "balance", "Added to the loan balance"


class SavingsStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DORMANT = "dormant", "Dormant"
    CLOSED = "closed", "Closed"


class SavingsTxnType(models.TextChoices):
    DEPOSIT = "deposit", "Deposit"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    INTEREST = "interest", "Interest credited"
    FEE = "fee", "Account fee"
    REVERSAL = "reversal", "Reversal"


class GroupStatus(models.TextChoices):
    FORMING = "forming", "Forming"
    ACTIVE = "active", "Active"
    DORMANT = "dormant", "Dormant"
    CLOSED = "closed", "Closed"


class MemberRole(models.TextChoices):
    LEADER = "leader", "Chairperson"
    TREASURER = "treasurer", "Treasurer"
    SECRETARY = "secretary", "Secretary"
    MEMBER = "member", "Member"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    MOBILE_MONEY = "mobile_money", "Mobile money"
    SALARY_DEDUCTION = "salary_deduction", "Salary deduction"


# ---------------------------------------------------------------- organisation
class Branch(models.Model):
    """A lending office. Staff, borrowers and loans all belong to one."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    address = models.TextField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "branches"
        ordering = ["code"]
        verbose_name_plural = "branches"

    def __str__(self):
        return f"{self.code} - {self.name}"


class OrganisationSetting(models.Model):
    """Single row of institution-wide configuration. Always id=1."""
    name = models.CharField(max_length=160, default="Loan Management System")
    currency = models.CharField(max_length=8, default="USD")
    address = models.TextField(null=True, blank=True)
    phone = models.CharField(max_length=30, null=True, blank=True)
    email = models.CharField(max_length=120, null=True, blank=True)

    # IFRS 9 expected-credit-loss provision rates, percent of exposure per stage
    ecl_stage1_pct = models.DecimalField(default=Decimal("1"), max_digits=6, decimal_places=2)
    ecl_stage2_pct = models.DecimalField(default=Decimal("20"), max_digits=6, decimal_places=2)
    ecl_stage3_pct = models.DecimalField(default=Decimal("60"), max_digits=6, decimal_places=2)
    # Days past due at which a loan moves to the next stage
    ecl_stage2_days = models.IntegerField(default=30)
    ecl_stage3_days = models.IntegerField(default=90)

    # How far ahead instalment reminders are generated
    reminder_days_before = models.IntegerField(default=3)

    # A loan officer may approve up to this amount; anything larger needs an admin.
    officer_approval_limit = models.DecimalField(default=Decimal("2000"), **MONEY)
    # Applications scoring below this are flagged to the approver (advisory, never blocking).
    min_credit_score = models.IntegerField(default=40)

    # Joint liability: refuse a new group loan while any member is this far behind.
    # Set to 0 to turn the rule off.
    group_arrears_block_days = models.IntegerField(default=30)

    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "organisation_settings"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1  # there is only ever one row
        self.updated_at = timezone.now()
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "OrganisationSetting":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ---------------------------------------------------------------- users
class UserManager(BaseUserManager):
    def create_user(self, username, password=None, **extra):
        if not username:
            raise ValueError("A username is required")
        user = self.model(username=username, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra):
        extra.setdefault("role", Role.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("full_name", username)
        return self.create_user(username, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(max_length=50, unique=True, db_index=True)
    full_name = models.CharField(max_length=120)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.LOAN_OFFICER)
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="staff")
    phone = models.CharField(max_length=30, null=True, blank=True)
    email = models.CharField(max_length=120, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    # Brute-force protection, maintained by the login view
    failed_login_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "users"
        ordering = ["id"]

    def __str__(self):
        return f"{self.username} ({self.role})"

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.username

    def has_role(self, *roles) -> bool:
        return self.role in {r.value if hasattr(r, "value") else r for r in roles}

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())


# ---------------------------------------------------------------- borrowers
class Borrower(models.Model):
    borrower_no = models.CharField(max_length=20, unique=True, db_index=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    national_id = models.CharField(max_length=40, unique=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, null=True, blank=True)
    phone = models.CharField(max_length=30)
    email = models.CharField(max_length=120, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    employer = models.CharField(max_length=120, null=True, blank=True)
    employee_no = models.CharField(max_length=40, null=True, blank=True)
    job_title = models.CharField(max_length=80, null=True, blank=True)
    net_salary = models.DecimalField(default=ZERO, **MONEY)
    payday = models.IntegerField(
        default=25,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Day of the month the salary is paid",
    )
    kyc_verified = models.BooleanField(default=False)
    is_blacklisted = models.BooleanField(default=False)
    notes = models.TextField(null=True, blank=True)
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="borrowers")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "borrowers"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.borrower_no} {self.full_name}"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Guarantor(models.Model):
    borrower = models.ForeignKey(Borrower, on_delete=models.CASCADE, related_name="guarantors")
    full_name = models.CharField(max_length=160)
    national_id = models.CharField(max_length=40)
    phone = models.CharField(max_length=30)
    relationship_to_borrower = models.CharField(max_length=60, null=True, blank=True)
    employer = models.CharField(max_length=120, null=True, blank=True)
    address = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "guarantors"
        ordering = ["id"]

    def __str__(self):
        return self.full_name


# ---------------------------------------------------------------- products
class LoanProduct(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(null=True, blank=True)
    interest_rate_pct = models.DecimalField(help_text="Monthly rate", **RATE)
    rate_method = models.CharField(max_length=10, choices=RateMethod.choices,
                                   default=RateMethod.REDUCING,
                                   help_text="Reducing balance charges interest on the outstanding "
                                             "balance; flat rate charges it on the original principal")
    min_amount = models.DecimalField(**MONEY)
    max_amount = models.DecimalField(**MONEY)
    min_term_months = models.IntegerField()
    max_term_months = models.IntegerField()
    admin_fee_pct = models.DecimalField(default=ZERO, help_text="Deducted upfront", **RATE)
    insurance_fee_pct = models.DecimalField(default=ZERO, help_text="Credit life, upfront", **RATE)
    penalty_rate_pct_per_day = models.DecimalField(default=Decimal("0.5"), **RATE)
    grace_days = models.IntegerField(default=3)
    max_instalment_to_salary_pct = models.DecimalField(default=Decimal("40"), max_digits=6, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "loan_products"
        ordering = ["id"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Charge(models.Model):
    """A fee in the catalogue, attachable to any number of products.

    The admin and credit-life fees on LoanProduct stay where they are - they are
    part of the core pricing. This catalogue is for everything else a lender
    bolts on: stamp duty, a processing charge, a mobile-money cash-out cost.
    """
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(null=True, blank=True)
    basis = models.CharField(max_length=10, choices=ChargeBasis.choices,
                             default=ChargeBasis.FIXED)
    value = models.DecimalField(max_digits=14, decimal_places=4,
                                help_text="A percentage when the basis is percent, "
                                          "otherwise an amount")
    timing = models.CharField(max_length=14, choices=ChargeTiming.choices,
                              default=ChargeTiming.DISBURSEMENT)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "charges"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    def amount_for(self, principal: Decimal) -> Decimal:
        from decimal import ROUND_HALF_UP
        raw = (Decimal(principal) * self.value / 100 if self.basis == ChargeBasis.PERCENT_OF_PRINCIPAL
               else self.value)
        return Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ProductCharge(models.Model):
    """Which catalogue charges a product carries."""
    product = models.ForeignKey("LoanProduct", on_delete=models.CASCADE,
                                related_name="product_charges")
    charge = models.ForeignKey(Charge, on_delete=models.CASCADE, related_name="product_charges")

    class Meta:
        db_table = "product_charges"
        constraints = [
            models.UniqueConstraint(fields=["product", "charge"], name="uq_product_charge"),
        ]

    def __str__(self):
        return f"{self.product_id}/{self.charge_id}"


class LoanCharge(models.Model):
    """A charge actually raised against a loan, with the amount frozen."""
    loan = models.ForeignKey("Loan", on_delete=models.CASCADE, related_name="charges")
    charge = models.ForeignKey(Charge, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="loan_charges")
    name = models.CharField(max_length=120, help_text="Snapshot, so history survives a rename")
    amount = models.DecimalField(**MONEY)
    applied_on = models.DateField()
    collection = models.CharField(max_length=10, choices=ChargeCollection.choices,
                                  default=ChargeCollection.COUNTER)
    instalment = models.ForeignKey("Instalment", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="charges",
                                   help_text="Set when the charge was added to the balance")
    transaction = models.ForeignKey("Transaction", on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name="loan_charges")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "loan_charges"
        ordering = ["id"]

    def __str__(self):
        return f"{self.name} {self.amount}"


# ---------------------------------------------------------------- groups
class BorrowerGroup(models.Model):
    """A joint-liability group. Members stand behind each other's loans."""
    group_no = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="borrower_groups")
    # Not "groups": User already has that, from PermissionsMixin.
    officer = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="officer_groups")
    meeting_day = models.CharField(max_length=12, null=True, blank=True)
    meeting_place = models.CharField(max_length=160, null=True, blank=True)
    formed_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=GroupStatus.choices,
                              default=GroupStatus.FORMING, db_index=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "borrower_groups"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.group_no} {self.name}"


class GroupMember(models.Model):
    group = models.ForeignKey(BorrowerGroup, on_delete=models.CASCADE, related_name="members")
    borrower = models.ForeignKey("Borrower", on_delete=models.CASCADE,
                                 related_name="group_memberships")
    role = models.CharField(max_length=12, choices=MemberRole.choices, default=MemberRole.MEMBER)
    joined_on = models.DateField(default=date.today)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "group_members"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["group", "borrower"], name="uq_group_member"),
        ]

    def __str__(self):
        return f"{self.borrower_id} in {self.group_id}"


# ---------------------------------------------------------------- savings
class SavingsProduct(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(null=True, blank=True)
    interest_rate_pct_pa = models.DecimalField(default=ZERO, max_digits=6, decimal_places=3,
                                               help_text="Annual rate, credited monthly")
    min_balance = models.DecimalField(default=ZERO, **MONEY)
    monthly_fee = models.DecimalField(default=ZERO, **MONEY)
    allow_withdrawals = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "savings_products"
        ordering = ["id"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class SavingsAccount(models.Model):
    account_no = models.CharField(max_length=20, unique=True, db_index=True)
    borrower = models.ForeignKey("Borrower", on_delete=models.PROTECT,
                                 related_name="savings_accounts", db_index=True)
    product = models.ForeignKey(SavingsProduct, on_delete=models.PROTECT, related_name="accounts")
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="savings_accounts")
    status = models.CharField(max_length=10, choices=SavingsStatus.choices,
                              default=SavingsStatus.ACTIVE, db_index=True)
    balance = models.DecimalField(default=ZERO, **MONEY)
    opened_on = models.DateField(default=date.today)
    closed_on = models.DateField(null=True, blank=True)
    last_interest_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "savings_accounts"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.account_no} ({self.balance})"

    @property
    def available_balance(self) -> Decimal:
        """What the member may actually draw, after the product's minimum."""
        return max(ZERO, (self.balance or ZERO) - (self.product.min_balance or ZERO))


class SavingsTransaction(models.Model):
    account = models.ForeignKey(SavingsAccount, on_delete=models.CASCADE,
                                related_name="transactions", db_index=True)
    txn_type = models.CharField(max_length=12, choices=SavingsTxnType.choices)
    txn_date = models.DateField(db_index=True)
    amount = models.DecimalField(**MONEY)
    balance_after = models.DecimalField(**MONEY)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, null=True, blank=True)
    reference = models.CharField(max_length=80, null=True, blank=True)
    narration = models.TextField(null=True, blank=True)
    reversed = models.BooleanField(default=False)
    reversal_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="reversals")
    posted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="savings_postings")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "savings_transactions"
        ordering = ["id"]

    def __str__(self):
        return f"{self.txn_type} {self.amount} on {self.account_id}"


# ---------------------------------------------------------------- loans
class Loan(models.Model):
    loan_no = models.CharField(max_length=20, unique=True, db_index=True)
    borrower = models.ForeignKey(Borrower, on_delete=models.PROTECT, related_name="loans", db_index=True)
    product = models.ForeignKey(LoanProduct, on_delete=models.PROTECT, related_name="loans")
    officer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="originated_loans")

    principal = models.DecimalField(**MONEY)
    interest_rate_pct = models.DecimalField(help_text="Monthly, snapshot from the product", **RATE)
    rate_method = models.CharField(max_length=10, choices=RateMethod.choices,
                                   default=RateMethod.REDUCING,
                                   help_text="Snapshot from the product at application")
    term_months = models.IntegerField()
    purpose = models.CharField(max_length=200, null=True, blank=True)
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="loans")
    group = models.ForeignKey("BorrowerGroup", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="loans",
                              help_text="Set when the loan is taken under joint liability")
    refinanced_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="refinanced_into",
                                        help_text="The loan this one tops up and settles")

    admin_fee = models.DecimalField(default=ZERO, **MONEY)
    insurance_fee = models.DecimalField(default=ZERO, **MONEY)
    other_charges = models.DecimalField(default=ZERO, **MONEY,
                                        help_text="Total of the catalogue charges on this loan")
    instalment_amount = models.DecimalField(default=ZERO, **MONEY)
    total_interest = models.DecimalField(default=ZERO, **MONEY)

    status = models.CharField(max_length=20, choices=LoanStatus.choices,
                              default=LoanStatus.PENDING, db_index=True)
    application_date = models.DateField(default=date.today)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="approved_loans")
    rejection_reason = models.TextField(null=True, blank=True)

    # Scorecard result, taken at application. See core/services/scoring.py.
    credit_score = models.IntegerField(null=True, blank=True)
    credit_grade = models.CharField(max_length=1, choices=CreditGrade.choices,
                                    null=True, blank=True)
    score_detail = models.TextField(null=True, blank=True,
                                    help_text="JSON breakdown of how the score was reached")
    disbursement_date = models.DateField(null=True, blank=True)
    first_instalment_date = models.DateField(null=True, blank=True)
    maturity_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # Running balances, maintained by core.services.loans.refresh_balances
    principal_outstanding = models.DecimalField(default=ZERO, **MONEY)
    interest_outstanding = models.DecimalField(default=ZERO, **MONEY)
    penalties_outstanding = models.DecimalField(default=ZERO, **MONEY)
    charges_outstanding = models.DecimalField(default=ZERO, **MONEY)
    total_paid = models.DecimalField(default=ZERO, **MONEY)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "loans"
        ordering = ["-id"]

    def __str__(self):
        return self.loan_no

    @property
    def total_outstanding(self) -> Decimal:
        return ((self.principal_outstanding or ZERO)
                + (self.interest_outstanding or ZERO)
                + (self.penalties_outstanding or ZERO)
                + (self.charges_outstanding or ZERO))

    @property
    def schedule(self):
        """The instalment schedule, oldest first. Uses the prefetch when present."""
        return self.instalments.all()


class Instalment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="instalments", db_index=True)
    number = models.IntegerField()
    due_date = models.DateField(db_index=True)
    principal_due = models.DecimalField(**MONEY)
    interest_due = models.DecimalField(**MONEY)
    penalty_due = models.DecimalField(default=ZERO, **MONEY)
    charge_due = models.DecimalField(default=ZERO, **MONEY,
                                     help_text="Fees added to this instalment mid-term")
    principal_paid = models.DecimalField(default=ZERO, **MONEY)
    interest_paid = models.DecimalField(default=ZERO, **MONEY)
    penalty_paid = models.DecimalField(default=ZERO, **MONEY)
    charge_paid = models.DecimalField(default=ZERO, **MONEY)
    opening_balance = models.DecimalField(**MONEY)
    closing_balance = models.DecimalField(**MONEY)
    status = models.CharField(max_length=20, choices=InstalmentStatus.choices,
                              default=InstalmentStatus.PENDING)
    last_penalty_date = models.DateField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "instalments"
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["loan", "number"], name="uq_instalment_loan_number"),
        ]

    def __str__(self):
        return f"{self.loan_id}/{self.number} due {self.due_date}"

    @property
    def total_due(self) -> Decimal:
        return self.principal_due + self.interest_due + self.penalty_due + self.charge_due

    @property
    def total_paid(self) -> Decimal:
        return self.principal_paid + self.interest_paid + self.penalty_paid + self.charge_paid

    @property
    def balance(self) -> Decimal:
        return self.total_due - self.total_paid


class Transaction(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="transactions", db_index=True)
    txn_type = models.CharField(max_length=20, choices=TxnType.choices)
    txn_date = models.DateField(db_index=True)
    amount = models.DecimalField(**MONEY)
    principal_component = models.DecimalField(default=ZERO, **MONEY)
    interest_component = models.DecimalField(default=ZERO, **MONEY)
    penalty_component = models.DecimalField(default=ZERO, **MONEY)
    charge_component = models.DecimalField(default=ZERO, **MONEY)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, null=True, blank=True)
    reference = models.CharField(max_length=80, null=True, blank=True)
    narration = models.TextField(null=True, blank=True)
    reversed = models.BooleanField(default=False)
    reversal_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="reversals")
    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="postings")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "transactions"
        ordering = ["id"]

    def __str__(self):
        return f"{self.txn_type} {self.amount} on {self.txn_date}"


class AuditLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="audit_entries")
    action = models.CharField(max_length=60, db_index=True)
    entity = models.CharField(max_length=40)
    entity_id = models.IntegerField(null=True, blank=True)
    detail = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "audit_log"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.entity}"


# ---------------------------------------------------------------- general ledger
class LedgerAccount(models.Model):
    """A line of the chart of accounts."""
    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=12, choices=AccountType.choices)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "ledger_accounts"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def is_debit_balance(self) -> bool:
        """Assets and expenses increase on the debit side; everything else on the credit side."""
        return self.type in (AccountType.ASSET, AccountType.EXPENSE)


class JournalEntry(models.Model):
    """One balanced double-entry posting.

    Every money movement in the loan book raises exactly one of these, so the
    ledger and the loan book can never drift apart. See core/services/ledger.py.
    """
    entry_no = models.CharField(max_length=20, unique=True, db_index=True)
    entry_date = models.DateField(db_index=True)
    narration = models.TextField()
    source = models.CharField(max_length=20, help_text="The transaction type that raised it")
    transaction = models.OneToOneField("Transaction", on_delete=models.CASCADE, null=True,
                                       blank=True, related_name="journal_entry")
    savings_transaction = models.OneToOneField("SavingsTransaction", on_delete=models.CASCADE,
                                               null=True, blank=True,
                                               related_name="journal_entry")
    loan = models.ForeignKey("Loan", on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="journal_entries")
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="journal_entries")
    posted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="journal_entries")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "journal_entries"
        ordering = ["-id"]
        verbose_name_plural = "journal entries"

    def __str__(self):
        return f"{self.entry_no} {self.narration[:40]}"

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines.all()), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines.all()), ZERO)


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(default=ZERO, **MONEY)
    credit = models.DecimalField(default=ZERO, **MONEY)
    description = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        db_table = "journal_lines"
        ordering = ["id"]

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.account.code} {side}"


# ---------------------------------------------------------------- collateral
class Collateral(models.Model):
    """Security pledged against a loan."""
    loan = models.ForeignKey("Loan", on_delete=models.CASCADE, related_name="collateral",
                             db_index=True)
    type = models.CharField(max_length=20, choices=CollateralType.choices,
                            default=CollateralType.OTHER)
    description = models.CharField(max_length=200)
    estimated_value = models.DecimalField(default=ZERO, **MONEY)
    valuation_date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=80, null=True, blank=True,
                                 help_text="Registration number, title deed, serial number")
    status = models.CharField(max_length=12, choices=CollateralStatus.choices,
                              default=CollateralStatus.PLEDGED)
    notes = models.TextField(null=True, blank=True)
    recorded_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="collateral_records")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "collateral"
        ordering = ["-id"]
        verbose_name_plural = "collateral"

    def __str__(self):
        return f"{self.type}: {self.description}"


class LoanNote(models.Model):
    """A collections follow-up: what was tried, what was promised, what is next."""
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="notes", db_index=True)
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="loan_notes")
    body = models.TextField()
    next_action_date = models.DateField(null=True, blank=True, db_index=True)
    promised_amount = models.DecimalField(null=True, blank=True, **MONEY)
    promised_date = models.DateField(null=True, blank=True)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "loan_notes"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.loan_id}: {self.body[:40]}"


class Notification(models.Model):
    """Outbox for borrower messages.

    Messages are generated by the reminder job and queued here. Marking them
    sent is a separate step, so the queue can be reviewed, exported for a bulk
    SMS provider, or wired to a real gateway without touching the loan book.
    """
    borrower = models.ForeignKey(Borrower, on_delete=models.CASCADE,
                                 related_name="notifications", db_index=True)
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="notifications")
    kind = models.CharField(max_length=20, choices=NotificationKind.choices)
    channel = models.CharField(max_length=10, choices=NotificationChannel.choices,
                               default=NotificationChannel.SMS)
    to_address = models.CharField(max_length=160)
    subject = models.CharField(max_length=200, null=True, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=12, choices=NotificationStatus.choices,
                              default=NotificationStatus.QUEUED, db_index=True)
    scheduled_for = models.DateField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    # Stops the reminder job queueing the same message twice
    dedupe_key = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notifications"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.kind} to {self.to_address} ({self.status})"


def document_path(instance, filename: str) -> str:
    return f"borrowers/{instance.borrower_id}/{filename}"


class BorrowerDocument(models.Model):
    """KYC and loan paperwork held against a borrower."""
    borrower = models.ForeignKey(Borrower, on_delete=models.CASCADE,
                                 related_name="documents", db_index=True)
    doc_type = models.CharField(max_length=20, choices=DocumentType.choices,
                                default=DocumentType.OTHER)
    file = models.FileField(upload_to=document_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, null=True, blank=True)
    size_bytes = models.IntegerField(default=0)
    note = models.CharField(max_length=200, null=True, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="uploads")
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "borrower_documents"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.original_name} ({self.doc_type})"


class Sequence(models.Model):
    """Per-prefix counters for human-readable numbers (BRW-000123, LN-000045)."""
    prefix = models.CharField(max_length=10, primary_key=True)
    value = models.IntegerField(default=0)

    class Meta:
        db_table = "sequences"

    def __str__(self):
        return f"{self.prefix}={self.value}"
