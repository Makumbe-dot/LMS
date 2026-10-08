"""Currencies: the exchange-rate table and the revaluation of foreign-currency balances."""
from datetime import date

from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import serializers as drf

from ..audit import audit
from ..exceptions import NotFound
from ..models import ExchangeRate, RevaluationRun
from ..permissions import CanAccounting, CanSetup
from ..serializers import (
    ExchangeRateSerializer,
    RevaluationPreviewSerializer,
    RevaluationRunSerializer,
)
from ..services import fx
from .helpers import paginate, parse_date


class _RateBody(drf.Serializer):
    code = drf.CharField(max_length=8)
    rate_date = drf.DateField()
    rate = drf.DecimalField(max_digits=18, decimal_places=6, min_value=0)
    note = drf.CharField(required=False, allow_blank=True, allow_null=True, max_length=120)


class _RevalueBody(drf.Serializer):
    as_of = drf.DateField(required=False, allow_null=True)
    narration = drf.CharField(required=False, allow_blank=True, allow_null=True)


@api_view(["GET"])
def currencies(request):
    """The base currency, every other currency in use, and each one's latest rate."""
    return Response(fx.describe())


@api_view(["GET", "POST"])
def rates(request):
    if request.method == "GET":
        qs = ExchangeRate.objects.select_related("set_by")
        code = request.query_params.get("code")
        if code:
            qs = qs.filter(code=fx.normalise(code))
        return Response(paginate(request, qs, ExchangeRateSerializer, default_size=50))

    if not CanSetup().has_permission(request, None):
        return Response({"detail": CanSetup.message}, status=status.HTTP_403_FORBIDDEN)
    body = _RateBody(data=request.data)
    body.is_valid(raise_exception=True)
    data = body.validated_data
    with transaction.atomic():
        row = fx.set_rate(data["code"], data["rate_date"], data["rate"], request.user,
                          data.get("note"))
        audit(request.user, "set_exchange_rate", "exchange_rate", row.id,
              f"{row.code} {row.rate} on {row.rate_date}")
    return Response(ExchangeRateSerializer(row).data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([CanSetup])
def rate_detail(request, rate_id: int):
    row = ExchangeRate.objects.filter(pk=rate_id).first()
    if row is None:
        raise NotFound("Rate not found")
    with transaction.atomic():
        audit(request.user, "delete_exchange_rate", "exchange_rate", row.id,
              f"{row.code} {row.rate} on {row.rate_date}")
        row.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
def revaluation_preview(request):
    """What a run as at ?as_of= would move. Posts nothing."""
    plan = fx.preview(parse_date(request, "as_of") or date.today())
    return Response(RevaluationPreviewSerializer(plan).data)


@api_view(["GET", "POST"])
def revaluations(request):
    if request.method == "GET":
        qs = RevaluationRun.objects.select_related("journal_entry", "run_by").prefetch_related(
            "lines__loan__borrower", "lines__savings_account__borrower", "lines__facility")
        return Response(paginate(request, qs, RevaluationRunSerializer, default_size=25))

    if not CanAccounting().has_permission(request, None):
        return Response({"detail": CanAccounting.message}, status=status.HTTP_403_FORBIDDEN)
    body = _RevalueBody(data=request.data)
    body.is_valid(raise_exception=True)
    run = fx.revalue(body.validated_data.get("as_of"), request.user,
                     body.validated_data.get("narration"))
    run = (RevaluationRun.objects.select_related("journal_entry", "run_by")
           .prefetch_related("lines__loan__borrower", "lines__savings_account__borrower",
                             "lines__facility").get(pk=run.pk))
    return Response(RevaluationRunSerializer(run).data, status=status.HTTP_201_CREATED)
