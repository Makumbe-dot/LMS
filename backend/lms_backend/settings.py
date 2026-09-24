"""Django settings for the Loan Management System.

Configuration comes from backend/.env (see .env.example). The database is
SQL Server, reached through the mssql-django backend and the Microsoft ODBC
driver, so the schema is manageable from SQL Server Management Studio.
"""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


def env_list(key: str, default: str = "") -> list[str]:
    return [p.strip() for p in env(key, default).split(",") if p.strip()]


# ---------------------------------------------------------------- core
SECRET_KEY = env("SECRET_KEY", "django-insecure-change-me-in-production")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

APP_NAME = env("APP_NAME", "Loan Management System")
CURRENCY = env("CURRENCY", "USD")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
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

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "EXCEPTION_HANDLER": "core.exceptions.detail_exception_handler",
    # Money stays a string on the wire ("1234.56"), never a float, so cents
    # survive the trip to the browser intact.
    "COERCE_DECIMAL_TO_STRING": True,
    "UNAUTHENTICATED_USER": None,
    # Rate limits. The login endpoint is the one worth throttling hard.
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "login": env("THROTTLE_LOGIN", "20/min"),
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("ACCESS_TOKEN_EXPIRE_MINUTES", str(60 * 8)))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": False,
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
