"""Closing a month to further postings, and reading the register."""
from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..exceptions import BusinessRuleError
from ..permissions import IsAdmin, IsOfficer
from ..serializers import (
    AccountingPeriodSerializer,
    PeriodCloseSerializer,
    PeriodMonthSerializer,
    PeriodReopenSerializer,
)
from ..services import periods as svc
from .helpers import parse_date, parse_int


def _month(year: str, month: str) -> tuple[int, int]:
    """re_path hands the view strings, and \\d{1,2} matches 0 and 13."""
    year_int, month_int = int(year), int(month)
    if not 1 <= month_int <= 12:
        raise BusinessRuleError(f"{month} is not a month")
    if not 2000 <= year_int <= 2999:
        raise BusinessRuleError(f"{year} is not a year this system handles")
    return year_int, month_int


def _register(year: int | None = None) -> dict:
    data = svc.register(year)
    return {
        "year": data["year"],
        "months": PeriodMonthSerializer(data["months"], many=True).data,
        "open_months": data["open_months"],
        "closed_through": data["closed_through"],
        "earliest_postable_date": data["earliest_postable_date"],
        "last_closed": (AccountingPeriodSerializer(data["last_closed"]).data
                        if data["last_closed"] else None),
        "next_to_close": data["next_to_close"],
        "reopened_periods": data["reopened_periods"],
    }


@api_view(["GET"])
def register(request):
    """The twelve months of one year and what has been signed off."""
    return Response(_register(parse_int(request, "year")))


@api_view(["GET"])
def status(request):
    """Whether one date can still be posted to. Read by every date field."""
    return Response(svc.status_for(parse_date(request, "on")))


@api_view(["GET"])
def period_detail(request, year, month):
    period = svc.get_period(*_month(year, month))
    return Response(AccountingPeriodSerializer(period, context={"with_snapshot": True}).data)


@api_view(["GET"])
@permission_classes([IsOfficer])
def preflight(request, year, month):
    """What a close would check and what it would freeze. Changes nothing."""
    return Response(svc.preflight(*_month(year, month)))


@api_view(["POST"])
@permission_classes([IsAdmin])
def close(request, year, month):
    body = PeriodCloseSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        period = svc.close_period(*_month(year, month), user=request.user,
                                  note=body.validated_data.get("note"),
                                  force=body.validated_data["force"])
    payload = _register(period.year)
    payload["period"] = AccountingPeriodSerializer(period).data
    return Response(payload)


@api_view(["POST"])
@permission_classes([IsAdmin])
def reopen(request, year, month):
    body = PeriodReopenSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        period = svc.reopen_period(*_month(year, month), user=request.user,
                                   reason=body.validated_data["reason"])
    payload = _register(period.year)
    payload["period"] = AccountingPeriodSerializer(period).data
    return Response(payload)
