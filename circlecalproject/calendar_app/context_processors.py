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
    cc_app_mode = bool(is_app_ua and (cc_app_param or cc_app_cookie))

    return {
        'cc_is_app_ua': is_app_ua,
        'cc_app_param': cc_app_param,
        'cc_app_cookie': cc_app_cookie,
        'cc_app_mode': cc_app_mode,
        'cc_app_platform': app_platform,
        'cc_app_ios': bool(app_platform == 'ios'),
        'cc_app_android': bool(app_platform == 'android'),
    }
