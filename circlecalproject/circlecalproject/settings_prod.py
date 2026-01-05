from .settings import *

import os
import sys
from urllib.parse import urlparse, parse_qs

# Production-like defaults (safe for local testing)
DEBUG = False

def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_csv(name: str, default_list: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return default_list
    items = [p.strip() for p in str(raw).split(",")]
    return [p for p in items if p]


# Hosts / CSRF must be explicit in production.
# You can override via env vars:
#   ALLOWED_HOSTS=www.circlecal.app,circlecal.app
#   CSRF_TRUSTED_ORIGINS=https://circlecal.app,https://www.circlecal.app
_allow_custom_domains = _env_bool("ALLOW_CUSTOM_DOMAINS", False)

# Canonical hosts are the hostnames you control directly.
# When ALLOW_CUSTOM_DOMAINS=1, we accept any Host at the Django layer and
# validate it in CustomDomainMiddleware against this list + verified custom domains.
CANONICAL_HOSTS = _env_csv(
    "CANONICAL_HOSTS",
    [
        "www.circlecal.app",
        "circlecal.app",
        # Local dev conveniences when running with production settings
        "127.0.0.1",
        "localhost",
        "[::1]",
    ],
)

if _allow_custom_domains:
    # Keep Django's host protection enabled; verified custom domains are
    # auto-allowed at runtime by CustomDomainMiddleware.
    ALLOWED_HOSTS = CANONICAL_HOSTS
else:
    ALLOWED_HOSTS = _env_csv("ALLOWED_HOSTS", CANONICAL_HOSTS)
CSRF_TRUSTED_ORIGINS = _env_csv(
    "CSRF_TRUSTED_ORIGINS",
    [
        "https://circlecal.app",
        "https://www.circlecal.app",
    ],
)


# --- Database (Render-friendly) ---
#
# If DATABASE_URL is provided (Render Postgres does this), switch to Postgres.
# Otherwise fall back to whatever base settings configured (SQLite for local).
_database_url = os.getenv("DATABASE_URL")
if _database_url:
    parsed = urlparse(_database_url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")

    # urlparse yields path like "/dbname"
    db_name = (parsed.path or "").lstrip("/")
    query = parse_qs(parsed.query or "")
    sslmode = (query.get("sslmode") or [None])[0]

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": db_name,
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        }
    }
    if sslmode:
        DATABASES["default"]["OPTIONS"] = {"sslmode": sslmode}

# Secure cookies (effective when served over HTTPS)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Lax")


# If deployed behind a reverse proxy (Render/Nginx), Django should trust
# X-Forwarded-Proto so it can correctly detect HTTPS.
if _env_bool("USE_X_FORWARDED_PROTO", True):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# Redirect HTTP to HTTPS.
# In real production, keep this enabled.
# When running the Django dev server (`manage.py runserver`) locally, default it
# to off so you don't get redirected to HTTPS (runserver does not serve HTTPS).
_is_runserver = any(arg == 'runserver' or arg.endswith('runserver') for arg in sys.argv)
SECURE_SSL_REDIRECT = _env_bool("SECURE_SSL_REDIRECT", False if _is_runserver else True)

# HSTS (only enable if your entire site is served over HTTPS).
# Defaults are "real prod" safe; for local runserver default to 0.
try:
    _hsts_default = "0" if _is_runserver else "31536000"
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", _hsts_default))
except Exception:
    SECURE_HSTS_SECONDS = 0 if _is_runserver else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = _env_bool("SECURE_HSTS_PRELOAD", False)

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'


# Static files: optional WhiteNoise (useful on platforms like Render)
_enable_whitenoise = _env_bool("ENABLE_WHITENOISE", True)
if _enable_whitenoise:
    try:
        import whitenoise  # noqa: F401

        # Use WhiteNoise's hashed+compressed static file storage in production.
        # For local runserver with settings_prod, avoid manifest storage because
        # it requires `collectstatic` and a manifest to exist.
        if _is_runserver:
            STORAGES = {
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                },
                "staticfiles": {
                    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
                },
            }
        else:
            STORAGES = {
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                },
                "staticfiles": {
                    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
                },
            }

        if isinstance(MIDDLEWARE, (list, tuple)):
            _mw = list(MIDDLEWARE)
            # Insert right after SecurityMiddleware when present.
            if "django.middleware.security.SecurityMiddleware" in _mw and "whitenoise.middleware.WhiteNoiseMiddleware" not in _mw:
                idx = _mw.index("django.middleware.security.SecurityMiddleware") + 1
                _mw.insert(idx, "whitenoise.middleware.WhiteNoiseMiddleware")
            elif "whitenoise.middleware.WhiteNoiseMiddleware" not in _mw:
                _mw.insert(0, "whitenoise.middleware.WhiteNoiseMiddleware")
            MIDDLEWARE = _mw

        # When running the dev server with production settings, static files
        # won't be served by Django because DEBUG=False. Tell WhiteNoise to use
        # Django's staticfiles finders so assets resolve without requiring an
        # explicit `collectstatic`.
        if _is_runserver:
            WHITENOISE_USE_FINDERS = True
            WHITENOISE_AUTOREFRESH = True
    except Exception:
        pass

# Use robust password validators from base settings
# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django.security': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': True,
        },
    },
}

# Email: Brevo SMTP configuration
# Set these in your .env file when using a real SMTP provider:
# BREVO_API_KEY=your_api_key_here
# BREVO_SMTP_USER=your_email@example.com
# BREVO_SMTP_PASSWORD=your_smtp_password
# Allow fallback to a safe local default when env vars are not present so
# running with `settings_prod` doesn't silently drop emails during testing.
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'localhost')
# Prefer port 587 for TLS-enabled SMTP by default
try:
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
except Exception:
    EMAIL_PORT = 587
# Normalize TLS flag from env var to boolean
_tls_raw = os.getenv('EMAIL_USE_TLS', 'True')
EMAIL_USE_TLS = str(_tls_raw).lower() in ('1', 'true', 'yes', 'on')
EMAIL_HOST_USER = os.getenv('BREVO_SMTP_USER')
EMAIL_HOST_PASSWORD = os.getenv('BREVO_SMTP_PASSWORD')
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', DEFAULT_FROM_EMAIL)

# If running with production settings but no SMTP credentials, log a warning so
# developers notice that emails will be printed to console (or not delivered).
import logging
if EMAIL_BACKEND == 'django.core.mail.backends.console.EmailBackend':
    logging.getLogger(__name__).warning('Using console email backend in settings_prod; password-reset emails will print to the runserver console.')

# Ensure .env is explicitly loaded from project root when using settings_prod
# (some runtimes may start Django with a different CWD, causing load_dotenv
# in base settings to miss the file). Also emit a short startup log so it's
# obvious what effective email settings are being used when the server starts.
from pathlib import Path
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / '.env'
    load_dotenv(env_path)
except Exception:
    pass

try:
    logging.getLogger(__name__).warning(
        'settings_prod email config: EMAIL_BACKEND=%r, EMAIL_HOST=%r, EMAIL_PORT=%r, EMAIL_USE_TLS=%r, EMAIL_HOST_USER=%r',
        EMAIL_BACKEND, EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS, bool(EMAIL_HOST_USER)
    )
except Exception:
    pass
