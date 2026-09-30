"""Funder borrowings and shareholders' capital: where the money to lend came from."""
from django.db import transaction
from django.db.models import Prefetch, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import (
    CapitalTransaction,
    CapitalTxnType,
    FacilityTransaction,
    FundingFacility,
)
from ..permissions import IsAdmin
from ..serializers import (
    BorrowingAccrualSerializer,
    CapitalMovementSerializer,
    CapitalTransactionSerializer,
    FacilityMovementSerializer,
    FacilityTransactionSerializer,
    FacilityUpdateSerializer,
    FundingSummarySerializer,
    FundingFacilityDetailSerializer,
    FundingFacilitySerializer,
    NarrationSerializer,
    OpenFacilitySerializer,
)
from ..services import funding as svc
from .helpers import csv_response, paginate, parse_date, parse_int


def _require_admin(request) -> None:
    """For routes that serve GET to everyone and POST to admins only."""
    if not IsAdmin().has_permission(request, None):
        raise BusinessRuleError(IsAdmin.message)


def _facility_or_404(facility_id: int, *, for_update: bool = False) -> FundingFacility:
    """Fetch a facility, optionally locking the row.

    A movement reads principal_outstanding, adds a delta and writes it back, so two
    concurrent drawdowns would otherwise lose one. for_update maps to an UPDLOCK on
    SQL Server — the same mechanism the sequence counters rely on — and requires an
    open transaction, which every caller that passes it has.
    """
    qs = FundingFacility.objects.filter(pk=facility_id).select_related("branch")
    if for_update:
        qs = qs.select_for_update()
    facility = qs.first()
    if facility is None:
        raise NotFound("Funding facility not found")
    return facility


# ---------------------------------------------------------------- facilities
@api_view(["GET", "POST"])
def facilities(request):
    if request.method == "POST":
        _require_admin(request)
        body = OpenFacilitySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        with transaction.atomic():
            facility = svc.open_facility(
                request.user, funder_name=data["funder_name"], name=data["name"],
                facility_limit=data["facility_limit"],
                interest_rate_pct_pa=data["interest_rate_pct_pa"],
                start_date=data.get("start_date"), maturity_date=data.get("maturity_date"),
                is_revolving=data["is_revolving"],
                repayment_terms=data.get("repayment_terms"), branch_id=data.get("branch"),
                notes=data.get("notes"))
            audit(request.user, "open_facility", "facility", facility.id,
                  f"{facility.facility_no} with {facility.funder_name}, limit "
                  f"{facility.facility_limit} at {facility.interest_rate_pct_pa}% a year")
        return Response(FundingFacilitySerializer(facility).data,
                        status=status.HTTP_201_CREATED)

    qs = FundingFacility.objects.select_related("branch", "created_by")
    if request.query_params.get("open") == "1":
        qs = qs.filter(closed_on__isnull=True)
    funder = request.query_params.get("funder")
    if funder:
        qs = qs.filter(funder_name__icontains=funder)
    branch_id = parse_int(request, "branch_id")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    search = (request.query_params.get("q") or "").strip()
    if search:
        qs = qs.filter(Q(facility_no__icontains=search) | Q(name__icontains=search)
                       | Q(funder_name__icontains=search))

    if request.query_params.get("fmt") == "csv":
        rows = [{
            "facility_no": f.facility_no, "funder_name": f.funder_name, "name": f.name,
            "facility_limit": f.facility_limit, "principal_outstanding": f.principal_outstanding,
            "interest_accrued": f.interest_accrued, "available": f.available,
            "interest_rate_pct_pa": f.interest_rate_pct_pa, "is_revolving": f.is_revolving,
            "start_date": f.start_date, "maturity_date": f.maturity_date,
            "closed_on": f.closed_on or "", "branch": f.branch.name if f.branch_id else "",
        } for f in qs[:5000]]
        return csv_response(rows, "funding_facilities")

    return Response(paginate(request, qs, FundingFacilitySerializer))


@api_view(["GET", "PATCH"])
def facility_detail(request, facility_id: int):
    if request.method == "PATCH":
        _require_admin(request)
        facility = _facility_or_404(facility_id, for_update=True)
        body = FacilityUpdateSerializer(facility, data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        limit = data.get("facility_limit", facility.facility_limit)
        if limit < facility.principal_outstanding:
            raise BusinessRuleError(
                f"A limit of {limit} is below the {facility.principal_outstanding} already drawn "
                f"and outstanding")
        maturity = data.get("maturity_date", facility.maturity_date)
        if maturity and maturity <= facility.start_date:
            raise BusinessRuleError("The maturity date must be after the start date")
        with transaction.atomic():
            facility = body.save()
            audit(request.user, "update_facility", "facility", facility.id,
                  str(sorted(data.keys())))
        return Response(FundingFacilitySerializer(facility).data)

    facility = (FundingFacility.objects
                .select_related("branch", "created_by")
                .prefetch_related(Prefetch(
                    "transactions",
                    queryset=FacilityTransaction.objects.select_related(
                        "journal_entry", "posted_by").order_by("id")))
                .filter(pk=facility_id).first())
    if facility is None:
        raise NotFound("Funding facility not found")
    return Response(FundingFacilityDetailSerializer(facility).data)


def _movement(request, facility_id: int, action, audit_action: str, message: str):
    body = FacilityMovementSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    data = body.validated_data
    with transaction.atomic():
        facility = _facility_or_404(facility_id, for_update=True)
        ftxn = action(facility, request.user, data["amount"], data.get("txn_date"),
                      data.get("method"), data.get("reference"), data.get("narration"))
        audit(request.user, audit_action, "facility", facility.id,
              f"{facility.facility_no}: {message} {ftxn.amount}")
    return Response(FacilityTransactionSerializer(ftxn).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAdmin])
