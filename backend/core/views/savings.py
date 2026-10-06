"""Savings products, accounts and counter movements."""
from django.db import transaction
from django.db.models import Prefetch, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import (
    Borrower,
    SavingsAccount,
    SavingsProduct,
    SavingsStatus,
    SavingsTransaction,
)
from ..permissions import CanAccounting, CanCash, CanSetup
from ..serializers import (
    NarrationSerializer,
    OpenSavingsSerializer,
    SavingsAccountDetailSerializer,
    SavingsAccountSerializer,
    SavingsMovementSerializer,
    SavingsPortfolioSerializer,
    SavingsProductSerializer,
    SavingsTransactionSerializer,
)
from ..services import savings as svc
from .helpers import table_response, wants_table, paginate, parse_date, parse_int


def _validated(serializer_class, request):
    body = serializer_class(data=request.data)
    body.is_valid(raise_exception=True)
    return body.validated_data


def account_queryset():
    return (SavingsAccount.objects
            .select_related("borrower", "product", "branch")
            .prefetch_related(Prefetch("transactions",
                                       queryset=SavingsTransaction.objects.order_by("id"))))


def get_account_or_404(account_id) -> SavingsAccount:
    account = account_queryset().filter(pk=account_id).first()
    if account is None:
        raise NotFound("Savings account not found")
    return account


