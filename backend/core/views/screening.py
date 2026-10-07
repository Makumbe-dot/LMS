"""AML screening: the watch lists, the hits and their review."""
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import ScreeningHit
from ..permissions import CanSupervise, IsAdmin
from ..services import screening as svc


class ScreeningHitSerializer(serializers.ModelSerializer):
    borrower_name = serializers.CharField(source="borrower.full_name", read_only=True)
    borrower_no = serializers.CharField(source="borrower.borrower_no", read_only=True)
    national_id = serializers.CharField(source="borrower.national_id", read_only=True)
    date_of_birth = serializers.DateField(source="borrower.date_of_birth", read_only=True)
    listed_aliases = serializers.CharField(source="entry.aliases", read_only=True, default="")
    listed_dob = serializers.CharField(source="entry.date_of_birth", read_only=True, default="")
    listed_notes = serializers.CharField(source="entry.notes", read_only=True, default="")
    reviewed_by_name = serializers.CharField(source="reviewed_by.full_name", read_only=True,
                                             default=None)

    class Meta:
        model = ScreeningHit
        fields = ["id", "borrower_id", "borrower_name", "borrower_no", "national_id",
                  "date_of_birth", "listed_name", "listed_aliases", "listed_dob", "listed_notes",
                  "source", "reference", "score", "reason", "status", "review_note",
                  "reviewed_by_name", "reviewed_at", "created_at"]


@api_view(["GET"])
def hits(request):
    """?status=open|cleared|confirmed (open by default), ?borrower=<id>."""
    qs = ScreeningHit.objects.select_related("borrower", "entry", "reviewed_by")
    borrower = request.query_params.get("borrower")
    if borrower:
        qs = qs.filter(borrower_id=borrower)
    wanted = request.query_params.get("status", "" if borrower else "open")
    if wanted:
        qs = qs.filter(status=wanted)
    return Response(ScreeningHitSerializer(qs[:500], many=True).data)


@api_view(["POST"])
def review(request, hit_id: int):
    """{decision: cleared|confirmed, note}. A supervisor's or an administrator's call."""
    if not (IsAdmin().has_permission(request, None) or CanSupervise().has_permission(request, None)):
        return Response({"detail": "Reviewing a screening match needs the supervise right."},
                        status=status.HTTP_403_FORBIDDEN)
    hit = ScreeningHit.objects.select_related("borrower").filter(pk=hit_id).first()
    if hit is None:
        raise NotFound("No such match")
    svc.review(hit, request.user, request.data.get("decision"), request.data.get("note"))
    audit(request.user, f"screening_{hit.status}", "borrower", hit.borrower_id,
          f"{hit.listed_name} ({hit.source}): {hit.review_note[:200]}")
    return Response(ScreeningHitSerializer(hit).data)


@api_view(["GET", "POST"])
@parser_classes([MultiPartParser, FormParser])
def lists(request):
    """GET the loaded lists; POST a file (format=un for the UN XML, csv otherwise,
    with `source` naming the list) to load it and screen everyone again."""
    if request.method == "GET":
        return Response({"lists": svc.lists(),
                         "open": ScreeningHit.objects.filter(status="open").count()})
    if not IsAdmin().has_permission(request, None):
        return Response({"detail": IsAdmin.message}, status=status.HTTP_403_FORBIDDEN)
    upload = request.FILES.get("file")
    if upload is None:
        raise BusinessRuleError("Choose a file")
    raw = upload.read()
    kind = (request.data.get("format") or "").lower() or (
        "un" if upload.name.lower().endswith(".xml") else "csv")
    if kind == "un":
        entries = svc.parse_un_xml(raw)
    else:
        source = (request.data.get("source") or "").strip()
        if not source:
            raise BusinessRuleError("Name the list (e.g. 'Local PEP list')")
        entries = svc.parse_csv(raw, source)
    with transaction.atomic():
        result = svc.load(entries)
        audit(request.user, "load_watchlist", "screening", None,
              f"{', '.join(result['lists'])}: {result['loaded']} names, {result['new_hits']} new matches")
    return Response(result, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAdmin])
def run(request):
    """Screen every borrower again against the lists as they stand."""
    result = svc.screen_everyone()
    audit(request.user, "screen_all", "screening", None, str(result))
    return Response(result)
