from accounts.models import Membership

def current_membership_role(request):
    """Expose the active membership role for the current request.organization as `user_org_role`.

    Returns None if no active membership exists or organization is not set.
    """
    role = None
    try:
        org = getattr(request, 'organization', None)
        if request.user.is_authenticated and org is not None:
            m = Membership.objects.filter(user=request.user, organization=org, is_active=True).first()
            if m:
                role = m.role
    except Exception:
        role = None

    return {'user_org_role': role}
