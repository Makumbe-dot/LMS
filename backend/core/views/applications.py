"""Online loan applications: the public Apply page, the portal, and staff review."""
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from ..audit import audit
from ..exceptions import NotFound
from ..models import ApplicationStatus, OnlineApplication
from ..permissions import CanBorrowers, CanLoans
from ..services import applications as svc
from .portal import portal_view


class ApplyThrottle(AnonRateThrottle):
    """Per calling address. Generous enough for a family sharing a phone, not for a script."""
    scope = "applications"


class OnlineApplicationSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    borrower_no = serializers.CharField(source="borrower.borrower_no", read_only=True, default=None)
    handled_by_name = serializers.CharField(source="handled_by.full_name", read_only=True,
                                            default=None)

    class Meta:
        model = OnlineApplication
        fields = ["id", "status", "source", "first_name", "last_name", "national_id", "phone",
                  "email", "address", "employer", "net_salary", "payday", "product_id",
                  "product_name", "amount", "term_months", "purpose", "borrower_id",
                  "borrower_no", "outcome", "created_at", "handled_by_name", "handled_at"]


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def public_products(request):
    """The products the Apply page offers."""
    return Response(svc.products())


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ApplyThrottle])
def public_apply(request):
    """A new client's application. Nothing about existing borrowers is revealed:
    the answer is the same whether or not the national ID is already known."""
    with transaction.atomic():
        made = svc.submit_public(request.data or {}, request.META.get("REMOTE_ADDR", ""))
        audit(None, "online_application", "online_application", made.id,
              f"{made.full_name} {made.amount} {made.product.name}")
    return Response({"reference": f"APP-{made.id:06d}",
                     "message": "Thank you. We have your application and will contact you."},
                    status=status.HTTP_201_CREATED)


@portal_view(["GET", "POST"])
def portal_applications(request):
    """A signed-in borrower's applications; POST {product_id, amount, term_months,
    purpose} to apply for another loan."""
    borrower = request.portal.borrower
    if request.method == "GET":
        mine = OnlineApplication.objects.filter(borrower=borrower).select_related("product")[:20]
        return Response([{"reference": f"APP-{a.id:06d}", "product": a.product.name,
                          "amount": a.amount, "term_months": a.term_months,
                          "status": a.get_status_display(), "created_at": a.created_at}
                         for a in mine])
    with transaction.atomic():
        made = svc.submit_portal(borrower, request.data or {})
        audit(None, "online_application", "borrower", borrower.id,
              f"portal {made.amount} {made.product.name}")
    return Response({"reference": f"APP-{made.id:06d}"}, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def staff_list(request):
    """?status=new|accepted|declined (new by default)."""
    wanted = request.query_params.get("status", ApplicationStatus.NEW)
    qs = OnlineApplication.objects.select_related("product", "borrower", "handled_by")
    if wanted:
        qs = qs.filter(status=wanted)
    return Response(OnlineApplicationSerializer(qs[:500], many=True).data)


def _get(application_id) -> OnlineApplication:
    found = OnlineApplication.objects.select_related("product", "borrower").filter(
        pk=application_id).first()
    if found is None:
        raise NotFound("No such application")
    return found


@api_view(["POST"])
def staff_accept(request, application_id: int):
    """Make the applicant a borrower and return the New loan form's starting figures."""
    if not (CanBorrowers().has_permission(request, None) and CanLoans().has_permission(request, None)):
        return Response({"detail": "Accepting needs the borrowers and loans rights."},
                        status=status.HTTP_403_FORBIDDEN)
    application = _get(application_id)
    result = svc.accept(application, request.user, request.data.get("branch"))
    audit(request.user, "accept", "online_application", application.id, application.outcome)
    return Response(result)


@api_view(["POST"])
@permission_classes([CanLoans])
def staff_decline(request, application_id: int):
    application = _get(application_id)
    svc.decline(application, request.user, request.data.get("reason"))
    audit(request.user, "decline", "online_application", application.id, application.outcome)
    return Response(OnlineApplicationSerializer(application).data)
