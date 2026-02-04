from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0023_orgsettings_staff_booking_push_notifications_enabled"),
    ]

    operations = [
        migrations.RenameField(
            model_name="orgsettings",
            old_name="staff_booking_push_notifications_enabled",
            new_name="owner_receives_staff_booking_push_notifications_enabled",
        ),
    ]
