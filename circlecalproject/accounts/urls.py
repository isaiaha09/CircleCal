from django.urls import path
from django.contrib.auth import views as auth_views
from two_factor.views import LoginView as TwoFactorLoginView
from .views import profile_view, delete_account_view
app_name = 'accounts'

# Use our custom templates under registration/
urlpatterns = [
    # Profile
    path('profile/', profile_view, name='profile'),
    
    # Delete account
    path('delete/', delete_account_view, name='delete_account'),

    # Login / Logout (use our styled template)
    path('login/', TwoFactorLoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/',
         auth_views.LogoutView.as_view(),
         name='logout'),

    path('password/reset/',
         auth_views.PasswordResetView.as_view(
             template_name='calendar_app/password_reset_form.html',
             email_template_name='calendar_app/password_reset_email.txt',
             subject_template_name='calendar_app/password_reset_subject.txt'
         ),
         name='password_reset'),

    # Aliases to catch default Django paths and ensure our custom branding is used
    path('password_reset/',
         auth_views.PasswordResetView.as_view(
             template_name='calendar_app/password_reset_form.html',
             email_template_name='calendar_app/password_reset_email.txt',
             subject_template_name='calendar_app/password_reset_subject.txt'
         ),
         name='password_reset_alias'),

    path('password/reset/done/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='calendar_app/password_reset_done.html'
         ),
         name='password_reset_done'),

    path('password_reset/done/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='calendar_app/password_reset_done.html'
         ),
         name='password_reset_done_alias'),

    path('reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='calendar_app/password_reset_confirm.html'
         ),
         name='password_reset_confirm'),

    path('reset/complete/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='calendar_app/password_reset_complete.html'
         ),
         name='password_reset_complete'),

    path('password_reset/complete/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='calendar_app/password_reset_complete.html'
         ),
         name='password_reset_complete_alias'),

    # Change password
    path('password/change/',
         auth_views.PasswordChangeView.as_view(
             template_name='accounts/password_change_form.html'
         ),
         name='password_change'),
    path('password/change/done/',
         auth_views.PasswordChangeDoneView.as_view(
             template_name='accounts/password_change_done.html'
         ),
         name='password_change_done'),
]
