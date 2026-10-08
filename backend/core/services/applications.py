"""Loans asked for online, and what staff do with them.

The public Apply page and the portal each make an OnlineApplication; staff accept
or decline it. Accepting creates the borrower when they are new (or finds them by
national ID when they are not) and hands back what the ordinary New loan form
needs, so a loan is still created, scored and approved the usual way, by a person.
"""
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from ..exceptions import BusinessRuleError
from ..models import (
    ApplicationStatus,
    Borrower,
    IncomeSource,
    LoanProduct,
    OnlineApplication,
    OrganisationSetting,
)
from .loans import next_number

# A new client may not flood the queue: this many waiting per national ID or phone.
MAX_OPEN_PER_PERSON = 2


def products() -> list[dict]:
    """What the public form offers: active products and their limits, no internals."""
    currency = OrganisationSetting.load().currency
    return [{"id": p.id, "name": p.name, "currency": p.currency or currency,
             "min_amount": p.min_amount, "max_amount": p.max_amount,
             "min_term": p.min_term_months, "max_term": p.max_term_months,
             "frequency": p.repayment_frequency}
            for p in LoanProduct.objects.filter(is_active=True).order_by("name")]


def _money(value, label) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BusinessRuleError(f"{label}: a number")
    if amount <= 0:
        raise BusinessRuleError(f"{label}: more than nothing")
    return amount


def _loan_terms(data: dict) -> dict:
    """The product, amount and term asked for, checked against the product."""
    product = LoanProduct.objects.filter(pk=data.get("product_id"), is_active=True).first()
    if product is None:
        raise BusinessRuleError("Choose a loan product")
    amount = _money(data.get("amount"), "Amount")
    if not product.min_amount <= amount <= product.max_amount:
        raise BusinessRuleError(f"{product.name}: from {product.min_amount:,.2f} to "
                                f"{product.max_amount:,.2f}")
    try:
        term = int(data.get("term_months"))
    except (TypeError, ValueError):
        raise BusinessRuleError("Term: a whole number")
    if not product.min_term_months <= term <= product.max_term_months:
        raise BusinessRuleError(f"{product.name}: a term of {product.min_term_months} to "
                                f"{product.max_term_months}")
    return {"product": product, "amount": amount, "term_months": term,
            "purpose": (data.get("purpose") or "").strip()[:200]}


def submit_public(data: dict, client_address: str = "") -> OnlineApplication:
    """A new client's application from the public Apply page."""
    if data.get("website"):
        # The honeypot: a field people never see, which form-filling robots do.
        raise BusinessRuleError("Your application could not be accepted")
    if not data.get("consent"):
        raise BusinessRuleError("Please agree to be contacted and checked before applying")
    fields = {}
    for key, label in (("first_name", "First name"), ("last_name", "Surname"),
                       ("national_id", "National ID"), ("phone", "Mobile number")):
        value = (data.get(key) or "").strip()
        if not value:
            raise BusinessRuleError(f"{label} is required")
        fields[key] = value[:80]
    if len(re.sub(r"\D", "", fields["phone"])) < 9:
        raise BusinessRuleError("Mobile number: give the whole number, e.g. 0771 234 567")
    email = (data.get("email") or "").strip()
    if email and "@" not in email:
        raise BusinessRuleError("Email: that is not an email address")
    salary = data.get("net_salary")
    payday = data.get("payday")
    source = data.get("income_source") or IncomeSource.EMPLOYED
    if source not in IncomeSource.values:
        raise BusinessRuleError("Say how you earn your income")
    business = (data.get("business_name") or "").strip()
    if source != IncomeSource.EMPLOYED and not business:
        raise BusinessRuleError("Business name: the business or trade you run")
    terms = _loan_terms(data)

    waiting = OnlineApplication.objects.filter(status=ApplicationStatus.NEW)
    if (waiting.filter(national_id__iexact=fields["national_id"]).count() >= MAX_OPEN_PER_PERSON
            or waiting.filter(phone=fields["phone"]).count() >= MAX_OPEN_PER_PERSON):
        raise BusinessRuleError("We already have your application and will be in touch")

    return OnlineApplication.objects.create(
        **fields, email=email[:120], address=(data.get("address") or "").strip()[:500],
        employer=(data.get("employer") or "").strip()[:120] if source == IncomeSource.EMPLOYED else "",
        income_source=source, business_name=business[:120],
        net_salary=_money(salary, "Net monthly income") if salary not in (None, "") else None,
        payday=int(payday) if str(payday or "").isdigit() and 1 <= int(payday) <= 31 else None,
        consent=True, source="public", client_address=(client_address or "")[:64],
        borrower=Borrower.objects.filter(national_id__iexact=fields["national_id"]).first(),
        **terms)


def submit_portal(borrower: Borrower, data: dict) -> OnlineApplication:
    """An existing borrower's application from the portal: we know who they are."""
    terms = _loan_terms(data)
    if OnlineApplication.objects.filter(borrower=borrower,
                                        status=ApplicationStatus.NEW).count() >= MAX_OPEN_PER_PERSON:
        raise BusinessRuleError("You already have an application waiting; we will be in touch")
    return OnlineApplication.objects.create(
        borrower=borrower, first_name=borrower.first_name, last_name=borrower.last_name,
        national_id=borrower.national_id, phone=borrower.phone, email=borrower.email or "",
        address=borrower.address or "", employer=borrower.employer or "",
        income_source=borrower.income_source, business_name=borrower.business_name,
        net_salary=borrower.net_salary, payday=borrower.payday, consent=True,
        source="portal", **terms)


@transaction.atomic
def accept(application: OnlineApplication, user, branch_id=None) -> dict:
    """Make the applicant a borrower (unless they are one) and return what the New
    loan form needs. The loan itself is created there, by the person accepting."""
    if application.status != ApplicationStatus.NEW:
        raise BusinessRuleError("This application has already been dealt with")
    borrower = application.borrower or Borrower.objects.filter(
        national_id__iexact=application.national_id).first()
    created = False
    if borrower is None:
        borrower = Borrower.objects.create(
            borrower_no=next_number("BRW"), first_name=application.first_name,
            last_name=application.last_name, national_id=application.national_id,
            phone=application.phone, email=application.email or None,
            address=application.address or None, employer=application.employer or None,
            income_source=application.income_source, business_name=application.business_name,
            net_salary=application.net_salary or 0, payday=application.payday or 25,
            branch_id=branch_id or getattr(user, "branch_id", None),
            notes="Applied online; KYC documents still to be collected.")
        created = True
        from .screening import screen_if_lists

        screen_if_lists(borrower)
    application.borrower = borrower
    application.status = ApplicationStatus.ACCEPTED
    application.outcome = ("New borrower created" if created else "Matched an existing borrower")
    application.handled_by = user
    application.handled_at = timezone.now()
    application.save()
    return {"borrower_id": borrower.id, "borrower_no": borrower.borrower_no,
            "created_borrower": created, "product_id": application.product_id,
            "principal": str(application.amount), "term_months": application.term_months,
            "purpose": application.purpose}


def decline(application: OnlineApplication, user, reason: str) -> OnlineApplication:
    if application.status != ApplicationStatus.NEW:
        raise BusinessRuleError("This application has already been dealt with")
    reason = (reason or "").strip()
    if not reason:
        raise BusinessRuleError("Give a reason for declining")
    application.status = ApplicationStatus.DECLINED
    application.outcome = reason[:255]
    application.handled_by = user
    application.handled_at = timezone.now()
    application.save()
    return application
