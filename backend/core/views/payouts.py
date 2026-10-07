"""Payouts: what disbursed loans still have to pay the borrower, and how it went."""
from django.db import transaction
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import NotFound
from ..models import Payout
from ..permissions import CanDisburse
from ..services import payouts as svc


class PayoutSerializer(serializers.ModelSerializer):
    loan_no = serializers.CharField(source="loan.loan_no", read_only=True)
    loan_id = serializers.IntegerField(source="loan.id", read_only=True)
    borrower_id = serializers.IntegerField(source="loan.borrower_id", read_only=True)
    updated_by_name = serializers.CharField(source="updated_by.full_name", read_only=True,
                                            default=None)

    class Meta:
        model = Payout
        fields = ["id", "loan_id", "loan_no", "borrower_id", "amount", "currency", "method",
                  "payee_name", "account", "bank_name", "bank_branch", "status", "batch",
                  "provider_reference", "error", "created_at", "sent_at", "paid_at",
                  "updated_by_name"]


def _get(payout_id) -> Payout:
    found = Payout.objects.select_related("loan__borrower").filter(pk=payout_id).first()
    if found is None:
        raise NotFound("No such payout")
    return found


@api_view(["GET"])
def payouts(request):
    """?status=pending|sent|paid|failed (pending by default; blank for all)."""
    wanted = request.query_params.get("status", "pending")
    qs = Payout.objects.select_related("loan", "updated_by")
    if wanted:
        qs = qs.filter(status=wanted)
    return Response({"live": svc.mobile_money_live(),
                     "results": PayoutSerializer(qs[:500], many=True).data})


@api_view(["POST"])
@permission_classes([CanDisburse])
def send_mobile(request):
    """Send pending wallet payouts (all, or {ids}) to the mobile-money provider."""
    result = svc.send_mobile_money(request.data.get("ids"), request.user)
    audit(request.user, "payout_mobile", "payouts", None, str(result))
    return Response(result)


@api_view(["POST"])
@permission_classes([CanDisburse])
def bank_file(request):
    """A CSV of pending bank payouts (all, or {ids}) for the bank's bulk upload."""
    name, content, count = svc.bank_file(request.data.get("ids"), request.user)
    audit(request.user, "payout_bank_file", "payouts", None, f"{name}: {count} payment(s)")
    response = HttpResponse(content, content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@api_view(["POST"])
@permission_classes([CanDisburse])
def outcome(request, payout_id: int, action: str):
    """paid {reference} | failed {reason} | retry."""
    payout = _get(payout_id)
    with transaction.atomic():
        if action == "paid":
            svc.mark_paid(payout, request.user, request.data.get("reference") or "")
        elif action == "failed":
            svc.mark_failed(payout, request.user, request.data.get("reason"))
        else:
            svc.retry(payout, request.user)
        audit(request.user, f"payout_{action}", "loan", payout.loan_id,
              f"{payout.amount} to {payout.account}")
    return Response(PayoutSerializer(payout).data)
