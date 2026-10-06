"""Request validation and response shaping for the API.

Money is rendered as a decimal string so cents survive JSON. Read serializers
add the derived values the UI needs (borrower name on a loan, arrears, the
totals on an instalment) rather than making the client recompute them.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from rest_framework import serializers

from .models import (
    AccountingPeriod,
    AuditLog,
    BankStatement,
    Borrower,
    CapitalTransaction,
    CapitalTxnType,
    FacilityTransaction,
    FundingFacility,
    BorrowerDocument,
    BureauEnquiry,
    BorrowerGroup,
    Branch,
    Charge,
    ChargeCollection,
    Collateral,
    DocumentType,
    GroupMember,
    LoanCharge,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    ManualJournal,
    ManualJournalLine,
    Guarantor,
    Holiday,
    InboundPayment,
    Instalment,
    Loan,
    LoanNote,
    LoanProduct,
    LoanStatus,
    Notification,
    NotificationStatus,
    OrganisationSetting,
    PaymentMethod,
    PayrollRun,
    PayrollRunLine,
    ProductCharge,
    ExchangeRate,
    ProvisionRun,
    ProvisionRunLine,
    RevaluationLine,
    RevaluationRun,
    ProvisionRunStatus,
    RateMethod,
    Right,
    Role,
    SavingsAccount,
    SavingsProduct,
    SavingsStatus,
    SavingsTransaction,
    StatementLine,
    TillSession,
    Transaction,
    TxnType,
    User,
)


def money(**kwargs):
    kwargs.setdefault("max_digits", 14)
    kwargs.setdefault("decimal_places", 2)
    return serializers.DecimalField(**kwargs)


# ---------------------------------------------------------------- auth / users
class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "code", "name", "address", "phone", "is_active"]


class UserSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "role", "rights", "approval_limit", "is_active",
                  "branch_id", "branch_name", "phone", "email", "mfa_enabled"]
        read_only_fields = ["mfa_enabled"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # What the user can actually do: every right for an administrator, in the
        # catalogue's order otherwise, and nothing stale from an old right.
        data["rights"] = instance.effective_rights()
        return data


class RightsField(serializers.ListField):
    """The access rights an administrator ticks for a user, as Right values."""

    child = serializers.ChoiceField(choices=Right.choices)

    def to_internal_value(self, data):
        rights = set(super().to_internal_value(data))
        return [r.value for r in Right if r.value in rights]


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)


class MfaLoginSerializer(serializers.Serializer):
    mfa_token = serializers.CharField()
    code = serializers.CharField(max_length=12)


class MfaCodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)


class MfaDisableSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)
    code = serializers.CharField(max_length=12)


class RefreshSerializer(serializers.Serializer):
    """The refresh token, for renewing a session and for signing out."""
    refresh_token = serializers.CharField()


class PasswordPolicyMixin:
    """Run Django's AUTH_PASSWORD_VALIDATORS on whatever password is being set.

    Without this the setting is dead configuration — nothing in the project called
    `validate_password`, so a serializer's `min_length` was the only rule that
    applied and CommonPasswordValidator never fired. "password1" was acceptable.

    The validated user is passed where it is known, so the
    UserAttributeSimilarityValidator can reject a password that is the username.
    """
    password_field = "password"

    def _check_password(self, value, user=None):
        if value in (None, ""):
            return value
        try:
            validate_password(value, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value


class UserCreateSerializer(PasswordPolicyMixin, serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(choices=Role.choices, default=Role.USER)
    rights = RightsField(required=False, default=list)

    class Meta:
        model = User
        fields = ["username", "full_name", "password", "role", "rights", "approval_limit",
                  "branch", "phone", "email"]

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists")
        return value

    def validate(self, data):
        # Validated here rather than in validate_password, so the username and
        # full name are available for the similarity check.
        self._check_password(data.get("password"),
                             user=User(username=data.get("username", ""),
                                       full_name=data.get("full_name", "")))
        return data

    def create(self, validated):
        return User.objects.create_user(**validated)


class UserUpdateSerializer(PasswordPolicyMixin, serializers.Serializer):
    full_name = serializers.CharField(required=False)
    role = serializers.ChoiceField(choices=Role.choices, required=False)
    rights = RightsField(required=False)
    approval_limit = serializers.DecimalField(max_digits=14, decimal_places=2, required=False,
                                              allow_null=True, min_value=0)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(required=False, allow_null=True)
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    phone = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    email = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    unlock = serializers.BooleanField(required=False,
                                      help_text="Clear a lockout from failed sign-ins")
    reset_mfa = serializers.BooleanField(required=False,
                                         help_text="Turn two-factor sign-in off: a lost phone")

    def validate_password(self, value):
        return self._check_password(value, user=self.context.get("target_user"))


class ChangePasswordSerializer(PasswordPolicyMixin, serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        return self._check_password(value, user=self.context.get("request") and
                                    self.context["request"].user)


# ---------------------------------------------------------------- borrowers
class GuarantorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Guarantor
        fields = ["id", "full_name", "national_id", "phone", "relationship_to_borrower",
                  "employer", "address"]


BORROWER_FIELDS = [
    "first_name", "last_name", "national_id", "date_of_birth", "gender", "phone", "email",
    "address", "employer", "employee_no", "job_title", "net_salary", "payday",
    "kyc_verified", "is_blacklisted", "notes", "branch",
]


class BorrowerDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.CharField(source="uploaded_by.full_name", read_only=True,
                                             default=None)
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = BorrowerDocument
        fields = ["id", "borrower_id", "doc_type", "original_name", "content_type", "size_bytes",
                  "note", "uploaded_by_name", "uploaded_at", "download_url"]

    def get_download_url(self, obj) -> str:
        return f"/api/borrowers/{obj.borrower_id}/documents/{obj.id}/download"


class BorrowerSerializer(serializers.ModelSerializer):
    guarantors = GuarantorSerializer(many=True, read_only=True)
    documents = BorrowerDocumentSerializer(many=True, read_only=True)
    active_loans = serializers.IntegerField(read_only=True, default=0)
    total_outstanding = money(read_only=True, default=Decimal("0"))
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)

    class Meta:
        model = Borrower
        fields = ["id", "borrower_no", "created_at", *BORROWER_FIELDS, "branch_name",
                  "guarantors", "documents", "active_loans", "total_outstanding"]


class BureauEnquirySerializer(serializers.ModelSerializer):
    enquired_by_name = serializers.CharField(source="enquired_by.full_name", read_only=True,
                                             default=None)
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True, default=None)

    class Meta:
        model = BureauEnquiry
        fields = ["id", "status", "provider", "reference", "score", "score_max",
                  "open_accounts", "accounts_in_arrears", "defaults", "worst_days_in_arrears",
                  "total_exposure", "summary", "error", "loan_no", "enquired_by_name",
                  "enquired_at"]


class BorrowerCreateSerializer(serializers.ModelSerializer):
    guarantors = GuarantorSerializer(many=True, required=False, default=list)

    class Meta:
        model = Borrower
        fields = [*BORROWER_FIELDS, "guarantors"]

    def validate_national_id(self, value):
        if Borrower.objects.filter(national_id=value).exists():
            raise serializers.ValidationError("A borrower with this national ID already exists")
        return value


class BorrowerUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Borrower
        # national_id is the borrower's identity in the register and is not editable
        fields = ["first_name", "last_name", "phone", "email", "address", "employer",
                  "employee_no", "job_title", "net_salary", "payday", "kyc_verified",
                  "is_blacklisted", "notes", "branch"]
        extra_kwargs = {f: {"required": False} for f in fields}


# ---------------------------------------------------------------- products
class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoanProduct
        fields = "__all__"

    def validate_interest_rate_pct(self, value):
        if value <= 0:
            raise serializers.ValidationError("Interest rate must be greater than zero")
        return value

    def validate_currency(self, value):
        from .services import fx

        code = fx.normalise(value)
        if not code or code == fx.base_currency():
            return ""
        if len(code) > 8 or not code.isalpha():
            raise serializers.ValidationError("A currency is a short code such as ZWG or ZAR")
        if fx.rate_on(code, strict=False) is None:
            raise serializers.ValidationError(
                f"No exchange rate for {code} yet. Add one on the Currencies page first.")
        return code

    def validate(self, attrs):
        def field(name):
            return attrs.get(name, getattr(self.instance, name, None))

        if field("min_amount") is not None and field("max_amount") is not None:
            if field("min_amount") > field("max_amount"):
                raise serializers.ValidationError("Minimums cannot exceed maximums")
        if field("min_term_months") is not None and field("max_term_months") is not None:
            if field("min_term_months") > field("max_term_months"):
                raise serializers.ValidationError("Minimums cannot exceed maximums")
        return attrs


class ProductUpdateSerializer(ProductSerializer):
    class Meta(ProductSerializer.Meta):
        # the code identifies the product in the loan book and stays fixed
        read_only_fields = ["code"]
        extra_kwargs = {}


# ---------------------------------------------------------------- loans
class LoanApplySerializer(serializers.Serializer):
    borrower_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    principal = money(min_value=Decimal("0.01"))
    term_months = serializers.IntegerField(min_value=1)
    purpose = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                    max_length=200)
    application_date = serializers.DateField(required=False, allow_null=True)
    guarantor_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_null=True,
        help_text="Which of the borrower's guarantors stand behind this loan; omit for all")


class LoanGuarantorsSerializer(serializers.Serializer):
    guarantor_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)


class LoanQuoteRequestSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    principal = money(min_value=Decimal("0.01"))
    term_months = serializers.IntegerField(min_value=1)
    disbursement_date = serializers.DateField(required=False, allow_null=True)
    borrower_id = serializers.IntegerField(required=False, allow_null=True)


class ScheduleRowSerializer(serializers.Serializer):
    number = serializers.IntegerField()
    due_date = serializers.DateField()
    opening_balance = money()
    principal_due = money()
    interest_due = money()
    instalment = money()
    closing_balance = money()


# ---------------------------------------------------------------- charges
class ChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Charge
        fields = ["id", "code", "name", "description", "basis", "value", "timing", "is_active"]


class LoanChargeSerializer(serializers.ModelSerializer):
    instalment_number = serializers.IntegerField(source="instalment.number", read_only=True,
                                                 default=None)

    class Meta:
        model = LoanCharge
        fields = ["id", "loan_id", "charge_id", "name", "amount", "applied_on", "collection",
                  "instalment_id", "instalment_number", "transaction_id"]


class ProductChargeSerializer(serializers.ModelSerializer):
    charge = ChargeSerializer(read_only=True)

    class Meta:
        model = ProductCharge
        fields = ["id", "product_id", "charge"]


class ManualChargeSerializer(serializers.Serializer):
    charge_id = serializers.IntegerField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    amount = money(min_value=Decimal("0.01"), required=False, allow_null=True)
    applied_on = serializers.DateField(required=False, allow_null=True)
    collection = serializers.ChoiceField(choices=ChargeCollection.choices,
                                         default=ChargeCollection.COUNTER)


class ScoreFactorSerializer(serializers.Serializer):
    factor = serializers.CharField()
    points = serializers.IntegerField()
    max = serializers.IntegerField()
    reason = serializers.CharField()


class ScorecardSerializer(serializers.Serializer):
    score = serializers.IntegerField()
    grade = serializers.CharField()
    grade_label = serializers.CharField()
    summary = serializers.CharField()
    factors = ScoreFactorSerializer(many=True)


class LoanQuoteSerializer(serializers.Serializer):
    currency = serializers.CharField(required=False)
    fx_rate = serializers.DecimalField(max_digits=18, decimal_places=6, required=False)
    principal = money()
    term_months = serializers.IntegerField()
    repayment_frequency = serializers.CharField()
    interest_rate_pct = serializers.DecimalField(max_digits=6, decimal_places=3)
    rate_method = serializers.CharField()
    instalment_amount = money()
    monthly_equivalent = money()
    total_interest = money()
    total_repayable = money()
    admin_fee = money()
    insurance_fee = money()
    other_charges = money(required=False)
    charges = serializers.ListField(child=serializers.DictField(), required=False)
    net_disbursed = money()
    total_cost_of_credit = money()
    apr_pct = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    affordability_pct = money(allow_null=True, required=False)
    affordable = serializers.BooleanField(allow_null=True, required=False)
    schedule = ScheduleRowSerializer(many=True)
    scorecard = ScorecardSerializer(allow_null=True, required=False)


class LoanDecisionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class LoanDisburseSerializer(serializers.Serializer):
    disbursement_date = serializers.DateField(required=False, allow_null=True)
    first_instalment_date = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices,
                                     default=PaymentMethod.BANK_TRANSFER)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                      max_length=80)


class InstalmentSerializer(serializers.ModelSerializer):
    total_due = money(read_only=True)
    total_paid = money(read_only=True)
    balance = money(read_only=True)

    class Meta:
        model = Instalment
        fields = ["id", "number", "due_date", "opening_balance", "principal_due", "interest_due",
                  "penalty_due", "charge_due", "principal_paid", "interest_paid", "penalty_paid",
                  "charge_paid", "closing_balance", "status", "paid_date", "total_due",
                  "total_paid", "balance"]


class TransactionSerializer(serializers.ModelSerializer):
    reversal_of_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Transaction
        fields = ["id", "loan_id", "txn_type", "txn_date", "amount", "principal_component",
                  "interest_component", "penalty_component", "charge_component", "fx_rate",
                  "method", "reference", "narration", "reversed", "reversal_of_id", "created_at"]


class LoanSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    officer_name = serializers.CharField(source="officer.full_name", read_only=True, default=None)
    collector_name = serializers.CharField(source="collector.full_name", read_only=True,
                                           default=None)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    total_outstanding = money(read_only=True)
    total_cost_of_credit = money(read_only=True)
    arrears_amount = money(read_only=True, default=Decimal("0"))
    days_in_arrears = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Loan
        fields = ["id", "loan_no", "external_ref", "borrower_id", "borrower_name", "product_id",
                  "product_name", "officer_id", "officer_name", "collector_id",
                  "collector_name", "branch_id", "branch_name",
                  "group_id", "refinanced_from_id",
                  "principal", "currency", "fx_rate", "interest_rate_pct", "rate_method",
                  "repayment_frequency",
                  "term_months", "purpose", "admin_fee", "insurance_fee", "other_charges",
                  "instalment_amount", "total_interest", "total_cost_of_credit", "apr_pct",
                  "status",
                  "application_date", "approved_at", "rejection_reason", "disbursement_date",
                  "first_instalment_date", "maturity_date", "closed_at", "principal_outstanding",
                  "interest_outstanding", "penalties_outstanding", "charges_outstanding",
                  "total_paid", "total_outstanding", "arrears_amount", "days_in_arrears",
                  "interest_accrued", "fees_deferred", "credit_score", "credit_grade"]


class LoanNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.full_name", read_only=True, default=None)

    class Meta:
        model = LoanNote
        fields = ["id", "loan_id", "author_name", "body", "next_action_date", "promised_amount",
                  "promised_date", "resolved", "created_at"]
        read_only_fields = ["loan_id", "author_name", "created_at"]


class CollateralSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.CharField(source="recorded_by.full_name", read_only=True,
                                             default=None)

    class Meta:
        model = Collateral
        fields = ["id", "loan_id", "type", "description", "estimated_value", "valuation_date",
                  "reference", "status", "notes", "recorded_by_name", "created_at"]
        read_only_fields = ["loan_id", "recorded_by_name", "created_at"]


class LoanDetailSerializer(LoanSerializer):
    schedule = InstalmentSerializer(many=True, read_only=True)
    transactions = TransactionSerializer(many=True, read_only=True)
    notes = LoanNoteSerializer(many=True, read_only=True)
    collateral = CollateralSerializer(many=True, read_only=True)
    charges = LoanChargeSerializer(many=True, read_only=True)
    guarantors = GuarantorSerializer(many=True, read_only=True)
    scorecard = serializers.SerializerMethodField()
    group_name = serializers.CharField(source="group.name", read_only=True, default=None)
    refinanced_from_no = serializers.CharField(source="refinanced_from.loan_no", read_only=True,
                                               default=None)

    class Meta(LoanSerializer.Meta):
        fields = LoanSerializer.Meta.fields + ["schedule", "transactions", "notes", "collateral",
                                               "charges", "guarantors", "scorecard", "group_name",
                                               "refinanced_from_no"]

    def get_scorecard(self, obj):
        from .services.scoring import read_from_loan

        result = read_from_loan(obj)
        return ScorecardSerializer(result).data if result else None


class RecoverySerializer(serializers.Serializer):
    amount = money(min_value=Decimal("0.01"))
    txn_date = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                      max_length=80)
    narration = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class SettlementQuoteSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    loan_no = serializers.CharField()
    principal_outstanding = money()
    interest_accrued = money()
    penalties_outstanding = money()
    charges_outstanding = money()
    interest_rebate = money()
    settlement_amount = money()
    total_outstanding = money()
    saving_vs_running_to_term = money()


class SettleSerializer(serializers.Serializer):
    amount = money(required=False, allow_null=True,
                   help_text="Optional confirmation of the quoted figure")
    txn_date = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                      max_length=80)
    narration = serializers.CharField(required=False, allow_null=True, allow_blank=True)


# ---------------------------------------------------------------- postings
class RepaymentSerializer(serializers.Serializer):
    amount = money(min_value=Decimal("0.01"))
    txn_date = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                      max_length=80)
    narration = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class WaiverSerializer(serializers.Serializer):
    amount = money(min_value=Decimal("0.01"))
    narration = serializers.CharField()


class NarrationSerializer(serializers.Serializer):
    """Write-off and reversal both just need a reason."""
    narration = serializers.CharField()


class RescheduleSerializer(serializers.Serializer):
    new_term_months = serializers.IntegerField(min_value=1)
    new_interest_rate_pct = serializers.DecimalField(max_digits=6, decimal_places=3,
                                                     required=False, allow_null=True)
    first_instalment_date = serializers.DateField(required=False, allow_null=True)
    narration = serializers.CharField(required=False, default="Loan rescheduled")


# ---------------------------------------------------------------- reports
class DashboardSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    borrowers = serializers.IntegerField()
    active_loans = serializers.IntegerField()
    pending_applications = serializers.IntegerField()
    portfolio_outstanding = money()
    principal_outstanding = money()
    par_30_amount = money()
    par_30_pct = money()
    disbursed_this_month = money()
    collected_this_month = money()
    due_this_month = money()
    collection_rate_pct = money()
    par_30_loans = serializers.IntegerField()
    arrears_total = money()
    loans_in_arrears = serializers.IntegerField()
    previous_month = serializers.DictField(child=money())
    arrears_buckets = serializers.DictField(child=money())
    arrears_bucket_loans = serializers.DictField(child=serializers.IntegerField())
    status_counts = serializers.DictField(child=serializers.IntegerField())
    monthly_series = serializers.ListField(child=serializers.DictField())
    due_next_7_days = serializers.DictField()
    watchlist = serializers.ListField(child=serializers.DictField())
    product_mix = serializers.ListField(child=serializers.DictField())
    recent_activity = serializers.ListField(child=serializers.DictField())


class NotificationSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True, default=None)
    body = serializers.CharField(source="shown_body", read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "borrower_id", "borrower_name", "loan_id", "loan_no", "kind", "channel",
                  "to_address", "subject", "body", "status", "scheduled_for", "sent_at", "error",
                  "attempts", "last_attempt_at", "provider", "provider_message_id",
                  "created_at"]


class NotificationActionSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    as_of = serializers.DateField(required=False, allow_null=True)


class OrganisationSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganisationSetting
        fields = ["name", "currency", "address", "phone", "email",
                  "registration", "statement_declarations",
                  "ecl_stage1_pct", "ecl_stage2_pct", "ecl_stage3_pct",
                  "ecl_stage2_days", "ecl_stage3_days", "reminder_days_before",
                  # The Settings page has always shown these; the API silently dropped
                  # them, so an edited approval limit was never saved.
                  "officer_approval_limit", "min_credit_score", "group_arrears_block_days",
                  "require_open_till", "require_signature", "closed_weekdays", "interest_method", "updated_at"]
        read_only_fields = ["updated_at"]

    def validate_interest_method(self, value):
        from .exceptions import BusinessRuleError
        from .services.eir import assert_can_change

        try:
            assert_can_change(value)
        except BusinessRuleError as exc:
            raise serializers.ValidationError(str(exc.detail))
        return value

    def validate_closed_weekdays(self, value):
        from .services.workdays import format_weekdays, parse_weekdays

        try:
            days = parse_weekdays(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        if len(days) == 7:
            raise serializers.ValidationError("The offices must open on at least one day a week")
        return format_weekdays(days)


class HolidaySerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True,
                                            default=None)

    class Meta:
        model = Holiday
        fields = ["id", "date", "name", "recurs_annually", "created_by_name", "created_at"]
        read_only_fields = ["created_at"]


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    doc_type = serializers.ChoiceField(choices=DocumentType.choices, default=DocumentType.OTHER)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=200)


class BulkImportSerializer(serializers.Serializer):
    file = serializers.FileField()
    commit = serializers.BooleanField(default=False)
    allow_partial = serializers.BooleanField(default=False)


class LoanBookImportSerializer(serializers.Serializer):
    file = serializers.FileField()
    commit = serializers.BooleanField(default=False)
    cutover_date = serializers.DateField(
        required=False, allow_null=True,
        help_text="The date the balances are true at; the opening postings carry it")


# ---------------------------------------------------------------- groups
class GroupMemberSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    borrower_no = serializers.CharField(source="borrower.borrower_no", read_only=True)
    phone = serializers.CharField(source="borrower.phone", read_only=True)

    class Meta:
        model = GroupMember
        fields = ["id", "group_id", "borrower_id", "borrower_no", "borrower_name", "phone",
                  "role", "joined_on", "is_active"]
        read_only_fields = ["group_id", "borrower_no", "borrower_name", "phone"]


class GroupSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    officer_name = serializers.CharField(source="officer.full_name", read_only=True, default=None)
    member_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = BorrowerGroup
        fields = ["id", "group_no", "name", "branch", "branch_name", "officer", "officer_name",
                  "meeting_day", "meeting_place", "formed_on", "status", "notes", "member_count",
                  "created_at"]
        read_only_fields = ["group_no", "created_at"]


class GroupDetailSerializer(GroupSerializer):
    members = GroupMemberSerializer(many=True, read_only=True)

    class Meta(GroupSerializer.Meta):
        fields = GroupSerializer.Meta.fields + ["members"]


class AddMemberSerializer(serializers.Serializer):
    borrower_id = serializers.IntegerField()
    role = serializers.ChoiceField(choices=["leader", "treasurer", "secretary", "member"],
                                   default="member")
    joined_on = serializers.DateField(required=False, allow_null=True)


# ---------------------------------------------------------------- savings
class SavingsProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavingsProduct
        fields = ["id", "code", "name", "description", "interest_rate_pct_pa", "min_balance",
                  "monthly_fee", "allow_withdrawals", "is_active"]


class SavingsAccountSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    borrower_no = serializers.CharField(source="borrower.borrower_no", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    available_balance = money(read_only=True)
    min_balance = money(source="product.min_balance", read_only=True)
    allow_withdrawals = serializers.BooleanField(source="product.allow_withdrawals",
                                                 read_only=True)

    class Meta:
        model = SavingsAccount
        fields = ["id", "account_no", "borrower_id", "borrower_no", "borrower_name",
                  "product_id", "product_name", "branch_name", "status", "balance",
                  "available_balance", "min_balance", "allow_withdrawals", "opened_on",
                  "closed_on", "last_interest_date", "created_at"]


class SavingsTransactionSerializer(serializers.ModelSerializer):
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True,
                                           default=None)

    class Meta:
        model = SavingsTransaction
        fields = ["id", "account_id", "txn_type", "txn_date", "amount", "balance_after",
                  "method", "reference", "narration", "reversed", "reversal_of_id",
                  "posted_by_name", "created_at"]


class SavingsAccountDetailSerializer(SavingsAccountSerializer):
    transactions = SavingsTransactionSerializer(many=True, read_only=True)

    class Meta(SavingsAccountSerializer.Meta):
        fields = SavingsAccountSerializer.Meta.fields + ["transactions"]


class OpenSavingsSerializer(serializers.Serializer):
    borrower_id = serializers.IntegerField()
    product_id = serializers.IntegerField()
    opening_deposit = money(required=False, allow_null=True)
    opened_on = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, required=False,
                                     allow_null=True)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                      max_length=80)


class SavingsMovementSerializer(serializers.Serializer):
    amount = money(min_value=Decimal("0.01"))
    txn_date = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                      max_length=80)
    narration = serializers.CharField(required=False, allow_null=True, allow_blank=True)


# ---------------------------------------------------------------- top-up
class TopUpRequestSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    principal = money(min_value=Decimal("0.01"))
    term_months = serializers.IntegerField(min_value=1)
    purpose = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                    max_length=200)
    application_date = serializers.DateField(required=False, allow_null=True)


class LedgerAccountSerializer(serializers.ModelSerializer):
    # Where postings to a reconciled account belong instead; null when a manual
    # journal may use it. Lets the journal form leave those accounts out rather
    # than offer them and refuse.
    controlled_by = serializers.SerializerMethodField()

    class Meta:
        model = LedgerAccount
        fields = ["id", "code", "name", "type", "description", "is_active", "controlled_by"]

    def get_controlled_by(self, obj) -> str | None:
        from .services.ledger import CONTROL_ACCOUNTS

        return CONTROL_ACCOUNTS.get(obj.code)


class JournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)

    class Meta:
        model = JournalLine
        fields = ["id", "account_code", "account_name", "debit", "credit", "description"]


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True, read_only=True)
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True, default=None)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True,
                                           default=None)
    total_debit = money(read_only=True)
    total_credit = money(read_only=True)

    class Meta:
        model = JournalEntry
        # Every source id, so a line in the journal can be traced back to whatever
        # raised it rather than rendering with no identity.
        fields = ["id", "entry_no", "entry_date", "narration", "source", "transaction_id",
                  "savings_transaction_id", "facility_transaction_id", "capital_transaction_id",
                  "loan_id", "loan_no", "branch_name", "posted_by_name", "total_debit",
                  "total_credit", "lines", "created_at"]


# ---------------------------------------------------------------- manual journals
class ManualJournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    account_type = serializers.CharField(source="account.type", read_only=True)

    class Meta:
        model = ManualJournalLine
        fields = ["id", "account_id", "account_code", "account_name", "account_type", "debit",
                  "credit", "description"]


class ManualJournalSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    prepared_by_name = serializers.CharField(source="prepared_by.full_name", read_only=True,
                                             default=None)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True,
                                           default=None)
    reversed_by_name = serializers.CharField(source="reversed_by.full_name", read_only=True,
                                             default=None)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    entry_no = serializers.CharField(source="journal_entry.entry_no", read_only=True,
                                     default=None)
    reversal_entry_no = serializers.CharField(source="reversal_entry.entry_no", read_only=True,
                                              default=None)
    lines = ManualJournalLineSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = ManualJournal
        fields = ["id", "journal_no", "entry_date", "narration", "reference", "branch_id",
                  "branch_name", "status", "status_label", "prepared_by_id", "prepared_by_name",
                  "prepared_at", "posted_by_name", "posted_at", "rejected_reason", "entry_no",
                  "reversal_entry_no", "reversed_by_name", "reversed_at", "reversal_reason",
                  "total", "lines", "created_at"]

    def get_total(self, obj) -> str:
        # From the prefetched lines, so a page of journals is not a query each.
        return str(sum((line.debit for line in obj.lines.all()), Decimal("0")))


class ManualJournalLineInputSerializer(serializers.Serializer):
    account_id = serializers.IntegerField(required=False, allow_null=True)
    account_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    debit = money(required=False, allow_null=True, min_value=Decimal("0"))
    credit = money(required=False, allow_null=True, min_value=Decimal("0"))
    description = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                        max_length=200)


class ManualJournalCreateSerializer(serializers.Serializer):
    entry_date = serializers.DateField(required=False, allow_null=True)
    narration = serializers.CharField()
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True,
                                      max_length=80)
    branch = serializers.IntegerField(required=False, allow_null=True)
    lines = ManualJournalLineInputSerializer(many=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, trim_whitespace=True)


# ---------------------------------------------------------------- tills
class TillMovementSerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    kind = serializers.CharField()
    reference = serializers.CharField()
    detail = serializers.CharField(allow_blank=True)
    amount = money()


class TillSessionSerializer(serializers.ModelSerializer):
    teller_name = serializers.CharField(source="teller.full_name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    verified_by_name = serializers.CharField(source="verified_by.full_name", read_only=True,
                                             default=None)
    variance_entry_no = serializers.CharField(source="variance_entry.entry_no", read_only=True,
                                              default=None)

    class Meta:
        model = TillSession
        fields = ["id", "session_no", "teller_id", "teller_name", "branch_name", "business_date",
                  "opened_at", "opening_float", "status", "status_label", "closed_at", "cash_in",
                  "cash_out", "expected_cash", "counted_cash", "variance", "close_note",
                  "verified_by_name", "verified_at", "verify_note", "variance_entry_no"]


class TillDetailSerializer(TillSessionSerializer):
    """A till with its live position: what it should hold now, and why."""
    position = serializers.SerializerMethodField()

    class Meta(TillSessionSerializer.Meta):
        fields = TillSessionSerializer.Meta.fields + ["position"]

    def get_position(self, obj) -> dict:
        from .services.tills import position

        now = position(obj)
        return {
            "cash_in": str(now["cash_in"]), "cash_out": str(now["cash_out"]),
            "expected_cash": str(now["expected_cash"]),
            "movements": TillMovementSerializer(now["movements"], many=True).data,
        }


class TillOpenSerializer(serializers.Serializer):
    opening_float = money(min_value=Decimal("0"))


class TillCountSerializer(serializers.Serializer):
    counted_cash = money(min_value=Decimal("0"))
    note = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_null=True, allow_blank=True)


# ---------------------------------------------------------------- bank reconciliation
class StatementLineSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    entry_no = serializers.CharField(source="journal_entry.entry_no", read_only=True,
                                     default=None)
    entry_narration = serializers.CharField(source="journal_entry.narration", read_only=True,
                                            default=None)
    entry_date = serializers.DateField(source="journal_entry.entry_date", read_only=True,
                                       default=None)
    matched_by_name = serializers.CharField(source="matched_by.full_name", read_only=True,
                                            default=None)

    class Meta:
        model = StatementLine
        fields = ["id", "line_no", "txn_date", "description", "reference", "amount", "status",
                  "status_label", "journal_entry_id", "entry_no", "entry_narration",
                  "entry_date", "auto_matched", "matched_by_name", "matched_at", "note"]


class BankStatementSerializer(serializers.ModelSerializer):
    channel_label = serializers.CharField(source="get_channel_display", read_only=True)
    uploaded_by_name = serializers.CharField(source="uploaded_by.full_name", read_only=True,
                                             default=None)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = BankStatement
        fields = ["id", "statement_no", "account_name", "channel", "channel_label",
                  "period_start", "period_end", "opening_balance", "closing_balance",
                  "file_name", "uploaded_by_name", "uploaded_at", "summary"]

    def get_summary(self, obj) -> dict:
        from .services.bankrec import summary

        data = summary(obj)
        return {key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in data.items()}


class BankStatementDetailSerializer(BankStatementSerializer):
    lines = StatementLineSerializer(many=True, read_only=True)

    class Meta(BankStatementSerializer.Meta):
        fields = BankStatementSerializer.Meta.fields + ["lines"]


class StatementUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    account_name = serializers.CharField(max_length=120)
    channel = serializers.ChoiceField(choices=[PaymentMethod.BANK_TRANSFER.value,
                                               PaymentMethod.MOBILE_MONEY.value],
                                      default=PaymentMethod.BANK_TRANSFER.value)
    opening_balance = serializers.DecimalField(max_digits=18, decimal_places=2, required=False,
                                               allow_null=True)
    closing_balance = serializers.DecimalField(max_digits=18, decimal_places=2, required=False,
                                               allow_null=True)


class StatementEntrySerializer(serializers.Serializer):
    """A ledger entry as a candidate for, or an outstanding item in, a reconciliation."""
    id = serializers.IntegerField()
    entry_no = serializers.CharField()
    entry_date = serializers.DateField()
    narration = serializers.CharField()
    source = serializers.CharField()
    amount = money(max_digits=18)
    method = serializers.CharField(allow_null=True)
    reference = serializers.CharField(allow_null=True)
    loan_no = serializers.CharField(allow_null=True)


class StatementMatchSerializer(serializers.Serializer):
    entry_id = serializers.IntegerField()


class StatementJournalSerializer(serializers.Serializer):
    account_code = serializers.CharField(max_length=20)


# ---------------------------------------------------------------- provisioning
class ExchangeRateSerializer(serializers.ModelSerializer):
    set_by_name = serializers.CharField(source="set_by.full_name", read_only=True, default=None)

    class Meta:
        model = ExchangeRate
        fields = ["id", "code", "rate_date", "rate", "note", "set_by_name", "created_at"]
        read_only_fields = ["created_at"]


class RevaluationLineSerializer(serializers.ModelSerializer):
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True)
    borrower = serializers.CharField(source="loan.borrower.full_name", read_only=True)

    class Meta:
        model = RevaluationLine
        fields = ["id", "loan_id", "loan_no", "borrower", "currency", "old_rate", "new_rate",
                  "principal_outstanding", "penalties_outstanding", "charges_outstanding",
                  "interest_receivable", "fees_deferred",
                  "principal_movement", "penalties_movement", "charges_movement",
                  "interest_movement", "fees_movement", "movement"]


class RevaluationPreviewLineSerializer(serializers.Serializer):
    loan_id = serializers.IntegerField()
    loan_no = serializers.CharField()
    borrower = serializers.CharField()
    currency = serializers.CharField()
    principal_outstanding = money()
    penalties_outstanding = money()
    charges_outstanding = money()
    old_rate = serializers.DecimalField(max_digits=18, decimal_places=6)
    new_rate = serializers.DecimalField(max_digits=18, decimal_places=6)
    carrying_before = money()
    carrying_after = money()
    interest_receivable = money()
    fees_deferred = money()
    principal_movement = money()
    penalties_movement = money()
    charges_movement = money()
    interest_movement = money()
    fees_movement = money()
    movement = money()


class RevaluationPreviewSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    base_currency = serializers.CharField()
    loans = serializers.IntegerField()
    movement = money()
    missing_rates = serializers.ListField(child=serializers.CharField())
    lines = RevaluationPreviewLineSerializer(many=True)


class RevaluationRunSerializer(serializers.ModelSerializer):
    entry_no = serializers.CharField(source="journal_entry.entry_no", read_only=True,
                                     default=None)
    run_by_name = serializers.CharField(source="run_by.full_name", read_only=True, default=None)
    lines = RevaluationLineSerializer(many=True, read_only=True)

    class Meta:
        model = RevaluationRun
        fields = ["id", "run_no", "as_of", "base_currency", "loans_revalued", "movement",
                  "narration", "entry_no", "run_by_name", "created_at", "lines"]


class ProvisionRunSerializer(serializers.ModelSerializer):
    entry_no = serializers.CharField(source="journal_entry.entry_no", read_only=True,
                                     default=None)
    reversal_entry_no = serializers.CharField(source="reversal_entry.entry_no", read_only=True,
                                              default=None)
    run_by_name = serializers.CharField(source="run_by.full_name", read_only=True, default=None)
    reversed_by_name = serializers.CharField(source="reversed_by.full_name", read_only=True,
                                             default=None)

    class Meta:
        model = ProvisionRun
        fields = ["id", "run_no", "period_end", "status", "loans_assessed", "loans_released",
                  "total_exposure", "total_carrying_amount", "provision_required",
                  "provision_before", "movement", "ledger_provision_before",
                  "stage1_pct", "stage2_pct", "stage3_pct", "stage2_days", "stage3_days",
                  "entry_no", "reversal_entry_no", "narration", "run_by_name",
                  "reversed_by_name", "created_at", "reversed_at"]


class ProvisionRunLineSerializer(serializers.ModelSerializer):
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True)
    borrower = serializers.CharField(source="loan.borrower.full_name", read_only=True)
    product = serializers.CharField(source="loan.product.name", read_only=True)

    class Meta:
        model = ProvisionRunLine
        fields = ["id", "loan_id", "loan_no", "borrower", "product", "loan_status", "stage",
                  "days_past_due", "exposure", "carrying_amount", "rate_pct",
                  "provision_required", "provision_before", "provision_after", "movement"]


class ProvisionRunDetailSerializer(ProvisionRunSerializer):
    lines = ProvisionRunLineSerializer(many=True, read_only=True)

    class Meta(ProvisionRunSerializer.Meta):
        fields = ProvisionRunSerializer.Meta.fields + ["lines"]


class ProvisionRunRequestSerializer(serializers.Serializer):
    as_of = serializers.DateField(required=False, allow_null=True)
    narration = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    force = serializers.BooleanField(default=False)


# ---------------------------------------------------------------- funding and capital
class FundingFacilitySerializer(serializers.ModelSerializer):
    available = money(read_only=True)
    total_outstanding = money(read_only=True)
    is_open = serializers.BooleanField(read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True,
                                           default=None)

    class Meta:
        model = FundingFacility
        fields = ["id", "facility_no", "funder_name", "name", "facility_limit",
                  "interest_rate_pct_pa", "is_revolving", "start_date", "maturity_date",
                  "repayment_terms", "principal_outstanding", "interest_accrued",
                  "last_accrual_date", "closed_on", "is_open", "available",
                  "total_outstanding", "branch", "branch_name", "notes", "created_by_name",
                  "created_at"]


class FacilityTransactionSerializer(serializers.ModelSerializer):
    txn_type_label = serializers.CharField(source="get_txn_type_display", read_only=True)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True,
                                          default=None)
    entry_no = serializers.CharField(source="journal_entry.entry_no", read_only=True, default=None)

    class Meta:
        model = FacilityTransaction
        fields = ["id", "txn_type", "txn_type_label", "txn_date", "amount", "principal_after",
                  "accrued_after", "method", "reference", "narration", "reversed",
                  "reversal_of", "posted_by_name", "entry_no", "created_at"]


class FundingFacilityDetailSerializer(FundingFacilitySerializer):
    transactions = FacilityTransactionSerializer(many=True, read_only=True)
    totals = serializers.SerializerMethodField()

    class Meta(FundingFacilitySerializer.Meta):
        fields = FundingFacilitySerializer.Meta.fields + ["transactions", "totals"]

    def get_totals(self, obj) -> dict:
        from .services.funding import facility_totals

        return facility_totals(obj)


class OpenFacilitySerializer(serializers.Serializer):
    funder_name = serializers.CharField(max_length=120)
    name = serializers.CharField(max_length=120)
    facility_limit = money(min_value=Decimal("0.01"))
    interest_rate_pct_pa = serializers.DecimalField(max_digits=6, decimal_places=3, required=False,
                                                    default=Decimal("0"), min_value=Decimal("0"))
    is_revolving = serializers.BooleanField(default=False)
    start_date = serializers.DateField(required=False, allow_null=True)
    maturity_date = serializers.DateField(required=False, allow_null=True)
    repayment_terms = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    branch = serializers.IntegerField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class FacilityUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FundingFacility
        # status is absent on purpose: a facility closes through the close action,
        # and its balances move only through a posting.
        fields = ["funder_name", "name", "facility_limit", "interest_rate_pct_pa",
                  "is_revolving", "maturity_date", "repayment_terms", "branch", "notes"]


class FacilityMovementSerializer(serializers.Serializer):
    amount = money(min_value=Decimal("0.01"))
    txn_date = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=[m.value for m in PaymentMethod], required=False,
                                     default=PaymentMethod.BANK_TRANSFER)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    narration = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class CapitalTransactionSerializer(serializers.ModelSerializer):
    txn_type_label = serializers.CharField(source="get_txn_type_display", read_only=True)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True,
                                          default=None)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    entry_no = serializers.CharField(source="journal_entry.entry_no", read_only=True, default=None)

    class Meta:
        model = CapitalTransaction
        fields = ["id", "txn_type", "txn_type_label", "txn_date", "amount", "contributor",
                  "method", "reference", "narration", "reversed", "reversal_of", "branch",
                  "branch_name", "posted_by_name", "entry_no", "created_at"]


class CapitalSummarySerializer(serializers.Serializer):
    injected = money()
    returned = money()
    dividends = money()
    net_capital = money()


class MaturingFacilitySerializer(serializers.Serializer):
    facility_no = serializers.CharField()
    funder_name = serializers.CharField()
    maturity_date = serializers.DateField()
    principal_outstanding = money()
    days = serializers.IntegerField()


class FundingSummarySerializer(serializers.Serializer):
    """The KPI strip. Money goes through money() so cents survive as strings."""
    as_of = serializers.DateField()
    facilities = serializers.IntegerField()
    open_facilities = serializers.IntegerField()
    total_limit = money()
    drawn = money()
    available = money()
    accrued_interest = money()
    utilisation_pct = money()
    capital = CapitalSummarySerializer()
    cash = money()
    maturing_soon = MaturingFacilitySerializer(many=True)


class LedgerRowSerializer(serializers.Serializer):
    """One trial-balance row, as trial_balance() and balance_sheet() emit it."""
    code = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    debit = money(max_digits=18)
    credit = money(max_digits=18)
    balance = money(max_digits=18)
    side = serializers.CharField()


class BalanceSheetSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    assets = LedgerRowSerializer(many=True)
    liabilities = LedgerRowSerializer(many=True)
    equity = LedgerRowSerializer(many=True)
    total_assets = money(max_digits=18)
    total_liabilities = money(max_digits=18)
    total_equity = money(max_digits=18)
    total_liabilities_and_equity = money(max_digits=18)
    retained_earnings = money(max_digits=18)
    income_to_date = money(max_digits=18)
    expense_to_date = money(max_digits=18)
    difference = money(max_digits=18)
    balanced = serializers.BooleanField()
    branch_id = serializers.IntegerField(allow_null=True)


class ReconciliationRowSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    ledger = money(max_digits=18)
    book = money(max_digits=18)
    difference = money(max_digits=18)
    agrees = serializers.BooleanField()
    sub_ledger = serializers.CharField()


class ReconciliationSerializer(serializers.Serializer):
    as_of = serializers.DateField()
    rows = ReconciliationRowSerializer(many=True)
    agrees = serializers.BooleanField()
    breaks = ReconciliationRowSerializer(many=True)
    trial_balance_balanced = serializers.BooleanField()
    balance_sheet_balanced = serializers.BooleanField()


class SavingsPortfolioProductSerializer(serializers.Serializer):
    product = serializers.CharField()
    accounts = serializers.IntegerField()
    balance = money()


class SavingsPortfolioSerializer(serializers.Serializer):
    """The savings book at a glance.

    A serializer rather than the raw dict: DRF renders a bare Decimal as a JSON
    float, and this API renders money as a decimal string so cents survive.
    """
    accounts = serializers.IntegerField()
    active_accounts = serializers.IntegerField()
    dormant_accounts = serializers.IntegerField()
    closed_accounts = serializers.IntegerField()
    total_balance = money()
    by_product = SavingsPortfolioProductSerializer(many=True)


class ProvisionRatesSerializer(serializers.Serializer):
    stage_1 = serializers.DecimalField(max_digits=6, decimal_places=2)
    stage_2 = serializers.DecimalField(max_digits=6, decimal_places=2)
    stage_3 = serializers.DecimalField(max_digits=6, decimal_places=2)
    stage_2_days = serializers.IntegerField()
    stage_3_days = serializers.IntegerField()


class ProvisionPreviewSerializer(serializers.Serializer):
    period_end = serializers.DateField()
    entry_date = serializers.DateField()
    loans_assessed = serializers.IntegerField()
    loans_released = serializers.IntegerField()
    total_exposure = money()
    total_carrying_amount = money()
    provision_required = money()
    provision_booked = money()
    movement = money()
    ledger_provision = money()
    ledger_agrees = serializers.BooleanField()
    already_posted = serializers.BooleanField()
    existing_run_no = serializers.CharField(allow_null=True)
    rates = ProvisionRatesSerializer()


class BorrowingAccrualSerializer(serializers.Serializer):
    as_of = serializers.CharField()
    facilities_accrued = serializers.IntegerField()
    months_posted = serializers.IntegerField()
    interest_accrued = money()
    months_skipped = serializers.IntegerField()
    skipped = serializers.ListField(child=serializers.CharField())


class CapitalMovementSerializer(serializers.Serializer):
    # Never 'reversal': a reversal is raised by the reverse action, against the
    # movement it undoes.
    txn_type = serializers.ChoiceField(choices=[
        CapitalTxnType.INJECTION.value,
        CapitalTxnType.RETURN_OF_CAPITAL.value,
        CapitalTxnType.DIVIDEND.value,
    ])
    amount = money(min_value=Decimal("0.01"))
    contributor = serializers.CharField(max_length=160)
    txn_date = serializers.DateField(required=False, allow_null=True)
    method = serializers.ChoiceField(choices=[m.value for m in PaymentMethod], required=False,
                                     default=PaymentMethod.BANK_TRANSFER)
    reference = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    narration = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    branch = serializers.IntegerField(required=False, allow_null=True)


# ---------------------------------------------------------------- period close
class AccountingPeriodSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    closed_by_name = serializers.CharField(source="closed_by.full_name", read_only=True,
                                           default=None)
    reopened_by_name = serializers.CharField(source="reopened_by.full_name", read_only=True,
                                             default=None)
    snapshot = serializers.SerializerMethodField()

    class Meta:
        model = AccountingPeriod
        fields = ["id", "year", "month", "label", "start_date", "end_date", "state",
                  "closed_at", "closed_by_name", "reopened_at", "reopened_by_name",
                  "reopen_reason", "reopen_count", "note", "snapshot_debits",
                  "snapshot_credits", "snapshot_entries", "snapshot_principal_outstanding",
                  "snapshot_savings_balance", "snapshot"]

    def get_snapshot(self, obj) -> dict | None:
        """The frozen trial balance, parsed out of the stored JSON.

        Only on the detail view: the register renders a dozen rows and none of them
        needs a whole trial balance inlined.
        """
        if not self.context.get("with_snapshot") or not obj.snapshot_json:
            return None
        import json

        try:
            return json.loads(obj.snapshot_json)
        except ValueError:
            return None


class PeriodMonthSerializer(serializers.Serializer):
    """One row of the register: a month, closed or not, with its period if it has one."""
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    label = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    state = serializers.CharField()
    closed_by_implication = serializers.BooleanField()
    has_ended = serializers.BooleanField()
    closable = serializers.BooleanField()
    transactions = serializers.IntegerField()
    period = AccountingPeriodSerializer(allow_null=True)


class PeriodCloseSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    force = serializers.BooleanField(default=False)


class PeriodReopenSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=10, trim_whitespace=True)


class AuditSerializer(serializers.ModelSerializer):
    username = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "user_id", "username", "action", "entity", "entity_id", "detail",
                  "created_at"]

    def get_username(self, obj) -> str:
        return obj.user.username if obj.user_id else "system"


STATUS_CHOICES = [s.value for s in LoanStatus]
TXN_TYPE_CHOICES = [t.value for t in TxnType]
RATE_METHOD_CHOICES = [m.value for m in RateMethod]
NOTIFICATION_STATUS_CHOICES = [s.value for s in NotificationStatus]


class InboundPaymentSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True, default=None)
    borrower_name = serializers.CharField(source="loan.borrower.full_name", read_only=True,
                                          default=None)
    resolved_by_name = serializers.CharField(source="resolved_by.full_name", read_only=True,
                                             default=None)

    class Meta:
        model = InboundPayment
        fields = ["id", "provider", "external_id", "method", "method_label", "amount",
                  "currency", "paid_on", "payer_phone", "payer_name", "account_ref",
                  "status", "status_label", "reason", "loan_id", "loan_no", "borrower_name",
                  "transaction_id", "matched_by", "resolved_by_name", "resolved_at",
                  "received_at"]


class PayrollRunLineSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True, default=None)
    shortfall = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = PayrollRunLine
        fields = ["id", "loan_id", "loan_no", "employee_no", "name", "file_line", "expected",
                  "deducted", "shortfall", "status", "status_label", "note", "transaction_id"]


class PayrollRunSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True,
                                            default=None)
    posted_by_name = serializers.CharField(source="posted_by.full_name", read_only=True,
                                           default=None)

    class Meta:
        model = PayrollRun
        fields = ["id", "employer", "period_start", "period_end", "received_on", "reference",
                  "status", "status_label", "expected_total", "deducted_total", "posted_total",
                  "file_name", "created_by_name", "created_at", "posted_by_name", "posted_at"]
