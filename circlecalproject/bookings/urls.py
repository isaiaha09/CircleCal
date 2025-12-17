from django.urls import path
from . import views

app_name = 'bookings'


urlpatterns = [
    # GET: list events for this org
    path('bus/<slug:org_slug>/events/', views.events, name='events'),

    # POST: create a single booking (API endpoint)
    path('bus/<slug:org_slug>/bookings/create/', views.create_booking, name='create_booking'),

    # POST: batch create bookings
    path('bus/<slug:org_slug>/bookings/batch_create/', views.batch_create, name='batch_create'),

    # POST: batch delete bookings
    path('bus/<slug:org_slug>/bookings/batch_delete/', views.batch_delete, name='batch_delete'),


    path("bus/<slug:org_slug>/service/<slug:service_slug>/", views.public_service_page, name="public_service_page"),
    # Backward-compatible alias used by existing templates
    path("bus/<slug:org_slug>/service/<slug:service_slug>/", views.public_service_page, name="services_page"),
    path("bus/<slug:org_slug>/service/<slug:service_slug>/booking_success/<int:booking_id>/", views.booking_success, name="booking_success"),
    path("api/<slug:org_slug>/<slug:service_slug>/book/", views.create_booking, name="create_service"),

    path("bus/<slug:org_slug>/", views.public_org_page, name="public_org_page"),

    # Public availability for FullCalendar:
    path(
        "bus/<slug:org_slug>/services/<slug:service_slug>/availability/",
        views.service_availability,
        name="service_availability"
    ),

    # Effective per-date service settings (returns frozen settings if a freeze exists)
    path(
        "bus/<slug:org_slug>/services/<slug:service_slug>/effective/",
        views.service_effective_settings,
        name="service_effective_settings"
    ),

    # Batch availability summary for a date range (returns {date: hasSlots} map)
    path(
        "bus/<slug:org_slug>/services/<slug:service_slug>/availability/batch/",
        views.batch_availability_summary,
        name="batch_availability_summary"
    ),

    # Public busy intervals for a date range (no auth): used by client to hide booked times
    path(
        "bus/<slug:org_slug>/busy/",
        views.public_busy,
        name="public_busy"
    ),

    # Public booking cancellation via signed token
    path("cancel/<int:booking_id>/", views.cancel_booking, name="cancel_booking"),
    # Public reschedule flow (displays reschedule landing page with link to booking UI)
    path("reschedule/<int:booking_id>/", views.reschedule_booking, name="reschedule_booking"),

]