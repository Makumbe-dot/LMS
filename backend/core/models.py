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


class RepaymentFrequency(models.TextChoices):
    """How often instalments fall. Salary loans are monthly; group loans usually
    repay at the group's weekly or fortnightly meeting."""
    MONTHLY = "monthly", "Monthly"
    FORTNIGHTLY = "fortnightly", "Fortnightly"
    WEEKLY = "weekly", "Weekly"


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


class ProvisionRunStatus(models.TextChoices):
    POSTED = "posted", "Posted"
    REVERSED = "reversed", "Reversed"


class FacilityTxnType(models.TextChoices):
    DRAWDOWN = "drawdown", "Drawdown"
    REPAYMENT = "repayment", "Principal repayment"
    INTEREST_ACCRUAL = "interest_accrual", "Interest accrued"
    INTEREST_PAYMENT = "interest_payment", "Interest paid"
    FEE = "fee", "Facility fee"
    REVERSAL = "reversal", "Reversal"


class CapitalTxnType(models.TextChoices):
    INJECTION = "injection", "Capital injection"
    RETURN_OF_CAPITAL = "return_of_capital", "Return of capital"
    DIVIDEND = "dividend", "Dividend"
    REVERSAL = "reversal", "Reversal"


class ManualJournalStatus(models.TextChoices):
    DRAFT = "draft", "Awaiting approval"
    POSTED = "posted", "Posted"
    REJECTED = "rejected", "Rejected"
    REVERSED = "reversed", "Reversed"


class TillStatus(models.TextChoices):
    OPEN = "open", "Open"
    COUNTED = "counted", "Counted, awaiting verification"
    VERIFIED = "verified", "Verified"


class BureauEnquiryStatus(models.TextChoices):
    OK = "ok", "Report received"
    FAILED = "failed", "Failed"


class StatementLineStatus(models.TextChoices):
    UNMATCHED = "unmatched", "Unmatched"
    MATCHED = "matched", "Matched"
    IGNORED = "ignored", "Ignored"


class PeriodState(models.TextChoices):
    """A month is either open to postings or closed to them.

    There is deliberately no LOCKED state. Anyone who could bypass a lock through
    a management command already has manage.py shell and SSMS, so a lock would be
    CLOSED with a worse error message rather than a stronger control.
    """
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"


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
    CAPITALISATION = "capitalisation", "Capitalised on reschedule"
    WAIVER = "waiver", "Waiver"
    WRITE_OFF = "write_off", "Write-off"
    RECOVERY = "recovery", "Recovery after write-off"
    REVERSAL = "reversal", "Reversal"
    OPENING_BALANCE = "opening_balance", "Opening balance brought forward"


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

    # When on, nobody can post a cash movement without an open till, so every note
    # taken or paid out at a counter lands in a count. Off by default, so a book that
    # has never used tills keeps posting until someone decides to start.
    require_open_till = models.BooleanField(default=False)

    # Weekdays the offices are shut every week, as three-letter names ("sat,sun").
    # An instalment never falls due on one of these, or on a public holiday; it moves
    # to the next working day. Empty by default, so a book that has never set it
    # keeps the due dates it always had.
    closed_weekdays = models.CharField(max_length=40, default="", blank=True)

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


