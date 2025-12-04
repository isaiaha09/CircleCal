# calendar_app/utils.py
from accounts.models import Membership

def user_has_role(user, organization, allowed_roles):
    """
    Returns True if the user has an active Membership in the organization
    AND their role is inside allowed_roles (a list or tuple).
    """
    if not user.is_authenticated:
        return False

    if organization is None:
        return False

    try:
        membership = Membership.objects.get(
            user=user,
            organization=organization,
            is_active=True,
        )
    except Membership.DoesNotExist:
        return False

    return membership.role in allowed_roles
