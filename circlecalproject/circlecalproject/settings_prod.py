from .settings import *

# Production-like defaults (safe for local testing)
DEBUG = False

# While developing without a domain, allow localhost
ALLOWED_HOSTS = ['www.circlecal.app','circlecal.app','127.0.0.1', 'localhost', 'nonpendant-profligately-tessa.ngrok-free.dev']
CSRF_TRUSTED_ORIGINS = ['https://circlecal.app', 'https://www.circlecal.app']

# Secure cookies (effective when served over HTTPS)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Redirect HTTP to HTTPS (toggle off locally if needed)
SECURE_SSL_REDIRECT = False

# HSTS (enable only behind HTTPS in real prod)
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'

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
