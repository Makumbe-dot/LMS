"""Loan lifecycle endpoints: quote, apply, approve, reject, disburse, repay,
reverse, waive, write off, reschedule, accrue penalties, statement."""
from datetime import date

from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, renderer_classes
from rest_framework.renderers import StaticHTMLRenderer
from rest_framework.response import Response

from ..audit import audit
from ..documents import logo_data_uri
from ..exceptions import NotFound
from ..models import (
    Borrower,
    Charge,
    Collateral,
    Loan,
    LoanNote,
    LoanProduct,
    LoanStatus,
    OrganisationSetting,
    Transaction,
)
from ..permissions import (
    CanApprove,
    CanCash,
    CanCollections,
    CanDisburse,
    CanLoans,
    CanRestructure,
    CanReverse,
    CanSupervise,
)
from ..serializers import (
    RATE_METHOD_CHOICES,
    STATUS_CHOICES,
    CollateralSerializer,
    LoanApplySerializer,
    LoanDecisionSerializer,
    LoanDetailSerializer,
    LoanDisburseSerializer,
    LoanGuarantorsSerializer,
    LoanNoteSerializer,
    LoanQuoteRequestSerializer,
    LoanChargeSerializer,
    LoanQuoteSerializer,
    LoanSerializer,
    ManualChargeSerializer,
    NarrationSerializer,
    RecoverySerializer,
    RepaymentSerializer,
    RescheduleSerializer,
    SettlementQuoteSerializer,
    SettleSerializer,
    TopUpRequestSerializer,
    TransactionSerializer,
    WaiverSerializer,
)
from ..services import arrears as arrears_svc
from ..services import charges as chg
from ..services import loans as svc
from ..services import repayments as rep
from ..services import signatures, workdays
from ..services.notifications import queue_receipt
from ..services.penalties import accrue_penalties
from ..services.reports import loan_statement
from .helpers import (
    arrears_from_annotation,
    get_loan_or_404,
    paginate,
    parse_date,
    parse_int,
    with_arrears,
)


def loan_response(loan_id) -> Response:
    """Re-read the loan so the response always reflects what is now in the database."""
    return Response(LoanSerializer(with_arrears(get_loan_or_404(loan_id))).data)


def detail_response(loan_id) -> Response:
    loan = with_arrears(get_loan_or_404(loan_id, with_transactions=True))
    return Response(LoanDetailSerializer(loan).data)


def _validated(serializer_class, request):
    body = serializer_class(data=request.data)
    body.is_valid(raise_exception=True)
    return body.validated_data


# ---------------------------------------------------------------- read
@api_view(["GET", "POST"])
def loans(request):
    if request.method == "GET":
        # No instalment prefetch on the list: arrears is annotated below and the
        # list serializer renders no schedule. officer and branch ARE selected —
        # LoanSerializer renders both names, so without them the list ran two extra
        # queries per row.
        qs = Loan.objects.select_related("borrower", "product", "officer", "collector", "branch")
        status_filter = request.query_params.get("status")
        if status_filter:
            if status_filter not in STATUS_CHOICES:
                raise NotFound(f"Unknown status '{status_filter}'")
            qs = qs.filter(status=status_filter)
        search = request.query_params.get("q")
        if search:
            qs = qs.filter(
                Q(loan_no__icontains=search) | Q(external_ref__icontains=search)
                | Q(borrower__first_name__icontains=search)
                | Q(borrower__last_name__icontains=search)
                | Q(borrower__national_id__icontains=search))
        for param, field in (("branch_id", "branch_id"), ("officer_id", "officer_id"),
                             ("product_id", "product_id")):
            value = parse_int(request, param)
            if value:
                qs = qs.filter(**{field: value})
        rate_method = request.query_params.get("rate_method")
        if rate_method:
            if rate_method not in RATE_METHOD_CHOICES:
                raise NotFound(f"Unknown rate method '{rate_method}'")
            qs = qs.filter(rate_method=rate_method)
        if request.query_params.get("in_arrears") == "1":
            # The shared definition, not a hand-rolled copy. The copy that used to
            # live here omitted charge_due/charge_paid, which happens not to be
            # reachable today but is exactly how two definitions drift apart.
            qs = qs.filter(status=LoanStatus.ACTIVE).filter(arrears_svc.is_overdue())
        # Annotated rather than prefetched: the list serializer does not render the
        # schedule, so fetching every instalment on the page to compute one number
        # per loan was pure cost.
        return Response(paginate(request, arrears_svc.with_arrears(qs).order_by("-id"),
                                 LoanSerializer, transform=arrears_from_annotation))

    # POST - capture an application
    if not CanLoans().has_permission(request, None):
        return Response({"detail": CanLoans.message}, status=status.HTTP_403_FORBIDDEN)
    data = _validated(LoanApplySerializer, request)
    borrower = Borrower.objects.filter(pk=data["borrower_id"]).first()
    product = LoanProduct.objects.filter(pk=data["product_id"]).first()
    if not borrower or not product:
        raise NotFound("Borrower or product not found")
    with transaction.atomic():
        loan = svc.apply(borrower, product, data["principal"], data["term_months"],
                         data.get("purpose"), request.user, data.get("application_date"),
                         guarantor_ids=data.get("guarantor_ids"))
        audit(request.user, "apply", "loan", loan.id,
              f"{loan.loan_no} {loan.principal} x {loan.term_months}m for {borrower.full_name}")
    response = loan_response(loan.id)
    response.status_code = status.HTTP_201_CREATED
    return response


