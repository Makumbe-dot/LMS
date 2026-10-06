"""Shared query and response helpers for the API views."""
import csv

from django.db.models import Prefetch
from django.http import HttpResponse

from ..exceptions import NotFound
from ..models import Instalment, Loan, Transaction
from ..services import loans as svc


def loan_queryset(with_transactions: bool = False):
    """Loans with everything the serializers and the arrears calculation need,
    so no view ever issues a query per row.

    For a LIST, prefer annotating arrears (see `services.arrears`) over this
    prefetch: the list serializer renders no schedule, so fetching every
    instalment to compute one number per loan is pure cost.
    """
    qs = (Loan.objects
          .select_related("borrower", "product", "officer", "collector", "branch")
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
    """Attach arrears_amount / days_in_arrears for the serializer.

    Walks the loan's schedule, which is the right shape for a single loan whose
    instalments are being fetched anyway. For a LIST, annotate the queryset with
    `services.arrears.with_arrears` and use `arrears_from_annotation` instead.
    """
    loan.arrears_amount, loan.days_in_arrears = svc.arrears(loan)
    return loan


def arrears_from_annotation(loan: Loan) -> Loan:
    """Fill days_in_arrears from an annotated queryset, fetching nothing."""
    from ..services import arrears as arrears_svc

    loan.arrears_amount = loan.arrears_amount or 0
    loan.days_in_arrears = arrears_svc.days_from(loan.oldest_arrears_due)
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


TABLE_FORMATS = ("csv", "xlsx")


def wants_table(request) -> bool:
    """Whether the caller asked for the listing as a file (?fmt=csv or ?fmt=xlsx)."""
    return request.query_params.get("fmt") in TABLE_FORMATS


def table_response(request, rows: list[dict], name: str) -> HttpResponse:
    """The listing as the file asked for: CSV, or an Excel sheet with typed columns."""
    if request.query_params.get("fmt") == "xlsx":
        from ..exports import single_sheet

        return single_sheet(rows, name)
    return csv_response(rows, name)


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


def paginate_list(request, rows: list, default_size: int = 50, max_size: int = 1000,
                  extra: dict | None = None) -> dict:
    """One page of an already-materialised list, in the same shape as paginate().

    For reports that compute their rows in Python. `extra` carries whole-set totals
    alongside the page, because a client that sums the rows it was given would
    under-report anything that did not fit on the page.
    """
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except ValueError:
        page = 1
    try:
        page_size = min(max(int(request.query_params.get("page_size", default_size)), 1), max_size)
    except ValueError:
        page_size = default_size

    count = len(rows)
    num_pages = max(1, -(-count // page_size))
    page = min(page, num_pages)
    offset = (page - 1) * page_size
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "num_pages": num_pages,
        "results": rows[offset:offset + page_size],
        **(extra or {}),
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