class Holiday(models.Model):
    """A public holiday: a day the offices are shut, so nothing falls due on it.

    A holiday that `recurs_annually` is shut on the same day and month every year
    (Christmas); one that does not is a single date (a moving feast, or a day
    declared at short notice).
    """
    date = models.DateField(unique=True)
    name = models.CharField(max_length=120)
    recurs_annually = models.BooleanField(default=False)
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "holidays"
        ordering = ["date"]

    def __str__(self):
        return f"{self.date} - {self.name}"


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

    # Every token this user holds carries this number as a claim. Bumping it
    # invalidates all of them at once, immediately — which is the only way to
    # recall an access token, since a JWT is checked by signature and cannot be
    # called back. Bumped on a password change, on being disabled, and by the
    # "sign out everywhere" action. Costs nothing to check: the authentication
    # layer already loads this row on every request.
    token_version = models.IntegerField(default=0)

    # Two-factor sign-in with an authenticator app (services/totp.py). The secret
    # is set when the user starts enrolling and only counts once a code from it has
    # been confirmed. The last step accepted is kept so a code works exactly once.
    mfa_secret = models.CharField(max_length=64, null=True, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    mfa_last_step = models.BigIntegerField(null=True, blank=True)

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


class RevokedToken(models.Model):
    """One refresh token that has been retired, by its `jti` claim.

    Signing out on one device, and rotation on refresh, both land here. The user's
    other sessions are untouched — that is the difference between this and bumping
    `User.token_version`, which ends every session at once.

    Rows are only useful until the token they name would have expired anyway, so
    `manage.py prune_tokens` clears the old ones. Nothing breaks if it never runs;
    the table just grows.
    """
    jti = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="revoked_tokens")
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(default=timezone.now)
    reason = models.CharField(max_length=80, null=True, blank=True)

    class Meta:
        db_table = "revoked_tokens"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.jti} revoked {self.revoked_at:%Y-%m-%d %H:%M}"


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
    repayment_frequency = models.CharField(
        max_length=12, choices=RepaymentFrequency.choices, default=RepaymentFrequency.MONTHLY,
        help_text="How often instalments fall. The rate stays a monthly rate whatever this is")
    min_amount = models.DecimalField(**MONEY)
    max_amount = models.DecimalField(**MONEY)
    # Counted in instalments of the product's frequency: months for a monthly
    # product, weeks for a weekly one. Named for the monthly products the system
    # began with; renaming the column would touch the SQL views, the API and every
    # client for no change in meaning on a monthly loan.
    min_term_months = models.IntegerField()
    max_term_months = models.IntegerField()
    admin_fee_pct = models.DecimalField(default=ZERO, help_text="Deducted upfront", **RATE)
    insurance_fee_pct = models.DecimalField(default=ZERO, help_text="Credit life, upfront", **RATE)
    penalty_rate_pct_per_day = models.DecimalField(default=Decimal("0.5"), **RATE)
    grace_days = models.IntegerField(default=3)
    max_instalment_to_salary_pct = models.DecimalField(default=Decimal("40"), max_digits=6, decimal_places=2)
    # The currency the product lends in. Blank means the organisation's own; any
    # other code needs a rate on the Currencies page before a loan can be quoted.
    currency = models.CharField(max_length=8, blank=True, default="",
                                help_text="Blank for the organisation's currency")
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
    # The number this loan had in the system it was migrated from. Payroll returns
    # keep quoting it for months after a cut-over, so the bulk importer matches on
    # it too, and it is what makes re-running a migration file refuse duplicates.
    external_ref = models.CharField(max_length=40, null=True, blank=True)
    borrower = models.ForeignKey(Borrower, on_delete=models.PROTECT, related_name="loans", db_index=True)
    product = models.ForeignKey(LoanProduct, on_delete=models.PROTECT, related_name="loans")
    officer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="originated_loans")

    principal = models.DecimalField(**MONEY)
    interest_rate_pct = models.DecimalField(help_text="Monthly, snapshot from the product", **RATE)
    rate_method = models.CharField(max_length=10, choices=RateMethod.choices,
                                   default=RateMethod.REDUCING,
                                   help_text="Snapshot from the product at application")
    repayment_frequency = models.CharField(max_length=12, choices=RepaymentFrequency.choices,
                                           default=RepaymentFrequency.MONTHLY,
                                           help_text="Snapshot from the product at application")
    # The number of instalments, in the loan's repayment frequency. See the note on
    # LoanProduct.min_term_months for why it keeps its monthly name.
    term_months = models.IntegerField()
    # Snapshot of the product's currency at application, and the rate the ledger
    # carries this loan's receivables at: base units per one unit of `currency`,
    # 1 for a loan in the base currency. Set at disbursement, moved by each
    # revaluation run. See services/fx.py.
    currency = models.CharField(max_length=8, blank=True, default="")
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    purpose = models.CharField(max_length=200, null=True, blank=True)
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="loans")
    group = models.ForeignKey("BorrowerGroup", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="loans",
                              help_text="Set when the loan is taken under joint liability")
    refinanced_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name="refinanced_into",
                                        help_text="The loan this one tops up and settles")
    # Who stands behind THIS loan. A guarantor is held against the borrower, but
    # agreed to a particular advance; the agreement used to list every guarantor the
    # borrower had ever had, including people added after it was signed.
    guarantors = models.ManyToManyField("Guarantor", blank=True, related_name="loans",
                                        db_table="loan_guarantors")
    # Which borrowed money funded this advance. Reporting only — it raises no
    # posting and no balance depends on it — but it is what makes "how much of the
    # CBZ line is on-lent" and "cost of funds on this book" answerable. Added now
    # because a nullable column on `loans` is a metadata-only change today and a
    # backfill over a populated table later.
    funding_facility = models.ForeignKey("FundingFacility", on_delete=models.SET_NULL, null=True,
                                        blank=True, related_name="loans",
                                        help_text="The facility this advance was funded from")

    admin_fee = models.DecimalField(default=ZERO, **MONEY)
    insurance_fee = models.DecimalField(default=ZERO, **MONEY)
    other_charges = models.DecimalField(default=ZERO, **MONEY,
                                        help_text="Total of the catalogue charges on this loan")
    instalment_amount = models.DecimalField(default=ZERO, **MONEY)
    total_interest = models.DecimalField(default=ZERO, **MONEY)
    # The annual percentage rate disclosed to the borrower: interest AND the upfront
    # fees, as one yearly rate. Snapshotted at application and recomputed on the real
    # dates at disbursement, so the agreement shows the figure the borrower signed.
    # Null when there is no rate to find. See amortisation.annual_percentage_rate.
    apr_pct = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

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
    # Expected credit loss booked to account 1900 against this loan, maintained by
    # core.services.provisioning. Deliberately NOT in loans.LOAN_BALANCE_FIELDS:
    # refresh_balances must never touch it.
    provision_held = models.DecimalField(default=ZERO, **MONEY)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "loans"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(fields=["external_ref"],
                                    condition=models.Q(external_ref__isnull=False),
                                    name="uq_loan_external_ref"),
        ]

    def __str__(self):
        return self.loan_no

    @property
    def total_outstanding(self) -> Decimal:
        return ((self.principal_outstanding or ZERO)
                + (self.interest_outstanding or ZERO)
                + (self.penalties_outstanding or ZERO)
                + (self.charges_outstanding or ZERO))

    @property
    def upfront_fees(self) -> Decimal:
        """Everything deducted from the advance at disbursement."""
        return ((self.admin_fee or ZERO) + (self.insurance_fee or ZERO)
                + (self.other_charges or ZERO))

    @property
    def total_cost_of_credit(self) -> Decimal:
        """What the loan costs over the money advanced: contractual interest plus fees."""
        return (self.total_interest or ZERO) + self.upfront_fees

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
        indexes = [
            # Covers the arrears subquery in services/arrears.py: seek the overdue
            # range on due_date, then read the eight money columns and the status
            # without touching the table. One index, not two: this is the hottest
            # write table in the system — every repayment bulk-updates eleven
            # columns — and each extra wide index is maintained on every one of
            # them. It also covers vw_collections_due, which selects status.
            models.Index(
                fields=["due_date", "loan"],
                include=["principal_due", "interest_due", "penalty_due", "charge_due",
                         "principal_paid", "interest_paid", "penalty_paid", "charge_paid",
                         "status"],
                name="ix_inst_due_loan_cover",
            ),
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
        """What this instalment still owes.

        These eight columns are named in three places and all three must agree:
        here, `core.services.arrears.OVERDUE_BALANCE` (the SQL expression the
        reports use), and `dbo.vw_loan_book` in sql/04_reporting_views_v2.sql.
        `core/tests/test_arrears.py` asserts the first two agree loan-for-loan.
        """
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
    # The spot rate on the transaction date (cash and income legs) and the loan's
    # booked rate at the time (receivable legs), base units per unit of the loan's
    # currency; both 1 for a base-currency loan. Filled by a pre_save hook when
    # left blank, copied from the original on a reversal.
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    book_rate = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
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
    # 32, not 20: the longest value is now "facility_interest_payment" (25).
    source = models.CharField(max_length=32, help_text="The transaction type that raised it")

    # One nullable OneToOneField per posting source. On SQL Server mssql-django
    # emits a FILTERED unique index (WHERE col IS NOT NULL) for these, which is the
    # only reason this table can already hold thousands of NULLs in each — a plain
    # SQL Server UNIQUE constraint permits exactly one.
    #
    # KNOWN DEBT: four of these, and any future source wants a fifth. Nothing
    # enforces that exactly one is set. A source_model/source_id pair would settle
    # it, but that is a migration over a populated table and a rewrite of every
    # post_* path, so it is its own slice. ledger.SOURCES is at least table-driven,
    # so backfill is one loop rather than one block per source.
    transaction = models.OneToOneField("Transaction", on_delete=models.CASCADE, null=True,
                                       blank=True, related_name="journal_entry")
    savings_transaction = models.OneToOneField("SavingsTransaction", on_delete=models.CASCADE,
                                               null=True, blank=True,
                                               related_name="journal_entry")
    facility_transaction = models.OneToOneField("FacilityTransaction", on_delete=models.CASCADE,
                                                null=True, blank=True,
                                                related_name="journal_entry")
    capital_transaction = models.OneToOneField("CapitalTransaction", on_delete=models.CASCADE,
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


class ManualJournal(models.Model):
    """A journal entry a person writes: for what nothing else in the system posts.

    Salaries, rent, a bank charge, a laptop, an opening balance. Prepared by
    anyone who handles money and posted only by an administrator, so a journal
    prepared by anyone else passes through a second pair of hands: an expense
    payment is the classic way money leaves a lender unnoticed.

    Posting raises one JournalEntry through `ledger.post_manual_entry`, hung off
    this row the way a provision run's is, rather than adding a fifth source column
    to JournalEntry. The accounts a sub-ledger reconciles against (1100, 2000 and
    the rest; see `ledger.CONTROL_ACCOUNTS`) are refused, so a journal can never
    open a reconciliation break.
    """
    journal_no = models.CharField(max_length=20, unique=True, db_index=True)
    entry_date = models.DateField(db_index=True)
    narration = models.TextField()
    reference = models.CharField(max_length=80, null=True, blank=True,
                                 help_text="Invoice, receipt or payslip number")
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="manual_journals")
    status = models.CharField(max_length=10, choices=ManualJournalStatus.choices,
                              default=ManualJournalStatus.DRAFT, db_index=True)

    prepared_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="journals_prepared")
    prepared_at = models.DateTimeField(default=timezone.now)
    posted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="journals_posted")
    posted_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.TextField(null=True, blank=True)

    journal_entry = models.OneToOneField("JournalEntry", on_delete=models.SET_NULL, null=True,
                                         blank=True, related_name="manual_journal")
    reversal_entry = models.OneToOneField("JournalEntry", on_delete=models.SET_NULL, null=True,
                                          blank=True, related_name="manual_journal_reversal")
    reversed_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="journals_reversed")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "manual_journals"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.journal_no} {self.narration[:40]}"


