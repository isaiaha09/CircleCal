from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0012_memberweeklyavailability"),
    ]

    operations = [
        migrations.CreateModel(
            name="FacilityResource",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField()),
                ("is_active", models.BooleanField(default=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="facility_resources",
                        to="accounts.business",
                    ),
                ),
            ],
            options={
                "unique_together": {("organization", "slug")},
            },
        ),
        migrations.CreateModel(
            name="ServiceResource",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "resource",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="service_links",
                        to="bookings.facilityresource",
                    ),
                ),
                (
                    "service",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="resource_links",
                        to="bookings.service",
                    ),
                ),
            ],
            options={
                "unique_together": {("service", "resource")},
            },
        ),
        migrations.AddField(
            model_name="booking",
            name="resource",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="bookings",
                to="bookings.facilityresource",
            ),
        ),
        migrations.AddIndex(
            model_name="facilityresource",
            index=models.Index(fields=["organization", "is_active"], name="bookings_fa_organiza_d55d7a_idx"),
        ),
        migrations.AddIndex(
            model_name="serviceresource",
            index=models.Index(fields=["service"], name="bookings_se_service_0d2d2f_idx"),
        ),
        migrations.AddIndex(
            model_name="serviceresource",
            index=models.Index(fields=["resource"], name="bookings_se_resourc_032caf_idx"),
        ),
    ]
