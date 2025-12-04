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
# Set these in your .env file:
# BREVO_API_KEY=your_api_key_here
# BREVO_SMTP_USER=your_email@example.com
# BREVO_SMTP_PASSWORD=your_smtp_password
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND')
EMAIL_HOST = os.getenv('EMAIL_HOST')
EMAIL_PORT = int(os.getenv('EMAIL_PORT'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS')
EMAIL_HOST_USER = os.getenv('BREVO_SMTP_USER')
EMAIL_HOST_PASSWORD = os.getenv('BREVO_SMTP_PASSWORD')
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL')