class ManualJournalLine(models.Model):
    journal = models.ForeignKey(ManualJournal, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT,
                                related_name="manual_journal_lines")
    debit = models.DecimalField(default=ZERO, **MONEY)
    credit = models.DecimalField(default=ZERO, **MONEY)
    description = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        db_table = "manual_journal_lines"
        ordering = ["id"]
        constraints = [
            # One side or the other, never both and never neither.
            models.CheckConstraint(
                condition=(models.Q(debit__gt=0, credit=0) | models.Q(debit=0, credit__gt=0)),
                name="ck_manual_line_one_side"),
        ]

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.account_id} {side}"


# ---------------------------------------------------------------- tills
class TillSession(models.Model):
    """One teller's cash drawer for one stretch of work: opened with a float,
    counted at close, verified by someone else.

    What the drawer should hold is never typed in. It is the float plus every cash
    movement the teller posted while the till was open (`services.tills`), so a
    count can only be compared with what the system itself recorded. A difference
    found on verification is posted to the ledger - a shortage to 6800, an overage
    to 4900 - because the cash the ledger says is in the building is not.
    """
    session_no = models.CharField(max_length=20, unique=True, db_index=True)
    teller = models.ForeignKey("User", on_delete=models.PROTECT, related_name="tills")
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="tills")
    business_date = models.DateField(default=date.today, db_index=True)
    opened_at = models.DateTimeField(default=timezone.now)
    opening_float = models.DecimalField(default=ZERO, **MONEY)
    status = models.CharField(max_length=10, choices=TillStatus.choices, default=TillStatus.OPEN,
                              db_index=True)

    # Snapshotted at the count, so a verified till still explains itself if a
    # posting inside its window is later reversed.
    closed_at = models.DateTimeField(null=True, blank=True)
    cash_in = models.DecimalField(null=True, blank=True, **MONEY)
    cash_out = models.DecimalField(null=True, blank=True, **MONEY)
    expected_cash = models.DecimalField(null=True, blank=True, **MONEY)
    counted_cash = models.DecimalField(null=True, blank=True, **MONEY)
    variance = models.DecimalField(null=True, blank=True, **MONEY,
                                   help_text="Counted less expected: negative is a shortage")
    close_note = models.TextField(null=True, blank=True)

    verified_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="tills_verified")
    verified_at = models.DateTimeField(null=True, blank=True)
    verify_note = models.TextField(null=True, blank=True)
    variance_entry = models.OneToOneField("JournalEntry", on_delete=models.SET_NULL, null=True,
                                          blank=True, related_name="till_session")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "till_sessions"
        ordering = ["-opened_at", "-id"]
        constraints = [
            # A teller has one drawer open at a time, or no count means anything.
            models.UniqueConstraint(fields=["teller"], condition=models.Q(status="open"),
                                    name="uq_till_one_open_per_teller"),
        ]

    def __str__(self):
        return f"{self.session_no} {self.teller_id} {self.status}"


