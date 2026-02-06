from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("calendar_app", "0003_merge"),
        ("admin", "0001_initial"),
        ("contenttypes", "0002_remove_content_type_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminUndoSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_id", models.TextField()),
                ("object_repr", models.TextField(blank=True)),
                ("action_flag", models.PositiveSmallIntegerField()),
                ("snapshot", models.JSONField(default=dict)),
                ("m2m", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "content_type",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="contenttypes.contenttype"),
                ),
                (
                    "created_by",
                    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL),
                ),
                (
                    "log_entry",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="undo_snapshot", to="admin.logentry"),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["content_type", "object_id"], name="calendar_app_content_2a4088_idx"),
                    models.Index(fields=["created_at"], name="calendar_app_created__c60c5c_idx"),
                ],
            },
        ),
    ]
