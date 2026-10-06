"""Django settings for the Loan Management System.

Configuration comes from backend/.env (see .env.example). The database is
SQL Server, reached through the mssql-django backend and the Microsoft ODBC
driver, so the schema is manageable from SQL Server Management Studio.
"""
import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def env_list(key: str, default: str = "") -> list[str]:
    return [p.strip() for p in env(key, default).split(",") if p.strip()]


def _pairs(raw: str) -> dict[str, str]:
    """"a=1|b=2" -> {"a": "1", "b": "2"}.

    Pipe-separated rather than comma, because an SMS gateway's fixed fields
    routinely contain commas (a sender id, a callback URL with a query string).
    """
    out = {}
    for chunk in raw.split("|"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            if key.strip():
                out[key.strip()] = value.strip()
    return out


# ---------------------------------------------------------------- core
SECRET_KEY = env("SECRET_KEY", "django-insecure-change-me-in-production")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

APP_NAME = env("APP_NAME", "Loan Management System")
CURRENCY = env("CURRENCY", "USD")
# A PNG or JPEG for the top of PDF statements and the loan agreement. The default
# is the Zinmad Capital monogram that ships with the code; a relative path is
# taken from this (backend) directory. Set it blank to print no logo.
STATEMENT_LOGO = env("STATEMENT_LOGO", "branding/zinmad-mark.png")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "drf_spectacular",
    # NOT rest_framework_simplejwt.token_blacklist: its migration 0008 alters an
    # int column to bigint, which SQL Server refuses while a unique constraint
    # depends on that column ("ALTER TABLE ALTER COLUMN token_id failed"). The test
    # database is built by running migrations, so a migration that cannot run on
    # this backend would break the entire suite. Revocation is in core instead —
    # see core.models.RevokedToken and User.token_version.
    "corsheaders",
    "core",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "lms_backend.urls"
WSGI_APPLICATION = "lms_backend.wsgi.application"
ASGI_APPLICATION = "lms_backend.asgi.application"

# The React production build (frontend/dist) is served from here when present,
# so one Django process can serve both the API and the SPA.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [FRONTEND_DIST] if FRONTEND_DIST.exists() else [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------- database
# SQL Server. Windows integrated authentication by default; set
# DB_TRUSTED_CONNECTION=0 plus DB_USER / DB_PASSWORD for a SQL login.
_extra_params = [f"TrustServerCertificate={'yes' if env_bool('DB_TRUST_SERVER_CERT', True) else 'no'}"]
if env_bool("DB_ENCRYPT", False):
    _extra_params.append("Encrypt=yes")

_db_options: dict = {
    "driver": env("DB_DRIVER", "ODBC Driver 17 for SQL Server"),
    "extra_params": ";".join(_extra_params),
    # Without this, every migration that adds a nullable column to a table with
    # rows is fine, but bulk inserts of many rows are much slower.
    "unicode_results": False,
}
if env_bool("DB_TRUSTED_CONNECTION", True):
    _db_options["trusted_connection"] = "yes"

DATABASES = {
    "default": {
        "ENGINE": "mssql",
        "NAME": env("DB_NAME", "LMS"),
        "HOST": env("DB_HOST", r"localhost\SQLEXPRESS"),
        "PORT": env("DB_PORT", ""),
        "USER": env("DB_USER", ""),
        "PASSWORD": env("DB_PASSWORD", ""),
        "OPTIONS": _db_options,
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
        "TEST": {"NAME": env("DB_TEST_NAME", "LMS_test")},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

# ---------------------------------------------------------------- auth
AUTH_USER_MODEL = "core.User"

# Ten characters, not six, and not the username or a number. This is a system
# through which money leaves a building; six characters is a few hours of offline
# guessing. Overridable because an existing deployment's users have to be able to
# keep signing in until they next change it — the validators only run on a SET.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": int(env("PASSWORD_MIN_LENGTH", "10"))}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    # user_attributes is spelled out because this project's User has `full_name`
    # rather than Django's first_name/last_name, so the default list would check
    # two fields that do not exist and miss the one that does.
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
     "OPTIONS": {"user_attributes": ("username", "full_name", "email")}},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # Not simplejwt's own class: this one rejects a token whose version claim
        # no longer matches the user's, which is what makes revocation immediate.
        "core.authentication.RevocableJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "EXCEPTION_HANDLER": "core.exceptions.detail_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Money stays a string on the wire ("1234.56"), never a float, so cents
    # survive the trip to the browser intact.
    "COERCE_DECIMAL_TO_STRING": True,
    "UNAUTHENTICATED_USER": None,
    # Rate limits. The login endpoint is the one worth throttling hard.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "login": env("THROTTLE_LOGIN", "20/min"),
        "inbound_payments": env("THROTTLE_INBOUND_PAYMENTS", "600/min"),
    },
}