# ---------------------------------------------------------------- bank reconciliation
class BankStatement(models.Model):
    """A bank or mobile-money statement, uploaded to be matched against the ledger.

    Each line is matched to the journal entry that moved the same cash through
    account 1000. Entries rather than transactions, because every way money moves
    - a repayment, a savings withdrawal, a facility drawdown, a salary journal -
    ends in exactly one entry, so one matching rule covers them all. What is left
    on either side is the reconciliation: lines the books do not know about (a bank
    charge, an unidentified deposit) and postings the bank has not seen.
    """
    statement_no = models.CharField(max_length=20, unique=True, db_index=True)
    account_name = models.CharField(max_length=120,
                                    help_text="Which bank or wallet account this is")
    channel = models.CharField(max_length=20, choices=PaymentMethod.choices,
                               default=PaymentMethod.BANK_TRANSFER)
    period_start = models.DateField()
    period_end = models.DateField()
    opening_balance = models.DecimalField(null=True, blank=True, max_digits=18, decimal_places=2)
    closing_balance = models.DecimalField(null=True, blank=True, max_digits=18, decimal_places=2)
    file_name = models.CharField(max_length=255, null=True, blank=True)
    uploaded_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="statements_uploaded")
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "bank_statements"
        ordering = ["-period_end", "-id"]

    def __str__(self):
        return f"{self.statement_no} {self.account_name} {self.period_start}..{self.period_end}"


