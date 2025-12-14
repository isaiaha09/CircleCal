# Generated migration to add public_ref to Booking and backfill existing rows
from django.db import migrations, models
import secrets

ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def generate_ref(n=8):
    return ''.join(secrets.choice(ALPHABET) for _ in range(n))


def forwards(apps, schema_editor):
    Booking = apps.get_model('bookings', 'Booking')
    # Backfill existing bookings
    for b in Booking.objects.filter(public_ref__isnull=True):
        # try a few times per row
        for _ in range(8):
            cand = generate_ref(8)
            if not Booking.objects.filter(public_ref=cand).exists():
                b.public_ref = cand
                b.save(update_fields=['public_ref'])
                break


def backwards(apps, schema_editor):
    Booking = apps.get_model('bookings', 'Booking')
    Booking.objects.filter(public_ref__isnull=False).update(public_ref=None)


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0007_auditbooking'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='public_ref',
            field=models.CharField(blank=True, db_index=True, help_text='Public booking reference shown to clients', max_length=16, null=True, unique=True),
        ),
        migrations.RunPython(forwards, backwards),
    ]
