"""Login, the current user, and user administration."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import Branch, User
from ..permissions import IsAdmin
from ..serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
)


class LoginThrottle(ScopedRateThrottle):
    scope = "login"


def _register_failure(user: User | None) -> None:
    """Count a bad password and lock the account once the limit is reached."""
    if user is None:
        return
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= settings.LOGIN_MAX_ATTEMPTS:
        user.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
        user.failed_login_attempts = 0
        audit(None, "lockout", "user", user.id,
              f"{user.username} locked for {settings.LOGIN_LOCKOUT_MINUTES} minutes")
    user.save(update_fields=["failed_login_attempts", "locked_until"])


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
@transaction.atomic
def login(request):
    """Accepts JSON or form-encoded username/password and returns a bearer token.

    Repeated bad passwords lock the account for a while, so a stolen username is
    not enough to grind through a password list.
    """
    body = LoginSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    username = body.validated_data["username"]
    password = body.validated_data["password"]

    existing = User.objects.filter(username=username).first()
    if existing and existing.is_locked:
        minutes = max(1, int((existing.locked_until - timezone.now()).total_seconds() // 60) + 1)
        return Response(
            {"detail": f"Too many failed sign-in attempts. Try again in {minutes} minute(s), "
                       f"or ask an administrator to unlock the account."},
            status=status.HTTP_403_FORBIDDEN)

    user = authenticate(request, username=username, password=password)
    if user is None:
        # Distinguish a disabled account from bad credentials, as the old API did.
        if existing and not existing.is_active and existing.check_password(password):
            return Response({"detail": "User account is disabled"},
                            status=status.HTTP_403_FORBIDDEN)
        _register_failure(existing)
        return Response({"detail": "Incorrect username or password"},
                        status=status.HTTP_401_UNAUTHORIZED)
    if not user.is_active:
        return Response({"detail": "User account is disabled"}, status=status.HTTP_403_FORBIDDEN)

    if user.failed_login_attempts or user.locked_until:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_attempts", "locked_until"])

    audit(user, "login", "user", user.id)
    token = RefreshToken.for_user(user)
    token["role"] = user.role
    token["sub"] = user.username
    return Response({
        "access_token": str(token.access_token),
        "refresh_token": str(token),
        "token_type": "bearer",
        "user": UserSerializer(user).data,
    })


@api_view(["POST"])
def change_password(request):
    """Any signed-in user can change their own password."""
    body = ChangePasswordSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    user = request.user
    if not user.check_password(body.validated_data["current_password"]):
        raise BusinessRuleError("Your current password is not correct")
    new_password = body.validated_data["new_password"]
    if user.check_password(new_password):
        raise BusinessRuleError("The new password must be different from the current one")
    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password"])
        audit(user, "change_password", "user", user.id, user.username)
    return Response({"detail": "Password changed. Sign in again on your other devices."})


@api_view(["GET"])
def me(request):
    return Response(UserSerializer(request.user).data)


@api_view(["GET", "POST"])
@permission_classes([IsAdmin])
def users(request):
    if request.method == "GET":
        return Response(UserSerializer(User.objects.order_by("id"), many=True).data)

    body = UserCreateSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    with transaction.atomic():
        user = body.save()
        audit(request.user, "create", "user", user.id, f"{user.username} ({user.role})")
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@api_view(["PATCH"])
@permission_classes([IsAdmin])
def user_detail(request, user_id: int):
    user = User.objects.filter(pk=user_id).first()
    if not user:
        raise NotFound("User not found")
    body = UserUpdateSerializer(data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    data = dict(body.validated_data)
    password = data.pop("password", None)
    unlock = data.pop("unlock", False)
    branch_id = data.pop("branch_id", "missing")

    if user == request.user and data.get("is_active") is False:
        raise BusinessRuleError("You cannot disable your own account")
    if user == request.user and data.get("role") and data["role"] != user.role:
        raise BusinessRuleError("You cannot change your own role")

    if branch_id != "missing":
        if branch_id and not Branch.objects.filter(pk=branch_id).exists():
            raise NotFound("Branch not found")
        user.branch_id = branch_id

    with transaction.atomic():
        if password:
            user.set_password(password)
        if unlock:
            user.locked_until = None
            user.failed_login_attempts = 0
        for key, value in data.items():
            setattr(user, key, value)
        user.save()
        changed = list(data) + (["password"] if password else []) + (["unlock"] if unlock else [])
        audit(request.user, "update", "user", user.id, str(changed))
    return Response(UserSerializer(user).data)
