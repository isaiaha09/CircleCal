from functools import wraps
from django.http import HttpResponseForbidden
from accounts.models import Membership

def require_roles(roles):
    """
    Decorator ensuring user has one of the required roles
    for the current request.organization.
    """
    if isinstance(roles, str):
        roles = [roles]

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            org = getattr(request, "organization", None)

            if not org:
                return HttpResponseForbidden("No organization found.")

            from calendar_app.utils import user_has_role
            if not user_has_role(request.user, org, roles):
                return HttpResponseForbidden("You do not have permission for this action.")

            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
