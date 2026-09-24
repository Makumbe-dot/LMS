"""Audit trail. Every posting and every decision leaves a row here."""
from .models import AuditLog, User


def audit(user: User | None, action: str, entity: str,
          entity_id: int | None = None, detail: str | None = None) -> AuditLog:
    return AuditLog.objects.create(
        user=user if (user and user.is_authenticated) else None,
        action=action,
        entity=entity,
        entity_id=entity_id,
        detail=detail,
    )
