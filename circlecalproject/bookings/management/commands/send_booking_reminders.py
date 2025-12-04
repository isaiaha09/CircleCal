from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from bookings.models import Booking
from bookings.emails import send_booking_reminder


class Command(BaseCommand):
    help = 'Send reminder emails for bookings happening in the next 24 hours'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=24,
            help='Send reminders for bookings within this many hours (default: 24)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show which bookings would get reminders without sending emails',
        )

    def handle(self, *args, **options):
        hours = options['hours']
        dry_run = options['dry_run']
        
        now = timezone.now()
        start_time = now
        end_time = now + timedelta(hours=hours)
        
        # Find bookings in the reminder window that aren't blocking events
        upcoming_bookings = Booking.objects.filter(
            start__gte=start_time,
            start__lte=end_time,
            is_blocking=False,
        ).exclude(
            client_email=''
        ).select_related('organization', 'service')
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'DRY RUN: Would send {upcoming_bookings.count()} reminders')
            )
            for booking in upcoming_bookings:
                self.stdout.write(
                    f"  - {booking.client_email}: {booking.service.name if booking.service else booking.title} "
                    f"at {booking.start.strftime('%Y-%m-%d %H:%M')}"
                )
            return
        
        sent_count = 0
        failed_count = 0
        
        for booking in upcoming_bookings:
            if send_booking_reminder(booking):
                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Sent reminder to {booking.client_email}')
                )
            else:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(f'✗ Failed to send reminder to {booking.client_email}')
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSummary: {sent_count} sent, {failed_count} failed out of {upcoming_bookings.count()} total'
            )
        )
