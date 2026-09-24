"""Request validation and response shaping for the API.

Money is rendered as a decimal string so cents survive JSON. Read serializers
add the derived values the UI needs (borrower name on a loan, arrears, the
totals on an instalment) rather than making the client recompute them.
"""
from decimal import Decimal

from rest_framework import serializers

from .models import (
    AuditLog,
    Borrower,
    BorrowerDocument,
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
    Guarantor,
    Instalment,
    Loan,
    LoanNote,
    LoanProduct,
    LoanStatus,
    Notification,
    NotificationStatus,
    OrganisationSetting,
    PaymentMethod,
    ProductCharge,
    ProvisionRun,
    ProvisionRunLine,
    ProvisionRunStatus,
    RateMethod,
    Role,
    SavingsAccount,
    SavingsProduct,
    SavingsStatus,
    SavingsTransaction,
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
        fields = ["id", "username", "full_name", "role", "is_active", "branch_id", "branch_name",
                  "phone", "email"]


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(style={"input_type": "password"}, trim_whitespace=False)


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(min_length=6, write_only=True)
    role = serializers.ChoiceField(choices=Role.choices, default=Role.LOAN_OFFICER)

    class Meta:
        model = User
        fields = ["username", "full_name", "password", "role", "branch", "phone", "email"]

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists")
        return value

    def create(self, validated):
        return User.objects.create_user(**validated)


class UserUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False)
    role = serializers.ChoiceField(choices=Role.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(min_length=6, required=False, allow_null=True)
    branch_id = serializers.IntegerField(required=False, allow_null=True)
    phone = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    email = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    unlock = serializers.BooleanField(required=False,
                                      help_text="Clear a lockout from failed sign-ins")


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(min_length=6)


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
    principal = money()
    term_months = serializers.IntegerField()
    interest_rate_pct = serializers.DecimalField(max_digits=6, decimal_places=3)
    rate_method = serializers.CharField()
    instalment_amount = money()
    total_interest = money()
    total_repayable = money()
    admin_fee = money()
    insurance_fee = money()
    other_charges = money(required=False)
    charges = serializers.ListField(child=serializers.DictField(), required=False)
    net_disbursed = money()
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
                  "interest_component", "penalty_component", "charge_component", "method",
                  "reference", "narration", "reversed", "reversal_of_id", "created_at"]


class LoanSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    officer_name = serializers.CharField(source="officer.full_name", read_only=True, default=None)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    total_outstanding = money(read_only=True)
    arrears_amount = money(read_only=True, default=Decimal("0"))
    days_in_arrears = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Loan
        fields = ["id", "loan_no", "borrower_id", "borrower_name", "product_id", "product_name",
                  "officer_id", "officer_name", "branch_id", "branch_name",
                  "group_id", "refinanced_from_id",
                  "principal", "interest_rate_pct", "rate_method", "term_months", "purpose",
                  "admin_fee", "insurance_fee", "other_charges",
                  "instalment_amount", "total_interest", "status",
                  "application_date", "approved_at", "rejection_reason", "disbursement_date",
                  "first_instalment_date", "maturity_date", "closed_at", "principal_outstanding",
                  "interest_outstanding", "penalties_outstanding", "charges_outstanding",
                  "total_paid", "total_outstanding", "arrears_amount", "days_in_arrears",
                  "credit_score", "credit_grade"]


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
    scorecard = serializers.SerializerMethodField()
    group_name = serializers.CharField(source="group.name", read_only=True, default=None)
    refinanced_from_no = serializers.CharField(source="refinanced_from.loan_no", read_only=True,
                                               default=None)

    class Meta(LoanSerializer.Meta):
        fields = LoanSerializer.Meta.fields + ["schedule", "transactions", "notes", "collateral",
                                               "charges", "scorecard", "group_name",
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
    arrears_buckets = serializers.DictField(child=money())
    status_counts = serializers.DictField(child=serializers.IntegerField())
    monthly_series = serializers.ListField(child=serializers.DictField())


class NotificationSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True, default=None)

    class Meta:
        model = Notification
        fields = ["id", "borrower_id", "borrower_name", "loan_id", "loan_no", "kind", "channel",
                  "to_address", "subject", "body", "status", "scheduled_for", "sent_at", "error",
                  "created_at"]


class NotificationActionSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    as_of = serializers.DateField(required=False, allow_null=True)


class OrganisationSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganisationSetting
        fields = ["name", "currency", "address", "phone", "email",
                  "ecl_stage1_pct", "ecl_stage2_pct", "ecl_stage3_pct",
                  "ecl_stage2_days", "ecl_stage3_days", "reminder_days_before", "updated_at"]
        read_only_fields = ["updated_at"]


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    doc_type = serializers.ChoiceField(choices=DocumentType.choices, default=DocumentType.OTHER)
    note = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=200)


class BulkImportSerializer(serializers.Serializer):
    file = serializers.FileField()
    commit = serializers.BooleanField(default=False)
    allow_partial = serializers.BooleanField(default=False)


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
    class Meta:
        model = LedgerAccount
        fields = ["id", "code", "name", "type", "description", "is_active"]


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
        fields = ["id", "entry_no", "entry_date", "narration", "source", "transaction_id",
                  "loan_id", "loan_no", "branch_name", "posted_by_name", "total_debit",
                  "total_credit", "lines", "created_at"]


# ---------------------------------------------------------------- provisioning
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