SIMPLE_JWT = {
    # Thirty minutes, not eight hours. An access token cannot be revoked — it is
    # checked by signature alone — so its lifetime IS the window in which a
    # disabled user keeps working. Short is only tolerable because the client
    # silently renews it; before POST /api/auth/refresh existed, eight hours was
    # the only thing standing between a teller and being logged out mid-receipt.
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(env("REFRESH_TOKEN_EXPIRE_DAYS", "7"))),
    # Rotation is done by core.views.auth.refresh, which issues a new pair and
    # revokes the one presented through core.services.tokens. The simplejwt
    # settings that would do it (ROTATE_REFRESH_TOKENS / BLACKLIST_AFTER_ROTATION)
    # are only read by simplejwt's own views, which this project does not use.
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "SIGNING_KEY": env("JWT_SIGNING_KEY", "") or SECRET_KEY,
}

# ---------------------------------------------------------------- cors
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

# ---------------------------------------------------------------- i18n / static
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [FRONTEND_DIST / "assets"] if (FRONTEND_DIST / "assets").exists() else []

# Borrower KYC documents. Served by Django in development; put a web server or
# object store in front of this in production.
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
MAX_UPLOAD_BYTES = int(env("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
ALLOWED_UPLOAD_TYPES = env_list(
    "ALLOWED_UPLOAD_TYPES",
    "application/pdf,image/jpeg,image/png,image/webp,image/tiff,"
    "application/msword,"
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

# Account lockout after repeated bad passwords
LOGIN_MAX_ATTEMPTS = int(env("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(env("LOGIN_LOCKOUT_MINUTES", "15"))

# ---------------------------------------------------------------- api schema
SPECTACULAR_SETTINGS = {
    "TITLE": "Loan Management System API",
    "DESCRIPTION": (
        "Borrowers, loans, repayments, savings, a double-entry general ledger, IFRS 9 "
        "provisioning, funder borrowings and period close.\n\n"
        "**Money is a decimal string**, never a JSON number — `\"1234.56\"` — so cents "
        "survive the round trip. Parse it as a decimal, not a float.\n\n"
        "**Authenticate** with `POST /api/auth/login`, then send `Authorization: Bearer "
        "<access_token>`. Access tokens last 30 minutes; exchange the refresh token at "
        "`POST /api/auth/refresh` to renew. A refresh token is good for exactly one use.\n\n"
        "**409 Conflict** means the date falls in a closed accounting period, which is "
        "distinct from a 400 validation failure and needs a different response from a "
        "client: pick another date, or ask an administrator to reopen the period."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Enum names are generated from the choice values, which collide across models
    # (several have an `active`); naming them explicitly keeps the generated
    # clients readable instead of producing Status1, Status2, Status3.
    "ENUM_NAME_OVERRIDES": {
        "LoanStatusEnum": "core.models.LoanStatus.choices",
        "InstalmentStatusEnum": "core.models.InstalmentStatus.choices",
        "TxnTypeEnum": "core.models.TxnType.choices",
        "SavingsStatusEnum": "core.models.SavingsStatus.choices",
        "SavingsTxnTypeEnum": "core.models.SavingsTxnType.choices",
        "NotificationStatusEnum": "core.models.NotificationStatus.choices",
        "RoleEnum": "core.models.Role.choices",
        "AccountTypeEnum": "core.models.AccountType.choices",
        "PeriodStateEnum": "core.models.PeriodState.choices",
        "FacilityTxnTypeEnum": "core.models.FacilityTxnType.choices",
        "CapitalTxnTypeEnum": "core.models.CapitalTxnType.choices",
    },
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
}

# ---------------------------------------------------------------- messages
# How borrower reminders, arrears notices and receipts actually leave the
# building. See core/services/gateways.py.
#
# "console" by default on purpose: a development machine running against seeded
# data must not text real-looking phone numbers. Set MESSAGE_SMS_BACKEND=http and
# fill in MESSAGE_HTTP_URL to deliver for real.
MESSAGE_SMS_BACKEND = env("MESSAGE_SMS_BACKEND", "console")
MESSAGE_EMAIL_BACKEND = env("MESSAGE_EMAIL_BACKEND", "console")
MESSAGE_MAX_ATTEMPTS = int(env("MESSAGE_MAX_ATTEMPTS", "3"))
MESSAGE_FILE_PATH = env("MESSAGE_FILE_PATH", "")

# The generic HTTP gateway. Everything a provider needs is configuration, so
# swapping aggregator is an .env change rather than a code change.
MESSAGE_HTTP = {
    "url": env("MESSAGE_HTTP_URL", ""),
    "method": env("MESSAGE_HTTP_METHOD", "POST"),
    "format": env("MESSAGE_HTTP_FORMAT", "form"),       # form | json
    "to_field": env("MESSAGE_HTTP_TO_FIELD", "to"),
    "body_field": env("MESSAGE_HTTP_BODY_FIELD", "message"),
    "id_path": env("MESSAGE_HTTP_ID_PATH", ""),          # e.g. SMSMessageData.Recipients.0.messageId
    "timeout": int(env("MESSAGE_HTTP_TIMEOUT", "20")),
    # Fixed fields and headers, as "key=value" pairs separated by "|". Kept out of
    # the URL so an API key never lands in an access log.
    "extra": _pairs(env("MESSAGE_HTTP_FIELDS", "")),
    "headers": _pairs(env("MESSAGE_HTTP_HEADERS", "")),
}

# ---------------------------------------------------------------- credit bureau
# "none" by default: a lender without a bureau contract sees no button, not a
# pretend report. "demo" fabricates a deterministic report for rehearsals and
# tests; "http" asks a real bureau. See core/services/bureau.py.
BUREAU_BACKEND = env("BUREAU_BACKEND", "none")
BUREAU_VALID_DAYS = int(env("BUREAU_VALID_DAYS", "90"))
BUREAU_HTTP = {
    "url": env("BUREAU_HTTP_URL", ""),
    "method": env("BUREAU_HTTP_METHOD", "POST"),
    "id_field": env("BUREAU_HTTP_ID_FIELD", "national_id"),
    "timeout": int(env("BUREAU_HTTP_TIMEOUT", "20")),
    "extra": _pairs(env("BUREAU_HTTP_FIELDS", "")),
    "headers": _pairs(env("BUREAU_HTTP_HEADERS", "")),
    # Dotted paths to each figure in the bureau's JSON answer.
    "paths": {
        "score": env("BUREAU_HTTP_SCORE_PATH", "score"),
        "score_max": env("BUREAU_HTTP_SCORE_MAX_PATH", ""),
        "open_accounts": env("BUREAU_HTTP_OPEN_ACCOUNTS_PATH", "open_accounts"),
        "accounts_in_arrears": env("BUREAU_HTTP_ARREARS_PATH", "accounts_in_arrears"),
        "defaults": env("BUREAU_HTTP_DEFAULTS_PATH", "defaults"),
        "worst_days_in_arrears": env("BUREAU_HTTP_WORST_DAYS_PATH", "worst_days_in_arrears"),
        "total_exposure": env("BUREAU_HTTP_EXPOSURE_PATH", "total_exposure"),
        "reference": env("BUREAU_HTTP_REFERENCE_PATH", "reference"),
    },
}

# ---------------------------------------------------------------- incoming payments
# Mobile-money and bank notifications arrive at /api/payments/inbound/<provider>,
# signed with HMAC-SHA256 of the raw body in the header named below. Each provider
# has its own secret ("provider=secret|provider=secret"); a provider with no secret
# is refused, so nothing unsigned is ever posted. See core/services/inbound.py.
INBOUND_PAYMENT_SECRETS = _pairs(env("INBOUND_PAYMENT_SECRETS", ""))
INBOUND_PAYMENT_SIGNATURE_HEADER = env("INBOUND_PAYMENT_SIGNATURE_HEADER", "X-Signature")
# Dotted paths to each field in the provider's JSON, the same for every provider
# unless a provider's own are given as INBOUND_PAYMENT_PATHS_<PROVIDER>.
INBOUND_PAYMENT_PATHS = {
    "id": "id", "amount": "amount", "currency": "currency", "date": "date",
    "phone": "phone", "name": "name", "reference": "reference",
    **_pairs(env("INBOUND_PAYMENT_PATHS", "")),
}
INBOUND_PAYMENT_PROVIDER_PATHS = {
    key[len("INBOUND_PAYMENT_PATHS_"):].lower(): _pairs(value)
    for key, value in os.environ.items() if key.startswith("INBOUND_PAYMENT_PATHS_")
}

# Email, for the email channel when MESSAGE_EMAIL_BACKEND is "smtp".
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = int(env("EMAIL_TIMEOUT", "20"))
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@example.com")

# ---------------------------------------------------------------- production
# These only bite when DEBUG is off, so development is unaffected. Behind a
# reverse proxy that terminates TLS, the proxy must set X-Forwarded-Proto.
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", str(60 * 60 * 24 * 365)))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

    if SECRET_KEY.startswith("django-insecure") or len(SECRET_KEY) < 32:
        raise ImproperlyConfigured(
            "SECRET_KEY is the development placeholder. Set a long random value in "
            "backend/.env before running with DEBUG off. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(64))"')

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": env("SQL_LOG_LEVEL", "WARNING"), "handlers": ["console"],
                               "propagate": False},
    },
}