# ---------------------------------------------------------------- products
@api_view(["GET", "POST"])
def products(request):
    if request.method == "GET":
        qs = SavingsProduct.objects.order_by("id")
        if request.query_params.get("include_inactive", "").lower() not in ("1", "true", "yes"):
            qs = qs.filter(is_active=True)
        return Response(SavingsProductSerializer(qs, many=True).data)

    if not CanSetup().has_permission(request, None):
        return Response({"detail": CanSetup.message}, status=status.HTTP_403_FORBIDDEN)
    if SavingsProduct.objects.filter(code=request.data.get("code")).exists():
        raise BusinessRuleError("A savings product with this code already exists")
    body = SavingsProductSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        product = body.save()
        audit(request.user, "create", "savings_product", product.id, product.code)
    return Response(SavingsProductSerializer(product).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([CanSetup])
def product_detail(request, product_id: int):
    product = SavingsProduct.objects.filter(pk=product_id).first()
    if product is None:
        raise NotFound("Savings product not found")
    body = SavingsProductSerializer(product, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "savings_product", product.id,
              str(list(body.validated_data.keys())))
    return Response(SavingsProductSerializer(product).data)


# ---------------------------------------------------------------- accounts
@api_view(["GET", "POST"])
def accounts(request):
    if request.method == "GET":
        qs = account_queryset()
        term = request.query_params.get("q")
        if term:
            qs = qs.filter(Q(account_no__icontains=term)
                           | Q(borrower__first_name__icontains=term)
                           | Q(borrower__last_name__icontains=term)
                           | Q(borrower__borrower_no__icontains=term)
                           | Q(borrower__national_id__icontains=term))
        state = request.query_params.get("status")
        if state:
            qs = qs.filter(status=state)
        branch_id = parse_int(request, "branch_id")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        borrower_id = parse_int(request, "borrower_id")
        if borrower_id:
            qs = qs.filter(borrower_id=borrower_id)

        if wants_table(request):
            rows = [{
                "account_no": a.account_no, "borrower": a.borrower.full_name,
                "borrower_no": a.borrower.borrower_no, "product": a.product.name,
                "branch": a.branch.name if a.branch_id else "", "status": a.status,
                "balance": a.balance, "opened_on": a.opened_on,
            } for a in qs[:5000]]
            return table_response(request, rows, "savings_accounts")
        return Response(paginate(request, qs.order_by("-id"), SavingsAccountSerializer))

    if not CanCash().has_permission(request, None):
        return Response({"detail": CanCash.message}, status=status.HTTP_403_FORBIDDEN)
    data = _validated(OpenSavingsSerializer, request)
    borrower = Borrower.objects.filter(pk=data["borrower_id"]).first()
    product = SavingsProduct.objects.filter(pk=data["product_id"]).first()
    if not borrower or not product:
        raise NotFound("Borrower or savings product not found")
    with transaction.atomic():
        account = svc.open_account(borrower, product, request.user, data.get("opening_deposit"),
                                   data.get("opened_on"), data.get("method"),
                                   data.get("reference"))
        audit(request.user, "open_savings", "savings", account.id,
              f"{account.account_no} for {borrower.full_name}")
    return Response(SavingsAccountSerializer(get_account_or_404(account.id)).data,
                    status=status.HTTP_201_CREATED)


@api_view(["GET"])
def account_detail(request, account_id: int):
    return Response(SavingsAccountDetailSerializer(get_account_or_404(account_id)).data)


@api_view(["GET"])
def statement(request, account_id: int):
    """The account's statement: JSON, or ?fmt=xlsx / ?fmt=pdf; ?start= and ?end= for a period."""
    from ..documents import savings_statement_pdf, savings_statement_xlsx
    from ..services.statements import savings_statement

    account = get_account_or_404(account_id)
    data = savings_statement(account, parse_date(request, "start"), parse_date(request, "end"))
    fmt = request.query_params.get("fmt")
    if fmt == "xlsx":
        return savings_statement_xlsx(data)
    if fmt == "pdf":
        return savings_statement_pdf(data)
    return Response(data)


@api_view(["POST"])
@permission_classes([CanCash])
def deposit(request, account_id: int):
    data = _validated(SavingsMovementSerializer, request)
    with transaction.atomic():
        account = get_account_or_404(account_id)
        stxn = svc.deposit(account, request.user, data["amount"], data.get("txn_date"),
                           data["method"], data.get("reference"), data.get("narration"))
        audit(request.user, "savings_deposit", "savings", account.id,
              f"{account.account_no} {stxn.amount}")
    return Response(SavingsTransactionSerializer(stxn).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([CanCash])
def withdraw(request, account_id: int):
    data = _validated(SavingsMovementSerializer, request)
    with transaction.atomic():
        account = get_account_or_404(account_id)
        stxn = svc.withdraw(account, request.user, data["amount"], data.get("txn_date"),
                            data["method"], data.get("reference"), data.get("narration"))
        audit(request.user, "savings_withdrawal", "savings", account.id,
              f"{account.account_no} {stxn.amount}")
    return Response(SavingsTransactionSerializer(stxn).data, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([CanCash])
def reverse(request, account_id: int, txn_id: int):
    data = _validated(NarrationSerializer, request)
    with transaction.atomic():
        account = get_account_or_404(account_id)
        stxn = SavingsTransaction.objects.filter(pk=txn_id, account_id=account.id).first()
        if stxn is None:
            raise NotFound("Savings transaction not found")
        reversal = svc.reverse(account, stxn, request.user, data["narration"])
        audit(request.user, "savings_reversal", "savings", account.id,
              f"{account.account_no} txn {txn_id}: {data['narration']}")
    return Response(SavingsTransactionSerializer(reversal).data)


@api_view(["POST"])
@permission_classes([CanAccounting])
def close(request, account_id: int):
    body = NarrationSerializer(data=request.data, partial=True)
    body.is_valid(raise_exception=False)
    with transaction.atomic():
        account = get_account_or_404(account_id)
        svc.close_account(account, request.user, body.data.get("narration"))
        audit(request.user, "close_savings", "savings", account.id, account.account_no)
    return Response(SavingsAccountSerializer(get_account_or_404(account_id)).data)


# ---------------------------------------------------------------- jobs / reports
@api_view(["POST"])
@permission_classes([CanAccounting])
def run_interest(request):
    """Credit a month of interest and take the monthly fee across the savings book."""
    as_of = parse_date(request, "as_of")
    with transaction.atomic():
        result = svc.accrue_interest(as_of)
        audit(request.user, "savings_interest_run", "system", None, str(result))
    return Response(result)


@api_view(["GET"])
def portfolio(request):
    return Response(
        SavingsPortfolioSerializer(svc.portfolio(parse_int(request, "branch_id"))).data)