@api_view(["POST"])
def quote(request):
    data = _validated(LoanQuoteRequestSerializer, request)
    product = LoanProduct.objects.filter(pk=data["product_id"]).first()
    if not product:
        raise NotFound("Product not found")
    borrower = (Borrower.objects.filter(pk=data["borrower_id"]).first()
                if data.get("borrower_id") else None)
    result = svc.quote(product, data["principal"], data["term_months"],
                       data.get("disbursement_date"), borrower)
    return Response(LoanQuoteSerializer(result).data)


@api_view(["GET"])
def loan_detail(request, loan_id: int):
    return detail_response(loan_id)


@api_view(["GET"])
def statement(request, loan_id: int):
    """The loan's statement: JSON for the screen, ?fmt=xlsx or ?fmt=pdf to download.

    ?start= and ?end= narrow it to a period, with the balance brought forward.
    """
    from ..documents import loan_statement_pdf, loan_statement_xlsx

    loan = get_loan_or_404(loan_id, with_transactions=True)
    data = loan_statement(loan, parse_date(request, "start"), parse_date(request, "end"))
    fmt = request.query_params.get("fmt")
    if fmt == "xlsx":
        return loan_statement_xlsx(data, loan)
    if fmt == "pdf":
        return loan_statement_pdf(data)
    return Response(data)


# ---------------------------------------------------------------- decisions
@api_view(["POST"])
@permission_classes([CanApprove])
def approve(request, loan_id: int):
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        svc.approve(loan, request.user)
        audit(request.user, "approve", "loan", loan.id, loan.loan_no)
    return loan_response(loan_id)