class StatementLine(models.Model):
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    line_no = models.IntegerField()
    txn_date = models.DateField(db_index=True)
    description = models.CharField(max_length=255, null=True, blank=True)
    reference = models.CharField(max_length=120, null=True, blank=True)
    # Signed from the institution's side: positive is money in, negative money out.
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, choices=StatementLineStatus.choices,
                              default=StatementLineStatus.UNMATCHED, db_index=True)
    # One entry, one line: an entry matched twice would hide a missing deposit.
    journal_entry = models.OneToOneField("JournalEntry", on_delete=models.SET_NULL, null=True,
                                         blank=True, related_name="statement_line")
    matched_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="statement_lines_matched")
    matched_at = models.DateTimeField(null=True, blank=True)
    auto_matched = models.BooleanField(default=False)
    note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "statement_lines"
        ordering = ["line_no"]
        constraints = [
            models.UniqueConstraint(fields=["statement", "line_no"], name="uq_statement_line_no"),
        ]

    def __str__(self):
        return f"{self.statement_id}/{self.line_no} {self.amount}"


# ---------------------------------------------------------------- currencies
class ExchangeRate(models.Model):
    """Base units per ONE unit of `code` on a date. The rate for any date is the
    latest one on or before it. See services/fx.py."""
    code = models.CharField(max_length=8, db_index=True)
    rate_date = models.DateField(db_index=True)
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    note = models.CharField(max_length=120, null=True, blank=True)
    set_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "exchange_rates"
        ordering = ["-rate_date", "code"]
        constraints = [models.UniqueConstraint(fields=["code", "rate_date"],
                                               name="uq_exchange_rate_code_date")]

    def __str__(self):
        return f"{self.code} {self.rate} on {self.rate_date}"


class RevaluationRun(models.Model):
    """One restatement of every open foreign-currency loan at a closing rate.

    Hangs its own journal entry, like a provision run, so a Rebuild can re-post it.
    """
    run_no = models.CharField(max_length=20, unique=True, db_index=True)
    as_of = models.DateField(db_index=True)
    base_currency = models.CharField(max_length=8)
    loans_revalued = models.IntegerField(default=0)
    movement = models.DecimalField(default=ZERO, **MONEY,
                                   help_text="Net change in the receivables, base currency")
    narration = models.TextField(null=True, blank=True)
    journal_entry = models.OneToOneField("JournalEntry", on_delete=models.SET_NULL, null=True,
                                         blank=True, related_name="revaluation_run")
    run_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "revaluation_runs"
        ordering = ["-as_of", "-id"]

    def __str__(self):
        return f"{self.run_no} as at {self.as_of}"


class RevaluationLine(models.Model):
    run = models.ForeignKey(RevaluationRun, on_delete=models.CASCADE, related_name="lines")
    loan = models.ForeignKey("Loan", on_delete=models.CASCADE, related_name="revaluation_lines")
    currency = models.CharField(max_length=8)
    old_rate = models.DecimalField(max_digits=18, decimal_places=6)
    new_rate = models.DecimalField(max_digits=18, decimal_places=6)
    principal_outstanding = models.DecimalField(default=ZERO, **MONEY)
    penalties_outstanding = models.DecimalField(default=ZERO, **MONEY)
    charges_outstanding = models.DecimalField(default=ZERO, **MONEY)
    principal_movement = models.DecimalField(default=ZERO, **MONEY)
    penalties_movement = models.DecimalField(default=ZERO, **MONEY)
    charges_movement = models.DecimalField(default=ZERO, **MONEY)
    movement = models.DecimalField(default=ZERO, **MONEY)

    class Meta:
        db_table = "revaluation_lines"
        ordering = ["id"]


