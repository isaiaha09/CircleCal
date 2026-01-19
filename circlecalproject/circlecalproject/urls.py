"""
URL configuration for circlecalproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, reverse_lazy
from django.conf import settings
from django.conf.urls.static import static
from django.urls import re_path
from django.views.generic.base import RedirectView
from django.contrib.auth import views as auth_views
from calendar_app import pwa_views
try:
    # Import module to access urlpatterns directly for namespacing
    from two_factor import urls as two_factor_urls
    # Normalize patterns: some versions expose urlpatterns as (app_name, patterns)
    _tf_urlpatterns = getattr(two_factor_urls, 'urlpatterns', None)
    if isinstance(_tf_urlpatterns, tuple) and len(_tf_urlpatterns) == 2:
        if isinstance(_tf_urlpatterns[0], (list, tuple)):
            TF_PATTERNS = _tf_urlpatterns[0]
        elif isinstance(_tf_urlpatterns[1], (list, tuple)):
            TF_PATTERNS = _tf_urlpatterns[1]
        else:
            TF_PATTERNS = _tf_urlpatterns
    else:
        TF_PATTERNS = _tf_urlpatterns
except Exception:
    two_factor_urls = None
    TF_PATTERNS = None

urlpatterns = [
    path('manifest.webmanifest', pwa_views.manifest_webmanifest, name='pwa_manifest'),
    path('manifest.json', pwa_views.manifest_json, name='pwa_manifest_json'),
    path('sw.js', pwa_views.service_worker, name='pwa_service_worker'),
    path('offline/', pwa_views.offline_page, name='pwa_offline'),
    # Top-level aliases for branded password reset (override admin defaults)
    path('password/reset/',
         auth_views.PasswordResetView.as_view(
             template_name='calendar_app/password_reset_form.html',
             email_template_name='calendar_app/password_reset_email.txt',
             subject_template_name='calendar_app/password_reset_subject.txt',
             success_url=reverse_lazy('password_reset_done_root')
         ),
         name='password_reset_root'),
    path('password_reset/',
         auth_views.PasswordResetView.as_view(
             template_name='calendar_app/password_reset_form.html',
             email_template_name='calendar_app/password_reset_email.txt',
             subject_template_name='calendar_app/password_reset_subject.txt'
         ),
         name='password_reset_root_alias'),
    path('password/reset/done/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='calendar_app/password_reset_done.html'
         ),
         name='password_reset_done_root'),
    path('reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='calendar_app/password_reset_confirm.html',
             success_url=reverse_lazy('password_reset_complete_root')
         ),
         name='password_reset_confirm_root'),
    path('reset/complete/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='calendar_app/password_reset_complete.html'
         ),
         name='password_reset_complete_root'),

    # Admin (optionally obscured via settings.ADMIN_PATH)
    path(f"{getattr(settings, 'ADMIN_PATH', 'admin')}/pin/", include('calendar_app.urls_admin_pin')),
    path(f"{getattr(settings, 'ADMIN_PATH', 'admin')}/", admin.site.urls),
    # Organization-scoped booking endpoints (API first so they take precedence)
    path('', include('bookings.urls')),
    path('', include('calendar_app.urls')),
    path('billing/', include('billing.urls')),
    # Backward-compatible redirects from /org/... to /bus/...
    re_path(r'^org/(?P<rest>.*)$', RedirectView.as_view(url='/bus/%(rest)s', permanent=True)),

    # Branded auth routes (password reset) using our custom templates
    path('accounts/', include('accounts.urls')),
    # Two-Factor Authentication under /accounts/...
    path('accounts/', include('accounts.twofactor_urls', namespace='two_factor')),
    # Back-compat redirects for older '/account/...' paths that the package uses
    re_path(r'^accounts/2fa/(?P<rest>.*)$', RedirectView.as_view(url='/accounts/two_factor/%(rest)s', permanent=True)),
    re_path(r'^account/(?P<rest>.*)$', RedirectView.as_view(url='/accounts/%(rest)s', permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
