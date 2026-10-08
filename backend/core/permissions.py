"""Access guards, checked per endpoint. An administrator may do anything; every
other user may do what the access rights an administrator granted them allow,
and with none granted may only read.
"""
from rest_framework.permissions import BasePermission

from .models import Right


def require_right(right: Right):
    """Build a DRF permission class that admits holders of one access right."""

    class _HasRight(BasePermission):
        message = f"Requires the access right: {right.label}"

        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and user.has_right(right))

    _HasRight.__name__ = f"HasRight_{right.value}"
    _HasRight.right = right.value
    return _HasRight


class IsAdmin(BasePermission):
    """Users, the audit log, branches, holidays, the organisation's settings and the loan
    book migration: never granted, an administrator's alone."""
    message = "Requires an administrator"

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_admin)


CanBorrowers = require_right(Right.BORROWERS)
CanLoans = require_right(Right.LOANS)
CanApprove = require_right(Right.APPROVE)
CanDisburse = require_right(Right.DISBURSE)
CanCash = require_right(Right.CASH)
CanReverse = require_right(Right.REVERSE)
CanSupervise = require_right(Right.SUPERVISE)
CanMessages = require_right(Right.MESSAGES)
CanCollections = require_right(Right.COLLECTIONS)
CanRestructure = require_right(Right.RESTRUCTURE)
CanAccounting = require_right(Right.ACCOUNTING)
CanSetup = require_right(Right.SETUP)
