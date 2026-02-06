from __future__ import annotations

import threading
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.apps import apps
from django.contrib import messages
from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_delete
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.template.response import TemplateResponse

from .models import AdminUndoSnapshot


_local = threading.local()


def _set_context(*, enabled: bool, user_id: int | None) -> None:
    _local.enabled = enabled
    _local.user_id = user_id
    if enabled:
        if not hasattr(_local, "pre_delete"):
            _local.pre_delete = {}
    else:
        _local.pre_delete = {}


def _is_enabled() -> bool:
    return bool(getattr(_local, "enabled", False))


def _to_jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        # Preserve tz when present.
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, date):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _serialize_instance(obj: Any) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Serialize a model instance into plain JSONable structures.

    - Stores FK values via field.attname (e.g. user_id) so restore can set IDs.
    - Captures auto-created M2M relations as lists of related PKs.
    """
    data: dict[str, Any] = {}
    m2m: dict[str, list[Any]] = {}

    for field in obj._meta.fields:
        name = getattr(field, "attname", field.name)
        try:
            data[name] = _to_jsonable(getattr(obj, name))
        except Exception:
            continue

    for m2m_field in obj._meta.many_to_many:
        try:
            # Only auto-created through tables (typical ManyToManyField).
            if not getattr(m2m_field.remote_field.through._meta, "auto_created", False):
                continue
            rel = getattr(obj, m2m_field.name)
            m2m[m2m_field.name] = list(rel.values_list("pk", flat=True))
        except Exception:
            continue

    return data, m2m


def set_request_context(request: HttpRequest, *, enabled: bool) -> None:
    user_id = None
    try:
        if enabled and getattr(request, "user", None) and request.user.is_authenticated:
            user_id = int(request.user.id)
    except Exception:
        user_id = None
    _set_context(enabled=enabled, user_id=user_id)


def _pre_delete_capture(sender, instance, **kwargs):
    if not _is_enabled():
        return

    # Avoid recursion / noise.
    if sender is LogEntry or sender is AdminUndoSnapshot:
        return

    try:
        ct = ContentType.objects.get_for_model(instance, for_concrete_model=False)
        object_id = str(instance.pk)
        snapshot, m2m = _serialize_instance(instance)
        _local.pre_delete[(ct.pk, object_id)] = {
            "content_type_id": ct.pk,
            "object_id": object_id,
            "object_repr": str(instance),
            "snapshot": snapshot,
            "m2m": m2m,
        }
    except Exception:
        # Best-effort only.
        return


def _logentry_post_save(sender, instance: LogEntry, created: bool, **kwargs):
    if not _is_enabled():
        return

    if not created:
        return

    if int(instance.action_flag) != int(DELETION):
        return

    try:
        ct_id = int(instance.content_type_id)
        object_id = str(instance.object_id)
    except Exception:
        return

    payload = None
    try:
        payload = _local.pre_delete.get((ct_id, object_id))
    except Exception:
        payload = None

    if not payload:
        return

    try:
        AdminUndoSnapshot.objects.create(
            log_entry=instance,
            content_type_id=ct_id,
            object_id=object_id,
            object_repr=payload.get("object_repr") or instance.object_repr,
            action_flag=instance.action_flag,
            snapshot=payload.get("snapshot") or {},
            m2m=payload.get("m2m") or {},
            created_by_id=getattr(_local, "user_id", None),
        )
    except Exception:
        return
    finally:
        try:
            del _local.pre_delete[(ct_id, object_id)]
        except Exception:
            pass


pre_delete.connect(_pre_delete_capture, dispatch_uid="cc_admin_undo_pre_delete")
post_save.connect(_logentry_post_save, sender=LogEntry, dispatch_uid="cc_admin_undo_logentry_post_save")


@staff_member_required
def admin_undo_logentry(request: HttpRequest, logentry_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect(reverse("admin:index"))

    try:
        entry = LogEntry.objects.select_related("content_type", "user").get(pk=int(logentry_id))
    except Exception:
        messages.error(request, "Undo failed: action not found.")
        return redirect(reverse("admin:index"))

    # Recent actions widget is per-user; keep this tight anyway.
    if entry.user_id != request.user.id and not getattr(request.user, "is_superuser", False):
        messages.error(request, "Undo failed: you can only undo your own recent actions.")
        return redirect(reverse("admin:index"))

    model = None
    try:
        model = entry.content_type.model_class() if entry.content_type else None
    except Exception:
        model = None

    if not model:
        messages.error(request, "Undo failed: unknown model type.")
        return redirect(reverse("admin:index"))

    app_label = entry.content_type.app_label
    model_name = entry.content_type.model

    # Undo ADDITION by deleting the created object.
    if int(entry.action_flag) == int(ADDITION):
        perm = f"{app_label}.delete_{model_name}"
        if not request.user.has_perm(perm):
            messages.error(request, "Undo failed: missing delete permission.")
            return redirect(reverse("admin:index"))

        try:
            obj = model._default_manager.filter(pk=entry.object_id).first()
            if not obj:
                messages.warning(request, "Nothing to undo: object no longer exists.")
                return redirect(reverse("admin:index"))
            obj.delete()
            messages.success(request, f"Undid creation: deleted {entry.object_repr}.")
            return redirect(reverse("admin:index"))
        except Exception as e:
            messages.error(request, f"Undo failed while deleting: {e}")
            return redirect(reverse("admin:index"))

    # Undo DELETION by restoring from snapshot.
    if int(entry.action_flag) == int(DELETION):
        perm = f"{app_label}.add_{model_name}"
        if not request.user.has_perm(perm):
            messages.error(request, "Undo failed: missing add permission to restore.")
            return redirect(reverse("admin:index"))

        snap = AdminUndoSnapshot.objects.filter(log_entry=entry).select_related("content_type").first()
        if not snap:
            messages.error(request, "Undo unavailable: no snapshot was captured for that delete.")
            return redirect(reverse("admin:index"))

        # If something already exists with that PK, don't clobber it.
        try:
            if model._default_manager.filter(pk=snap.object_id).exists():
                messages.error(request, "Undo failed: an object with that ID already exists.")
                return redirect(reverse("admin:index"))
        except Exception:
            pass

        try:
            instance = model()
            pk_attname = model._meta.pk.attname
            setattr(instance, pk_attname, snap.object_id)

            snapshot = snap.snapshot or {}
            for key, value in snapshot.items():
                if key == pk_attname:
                    continue
                try:
                    setattr(instance, key, value)
                except Exception:
                    continue

            instance.save(force_insert=True)

            # Restore M2M.
            m2m = snap.m2m or {}
            for field_name, pk_list in m2m.items():
                try:
                    getattr(instance, field_name).set(pk_list)
                except Exception:
                    continue

            messages.success(request, f"Restored deleted object: {snap.object_repr}.")
            return redirect(reverse("admin:index"))
        except Exception as e:
            messages.error(request, f"Undo failed while restoring: {e}")
            return redirect(reverse("admin:index"))

    messages.error(request, "Undo not supported for that action type (changes can’t be reversed safely).")
    return redirect(reverse("admin:index"))


@staff_member_required
def admin_undo_history(request: HttpRequest) -> HttpResponse:
    """Discoverable undo history inside Admin.

    Shows recent admin actions for the current staff user and indicates whether
    an undo is available.
    """
    qs = (
        LogEntry.objects.select_related("content_type", "user")
        .filter(user_id=request.user.id)
        .order_by("-action_time")
    )

    try:
        limit = int(request.GET.get("limit") or 50)
    except Exception:
        limit = 50
    if limit < 10:
        limit = 10
    if limit > 200:
        limit = 200

    entries = list(qs[:limit])
    snap_map = {}
    try:
        snap_map = {
            s.log_entry_id: True
            for s in AdminUndoSnapshot.objects.filter(log_entry_id__in=[e.id for e in entries]).only("log_entry_id")
        }
    except Exception:
        snap_map = {}

    rows = []
    for e in entries:
        can_undo = False
        reason = None
        if int(e.action_flag) == int(ADDITION):
            can_undo = True
        elif int(e.action_flag) == int(DELETION):
            can_undo = bool(snap_map.get(e.id))
            if not can_undo:
                reason = "No snapshot captured"
        else:
            reason = "Edits can’t be undone safely"

        rows.append(
            {
                "id": e.id,
                "action_time": e.action_time,
                "object_repr": e.object_repr,
                "content_type": e.content_type.name if e.content_type else "Unknown",
                "action_flag": e.action_flag,
                "can_undo": can_undo,
                "reason": reason,
            }
        )

    ctx = {
        "title": "Undo history",
        "rows": rows,
        "limit": limit,
    }
    return TemplateResponse(request, "admin/undo_history.html", ctx)
