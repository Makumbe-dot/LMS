"""The charges catalogue and what each product carries."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import Charge, LoanProduct, ProductCharge
from ..permissions import IsAdmin
from ..serializers import ChargeSerializer


@api_view(["GET", "POST"])
def charges(request):
    if request.method == "GET":
        qs = Charge.objects.order_by("code")
        if request.query_params.get("include_inactive", "").lower() not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return Response(ChargeSerializer(qs, many=True).data)

    if not IsAdmin().has_permission(request, None):
        return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)
    if Charge.objects.filter(code=request.data.get("code")).exists():
        raise BusinessRuleError("A charge with this code already exists")
    body = ChargeSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        charge = body.save()
        audit(request.user, "create", "charge", charge.id, charge.code)
    return Response(ChargeSerializer(charge).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAdmin])
def charge_detail(request, charge_id: int):
    charge = Charge.objects.filter(pk=charge_id).first()
    if charge is None:
        raise NotFound("Charge not found")

    if request.method == "DELETE":
        if charge.loan_charges.exists():
            raise BusinessRuleError(
                "That charge has already been raised against a loan; deactivate it instead of "
                "deleting it, so history keeps its meaning.")
        with transaction.atomic():
            code = charge.code
            charge.delete()
            audit(request.user, "delete", "charge", charge_id, code)
        return Response(status=status.HTTP_204_NO_CONTENT)

    body = ChargeSerializer(charge, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "charge", charge.id, str(list(body.validated_data.keys())))
    return Response(ChargeSerializer(charge).data)


@api_view(["GET", "PUT"])
def product_charges(request, product_id: int):
    """Which catalogue charges a product carries. PUT replaces the whole set."""
    product = LoanProduct.objects.filter(pk=product_id).first()
    if product is None:
        raise NotFound("Product not found")

    if request.method == "GET":
        attached = Charge.objects.filter(product_charges__product=product).order_by("code")
        return Response(ChargeSerializer(attached, many=True).data)

    if not IsAdmin().has_permission(request, None):
        return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)

    ids = request.data.get("charge_ids")
    if not isinstance(ids, list):
        raise BusinessRuleError("Send charge_ids as a list, for example {\"charge_ids\": [1, 2]}")
    found = list(Charge.objects.filter(id__in=ids))
    missing = set(ids) - {c.id for c in found}
    if missing:
        raise NotFound(f"No charge with id {', '.join(str(m) for m in sorted(missing))}")

    with transaction.atomic():
        ProductCharge.objects.filter(product=product).delete()
        ProductCharge.objects.bulk_create(
            [ProductCharge(product=product, charge=charge) for charge in found])
        audit(request.user, "set_product_charges", "product", product.id,
              ", ".join(c.code for c in found) or "none")
    return Response(ChargeSerializer(found, many=True).data)
