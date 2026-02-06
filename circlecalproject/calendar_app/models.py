from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
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


class AdminUndoSnapshot(models.Model):
	"""Snapshot captured for admin deletes so they can be undone.

	This is intentionally limited to restoring *deletions*.
	Django's built-in LogEntry does not store prior values for changes.
	"""
	log_entry = models.OneToOneField(LogEntry, on_delete=models.CASCADE, related_name="undo_snapshot")
	content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
	object_id = models.TextField()
	object_repr = models.TextField(blank=True)
	action_flag = models.PositiveSmallIntegerField()
	snapshot = models.JSONField(default=dict)
	m2m = models.JSONField(default=dict)
	created_at = models.DateTimeField(auto_now_add=True)
	created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

	class Meta:
		indexes = [
			models.Index(fields=["content_type", "object_id"]),
			models.Index(fields=["created_at"]),
		]

	def __str__(self) -> str:
		return f"Undo snapshot for {self.content_type.app_label}.{self.content_type.model} id={self.object_id}"
