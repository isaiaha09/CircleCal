from accounts.models import Membership


def navigation_context(request):
    """Provide a stable org context for the shared logged-in navbar."""
    nav_organization = getattr(request, 'organization', None)
    nav_user_org_role = None

    try:
        if request.user.is_authenticated:
            memberships = Membership.objects.filter(user=request.user, is_active=True).select_related('organization')
            membership = None

            if nav_organization is not None:
                membership = memberships.filter(organization=nav_organization).first()
            else:
                active_org_id = 0
                try:
                    active_org_id = int(request.session.get('cc_active_org_id') or 0)
                except Exception:
                    active_org_id = 0

                if active_org_id:
                    membership = memberships.filter(organization_id=active_org_id).first()
                if membership is None:
                    membership = memberships.order_by('id').first()
                if membership is not None:
                    nav_organization = membership.organization

            if membership is not None:
                nav_user_org_role = getattr(membership, 'role', None)
    except Exception:
        nav_organization = nav_organization or None
        nav_user_org_role = None

    return {
        'nav_organization': nav_organization,
        'nav_user_org_role': nav_user_org_role,
    }

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


def cc_app_context(request):
    """Expose app-webview related flags.

    We keep the logic out of templates to avoid Django template smart-if
    parsing limitations and to ensure consistent behavior across pages.
    """
    ua = (request.META.get('HTTP_USER_AGENT') or '')
    ua_lower = ua.lower()
    is_app_ua = 'circlecalapp' in ua_lower

    app_platform = None
    if 'circlecalapp-ios' in ua_lower:
        app_platform = 'ios'
    elif 'circlecalapp-android' in ua_lower:
        app_platform = 'android'

    cc_app_param = request.GET.get('cc_app') == '1'
    cc_app_cookie = request.COOKIES.get('cc_app') == '1'
    try:
        cc_app_flow = bool(request.session.get('cc_app_flow'))
    except Exception:
        cc_app_flow = False

    # UA is set by the in-app WebView. Query params and cookies are best-effort.
    cc_app_mode = bool(is_app_ua or cc_app_param or cc_app_cookie or cc_app_flow)

    return {
        'cc_is_app_ua': is_app_ua,
        'cc_app_param': cc_app_param,
        'cc_app_cookie': cc_app_cookie,
        'cc_app_mode': cc_app_mode,
        'cc_app_flow': cc_app_flow,
        'cc_app_platform': app_platform,
        'cc_app_ios': bool(app_platform == 'ios'),
        'cc_app_android': bool(app_platform == 'android'),
    }
