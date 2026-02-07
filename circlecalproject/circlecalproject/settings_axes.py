# Django Axes (Rate Limiting)
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25  # 15 minutes (in hours)
AXES_LOCKOUT_PARAMETERS = [['username', 'ip_address']]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = None
AXES_HANDLER = "circlecalproject.axes_handlers.PrivacyPreservingAxesDatabaseHandler"
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'axes.backends.AxesBackend',
]
