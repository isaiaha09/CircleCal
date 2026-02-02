from django.urls import path
from . import views
from django.views.generic import TemplateView

app_name = 'calendar_app'

urlpatterns = [

    path("", views.home, name="home"),
    path("contact/", views.contact, name="contact"),
    path("about/", views.about, name="about"),
    path("terms/", TemplateView.as_view(template_name="calendar_app/terms.html"), name="terms"),
    path("privacy/", TemplateView.as_view(template_name="calendar_app/privacy.html"), name="privacy"),
    path("plans/<slug:plan_slug>/", views.plan_detail, name="plan_detail"),
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
    path('bus/<slug:org_slug>/billing-unavailable/', views.app_billing_unavailable, name="app_billing_unavailable"),

    # Services
    path('bus/<slug:org_slug>/services/', views.services_page, name="services_page"),
    path('bus/<slug:org_slug>/services/create/', views.create_service, name="create_service"),
    path('bus/<slug:org_slug>/services/availability-constraints/', views.service_availability_constraints, name='service_availability_constraints'),
    path('bus/<slug:org_slug>/services/<int:service_id>/edit/', views.edit_service, name="edit_service"),
    path('bus/<slug:org_slug>/services/<int:service_id>/delete/', views.delete_service, name="delete_service"),
    path('bus/<slug:org_slug>/services/<int:service_id>/update_settings/', views.update_service_settings, name='update_service_settings'),
    path('bus/<slug:org_slug>/services/<int:service_id>/preview_update/', views.preview_service_update, name='preview_service_update'),
    path('bus/<slug:org_slug>/services/<int:service_id>/apply_update/', views.apply_service_update, name='apply_service_update'),

    # Facility Resources (owner-managed cages/rooms)
    path('bus/<slug:org_slug>/resources/', views.resources_page, name='resources_page'),
    path('bus/<slug:org_slug>/resources/<int:resource_id>/edit/', views.edit_resource, name='edit_resource'),
    path('bus/<slug:org_slug>/resources/<int:resource_id>/toggle/', views.toggle_resource_active, name='toggle_resource_active'),

    # Bookings
    path('bus/<slug:org_slug>/bookings/', views.bookings_list, name="bookings_list"),
    path('bus/<slug:org_slug>/bookings/<int:booking_id>/delete/', views.delete_booking, name="delete_booking"),
    path('bus/<slug:org_slug>/bookings/bulk_delete/', views.bulk_delete_bookings, name='bulk_delete_bookings'),
    # Audit endpoints for owner-facing audit snippets
    path('bus/<slug:org_slug>/bookings/audit/', views.bookings_audit_list, name='bookings_audit_list'),
    path('bus/<slug:org_slug>/bookings/audit/export/', views.bookings_audit_export, name='bookings_audit_export'),
    path('bus/<slug:org_slug>/bookings/audit/delete/', views.bookings_audit_delete, name='bookings_audit_delete'),
    path('bus/<slug:org_slug>/bookings/audit/undo/', views.bookings_audit_undo, name='bookings_audit_undo'),
    path('bus/<slug:org_slug>/bookings/<int:booking_id>/audit/', views.bookings_audit_for_booking, name='bookings_audit_for_booking'),
    path('bus/<slug:org_slug>/bookings/<int:booking_id>/payment/', views.booking_payment_details, name='booking_payment_details'),
    path('bus/<slug:org_slug>/bookings/recent/', views.bookings_recent, name='bookings_recent'),

    # Dashboard (org-specific)
    path('bus/<slug:org_slug>/dashboard/', views.dashboard, name="dashboard"),

    # Org refund settings
    path('bus/<slug:org_slug>/settings/refunds/', views.org_refund_settings, name="org_refund_settings"),
    path('bus/<slug:org_slug>/settings/domain/', views.org_custom_domain_settings, name="org_custom_domain_settings"),


]

