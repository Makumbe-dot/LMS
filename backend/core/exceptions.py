"""Error types and a DRF exception handler that always yields {"detail": "..."}.

The React client reads `detail` for every failure, so field errors from
serializers are flattened into one readable sentence instead of a nested
object.
"""
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


class BusinessRuleError(APIException):
    """A rule of the loan book was broken (400). The message is shown to the user."""
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The request breaks a business rule."


class NotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Not found."


def _flatten(data, prefix: str = "") -> list[str]:
    if isinstance(data, dict):
        out = []
        for key, value in data.items():
            label = "" if key in ("detail", "non_field_errors") else str(key)
            out += _flatten(value, f"{prefix}{label}: " if label else prefix)
        return out
    if isinstance(data, (list, tuple)):
        return [msg for item in data for msg in _flatten(item, prefix)]
    return [f"{prefix}{data}"]


def detail_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(response.data, dict) and set(response.data) == {"detail"}:
        return response
    messages = _flatten(response.data)
    response.data = {"detail": "; ".join(messages) if messages else "Request failed"}
    return response