# ---------------------------------------------------------------- provisioning
class ProvisionRun(models.Model):
    """One month-end booking of the IFRS 9 expected credit loss provision.

    The run books only the MOVEMENT between the provision required and the
    provision already carried, so running a period twice posts nothing. The
    provision carried is tracked per loan in Loan.provision_held, which gives a
    fifth reconciliation identity: the credit balance on account 1900 equals the
    sum of provision_held over every loan.

    Balances are read as they stand when the run executes; period_end is the
    label the run is filed under, not a point-in-time restatement.
    """
    run_no = models.CharField(max_length=20, unique=True, db_index=True)
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=ProvisionRunStatus.choices,
                              default=ProvisionRunStatus.POSTED)

    loans_assessed = models.IntegerField(default=0)
    loans_released = models.IntegerField(default=0)

    total_exposure = models.DecimalField(default=ZERO, **MONEY)
    total_carrying_amount = models.DecimalField(default=ZERO, **MONEY)
    provision_required = models.DecimalField(default=ZERO, **MONEY)
    provision_before = models.DecimalField(default=ZERO, **MONEY)
    movement = models.DecimalField(default=ZERO, **MONEY)
    ledger_provision_before = models.DecimalField(default=ZERO, **MONEY)

    # The rates are snapshotted so a historical run still explains itself after
    # someone edits Settings.
    stage1_pct = models.DecimalField(default=ZERO, max_digits=6, decimal_places=2)
    stage2_pct = models.DecimalField(default=ZERO, max_digits=6, decimal_places=2)
    stage3_pct = models.DecimalField(default=ZERO, max_digits=6, decimal_places=2)
    stage2_days = models.IntegerField(default=0)
    stage3_days = models.IntegerField(default=0)

    journal_entry = models.OneToOneField("JournalEntry", on_delete=models.SET_NULL, null=True,
                                         blank=True, related_name="provision_run")
    reversal_entry = models.OneToOneField("JournalEntry", on_delete=models.SET_NULL, null=True,
                                          blank=True, related_name="provision_reversal")
    narration = models.TextField(null=True, blank=True)
    run_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="provision_runs")
    created_at = models.DateTimeField(default=timezone.now)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="reversed_provision_runs")

    class Meta:
        db_table = "provision_runs"
        ordering = ["-period_end", "-id"]
        constraints = [
            # One posted run per period. A reversed run keeps its period_end and
            # does not block a re-run.
            models.UniqueConstraint(fields=["period_end"], condition=models.Q(status="posted"),
                                    name="uq_provision_run_posted_period"),
        ]

    def __str__(self):
        return f"{self.run_no} {self.period_end} {self.movement}"


class ProvisionRunLine(models.Model):
    run = models.ForeignKey(ProvisionRun, on_delete=models.CASCADE, related_name="lines")
    loan = models.ForeignKey("Loan", on_delete=models.CASCADE, related_name="provision_lines")
    loan_status = models.CharField(max_length=20, choices=LoanStatus.choices)
    stage = models.CharField(max_length=1, choices=ECLStage.choices, null=True, blank=True)
    days_past_due = models.IntegerField(default=0)
    exposure = models.DecimalField(default=ZERO, **MONEY)
    carrying_amount = models.DecimalField(default=ZERO, **MONEY)
    rate_pct = models.DecimalField(default=ZERO, max_digits=6, decimal_places=2)
    provision_required = models.DecimalField(default=ZERO, **MONEY)
    provision_before = models.DecimalField(default=ZERO, **MONEY)
    provision_after = models.DecimalField(default=ZERO, **MONEY)
    movement = models.DecimalField(default=ZERO, **MONEY)

    class Meta:
        db_table = "provision_run_lines"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["run", "loan"], name="uq_provision_run_loan"),
        ]

    def __str__(self):
        return f"{self.run_id}/{self.loan_id} {self.movement}"


