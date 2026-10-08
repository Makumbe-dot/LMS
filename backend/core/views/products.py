"""Loan products: pricing, limits, fees, penalty terms and the affordability cap."""
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import LoanProduct
from ..permissions import CanSetup
from ..serializers import ProductSerializer, ProductUpdateSerializer


@api_view(["GET", "POST"])
def products(request):
    if request.method == "GET":
        qs = LoanProduct.objects.order_by("id")
        include_inactive = request.query_params.get("include_inactive", "").lower() in (
            "1", "true", "yes")
        if not include_inactive:
            qs = qs.filter(is_active=True)
        return Response(ProductSerializer(qs, many=True).data)

    if not CanSetup().has_permission(request, None):
        return Response({"detail": CanSetup.message}, status=status.HTTP_403_FORBIDDEN)

    if LoanProduct.objects.filter(code=request.data.get("code")).exists():
        raise BusinessRuleError("Product code already exists")
    body = ProductSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        product = body.save()
        audit(request.user, "create", "product", product.id, product.code)
    return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([CanSetup])
def product_detail(request, product_id: int):
    product = LoanProduct.objects.filter(pk=product_id).first()
    if product is None:
        raise NotFound("Product not found")
    body = ProductUpdateSerializer(product, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "product", product.id,
              str(list(body.validated_data.keys())))
    return Response(ProductSerializer(product).data)
