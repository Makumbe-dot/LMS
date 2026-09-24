"""Shared query and response helpers for the API views."""
import csv

from django.db.models import Prefetch
from django.http import HttpResponse

from ..exceptions import NotFound
from ..models import Instalment, Loan, Transaction
from ..services import loans as svc


def loan_queryset(with_transactions: bool = False):
    """Loans with everything the serializers and the arrears calculation need,
    so no view ever issues a query per row."""
    qs = (Loan.objects
          .select_related("borrower", "product")
          .prefetch_related(Prefetch("instalments", queryset=Instalment.objects.order_by("number"))))
    if with_transactions:
        qs = qs.prefetch_related(
            Prefetch("transactions", queryset=Transaction.objects.order_by("id")))
    return qs


def get_loan_or_404(loan_id, with_transactions: bool = False) -> Loan:
    loan = loan_queryset(with_transactions).filter(pk=loan_id).first()
    if loan is None:
        raise NotFound("Loan not found")
    return loan


def with_arrears(loan: Loan) -> Loan:
    """Attach arrears_amount / days_in_arrears for the serializer."""
    loan.arrears_amount, loan.days_in_arrears = svc.arrears(loan)
    return loan


def csv_response(rows: list[dict], name: str) -> HttpResponse:
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    if rows:
        writer = csv.DictWriter(response, fieldnames=list(rows[0].keys()),
                               lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
    return response


def paginate(request, queryset, serializer_class, transform=None, default_size: int = 50,
             max_size: int = 1000) -> dict:
    """Return one page as {count, page, page_size, num_pages, results}.

    `transform` runs over the page's objects before serialisation, which is where
    per-row arrears are attached without touching the rest of the table.
    """
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except ValueError:
        page = 1
    try:
        page_size = min(max(int(request.query_params.get("page_size", default_size)), 1), max_size)
    except ValueError:
        page_size = default_size

    count = queryset.count()
    num_pages = max(1, -(-count // page_size))  # ceiling division
    page = min(page, num_pages)
    offset = (page - 1) * page_size
    rows = list(queryset[offset:offset + page_size])
    if transform:
        rows = [transform(row) for row in rows]
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "num_pages": num_pages,
        "results": serializer_class(rows, many=True).data,
    }


def parse_int(request, key: str, default=None):
    raw = request.query_params.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        from ..exceptions import BusinessRuleError
        raise BusinessRuleError(f"{key} must be a whole number")


def parse_date(request, key: str, default=None):
    """Read an ISO date from the query string."""
    from datetime import date as _date

    raw = request.query_params.get(key)
    if not raw:
        return default
    try:
        return _date.fromisoformat(raw)
    except ValueError:
        from ..exceptions import BusinessRuleError
        raise BusinessRuleError(f"{key} must be an ISO date (YYYY-MM-DD)")
