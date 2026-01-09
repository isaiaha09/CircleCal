import datetime
from django.utils import timezone


def _format_dt_as_utc(dt):
    if dt is None:
        return ''
    if getattr(dt, 'tzinfo', None) is None:
        dt = timezone.make_aware(dt)
    dt_utc = dt.astimezone(datetime.timezone.utc)
    return dt_utc.strftime('%Y%m%dT%H%M%SZ')


def booking_to_ics_string(booking):
    """Return a string containing an iCalendar (.ics) file for a Booking."""
    uid = f"circlecal-booking-{getattr(booking, 'id', '0')}@circlecal"
    dtstamp = _format_dt_as_utc(timezone.now())
    dtstart = _format_dt_as_utc(getattr(booking, 'start', None))
    dtend = _format_dt_as_utc(getattr(booking, 'end', None))
    summary = (getattr(booking, 'service', None).name if getattr(booking, 'service', None) else getattr(booking, 'title', 'Booking'))
    org = getattr(booking, 'organization', None)
    location = getattr(org, 'name', '') if org else ''
    description_lines = []
    if getattr(booking, 'client_name', None):
        description_lines.append(f"Client: {booking.client_name}")
    if getattr(booking, 'client_email', None):
        description_lines.append(f"Email: {booking.client_email}")
    if getattr(booking, 'public_ref', None):
        description_lines.append(f"Ref: {booking.public_ref}")
    description = "\\n".join(description_lines)

    ics = [
        'BEGIN:VCALENDAR',
        'PRODID:-//CircleCal//EN',
        'VERSION:2.0',
        'CALSCALE:GREGORIAN',
        'BEGIN:VEVENT',
        f'UID:{uid}',
        f'DTSTAMP:{dtstamp}',
        f'DTSTART:{dtstart}',
        f'DTEND:{dtend}',
        f'SUMMARY:{summary}',
        f'DESCRIPTION:{description}',
        f'LOCATION:{location}',
        'END:VEVENT',
        'END:VCALENDAR',
    ]
    return "\r\n".join(ics)
