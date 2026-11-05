"""
Django settings for OyaWork.

Every deploy-specific value comes from environment variables, so the same code
runs locally (SQLite or a local Postgres), on Render (backend) and against
Supabase (Postgres + Storage). See docs/ENVIRONMENT.md for the full list.
"""
import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.getenv("SECRET_KEY") or (
    "django-insecure-local-dev-only-change-me" if DEBUG else None
)
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set when DEBUG is off")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
# Render sets this automatically for every web service.
if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_yasg",
    "core",
    "accounts",
    "jobs",
    "bookings",
    "payments",
    "tracking",
    "reports",
    "reviews",
    "messaging",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "OyaWork.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "OyaWork.wsgi.application"


# Database
# Production: DATABASE_URL (the Supabase pooler connection string).
# Local: DATABASE_URL, or the original DB_NAME/DB_USER/... variables, or SQLite.
def _database_config():
    url = os.getenv("DATABASE_URL")
    if not url and os.getenv("DB_NAME"):
        url = "postgres://{user}:{password}@{host}:{port}/{name}".format(
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            name=os.getenv("DB_NAME"),
        )
    if not url:
        url = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    is_postgres = url.startswith(("postgres://", "postgresql://"))
    config = dj_database_url.parse(
        url,
        conn_max_age=int(os.getenv("DB_CONN_MAX_AGE", "60")),
        conn_health_checks=True,
        ssl_require=is_postgres and env_bool("DB_SSL_REQUIRE", not DEBUG),
    )
    # Supabase's transaction pooler (port 6543) does not support server-side cursors.
    if is_postgres and env_bool("DB_USE_TRANSACTION_POOLER", ":6543" in url):
        config["DISABLE_SERVER_SIDE_CURSORS"] = True
        config["CONN_MAX_AGE"] = 0
    return config


DATABASES = {"default": _database_config()}

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Static files are served by WhiteNoise (admin + Swagger assets).
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"

# Uploaded files (profile photos, ID documents, job evidence).
# When the Supabase S3 variables are present, files go to Supabase Storage;
# otherwise they are written to MEDIA_ROOT (fine locally, NOT on Render, whose disk is wiped on deploy).
SUPABASE_S3_ENDPOINT = os.getenv("SUPABASE_S3_ENDPOINT")
if SUPABASE_S3_ENDPOINT:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "endpoint_url": SUPABASE_S3_ENDPOINT,
                "region_name": os.getenv("SUPABASE_S3_REGION", "eu-west-2"),
                "access_key": os.getenv("SUPABASE_S3_ACCESS_KEY_ID"),
                "secret_key": os.getenv("SUPABASE_S3_SECRET_ACCESS_KEY"),
                "bucket_name": os.getenv("SUPABASE_STORAGE_BUCKET", "oyawork-media"),
                "signature_version": "s3v4",
                "addressing_style": "path",
                "file_overwrite": False,
                # Bucket stays private; the API hands out short-lived signed URLs.
                "querystring_auth": True,
                "querystring_expire": 3600,
                "default_acl": None,
            },
        },
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }

DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

# CORS / CSRF: the Vercel frontend calls this API with a bearer token.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
CORS_ALLOWED_ORIGIN_REGEXES = env_list("CORS_ALLOWED_ORIGIN_REGEXES")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^api/health/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
    SECURE_CONTENT_TYPE_NOSNIFF = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": None,
    "DEFAULT_THROTTLE_RATES": {"auth": "20/min", "otp": "5/min"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "60"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "14"))),
    "ROTATE_REFRESH_TOKENS": False,
    "UPDATE_LAST_LOGIN": True,
}

SWAGGER_SETTINGS = {
    "SECURITY_DEFINITIONS": {
        "Bearer": {"type": "apiKey", "name": "Authorization", "in": "header"}
    },
    "USE_SESSION_AUTH": False,
}

# Product switches -----------------------------------------------------------
# Mark a profile "active" as soon as the person submits identity + address
# documents. Turn off once an admin reviews documents in /admin.
AUTO_APPROVE_VERIFICATION = env_bool("AUTO_APPROVE_VERIFICATION", True)
# Without a Paystack key, card / bank transfer payments are simulated so the
# whole flow can be tested end to end. Never leave this on with real users.
DEMO_PAYMENTS = env_bool("DEMO_PAYMENTS", True)
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
# SMS OTP for phone verification. Without a Termii key the code is returned in
# the API response when DEMO_OTP is on (for testing only).
TERMII_API_KEY = os.getenv("TERMII_API_KEY", "")
TERMII_SENDER_ID = os.getenv("TERMII_SENDER_ID", "OyaWork")
# "dnd" reaches Nigerian numbers on Do-Not-Disturb (recommended for codes); "generic" is cheaper but may not deliver to them.
TERMII_CHANNEL = os.getenv("TERMII_CHANNEL", "dnd")
DEMO_OTP = env_bool("DEMO_OTP", True)
# Automatic NIN + selfie matching through Dojah. Leave the keys empty to review IDs by hand.
DOJAH_APP_ID = os.getenv("DOJAH_APP_ID", "")
DOJAH_SECRET_KEY = os.getenv("DOJAH_SECRET_KEY", "")
# https://api.dojah.io for live checks; https://sandbox.dojah.io with sandbox keys.
DOJAH_BASE_URL = os.getenv("DOJAH_BASE_URL", "https://api.dojah.io")
# Minimum face-match score (50-100) for a selfie to count as the NIN holder.
DOJAH_SELFIE_THRESHOLD = int(os.getenv("DOJAH_SELFIE_THRESHOLD", "90"))
# Percentage OyaWork keeps from each released escrow (0 = none).
PLATFORM_FEE_PERCENT = float(os.getenv("PLATFORM_FEE_PERCENT", "0"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
