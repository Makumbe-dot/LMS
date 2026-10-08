"""A user's saved filters on the list pages. Each user sees and changes only their own."""
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..exceptions import BusinessRuleError, NotFound
from ..models import SavedView

PAGES = {"loans", "borrowers", "payments", "collections"}
MAX_PER_PAGE = 30


class SavedViewSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedView
        fields = ["id", "page", "name", "params", "created_at"]
        read_only_fields = ["created_at"]

    def validate_page(self, value):
        if value not in PAGES:
            raise serializers.ValidationError(
                f"Saved filters are kept for: {', '.join(sorted(PAGES))}")
        return value

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Give the view a name")
        return value

    def validate_params(self, value):
        # Filters are short strings and flags; anything else is not a filter.
        if not isinstance(value, dict) or len(value) > 20 or not all(
                isinstance(k, str) and isinstance(v, (str, bool, int)) and len(str(v)) <= 100
                for k, v in value.items()):
            raise serializers.ValidationError("Filters must be a few short values")
        return value


@api_view(["GET", "POST"])
def saved_views(request):
    mine = SavedView.objects.filter(user=request.user)
    if request.method == "GET":
        page = request.query_params.get("page")
        return Response(SavedViewSerializer(mine.filter(page=page) if page else mine,
                                            many=True).data)
    body = SavedViewSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    data = body.validated_data
    if mine.filter(page=data["page"]).count() >= MAX_PER_PAGE:
        raise BusinessRuleError(f"At most {MAX_PER_PAGE} saved views a page; delete one first")
    view, _ = SavedView.objects.update_or_create(
        user=request.user, page=data["page"], name=data["name"],
        defaults={"params": data["params"]})
    return Response(SavedViewSerializer(view).data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
def saved_view_detail(request, view_id: int):
    deleted, _ = SavedView.objects.filter(pk=view_id, user=request.user).delete()
    if not deleted:
        raise NotFound("Saved view not found")
    return Response(status=status.HTTP_204_NO_CONTENT)
