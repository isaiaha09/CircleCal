from django.template.loader import render_to_string
from bookings.models import Service
s = Service.objects.get(id=22)
print('service flag raw:', s.allow_ends_after_availability)
ctx = {'org': s.organization, 'service': s, 'needs_migration': False}
html = render_to_string('calendar_app/edit_service.html', ctx)
idx = html.find('name="allow_ends_after_availability"')
print('found index', idx)
print(html[idx-300:idx+300])