@api_view(["POST"])
@permission_classes([CanApprove])
def reject(request, loan_id: int):
    data = _validated(LoanDecisionSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        svc.reject(loan, request.user, data.get("reason"))
        audit(request.user, "reject", "loan", loan.id, f"{loan.loan_no}: {data.get('reason')}")
    return loan_response(loan_id)


@api_view(["POST"])
@permission_classes([CanDisburse])
def disburse(request, loan_id: int):
    data = _validated(LoanDisburseSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        svc.disburse(loan, request.user, data.get("disbursement_date"),
                     data.get("first_instalment_date"), data["method"], data.get("reference"))
        audit(request.user, "disburse", "loan", loan.id,
              f"{loan.loan_no} {loan.principal} on {loan.disbursement_date}")
    return detail_response(loan_id)


# ---------------------------------------------------------------- money
@api_view(["POST"])
@permission_classes([CanCash])
def repayments(request, loan_id: int):
    data = _validated(RepaymentSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        txn = rep.post_repayment(loan, request.user, data["amount"], data.get("txn_date"),
                                 data["method"], data.get("reference"), data.get("narration"))
        audit(request.user, "repayment", "loan", loan.id,
              f"{loan.loan_no} {txn.amount} via {data['method']}")
        queue_receipt(loan, txn.amount, txn.id, txn.txn_date)
    return Response(TransactionSerializer(txn).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def settlement_quote(request, loan_id: int):
    """What it costs to close this loan today, with the unearned interest rebated."""
    loan = get_loan_or_404(loan_id)
    return Response(SettlementQuoteSerializer(
        svc.settlement_quote(loan, parse_date(request, "as_of"))).data)


@api_view(["POST"])
@permission_classes([CanCash])
def settle(request, loan_id: int):
    data = _validated(SettleSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        result = svc.settle_early(loan, request.user, data.get("amount"), data.get("txn_date"),
                                  data["method"], data.get("reference"), data.get("narration"))
        audit(request.user, "settle_early", "loan", loan.id,
              f"{loan.loan_no} settled for {result['quote']['settlement_amount']}, "
              f"rebate {result['quote']['interest_rebate']}")
        queue_receipt(loan, result["quote"]["settlement_amount"], result["transaction_id"],
                      data.get("txn_date") or date.today())
    return detail_response(loan_id)


@api_view(["POST"])
@permission_classes([CanCash])
def recovery(request, loan_id: int):
    """Money collected on a loan that was already written off."""
    data = _validated(RecoverySerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        txn = svc.record_recovery(loan, request.user, data["amount"], data.get("txn_date"),
                                  data["method"], data.get("reference"), data.get("narration"))
        audit(request.user, "recovery", "loan", loan.id, f"{loan.loan_no} {txn.amount}")
    return Response(TransactionSerializer(txn).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- top-up
@api_view(["POST"])
def top_up_quote(request, loan_id: int):
    """What a top-up of this loan would look like, before anything is captured."""
    data = _validated(TopUpRequestSerializer, request)
    product = LoanProduct.objects.filter(pk=data["product_id"]).first()
    if not product:
        raise NotFound("Product not found")
    old = get_loan_or_404(loan_id)
    return Response(svc.top_up_quote(old, product, data["principal"], data["term_months"],
                                     data.get("application_date")))


@api_view(["POST"])
@permission_classes([CanLoans])
def top_up(request, loan_id: int):
    """Capture a top-up application. The old loan is settled on disbursement."""
    data = _validated(TopUpRequestSerializer, request)
    product = LoanProduct.objects.filter(pk=data["product_id"]).first()
    if not product:
        raise NotFound("Product not found")
    with transaction.atomic():
        old = get_loan_or_404(loan_id)
        new = svc.apply_top_up(old, product, data["principal"], data["term_months"],
                               data.get("purpose"), request.user, data.get("application_date"))
        audit(request.user, "top_up", "loan", new.id,
              f"{new.loan_no} tops up and will settle {old.loan_no}")
    response = loan_response(new.id)
    response.status_code = status.HTTP_201_CREATED
    return response


# ---------------------------------------------------------------- charges
@api_view(["POST"])
@permission_classes([CanLoans])
def raise_charge(request, loan_id: int):
    """Raise a one-off charge against a loan, settled at the counter."""
    data = _validated(ManualChargeSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        catalogue_charge = None
        name = data.get("name")
        amount = data.get("amount")
        if data.get("charge_id"):
            catalogue_charge = Charge.objects.filter(pk=data["charge_id"]).first()
            if catalogue_charge is None:
                raise NotFound("Charge not found")
            name = name or catalogue_charge.name
            amount = amount or catalogue_charge.amount_for(loan.principal)
        if not name:
            raise NotFound("Give the charge a name, or pick one from the catalogue")
        if amount is None:
            raise NotFound("Give the charge an amount")

        item = chg.raise_manual(loan, request.user, catalogue_charge, name, amount,
                                data.get("applied_on"), data["collection"])
        audit(request.user, "raise_charge", "loan", loan.id,
              f"{item.name} {item.amount} ({item.collection})")
    return Response(LoanChargeSerializer(item).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- agreement
@api_view(["GET"])
@renderer_classes([StaticHTMLRenderer])
def agreement(request, loan_id: int):
    """The printable loan agreement. The client fetches it and opens it for printing."""
    return Response(agreement_html(get_loan_or_404(loan_id)))


def agreement_html(loan) -> str:
    """The agreement as HTML: for staff here, and for the borrower in the portal."""
    config = OrganisationSetting.load()
    rows = svc.sched(loan)
    apr = loan.apr_pct
    if rows:
        schedule = [{
            "number": r.number, "due_date": r.due_date, "opening_balance": r.opening_balance,
            "principal_due": r.principal_due, "interest_due": r.interest_due,
            "instalment": r.principal_due + r.interest_due,
            "closing_balance": r.closing_balance,
        } for r in rows]
    else:
        # Not yet disbursed: show the schedule the borrower is being offered.
        preview = svc.quote(loan.product, loan.principal, loan.term_months,
                            loan.application_date, loan.borrower)
        schedule = preview["schedule"]
        if apr is None:
            apr = preview["apr_pct"]

    # The day the monthly instalment falls on: the first instalment's day when there
    # is one, since an officer may have set it off the borrower's payday.
    first_due = loan.first_instalment_date or (schedule[0]["due_date"] if schedule else None)
    payday = first_due.day if first_due else (loan.borrower.payday or 1)
    suffix = ("th" if 11 <= payday % 100 <= 13
              else {1: "st", 2: "nd", 3: "rd"}.get(payday % 10, "th"))

    return render_to_string("core/loan_agreement.html", {
        "loan": loan,
        "borrower": loan.borrower,
        "org": config,
        "logo": logo_data_uri(),
        "currency": config.currency,
        "today": date.today(),
        "rate_method": "reducing balance" if loan.rate_method == "reducing" else "flat rate",
        "total_repayable": loan.principal + loan.total_interest,
        "net_disbursed": loan.principal - loan.upfront_fees,
        "total_cost_of_credit": loan.total_cost_of_credit,
        "apr": apr,
        "charges": loan.charges.all(),
        "guarantors": loan.guarantors.all(),
        "collateral": loan.collateral.filter(status="pledged"),
        "schedule": schedule,
        "payday_ordinal": f"{payday}{suffix}",
        "frequency": loan.repayment_frequency,
        "frequency_label": loan.get_repayment_frequency_display(),
        "term_unit": svc.TERM_UNITS.get(loan.repayment_frequency, "instalments"),
        "first_due": first_due,
        "closed_days": bool(workdays.load()),
        "signature": signatures.current(loan),
    })


@api_view(["GET"])
def signature(request, loan_id: int):
    """Whether the borrower has signed this loan's current terms."""
    return Response(signatures.state(get_loan_or_404(loan_id)))


@api_view(["POST"])
@permission_classes([CanLoans])
def signature_code(request, loan_id: int):
    """Text the borrower a code to sign with."""
    loan = get_loan_or_404(loan_id)
    sent = signatures.send_code(loan, request.user, "counter")
    return Response({**signatures.state(loan), "sent_to": sent.phone},
                    status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([CanLoans])
def signature_verify(request, loan_id: int):
    """The borrower, at the counter, enters the code they were sent."""
    loan = get_loan_or_404(loan_id)
    signatures.verify(loan, request.data.get("code"), channel="counter",
                      witnessed_by=request.user, ip=request.META.get("REMOTE_ADDR"),
                      user_agent=request.headers.get("User-Agent"))
    return Response(signatures.state(loan))


# ---------------------------------------------------------------- guarantors
@api_view(["PUT"])
@permission_classes([CanLoans])
def guarantors(request, loan_id: int):
    """Set which of the borrower's guarantors stand behind this loan."""
    data = _validated(LoanGuarantorsSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        chosen = svc.set_guarantors(loan, data["guarantor_ids"])
        audit(request.user, "set_guarantors", "loan", loan.id,
              f"{loan.loan_no}: {', '.join(g.full_name for g in chosen) or 'none'}")
    return detail_response(loan_id)


# ---------------------------------------------------------------- collateral
@api_view(["GET", "POST"])
def collateral(request, loan_id: int):
    loan = get_loan_or_404(loan_id)
    if request.method == "GET":
        return Response(CollateralSerializer(loan.collateral.all(), many=True).data)

    if not CanLoans().has_permission(request, None):
        return Response({"detail": CanLoans.message}, status=status.HTTP_403_FORBIDDEN)
    body = CollateralSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        item = Collateral.objects.create(loan=loan, recorded_by=request.user,
                                         **body.validated_data)
        audit(request.user, "add_collateral", "loan", loan.id,
              f"{item.type}: {item.description} at {item.estimated_value}")
    return Response(CollateralSerializer(item).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([CanLoans])
def collateral_detail(request, loan_id: int, collateral_id: int):
    item = Collateral.objects.filter(pk=collateral_id, loan_id=loan_id).first()
    if item is None:
        raise NotFound("Collateral not found")
    if request.method == "DELETE":
        with transaction.atomic():
            description = item.description
            item.delete()
            audit(request.user, "remove_collateral", "loan", loan_id, description)
        return Response(status=status.HTTP_204_NO_CONTENT)

    body = CollateralSerializer(item, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update_collateral", "loan", loan_id,
              str(list(body.validated_data.keys())))
    return Response(CollateralSerializer(item).data)


# ---------------------------------------------------------------- collections notes
@api_view(["GET", "POST"])
def notes(request, loan_id: int):
    loan = get_loan_or_404(loan_id)
    if request.method == "GET":
        return Response(LoanNoteSerializer(loan.notes.all(), many=True).data)

    if not CanCollections().has_permission(request, None):
        return Response({"detail": CanCollections.message}, status=status.HTTP_403_FORBIDDEN)
    body = LoanNoteSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        note = LoanNote.objects.create(loan=loan, author=request.user, **body.validated_data)
        audit(request.user, "note", "loan", loan.id, note.body[:120])
    return Response(LoanNoteSerializer(note).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([CanCollections])
def note_detail(request, loan_id: int, note_id: int):
    note = LoanNote.objects.filter(pk=note_id, loan_id=loan_id).first()
    if note is None:
        raise NotFound("Note not found")
    if request.method == "DELETE":
        with transaction.atomic():
            note.delete()
            audit(request.user, "delete_note", "loan", loan_id, str(note_id))
        return Response(status=status.HTTP_204_NO_CONTENT)

    body = LoanNoteSerializer(note, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update_note", "loan", loan_id, str(list(body.validated_data.keys())))
    return Response(LoanNoteSerializer(note).data)


@api_view(["POST"])
@permission_classes([CanReverse])
def reverse(request, loan_id: int, txn_id: int):
    data = _validated(NarrationSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        txn = Transaction.objects.filter(pk=txn_id, loan_id=loan.id).first()
        if txn is None:
            raise NotFound("Transaction not found")
        rev = rep.reverse_transaction(loan, txn, request.user, data["narration"])
        audit(request.user, "reverse", "loan", loan.id,
              f"{loan.loan_no} txn {txn_id}: {data['narration']}")
    return Response(TransactionSerializer(rev).data)


@api_view(["POST"])
@permission_classes([CanRestructure])
def waive_penalties(request, loan_id: int):
    data = _validated(WaiverSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        txn = rep.waive_penalties(loan, request.user, data["amount"], data["narration"])
        audit(request.user, "waive", "loan", loan.id,
              f"{loan.loan_no} {data['amount']}: {data['narration']}")
    return Response(TransactionSerializer(txn).data)


@api_view(["POST"])
@permission_classes([CanRestructure])
def write_off(request, loan_id: int):
    data = _validated(NarrationSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        svc.write_off(loan, request.user, data["narration"])
        audit(request.user, "write_off", "loan", loan.id,
              f"{loan.loan_no}: {data['narration']}")
    return loan_response(loan_id)


@api_view(["POST"])
@permission_classes([CanRestructure])
def reschedule(request, loan_id: int):
    data = _validated(RescheduleSerializer, request)
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        svc.reschedule(loan, request.user, data["new_term_months"],
                       data.get("new_interest_rate_pct"), data.get("first_instalment_date"),
                       data.get("narration") or "Loan rescheduled")
        audit(request.user, "reschedule", "loan", loan.id,
              f"{loan.loan_no} -> {data['new_term_months']}m")
    return detail_response(loan_id)


@api_view(["POST"])
@permission_classes([CanSupervise])
def accrue_one(request, loan_id: int):
    as_of = parse_date(request, "as_of")
    with transaction.atomic():
        loan = get_loan_or_404(loan_id)
        result = accrue_penalties(as_of, loan)
        audit(request.user, "accrue_penalties", "loan", loan.id, str(result))
    return Response(result)
