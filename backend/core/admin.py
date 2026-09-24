"""Django admin registration.

The React app is the real user interface; admin is here for support work -
looking up a row, fixing a typo in a borrower record, reading the audit log -
without opening SSMS. Postings are deliberately read-only here so that money
only ever moves through the API, where the waterfall and the audit trail apply.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    AuditLog,
    Borrower,
    BorrowerDocument,
    BorrowerGroup,
    Branch,
    Charge,
    Collateral,
    GroupMember,
    Guarantor,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Instalment,
    Loan,
    LoanNote,
    LoanProduct,
    LoanCharge,
    Notification,
    OrganisationSetting,
    ProductCharge,
    SavingsAccount,
    SavingsProduct,
    SavingsTransaction,
    Sequence,
    Transaction,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "full_name")
    ordering = ("id",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Profile", {"fields": ("full_name", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups",
                                    "user_permissions")}),
        ("Dates", {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("username", "full_name", "role", "password1", "password2")}),
    )
    readonly_fields = ("created_at",)


class GuarantorInline(admin.TabularInline):
    model = Guarantor
    extra = 0


@admin.register(Borrower)
class BorrowerAdmin(admin.ModelAdmin):
    list_display = ("borrower_no", "first_name", "last_name", "national_id", "phone",
                    "employer", "net_salary", "kyc_verified", "is_blacklisted")
    list_filter = ("kyc_verified", "is_blacklisted", "employer")
    search_fields = ("borrower_no", "first_name", "last_name", "national_id", "phone")
    inlines = [GuarantorInline]


@admin.register(LoanProduct)
class LoanProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "interest_rate_pct", "min_amount", "max_amount",
                    "min_term_months", "max_term_months", "is_active")
    list_filter = ("is_active",)


class InstalmentInline(admin.TabularInline):
    model = Instalment
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in Instalment._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("loan_no", "borrower", "product", "status", "principal",
                    "interest_rate_pct", "term_months", "principal_outstanding",
                    "disbursement_date", "maturity_date")
    list_filter = ("status", "product")
    search_fields = ("loan_no", "borrower__first_name", "borrower__last_name",
                     "borrower__national_id")
    autocomplete_fields = ("borrower",)
    inlines = [InstalmentInline]
    readonly_fields = ("principal_outstanding", "interest_outstanding", "penalties_outstanding",
                       "total_paid", "instalment_amount", "total_interest", "maturity_date")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "txn_date", "loan", "txn_type", "amount", "principal_component",
                    "interest_component", "penalty_component", "method", "reversed")
    list_filter = ("txn_type", "method", "reversed")
    search_fields = ("loan__loan_no", "reference")
    date_hierarchy = "txn_date"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "entity", "entity_id", "detail")
    list_filter = ("action", "entity")
    search_fields = ("detail",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "phone", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(OrganisationSetting)
class OrganisationSettingAdmin(admin.ModelAdmin):
    list_display = ("name", "currency", "updated_at")

    def has_add_permission(self, request):
        return not OrganisationSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoanNote)
class LoanNoteAdmin(admin.ModelAdmin):
    list_display = ("created_at", "loan", "author", "next_action_date", "promised_amount",
                    "resolved")
    list_filter = ("resolved",)
    search_fields = ("body", "loan__loan_no")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("scheduled_for", "kind", "channel", "to_address", "borrower", "status",
                    "sent_at")
    list_filter = ("status", "kind", "channel")
    search_fields = ("to_address", "body", "borrower__first_name", "borrower__last_name")
    date_hierarchy = "scheduled_for"


@admin.register(BorrowerDocument)
class BorrowerDocumentAdmin(admin.ModelAdmin):
    list_display = ("uploaded_at", "borrower", "doc_type", "original_name", "size_bytes",
                    "uploaded_by")
    list_filter = ("doc_type",)
    search_fields = ("original_name", "borrower__first_name", "borrower__last_name")


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type", "is_active")
    list_filter = ("type", "is_active")
    search_fields = ("code", "name")


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    can_delete = False
    readonly_fields = ("account", "debit", "credit", "description")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("entry_no", "entry_date", "source", "loan", "narration", "posted_by")
    list_filter = ("source", "branch")
    search_fields = ("entry_no", "narration", "loan__loan_no")
    date_hierarchy = "entry_date"
    inlines = [JournalLineInline]

    # The ledger is written by the posting rules, never by hand.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Collateral)
class CollateralAdmin(admin.ModelAdmin):
    list_display = ("loan", "type", "description", "estimated_value", "status", "valuation_date")
    list_filter = ("type", "status")
    search_fields = ("description", "reference", "loan__loan_no")


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "basis", "value", "timing", "is_active")
    list_filter = ("basis", "timing", "is_active")
    search_fields = ("code", "name")


@admin.register(LoanCharge)
class LoanChargeAdmin(admin.ModelAdmin):
    list_display = ("loan", "name", "amount", "applied_on")
    search_fields = ("name", "loan__loan_no")
    date_hierarchy = "applied_on"


admin.site.register(ProductCharge)


class GroupMemberInline(admin.TabularInline):
    model = GroupMember
    extra = 0
    autocomplete_fields = ("borrower",)


@admin.register(BorrowerGroup)
class BorrowerGroupAdmin(admin.ModelAdmin):
    list_display = ("group_no", "name", "branch", "officer", "status", "formed_on")
    list_filter = ("status", "branch")
    search_fields = ("group_no", "name")
    inlines = [GroupMemberInline]


@admin.register(SavingsProduct)
class SavingsProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "interest_rate_pct_pa", "min_balance", "monthly_fee",
                    "allow_withdrawals", "is_active")
    list_filter = ("is_active", "allow_withdrawals")


class SavingsTransactionInline(admin.TabularInline):
    model = SavingsTransaction
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in SavingsTransaction._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SavingsAccount)
class SavingsAccountAdmin(admin.ModelAdmin):
    list_display = ("account_no", "borrower", "product", "branch", "status", "balance",
                    "opened_on")
    list_filter = ("status", "product", "branch")
    search_fields = ("account_no", "borrower__first_name", "borrower__last_name")
    autocomplete_fields = ("borrower",)
    readonly_fields = ("balance",)
    inlines = [SavingsTransactionInline]


admin.site.register(Sequence)
admin.site.site_header = "Loan Management System"
admin.site.site_title = "LMS admin"