# ---------------------------------------------------------------- funding
class FundingFacility(models.Model):
    """A line of credit the institution borrows on to fund its lending.

    There is no separate funder table. `funder_name` is a snapshot, the same
    treatment LoanCharge.name gets and for the same reason: a funder register
    would be an address book the balance sheet never asks anything of, and a
    snapshot means history survives a rename.

    Only the two balances the ledger reconciles against are stored:
    principal_outstanding ties to account 2100 and interest_accrued to 2110.
    Everything else (total drawn, repaid, interest paid) is a SUM over the
    transactions, computed when asked. The loan book keeps running totals because
    arrears, PAR and ECL read them on every row of every report; there will be
    three facilities, not three thousand, so five denormalised columns here would
    only be five ways to drift from the ledger.
    """
    facility_no = models.CharField(max_length=20, unique=True, db_index=True)
    funder_name = models.CharField(max_length=120)
    name = models.CharField(max_length=120)
    facility_limit = models.DecimalField(**MONEY)
    # Per annum, simple, on the drawn balance — matching SavingsProduct, not the
    # monthly LoanProduct convention. Funders quote annual rates.
    interest_rate_pct_pa = models.DecimalField(default=ZERO, **RATE)
    is_revolving = models.BooleanField(
        default=False, help_text="A revolving facility frees its limit as principal is repaid")
    start_date = models.DateField()
    maturity_date = models.DateField(null=True, blank=True)
    repayment_terms = models.CharField(max_length=200, null=True, blank=True)

    principal_outstanding = models.DecimalField(default=ZERO, **MONEY)
    interest_accrued = models.DecimalField(default=ZERO, **MONEY)
    last_accrual_date = models.DateField(null=True, blank=True)

    # A date rather than a status enum. Four states plus a cancel action is a state
    # machine for something with two interesting conditions, and it is where a
    # "fully repaid revolving facility can never be drawn again" bug lives.
    closed_on = models.DateField(null=True, blank=True)
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="facilities")
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="facilities_created")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "funding_facilities"
        ordering = ["-id"]
        verbose_name_plural = "funding facilities"

    @property
    def is_open(self) -> bool:
        return self.closed_on is None

    @property
    def total_outstanding(self):
        return (self.principal_outstanding or ZERO) + (self.interest_accrued or ZERO)

    @property
    def available(self):
        """Headroom to draw.

        A revolving facility measures against what is currently outstanding, a term
        facility against everything ever drawn.
        """
        from django.db.models import Sum

        if self.is_revolving:
            used = self.principal_outstanding or ZERO
        else:
            used = (self.transactions
                    .filter(txn_type=FacilityTxnType.DRAWDOWN, reversed=False)
                    .aggregate(v=Sum("amount"))["v"] or ZERO)
        return max(ZERO, (self.facility_limit or ZERO) - used)

    def __str__(self):
        return f"{self.facility_no} {self.funder_name} {self.principal_outstanding}"


class FacilityTransaction(models.Model):
    """One movement on a funding facility."""
    facility = models.ForeignKey(FundingFacility, on_delete=models.CASCADE,
                                 related_name="transactions", db_index=True)
    txn_type = models.CharField(max_length=20, choices=FacilityTxnType.choices)
    txn_date = models.DateField(db_index=True)
    amount = models.DecimalField(**MONEY)
    # Both balances after the movement, because two of the six row types move the
    # accrual and not the principal. One column would leave an accrual row saying
    # nothing about what changed.
    principal_after = models.DecimalField(default=ZERO, **MONEY)
    accrued_after = models.DecimalField(default=ZERO, **MONEY)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, null=True, blank=True)
    reference = models.CharField(max_length=80, null=True, blank=True)
    narration = models.TextField(null=True, blank=True)
    reversed = models.BooleanField(default=False)
    reversal_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="reversals")
    posted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="facility_postings")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "facility_transactions"
        ordering = ["id"]

    def __str__(self):
        return f"{self.txn_date} {self.txn_type} {self.amount}"


class CapitalTransaction(models.Model):
    """Shareholders' money going in, or coming back out."""
    txn_type = models.CharField(max_length=20, choices=CapitalTxnType.choices)
    txn_date = models.DateField(db_index=True)
    amount = models.DecimalField(**MONEY)
    contributor = models.CharField(
        max_length=160, help_text="Who put the money in — a snapshot, so history survives a rename")
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, null=True, blank=True)
    reference = models.CharField(max_length=80, null=True, blank=True)
    narration = models.TextField(null=True, blank=True)
    reversed = models.BooleanField(default=False)
    reversal_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="reversals")
    branch = models.ForeignKey("Branch", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="capital_transactions")
    posted_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="capital_postings")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "capital_transactions"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.txn_date} {self.txn_type} {self.amount}"


