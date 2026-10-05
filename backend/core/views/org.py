"""Branches, institution settings, the holiday calendar and cross-entity search."""
from datetime import date

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import Borrower, Branch, Holiday, Loan, OrganisationSetting
from ..permissions import IsAdmin
from ..serializers import BranchSerializer, HolidaySerializer, OrganisationSettingSerializer
from ..services import workdays


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
    closing = config.closed_weekdays
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "settings", 1, str(list(body.validated_data.keys())))
        moved = 0
        if config.closed_weekdays != closing:
            moved = workdays.move_upcoming_instalments(workdays.load(), date.today())
            if moved:
                audit(request.user, "move_instalments", "settings", 1,
                      f"{moved} instalments moved off {config.closed_weekdays}")
    data = dict(OrganisationSettingSerializer(OrganisationSetting.load()).data)
    data["instalments_moved"] = moved
    return Response(data)


@api_view(["GET", "POST"])
def holidays(request):
    """The public-holiday calendar. Everyone reads it; only an admin adds to it.

    Adding a holiday moves the unpaid instalments of running loans that fall due
    on it to the next working day, so a day declared at short notice reaches the
    loans already on the book. A holiday already past moves nothing.
    """
    if request.method == "GET":
        qs = Holiday.objects.select_related("created_by").order_by("date")
        year = request.query_params.get("year")
        if year and year.isdigit():
            qs = qs.filter(Q(date__year=int(year)) | Q(recurs_annually=True))
        return Response(HolidaySerializer(qs, many=True).data)

    if not IsAdmin().has_permission(request, None):
        return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)
    body = HolidaySerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        holiday = body.save(created_by=request.user)
        moved = workdays.move_upcoming_instalments(workdays.load(), date.today())
        audit(request.user, "create", "holiday", holiday.id,
              f"{holiday.date} {holiday.name}; {moved} instalments moved")
    data = dict(HolidaySerializer(holiday).data)
    data["instalments_moved"] = moved
    return Response(data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([IsAdmin])
def holiday_detail(request, holiday_id: int):
    """Take a holiday off the calendar. Instalments already moved off it stay where
    they are: the borrower was told the new date, and a day later is never dearer."""
    holiday = Holiday.objects.filter(pk=holiday_id).first()
    if holiday is None:
        raise NotFound("Holiday not found")
    with transaction.atomic():
        audit(request.user, "delete", "holiday", holiday.id, f"{holiday.date} {holiday.name}")
        holiday.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


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
