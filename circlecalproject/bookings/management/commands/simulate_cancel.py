from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from bookings.models import Booking, AuditBooking
from accounts.models import Business


class Command(BaseCommand):
    help = "Simulate a client cancellation by creating an AuditBooking with event_type 'cancelled' for an upcoming booking. Does not delete the booking."

    def add_arguments(self, parser):
        parser.add_argument('--org', dest='org', help='Organization slug (optional)', required=False)
        parser.add_argument('--booking', dest='booking_id', type=int, help='Booking id to mark cancelled (optional)')

    def handle(self, *args, **options):
        slug = options.get('org')
        bid = options.get('booking_id')

        if slug:
            try:
                org = Business.objects.get(slug=slug)
            except Business.DoesNotExist:
                raise CommandError(f"Organization with slug '{slug}' not found")
        else:
            org = Business.objects.first()
            if not org:
                raise CommandError('No organizations found; create one or pass --org')

        if bid:
            try:
                booking = Booking.objects.get(id=bid, organization=org)
            except Booking.DoesNotExist:
                raise CommandError(f'Booking id {bid} not found for org {org.slug}')
        else:
            # pick the first upcoming booking for the org
            now = timezone.now()
            booking = Booking.objects.filter(organization=org, is_blocking=False, start__gt=now).order_by('start').first()
            if not booking:
                raise CommandError('No upcoming bookings found for this org')

        # build snapshot
        snapshot = {
            'id': booking.id,
            'title': getattr(booking, 'title', None),
            'start': booking.start.isoformat() if getattr(booking, 'start', None) else None,
            'end': booking.end.isoformat() if getattr(booking, 'end', None) else None,
            'client_name': getattr(booking, 'client_name', None),
            'client_email': getattr(booking, 'client_email', None),
            'is_blocking': bool(getattr(booking, 'is_blocking', False)),
            'service_id': booking.service.id if getattr(booking, 'service', None) else None,
            'service_slug': booking.service.slug if getattr(booking, 'service', None) else None,
            'created_at': booking.created_at.isoformat() if getattr(booking, 'created_at', None) else None,
        }

        ab = AuditBooking.objects.create(
            organization=org,
            booking_id=booking.id,
            event_type=AuditBooking.EVENT_CANCELLED,
            booking_snapshot=snapshot,
            service=booking.service if getattr(booking, 'service', None) else None,
            start=booking.start if getattr(booking, 'start', None) else None,
            end=booking.end if getattr(booking, 'end', None) else None,
            client_name=booking.client_name or '',
            client_email=booking.client_email or '',
        )

        self.stdout.write(self.style.SUCCESS(f'Created cancelled audit id={ab.id} for booking id={booking.id}'))