# ---------------------------------------------------------------- period close
class AccountingPeriod(models.Model):
    """One month closed to further postings, with the trial balance it froze.

    A row exists only for a month that has actually been closed. A month with no
    row is open, which is what keeps an existing book posting normally until an
    administrator closes something for the first time.

    Months close in order and only forwards, so the set of closed months is always
    a contiguous prefix ending at `end_date`. That single rule is what makes
    "the earliest date you may still post to" a correct answer rather than a guess:
    it is the day after the latest closed month's end. Closing August therefore
    closes everything up to 31 August, whether or not July has a row of its own.
    """
    year = models.IntegerField()
    month = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    start_date = models.DateField()
    end_date = models.DateField()
    state = models.CharField(max_length=8, choices=PeriodState.choices,
                             default=PeriodState.CLOSED)

    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name="periods_closed")
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey("User", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="periods_reopened")
    reopen_reason = models.TextField(null=True, blank=True)
    reopen_count = models.IntegerField(default=0)
    note = models.TextField(null=True, blank=True)

    # The trial balance as it stood at the close, so a later reopen-and-change is
    # visible forever rather than silent. Money is 18,2 rather than the usual 14,2
    # because these are whole-book totals, not one loan's balance.
    snapshot_debits = models.DecimalField(default=ZERO, max_digits=18, decimal_places=2)
    snapshot_credits = models.DecimalField(default=ZERO, max_digits=18, decimal_places=2)
    snapshot_entries = models.IntegerField(default=0)
    snapshot_principal_outstanding = models.DecimalField(default=ZERO, max_digits=18,
                                                         decimal_places=2)
    snapshot_savings_balance = models.DecimalField(default=ZERO, max_digits=18, decimal_places=2)
    # TextField rather than JSONField, following the Loan.score_detail precedent:
    # mssql-django maps JSONField with an ISJSON check constraint used nowhere else
    # in this schema.
    snapshot_json = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "accounting_periods"
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="uq_accounting_period"),
            models.CheckConstraint(condition=models.Q(month__gte=1, month__lte=12),
                                   name="ck_period_month"),
        ]

    def save(self, *args, **kwargs):
        from calendar import monthrange

        self.start_date = date(self.year, self.month, 1)
        self.end_date = date(self.year, self.month, monthrange(self.year, self.month)[1])
        return super().save(*args, **kwargs)

    @property
    def label(self) -> str:
        return f"{self.start_date:%B %Y}"

    @property
    def is_open(self) -> bool:
        return self.state == PeriodState.OPEN

    def __str__(self):
        return f"{self.year}-{self.month:02d} ({self.state})"


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

    Messages are generated by the reminder job and queued here; delivery is a
    separate step, so the queue can be reviewed before anything leaves the
    building. `core.services.gateways` does the sending.

    Delivery is recorded per message, not in bulk. The previous version marked the
    whole queue sent in one UPDATE without contacting anyone, which meant the
    system reported arrears notices as delivered that no borrower ever received —
    worse than admitting it could not send them.
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

    # How many delivery attempts this message has had, and when the last one was.
    # A gateway that is down comes back; a wrong phone number does not, so a
    # message is only given up on after MESSAGE_MAX_ATTEMPTS.
    attempts = models.IntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    # What the provider called it, so a delivery query to the provider can be tied
    # back to the borrower it was about.
    provider = models.CharField(max_length=20, null=True, blank=True)
    provider_message_id = models.CharField(max_length=120, null=True, blank=True)

    # Stops the reminder job queueing the same message twice
    dedupe_key = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notifications"
        ordering = ["-id"]
        indexes = [
            # The send loop's query: queued, due, fewest attempts first.
            models.Index(fields=["status", "scheduled_for"], name="ix_notif_status_due"),
        ]

    @property
    def can_retry(self) -> bool:
        from django.conf import settings

        return self.attempts < getattr(settings, "MESSAGE_MAX_ATTEMPTS", 3)

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


class BureauEnquiry(models.Model):
    """One question put to a credit bureau about a borrower, and its answer.

    Kept as a register rather than a field on the borrower, because a report is
    dated: the scorecard only reads one younger than BUREAU_VALID_DAYS, and an
    officer can see what the bureau said at the time of an earlier application.
    A failed attempt is kept too, so "the bureau was down" is on record.
    """
    borrower = models.ForeignKey(Borrower, on_delete=models.CASCADE,
                                 related_name="bureau_enquiries", db_index=True)
    loan = models.ForeignKey("Loan", on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="bureau_enquiries",
                             help_text="The application it was run for, if any")
    status = models.CharField(max_length=10, choices=BureauEnquiryStatus.choices,
                              default=BureauEnquiryStatus.OK)
    provider = models.CharField(max_length=20)
    reference = models.CharField(max_length=80, null=True, blank=True,
                                 help_text="The bureau's own reference for the report")
    score = models.IntegerField(null=True, blank=True)
    score_max = models.IntegerField(default=1000)
    open_accounts = models.IntegerField(default=0)
    accounts_in_arrears = models.IntegerField(default=0)
    defaults = models.IntegerField(default=0)
    worst_days_in_arrears = models.IntegerField(default=0)
    total_exposure = models.DecimalField(default=ZERO, **MONEY,
                                         help_text="Owed to other lenders")
    summary = models.TextField(null=True, blank=True)
    detail = models.TextField(null=True, blank=True, help_text="JSON: the figures as received")
    error = models.TextField(null=True, blank=True)
    enquired_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    enquired_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "bureau_enquiries"
        ordering = ["-enquired_at", "-id"]

    def __str__(self):
        return f"{self.borrower_id} via {self.provider} on {self.enquired_at:%Y-%m-%d}"


class Sequence(models.Model):
    """Per-prefix counters for human-readable numbers (BRW-000123, LN-000045)."""
    prefix = models.CharField(max_length=10, primary_key=True)
    value = models.IntegerField(default=0)

    class Meta:
        db_table = "sequences"

    def __str__(self):
        return f"{self.prefix}={self.value}"
