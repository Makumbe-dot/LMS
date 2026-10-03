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

from ..audit import audit
from ..exceptions import BusinessRuleError, NotFound
from ..models import Branch, User
from ..permissions import IsAdmin
from ..serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    RefreshSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
)
from ..services import tokens


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
    return Response({**tokens.issue(user), "user": UserSerializer(user).data})


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def refresh(request):
    """Exchange a refresh token for a new pair.

    AllowAny because the whole point is to be callable once the access token has
    expired; the refresh token is the credential. Throttled on the login scope, so
    it cannot be used to grind.

    Every reason a session should be over is checked here, because a verified
    signature is not an answer to "should this person still be signed in": the
    token may have been revoked, the account may have been disabled or locked, and
    the user's whole session set may have been ended since.
    """
    body = RefreshSerializer(data=request.data)
    body.is_valid(raise_exception=True)

    try:
        token = tokens.parse_refresh(body.validated_data["refresh_token"])
    except tokens.TokenError:
        return Response({"detail": "Your session has expired. Please sign in again."},
                        status=status.HTTP_401_UNAUTHORIZED)

    if tokens.is_revoked(token.get("jti")):
        # Already used or signed out. Rotation means a replayed token is either a
        # stale client or a stolen one; both get the same answer.
        return Response({"detail": "This session has been signed out. Please sign in again."},
                        status=status.HTTP_401_UNAUTHORIZED)

    user = tokens.user_for(token)
    if user is None or not user.is_active:
        return Response({"detail": "User account is disabled"},
                        status=status.HTTP_403_FORBIDDEN)
    if user.is_locked:
        return Response({"detail": "This account is locked. Ask an administrator to unlock it."},
                        status=status.HTTP_403_FORBIDDEN)
    if token.get(tokens.VERSION_CLAIM) not in (None, user.token_version):
        return Response({"detail": "This session has been signed out. Please sign in again."},
                        status=status.HTTP_401_UNAUTHORIZED)

    with transaction.atomic():
        # Rotate: the presented token is retired as the new pair is issued, so a
        # refresh token is good for exactly one use.
        tokens.revoke(token, user, "rotated on refresh")
        payload = tokens.issue(user)
    payload["user"] = UserSerializer(user).data
    return Response(payload)


@api_view(["POST"])
def logout(request):
    """End this session. The user's other devices stay signed in.

    The access token keeps working until it expires — nothing can recall a signed
    JWT — which is why its lifetime is thirty minutes rather than eight hours. Use
    `sign-out-everywhere` when that is not good enough.

    Answers 200 even when the token presented was already unusable: a sign-out that
    fails leaves the user signed in, which is the worse outcome.
    """
    body = RefreshSerializer(data=request.data, partial=True)
    body.is_valid(raise_exception=True)
    presented = body.validated_data.get("refresh_token")
    retired = False
    if presented:
        try:
            retired = tokens.revoke(tokens.parse_refresh(presented), request.user, "signed out")
        except tokens.TokenError:
            retired = False
    audit(request.user, "logout", "user", request.user.id,
          request.user.username + ("" if retired else " (no usable refresh token presented)"))
    return Response({"detail": "Signed out.", "refresh_token_retired": retired})


@api_view(["POST"])
def sign_out_everywhere(request):
    """End every session this user has, on every device, immediately.

    Bumps the token version, so access tokens already in flight are refused on
    their next request rather than at the end of their thirty minutes.
    """
    with transaction.atomic():
        version = tokens.revoke_all(request.user, "signed out everywhere")
        audit(request.user, "sign_out_everywhere", "user", request.user.id,
              f"{request.user.username} (token version now {version})")
    return Response({"detail": "Signed out on every device. Sign in again to continue."})


@api_view(["POST"])
def change_password(request):
    """Any signed-in user can change their own password."""
    # The request goes in the context so the password policy can refuse a password
    # that is the user's own username.
    body = ChangePasswordSerializer(data=request.data, context={"request": request})
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
        # A password change that leaves the old sessions working is not a password
        # change: the usual reason for one is that the old password is compromised.
        tokens.revoke_all(user, "password changed")
        audit(user, "change_password", "user", user.id, user.username)
    fresh = tokens.issue(user)
    return Response({
        "detail": "Password changed. Every other device has been signed out.",
        # The caller's own token was just invalidated too, so hand back a new pair
        # rather than bouncing them to the sign-in screen for doing the right thing.
        **fresh,
    })


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
    body = UserUpdateSerializer(data=request.data, partial=True,
                                context={"target_user": user})
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

    # Disabling an account, changing someone's role, or resetting their password
    # must take effect now, not whenever their access token happens to expire.
    # Without this a dismissed employee keeps posting for up to thirty minutes, and
    # a demoted one keeps the permissions of the role they just lost.
    revoke_reasons = []
    if password:
        revoke_reasons.append("password reset by an administrator")
    if data.get("is_active") is False:
        revoke_reasons.append("account disabled")
    if data.get("role") and data["role"] != user.role:
        revoke_reasons.append(f"role changed from {user.role} to {data['role']}")

    with transaction.atomic():
        if password:
            user.set_password(password)
        if unlock:
            user.locked_until = None
            user.failed_login_attempts = 0
        for key, value in data.items():
            setattr(user, key, value)
        user.save()
        if revoke_reasons:
            tokens.revoke_all(user, "; ".join(revoke_reasons))

        changed = list(data) + (["password"] if password else []) + (["unlock"] if unlock else [])
        audit(request.user, "update", "user", user.id,
              str(changed) + (f" — sessions ended ({'; '.join(revoke_reasons)})"
                              if revoke_reasons else ""))
    return Response(UserSerializer(user).data)