def drawdown(request, facility_id: int):
    return _movement(request, facility_id, svc.drawdown, "facility_drawdown", "drew")


@api_view(["POST"])
@permission_classes([IsAdmin])
def repay(request, facility_id: int):
    return _movement(request, facility_id, svc.repay, "facility_repayment", "repaid")


@api_view(["POST"])
@permission_classes([IsAdmin])
def pay_interest(request, facility_id: int):
    return _movement(request, facility_id, svc.pay_interest, "facility_interest", "paid interest")


@api_view(["POST"])
@permission_classes([IsAdmin])
def charge_fee(request, facility_id: int):
    return _movement(request, facility_id, svc.charge_fee, "facility_fee", "paid a fee of")


@api_view(["POST"])
@permission_classes([IsAdmin])
def close(request, facility_id: int):
    body = NarrationSerializer(data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        facility = _facility_or_404(facility_id, for_update=True)
        svc.close_facility(facility, request.user, body.validated_data.get("narration"))
        audit(request.user, "close_facility", "facility", facility.id, facility.facility_no)
    return Response(FundingFacilitySerializer(facility).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def reverse_movement(request, facility_id: int, txn_id: int):
    body = NarrationSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        facility = _facility_or_404(facility_id, for_update=True)
        ftxn = FacilityTransaction.objects.filter(pk=txn_id,
                                                  facility_id=facility.id).first()
        if ftxn is None:
            raise NotFound("Facility movement not found")
        reversal = svc.reverse_facility_transaction(facility, ftxn, request.user,
                                                    body.validated_data["narration"])
        audit(request.user, "facility_reversal", "facility", facility.id,
              f"{facility.facility_no}: reversed {ftxn.txn_type} of {ftxn.amount} — "
              f"{body.validated_data['narration']}")
    return Response(FacilityTransactionSerializer(reversal).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def accrue(request):
    facility = None
    facility_id = parse_int(request, "facility_id")
    if facility_id:
        facility = _facility_or_404(facility_id)
    with transaction.atomic():
        result = svc.accrue_interest(parse_date(request, "as_of"), facility)
        audit(request.user, "borrowing_interest_run", "system", None, str(result))
    return Response(BorrowingAccrualSerializer(result).data)


# ---------------------------------------------------------------- capital
@api_view(["GET", "POST"])
def capital(request):
    if request.method == "POST":
        _require_admin(request)
        body = CapitalMovementSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        writer = {
            CapitalTxnType.INJECTION: svc.inject_capital,
            CapitalTxnType.RETURN_OF_CAPITAL: svc.return_capital,
            CapitalTxnType.DIVIDEND: svc.pay_dividend,
        }[data["txn_type"]]
        with transaction.atomic():
            ctxn = writer(request.user, data["amount"], data["contributor"],
                          data.get("txn_date"), data.get("method"), data.get("reference"),
                          data.get("narration"), data.get("branch"))
            audit(request.user, f"capital_{data['txn_type']}", "capital", ctxn.id,
                  f"{ctxn.amount} — {ctxn.contributor}")
        return Response(CapitalTransactionSerializer(ctxn).data, status=status.HTTP_201_CREATED)

    qs = CapitalTransaction.objects.select_related("branch", "posted_by", "journal_entry")
    kind = request.query_params.get("txn_type")
    if kind:
        if kind not in CapitalTxnType.values:
            raise BusinessRuleError(f"Unknown movement type '{kind}'")
        qs = qs.filter(txn_type=kind)
    start = parse_date(request, "start")
    end = parse_date(request, "end")
    if start:
        qs = qs.filter(txn_date__gte=start)
    if end:
        qs = qs.filter(txn_date__lte=end)

    if request.query_params.get("fmt") == "csv":
        rows = [{
            "txn_date": c.txn_date, "txn_type": c.txn_type, "amount": c.amount,
            "contributor": c.contributor, "method": c.method or "",
            "reference": c.reference or "", "reversed": c.reversed,
            "entry_no": c.journal_entry.entry_no if hasattr(c, "journal_entry") else "",
        } for c in qs[:5000]]
        return csv_response(rows, "capital_movements")

    payload = paginate(request, qs, CapitalTransactionSerializer, default_size=25)
    payload["summary"] = svc.capital_summary()
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAdmin])
def reverse_capital(request, txn_id: int):
    body = NarrationSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        ctxn = CapitalTransaction.objects.filter(pk=txn_id).first()
        if ctxn is None:
            raise NotFound("Capital movement not found")
        reversal = svc.reverse_capital_transaction(ctxn, request.user,
                                                   body.validated_data["narration"])
        audit(request.user, "capital_reversal", "capital", ctxn.id,
              f"reversed {ctxn.txn_type} of {ctxn.amount} — "
              f"{body.validated_data['narration']}")
    return Response(CapitalTransactionSerializer(reversal).data)


@api_view(["GET"])
def summary(request):
    # Through a serializer, not straight to Response: a bare dict of Decimals is
    # rendered as JSON floats, and the whole API renders money as a decimal string
    # so cents survive the round trip.
    return Response(FundingSummarySerializer(svc.funding_summary(
        parse_date(request, "as_of"))).data)
