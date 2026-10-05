"""Credit-life claims: lodged by an officer, decided by an admin once the insurer
has answered."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import ClaimStatus, InsuranceClaim
from ..permissions import IsAdmin, IsOfficer
from ..serializers import (
    ClaimLodgeSerializer,
    ClaimPaySerializer,
    ClaimRejectSerializer,
    InsuranceClaimSerializer,
)
from ..services import claims as svc
from ..services.imports import _loan_for
from .helpers import paginate


def _claims():
    return InsuranceClaim.objects.select_related("loan__borrower", "lodged_by", "decided_by")


@api_view(["GET", "POST"])
def claims(request):
    if request.method == "POST":
        if not IsOfficer().has_permission(request, None):
            return Response({"detail": IsOfficer.message}, status=status.HTTP_403_FORBIDDEN)
        body = ClaimLodgeSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        with transaction.atomic():
            loan = _loan_for(data["loan_no"].strip())
            if loan is None:
                raise NotFound(f"No loan numbered {data['loan_no']}")
            claim = svc.lodge(loan, request.user, data["cause"], data["event_date"],
                              data.get("insurer_reference"), data.get("notes"))
            audit(request.user, "lodge_claim", "loan", loan.id,
                  f"{claim.claim_no} ({claim.cause}) for {claim.amount_claimed}")
        return Response(InsuranceClaimSerializer(_claims().get(pk=claim.pk)).data,
                        status=status.HTTP_201_CREATED)

    qs = _claims()
    state = request.query_params.get("status")
    if state:
        if state not in ClaimStatus.values:
            raise BusinessRuleError(f"Unknown status '{state}'")
        qs = qs.filter(status=state)
    loan_id = request.query_params.get("loan_id")
    if loan_id and loan_id.isdigit():
        qs = qs.filter(loan_id=int(loan_id))
    payload = paginate(request, qs, InsuranceClaimSerializer, default_size=25)
    payload["open"] = InsuranceClaim.objects.filter(status=ClaimStatus.LODGED).count()
    return Response(payload)


def _claim_for_update(claim_id: int) -> InsuranceClaim:
    claim = _claims().select_for_update().filter(pk=claim_id).first()
    if claim is None:
        raise NotFound("Claim not found")
    return claim


@api_view(["POST"])
@permission_classes([IsAdmin])
def pay(request, claim_id: int):
    body = ClaimPaySerializer(data=request.data)
    body.is_valid(raise_exception=True)
    data = body.validated_data
    with transaction.atomic():
        claim = _claim_for_update(claim_id)
        svc.pay(claim, request.user, data["amount"], data.get("paid_on"),
                data.get("reference"), data["write_off_remainder"], data.get("note"))
        audit(request.user, "pay_claim", "loan", claim.loan_id,
              f"{claim.claim_no} paid {claim.amount_paid}"
              + (f", {claim.remainder_written_off} written off"
                 if claim.remainder_written_off else ""))
    return Response(InsuranceClaimSerializer(_claims().get(pk=claim.pk)).data)


@api_view(["POST"])
@permission_classes([IsAdmin])
def reject(request, claim_id: int):
    body = ClaimRejectSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        claim = _claim_for_update(claim_id)
        svc.reject(claim, request.user, body.validated_data["note"])
        audit(request.user, "reject_claim", "loan", claim.loan_id,
              f"{claim.claim_no}: {claim.decision_note}")
    return Response(InsuranceClaimSerializer(_claims().get(pk=claim.pk)).data)
