"""Borrower register, guarantors, and a borrower's loan history."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from django.conf import settings as django_settings
from django.http import FileResponse

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import Borrower, BorrowerDocument, Guarantor, LoanStatus
from ..permissions import IsOfficer
from ..serializers import (
    BorrowerCreateSerializer,
    BorrowerDocumentSerializer,
    BorrowerSerializer,
    BorrowerUpdateSerializer,
    DocumentUploadSerializer,
    GuarantorSerializer,
    LoanSerializer,
)
from ..services.loans import next_number
from .helpers import loan_queryset, paginate, parse_int, with_arrears

_money = DecimalField(max_digits=18, decimal_places=2)


def borrower_queryset():
    """Borrowers with their live exposure, computed in SQL Server rather than in Python."""
    active = Q(loans__status=LoanStatus.ACTIVE)
    return (Borrower.objects
            .select_related("branch")
            .prefetch_related("guarantors", "documents")
            .annotate(
                active_loans=Count("loans", filter=active, distinct=True),
                total_outstanding=Coalesce(
                    Sum(F("loans__principal_outstanding")
                        + F("loans__interest_outstanding")
                        + F("loans__penalties_outstanding"),
                        filter=active, output_field=_money),
                    Value(Decimal("0"), output_field=_money)),
            ))


def get_borrower_or_404(borrower_id) -> Borrower:
    borrower = borrower_queryset().filter(pk=borrower_id).first()
    if borrower is None:
        raise NotFound("Borrower not found")
    return borrower


@api_view(["GET", "POST"])
def borrowers(request):
    if request.method == "GET":
        qs = borrower_queryset()
        search = request.query_params.get("q")
        if search:
            qs = qs.filter(
                Q(first_name__icontains=search) | Q(last_name__icontains=search)
                | Q(national_id__icontains=search) | Q(borrower_no__icontains=search)
                | Q(phone__icontains=search) | Q(employer__icontains=search))
        branch_id = parse_int(request, "branch_id")
        if branch_id:
            qs = qs.filter(branch_id=branch_id)
        kyc = request.query_params.get("kyc")
        if kyc in ("1", "0"):
            qs = qs.filter(kyc_verified=(kyc == "1"))
        if request.query_params.get("blacklisted") == "1":
            qs = qs.filter(is_blacklisted=True)
        return Response(paginate(request, qs.order_by("-id"), BorrowerSerializer))

    # POST - officers and admins only
    if not IsOfficer().has_permission(request, None):
        return Response({"detail": IsOfficer.message}, status=status.HTTP_403_FORBIDDEN)

    body = BorrowerCreateSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    guarantors = body.validated_data.pop("guarantors", [])
    with transaction.atomic():
        borrower = Borrower.objects.create(borrower_no=next_number("BRW"), **body.validated_data)
        for g in guarantors:
            Guarantor.objects.create(borrower=borrower, **g)
        audit(request.user, "create", "borrower", borrower.id, borrower.full_name)
    return Response(BorrowerSerializer(get_borrower_or_404(borrower.id)).data,
                    status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH"])
def borrower_detail(request, borrower_id: int):
    if request.method == "GET":
        return Response(BorrowerSerializer(get_borrower_or_404(borrower_id)).data)

    if not IsOfficer().has_permission(request, None):
        return Response({"detail": IsOfficer.message}, status=status.HTTP_403_FORBIDDEN)

    borrower = Borrower.objects.filter(pk=borrower_id).first()
    if borrower is None:
        raise NotFound("Borrower not found")
    body = BorrowerUpdateSerializer(borrower, data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        body.save()
        audit(request.user, "update", "borrower", borrower.id,
              str(list(body.validated_data.keys())))
    return Response(BorrowerSerializer(get_borrower_or_404(borrower_id)).data)


@api_view(["GET"])
def borrower_loans(request, borrower_id: int):
    loans = loan_queryset().filter(borrower_id=borrower_id).order_by("-id")
    return Response(LoanSerializer([with_arrears(l) for l in loans], many=True).data)


@api_view(["POST"])
@permission_classes([IsOfficer])
def add_guarantor(request, borrower_id: int):
    borrower = Borrower.objects.filter(pk=borrower_id).first()
    if borrower is None:
        raise NotFound("Borrower not found")
    body = GuarantorSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        guarantor = Guarantor.objects.create(borrower=borrower, **body.validated_data)
        audit(request.user, "add_guarantor", "borrower", borrower.id, guarantor.full_name)
    return Response(GuarantorSerializer(guarantor).data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
@permission_classes([IsOfficer])
def remove_guarantor(request, borrower_id: int, guarantor_id: int):
    guarantor = Guarantor.objects.filter(pk=guarantor_id, borrower_id=borrower_id).first()
    if guarantor is None:
        raise NotFound("Guarantor not found")
    # Deleting them would quietly take their name off a running loan's agreement.
    standing = guarantor.loans.exclude(
        status__in=[LoanStatus.CLOSED, LoanStatus.REJECTED, LoanStatus.WRITTEN_OFF]).first()
    if standing:
        raise BusinessRuleError(
            f"{guarantor.full_name} guarantees {standing.loan_no}, which is {standing.status}. "
            f"Take them off that loan first, or wait until it closes.")
    with transaction.atomic():
        name = guarantor.full_name
        guarantor.delete()
        audit(request.user, "remove_guarantor", "borrower", borrower_id, name)
    return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- documents
@api_view(["GET", "POST"])
def documents(request, borrower_id: int):
    borrower = Borrower.objects.filter(pk=borrower_id).first()
    if borrower is None:
        raise NotFound("Borrower not found")

    if request.method == "GET":
        return Response(BorrowerDocumentSerializer(borrower.documents.all(), many=True).data)

    if not IsOfficer().has_permission(request, None):
        return Response({"detail": IsOfficer.message}, status=status.HTTP_403_FORBIDDEN)

    body = DocumentUploadSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    upload = body.validated_data["file"]

    if upload.size > django_settings.MAX_UPLOAD_BYTES:
        limit_mb = django_settings.MAX_UPLOAD_BYTES / (1024 * 1024)
        raise BusinessRuleError(f"The file is larger than the {limit_mb:.0f} MB limit")
    if upload.content_type not in django_settings.ALLOWED_UPLOAD_TYPES:
        raise BusinessRuleError(
            f"'{upload.content_type}' is not an accepted file type. Upload a PDF, an image or a "
            f"Word document.")

    with transaction.atomic():
        document = BorrowerDocument.objects.create(
            borrower=borrower,
            doc_type=body.validated_data["doc_type"],
            file=upload,
            original_name=upload.name[:255],
            content_type=upload.content_type,
            size_bytes=upload.size,
            note=body.validated_data.get("note") or None,
            uploaded_by=request.user,
        )
        audit(request.user, "upload_document", "borrower", borrower.id,
              f"{document.doc_type}: {document.original_name}")
    return Response(BorrowerDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def download_document(request, borrower_id: int, document_id: int):
    document = BorrowerDocument.objects.filter(pk=document_id, borrower_id=borrower_id).first()
    if document is None:
        raise NotFound("Document not found")
    try:
        handle = document.file.open("rb")
    except (FileNotFoundError, ValueError):
        raise NotFound("The stored file is missing from disk")
    response = FileResponse(handle, content_type=document.content_type or "application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{document.original_name}"'
    return response


@api_view(["DELETE"])
@permission_classes([IsOfficer])
def delete_document(request, borrower_id: int, document_id: int):
    document = BorrowerDocument.objects.filter(pk=document_id, borrower_id=borrower_id).first()
    if document is None:
        raise NotFound("Document not found")
    with transaction.atomic():
        name = document.original_name
        try:
            document.file.delete(save=False)
        except OSError:
            # The record goes either way; a file the OS will not release yet
            # (a download still streaming, say) is left for housekeeping.
            pass
        document.delete()
        audit(request.user, "delete_document", "borrower", borrower_id, name)
    return Response(status=status.HTTP_204_NO_CONTENT)
