from django.db import models
from django.contrib.auth.hashers import make_password, check_password


class AdminPin(models.Model):
	"""Store a hashed admin PIN for gating the admin area.

	Keep only the latest entry; older rows are preserved for audit but the
	latest (highest id) is used.
	"""
	pin_hash = models.CharField(max_length=128)
	created_at = models.DateTimeField(auto_now=True)

	@classmethod
	def get_latest_hash(cls):
		obj = cls.objects.order_by('-id').first()
		return obj.pin_hash if obj else None

	@classmethod
	def set_pin(cls, raw_pin):
		h = make_password(raw_pin)
		return cls.objects.create(pin_hash=h)

	@classmethod
	def clear_pins(cls):
		cls.objects.all().delete()

	@classmethod
	def check_pin(cls, raw_pin):
		h = cls.get_latest_hash()
		if not h:
			return False
		try:
			return check_password(raw_pin, h)
		except Exception:
			return False
