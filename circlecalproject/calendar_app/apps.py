from __future__ import annotations

from django.apps import AppConfig


class CalendarAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "calendar_app"

    def ready(self) -> None:
        # Register admin undo signals.
        from . import admin_undo  # noqa: F401
