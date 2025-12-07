from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path("bus/<slug:org_slug>/checkout/<int:plan_id>/", views.create_checkout_session, name="create_checkout_session"),
    path("bus/<slug:org_slug>/portal/", views.billing_portal, name="billing_portal"),
    path("webhook/", views.stripe_webhook, name="stripe_webhook"),
    # Embedded Payment Element routes
    path("bus/<slug:org_slug>/embedded/<int:plan_id>/", views.embedded_checkout_page, name="embedded_checkout_page"),
    path("api/bus/<slug:org_slug>/embedded/<int:plan_id>/create", views.create_embedded_subscription, name="create_embedded_subscription"),
    # Billing management (custom embedded portal replacement)
    path("bus/<slug:org_slug>/manage/", views.manage_billing, name="manage_billing"),
    path("api/bus/<slug:org_slug>/payment_method/setup_intent/", views.create_setup_intent, name="create_setup_intent"),
    path("api/bus/<slug:org_slug>/payment_method/default/", views.set_default_payment_method, name="set_default_payment_method"),
    path("api/bus/<slug:org_slug>/payment_method/delete/", views.delete_payment_method, name="delete_payment_method"),
    path("api/bus/<slug:org_slug>/payment_method/list/", views.list_payment_methods, name="list_payment_methods"),
    path("api/bus/<slug:org_slug>/subscription/cancel/", views.cancel_subscription, name="cancel_subscription"),
    path("api/bus/<slug:org_slug>/subscription/reactivate/", views.reactivate_subscription, name="reactivate_subscription"),
    path("api/bus/<slug:org_slug>/subscription/resubscribe/", views.resubscribe_subscription, name="resubscribe_subscription"),
    path("api/bus/<slug:org_slug>/subscription/change_plan/<int:plan_id>/", views.change_subscription_plan, name="change_subscription_plan"),
    path("api/bus/<slug:org_slug>/subscription/preview_change/<int:plan_id>/", views.preview_plan_change, name="preview_plan_change"),
    path("api/bus/<slug:org_slug>/subscription/cancel_scheduled/", views.cancel_scheduled_change, name="cancel_scheduled_change"),
    # Invoice actions
    path("api/bus/<slug:org_slug>/invoice/<str:invoice_id>/hide/", views.invoice_hide, name="invoice_hide"),
    path("api/bus/<slug:org_slug>/invoice/<str:invoice_id>/void/", views.invoice_void, name="invoice_void"),
    path("api/bus/<slug:org_slug>/invoice/<str:invoice_id>/unhide/", views.invoice_unhide, name="invoice_unhide"),
]