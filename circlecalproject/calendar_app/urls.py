from django.urls import path
from . import views
from django.views.generic import TemplateView

app_name = 'calendar_app'

urlpatterns = [

    path("", TemplateView.as_view(template_name="calendar_app/index.html"), name="home"),
    path("demo/", views.demo_calendar_view, name="demo_calendar"),

    path('bus/<slug:org_slug>/calendar/', views.calendar_view, name="calendar"),
    path('bus/<slug:slug>/availability/save/', views.save_availability, name='save_availability_org'),
    path('coach/<slug:slug>/availability/save/', views.save_availability, name='save_availability'),
    path('api/availability/save/', views.save_availability_general, name='save_availability_general'),

    path("create-business/", views.create_business, name="create_business"),
    path("choose-business/", views.choose_business, name="choose_business"),
    path("bus/<slug:org_slug>/edit/", views.edit_business, name="edit_business"),
    path("bus/<slug:org_slug>/delete/", views.delete_business, name="delete_business"),


    path("signup/", views.signup, name="signup"),
    path("logout/", views.logout, name="logout"),
    path("post-login/", views.post_login_redirect, name="post_login"),


    # TEAM DASHBOARD
    path("bus/<slug:org_slug>/team/", views.team_dashboard, name="team_dashboard"),
    path("bus/<slug:org_slug>/team/invite/", views.invite_member, name="invite_member"),
    path("bus/<slug:org_slug>/team/remove/<int:member_id>/", views.remove_member, name="remove_member"),
    path("bus/<slug:org_slug>/team/update-role/<int:member_id>/", views.update_member_role, name="update_member_role"),

    # Invite acceptance
    path("invite/<str:token>/", views.accept_invite, name="accept_invite"),

    # Pricing page
    path('bus/<slug:org_slug>/pricing/', views.pricing_page, name="pricing_page"),

    # Services
    path('bus/<slug:org_slug>/services/', views.services_page, name="services_page"),
    path('bus/<slug:org_slug>/services/create/', views.create_service, name="create_service"),
    path('bus/<slug:org_slug>/services/<int:service_id>/edit/', views.edit_service, name="edit_service"),
    path('bus/<slug:org_slug>/services/<int:service_id>/delete/', views.delete_service, name="delete_service"),

    # Bookings
    path('bus/<slug:org_slug>/bookings/', views.bookings_list, name="bookings_list"),
    path('bus/<slug:org_slug>/bookings/<int:booking_id>/delete/', views.delete_booking, name="delete_booking"),

    # Dashboard (org-specific)
    path('bus/<slug:org_slug>/dashboard/', views.dashboard, name="dashboard"),

    # Org refund settings
    path('bus/<slug:org_slug>/settings/refunds/', views.org_refund_settings, name="org_refund_settings"),


]

