from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from two_factor.views import LoginView as TwoFactorLoginView
from .views import (
    profile_view,
    delete_account_view,
    deactivate_account_view,
    deactivate_confirm_view,
    delete_confirm_view,
    reactivate_account_view,
    reactivate_account_action,
    CustomLoginView,
    login_choice_view,
    StaffLoginView,
)
from .forms import PasswordResetIncludeInactiveForm
app_name = 'accounts'

# Use our custom templates under registration/
urlpatterns = [
    # Profile
    path('profile/', profile_view, name='profile'),
    
    # Delete account
    path('delete/', delete_account_view, name='delete_account'),
    # Deactivate account (soft)
    path('deactivate/', deactivate_account_view, name='deactivate_account'),
    # Confirm pages
    path('deactivate/confirm/', deactivate_confirm_view, name='deactivate_confirm'),
    path('delete/confirm/', delete_confirm_view, name='delete_confirm'),

    # Reactivate self-serve
    path('reactivate/', reactivate_account_view, name='reactivate'),
    path('reactivate/action/', reactivate_account_action, name='reactivate_action'),

    # Login / Logout (use our styled template)
    # Show a choice page that splits owner vs staff/manager login flows
    path('login/', login_choice_view, name='login'),
    path('login/owner/', CustomLoginView.as_view(template_name='registration/login_owner.html'), name='login_owner'),
    path('login/staff/', StaffLoginView.as_view(template_name='registration/login_staff.html'), name='login_staff'),
    path('logout/',
         auth_views.LogoutView.as_view(),
         name='logout'),

    path('password/reset/',
         auth_views.PasswordResetView.as_view(
                template_name='calendar_app/password_reset_form.html',
                email_template_name='calendar_app/password_reset_email.txt',
                subject_template_name='calendar_app/password_reset_subject.txt',
            form_class=PasswordResetIncludeInactiveForm,
                success_url=reverse_lazy('accounts:password_reset_done')
         ),
         name='password_reset'),

    # Aliases to catch default Django paths and ensure our custom branding is used
    path('password_reset/',
         auth_views.PasswordResetView.as_view(
                template_name='calendar_app/password_reset_form.html',
                email_template_name='calendar_app/password_reset_email.txt',
                subject_template_name='calendar_app/password_reset_subject.txt',
            form_class=PasswordResetIncludeInactiveForm,
                success_url=reverse_lazy('accounts:password_reset_done')
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
             template_name='calendar_app/password_reset_confirm.html',
             success_url=reverse_lazy('accounts:password_reset_complete')
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
