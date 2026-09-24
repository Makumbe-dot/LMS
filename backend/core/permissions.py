"""Role guards. Four roles, checked per endpoint, mirroring the rules the loan
book is run by: officers originate and decide, tellers take money, admin does
the irreversible things, viewers only read.
"""
from rest_framework.permissions import BasePermission

from .models import Role


def require_roles(*roles):
    """Build a DRF permission class that admits only the given roles."""
    allowed = tuple(r.value if hasattr(r, "value") else r for r in roles)

    class _HasRole(BasePermission):
        message = f"Requires role: {', '.join(allowed)}"

        def has_permission(self, request, view):
            user = request.user
            return bool(user and user.is_authenticated and user.role in allowed)

    _HasRole.__name__ = "HasRole_" + "_".join(allowed)
    _HasRole.allowed_roles = allowed
    return _HasRole


IsAdmin = require_roles(Role.ADMIN)
IsOfficer = require_roles(Role.ADMIN, Role.LOAN_OFFICER)
IsTeller = require_roles(Role.ADMIN, Role.LOAN_OFFICER, Role.TELLER)
