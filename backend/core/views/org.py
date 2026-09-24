"""Branches, institution settings and cross-entity search."""
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import Borrower, Branch, Loan, OrganisationSetting
from ..permissions import IsAdmin
from ..serializers import BranchSerializer, OrganisationSettingSerializer


@api_view(["GET", "POST"])
def branches(request):
    if request.method == "GET":
        qs = Branch.objects.order_by("code")
        if request.query_params.get("include_inactive", "").lower() not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return Response(BranchSerializer(qs, many=True).data)

    if not IsAdmin().has_permission(request, None):
        return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)
    if Branch.objects.filter(code=request.data.get("code")).exists():
        raise BusinessRuleError("Branch code already exists")
    body = BranchSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        branch = body.save()
        audit(request.user, "create", "branch", branch.id, branch.code)
    return Response(BranchSerializer(branch).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAdmin])
def branch_detail(request, branch_id: int):
    branch = Branch.objects.filter(pk=branch_id).first()
    if branch is None:
        raise NotFound("Branch not found")
    body = BranchSerializer(branch, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "branch", branch.id, str(list(body.validated_data.keys())))
    return Response(BranchSerializer(branch).data)


@api_view(["GET", "PATCH"])
def settings_view(request):
    """Institution-wide configuration. Everyone reads it (the UI needs the
    currency); only an admin changes it."""
    config = OrganisationSetting.load()
    if request.method == "GET":
        return Response(OrganisationSettingSerializer(config).data)

    if not IsAdmin().has_permission(request, None):
        return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)
    body = OrganisationSettingSerializer(config, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "settings", 1, str(list(body.validated_data.keys())))
    return Response(OrganisationSettingSerializer(OrganisationSetting.load()).data)


@api_view(["GET"])
def search(request):
    """One search box over borrowers and loans, for the header."""
    term = (request.query_params.get("q") or "").strip()
    if len(term) < 2:
        return Response({"query": term, "borrowers": [], "loans": []})

    borrowers = (Borrower.objects
                 .filter(Q(first_name__icontains=term) | Q(last_name__icontains=term)
                         | Q(borrower_no__icontains=term) | Q(national_id__icontains=term)
                         | Q(phone__icontains=term) | Q(employer__icontains=term))
                 .order_by("-id")[:8])
    loans = (Loan.objects
             .filter(Q(loan_no__icontains=term) | Q(borrower__first_name__icontains=term)
                     | Q(borrower__last_name__icontains=term)
                     | Q(borrower__national_id__icontains=term))
             .select_related("borrower")
             .order_by("-id")[:8])

    return Response({
        "query": term,
        "borrowers": [{"id": b.id, "label": f"{b.borrower_no} - {b.full_name}",
                       "sub": b.employer or b.phone} for b in borrowers],
        "loans": [{"id": l.id, "label": f"{l.loan_no} - {l.borrower.full_name}",
                   "sub": l.status} for l in loans],
    })
