"""The collections work queue, assignment and per-collector results. See
services/collections.py."""
from datetime import date

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..exceptions import BusinessRuleError, NotFound
from ..models import Right, User
from ..permissions import CanSupervise
from ..services import collections as svc
from .helpers import parse_date, parse_int, table_response, wants_table


@api_view(["GET"])
def queue(request):
    """?collector=me|<id>|none, ?branch_id=, ?min_days=, ?as_of=."""
    who = request.query_params.get("collector", "")
    collector_id, unassigned = None, False
    if who == "me":
        collector_id = request.user.id
    elif who == "none":
        unassigned = True
    elif who:
        collector_id = parse_int(request, "collector")
    rows = svc.queue(parse_date(request, "as_of"), collector_id=collector_id,
                     unassigned=unassigned, branch_id=parse_int(request, "branch_id"),
                     min_days=parse_int(request, "min_days", 1))
    if wants_table(request):
        flat = [{**{k: v for k, v in r.items() if k != "promise"},
                 "promised": r["promise"]["amount"] if r["promise"] else "",
                 "promised_by": r["promise"]["date"] if r["promise"] else "",
                 "promise": r["promise"]["state"] if r["promise"] else ""} for r in rows]
        return table_response(request, flat, "collections_queue")
    return Response(rows)


@api_view(["GET"])
def collectors(request):
    """Everyone a loan can be given to: active users holding the Collections right."""
    users = [u for u in User.objects.filter(is_active=True).order_by("full_name")
             if u.has_right(Right.COLLECTIONS)]
    return Response([{"id": u.id, "full_name": u.full_name} for u in users])


@api_view(["POST"])
@permission_classes([CanSupervise])
def assign(request):
    """{"loan_ids": [...], "collector_id": 5 | null}"""
    loan_ids = request.data.get("loan_ids") or []
    if not isinstance(loan_ids, list) or not all(isinstance(i, int) for i in loan_ids):
        raise BusinessRuleError("loan_ids must be a list of loan ids")
    if not loan_ids:
        raise BusinessRuleError("Choose at least one loan")
    collector = None
    if request.data.get("collector_id") is not None:
        collector = User.objects.filter(pk=request.data["collector_id"]).first()
        if collector is None:
            raise NotFound("No such user")
    changed = svc.assign(loan_ids, collector, request.user)
    return Response({"changed": changed})


@api_view(["GET"])
def performance(request):
    end = parse_date(request, "end", date.today())
    start = parse_date(request, "start", end.replace(day=1))
    rows = svc.performance(start, end)
    if wants_table(request):
        return table_response(request, rows, f"collector_performance_{start:%Y%m%d}")
    return Response(rows)
