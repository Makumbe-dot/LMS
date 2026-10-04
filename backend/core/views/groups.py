"""Joint-liability groups: the register, membership and group standing."""
from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import NotFound
from ..models import Borrower, BorrowerGroup, GroupMember
from ..permissions import IsOfficer
from ..serializers import (
    AddMemberSerializer,
    GroupDetailSerializer,
    GroupMemberSerializer,
    GroupSerializer,
    LoanSerializer,
)
from ..services import groups as svc
from .helpers import table_response, wants_table, paginate, parse_date, parse_int, with_arrears


def group_queryset():
    return (BorrowerGroup.objects
            .select_related("branch", "officer")
            .annotate(member_count=Count("members", filter=Q(members__is_active=True))))


def get_group_or_404(group_id) -> BorrowerGroup:
    group = group_queryset().filter(pk=group_id).first()
    if group is None:
        raise NotFound("Group not found")
    return group


@api_view(["GET", "POST"])
def groups(request):
    if request.method == "GET":
        qs = group_queryset()
        term = request.query_params.get("q")
        if term:
            qs = qs.filter(Q(name__icontains=term) | Q(group_no__icontains=term))
        state = request.query_params.get("status")
        if state:
            qs = qs.filter(status=state)
        branch_id = parse_int(request, "branch_id")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        return Response(paginate(request, qs.order_by("-id"), GroupSerializer))

    if not IsOfficer().has_permission(request, None):
        return Response({"detail": IsOfficer.message}, status=status.HTTP_403_FORBIDDEN)
    body = GroupSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    data = dict(body.validated_data)
    with transaction.atomic():
        group = svc.create_group(
            name=data.pop("name"),
            branch_id=(data.pop("branch").id if data.get("branch") else None),
            officer=data.pop("officer", None) or request.user,
            **data)
        audit(request.user, "create", "group", group.id, f"{group.group_no} {group.name}")
    return Response(GroupSerializer(get_group_or_404(group.id)).data,
                    status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH"])
def group_detail(request, group_id: int):
    group = get_group_or_404(group_id)
    if request.method == "GET":
        return Response(GroupDetailSerializer(group).data)

    if not IsOfficer().has_permission(request, None):
        return Response({"detail": IsOfficer.message}, status=status.HTTP_403_FORBIDDEN)
    body = GroupSerializer(group, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "group", group.id, str(list(body.validated_data.keys())))
    return Response(GroupDetailSerializer(get_group_or_404(group_id)).data)


@api_view(["POST"])
@permission_classes([IsOfficer])
def add_member(request, group_id: int):
    body = AddMemberSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    group = get_group_or_404(group_id)
    borrower = Borrower.objects.filter(pk=body.validated_data["borrower_id"]).first()
    if borrower is None:
        raise NotFound("Borrower not found")
    with transaction.atomic():
        member = svc.add_member(group, borrower, body.validated_data["role"],
                                body.validated_data.get("joined_on"))
        audit(request.user, "add_group_member", "group", group.id, borrower.full_name)
    return Response(GroupMemberSerializer(member).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsOfficer])
def member_detail(request, group_id: int, member_id: int):
    member = (GroupMember.objects
              .filter(pk=member_id, group_id=group_id)
              .select_related("borrower").first())
    if member is None:
        raise NotFound("Member not found")
    group = get_group_or_404(group_id)

    if request.method == "DELETE":
        with transaction.atomic():
            svc.remove_member(group, member)
            audit(request.user, "remove_group_member", "group", group_id,
                  member.borrower.full_name)
        return Response(status=status.HTTP_204_NO_CONTENT)

    body = GroupMemberSerializer(member, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update_group_member", "group", group_id,
              str(list(body.validated_data.keys())))
    return Response(GroupMemberSerializer(member).data)


@api_view(["GET"])
def standing(request, group_id: int):
    """What the group owes and how far behind its worst member is."""
    group = get_group_or_404(group_id)
    return Response(svc.standing(group, parse_date(request, "as_of")))


@api_view(["GET"])
def group_loans(request, group_id: int):
    group = get_group_or_404(group_id)
    loans = [with_arrears(l) for l in svc.member_loans(group)]
    return Response(LoanSerializer(loans, many=True).data)


@api_view(["GET"])
def performance(request):
    rows = svc.performance(parse_int(request, "branch_id"), parse_date(request, "as_of"))
    if wants_table(request):
        return table_response(request, rows, "group_performance")
    return Response(rows)
