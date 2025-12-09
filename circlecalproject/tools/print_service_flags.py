from bookings.models import Service
print('ID\tSLUG\tALLOW_ENDS_AFTER')
for s in Service.objects.all():
    print(f"{s.id}\t{s.slug}\t{s.allow_ends_after_availability}")
