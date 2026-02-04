from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from accounts.models import Business, Membership
from bookings.models import AuditBooking, Booking

try:
    from rest_framework.exceptions import ValidationError
    from rest_framework.permissions import IsAuthenticated
    from rest_framework.response import Response
    from rest_framework.views import APIView
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Django REST Framework is required for API views. "
        "Install 'djangorestframework' and ensure 'rest_framework' is in INSTALLED_APPS."
    ) from exc


def _get_org_and_membership(*, user, org_param: str | None):
    if not org_param:
        raise ValidationError({"org": "This query param is required (org slug or id)."})

    org: Business | None
    if str(org_param).isdigit():
        org = Business.objects.filter(id=int(org_param)).first()
    else:
        org = Business.objects.filter(slug=str(org_param)).first()

    if not org:
        raise ValidationError({"org": "Unknown organization."})

    membership = Membership.objects.filter(user=user, organization=org, is_active=True).first()
    if not membership:
        # Do not leak existence details beyond the org param validation above.
        raise ValidationError({"detail": "You do not have access to this organization."})

    return org, membership


def _parse_from_to(*, from_raw: str | None, to_raw: str | None, org_tz: ZoneInfo):
    """Parse optional from/to into aware datetimes in org timezone.

    - Accepts ISO datetimes or YYYY-MM-DD.
    - Date inputs are interpreted as whole-day bounds: [from, to).
    """

    def _parse_one(raw: str | None) -> datetime | None:
        if not raw:
            return None
        dt = parse_datetime(raw)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=org_tz)
            return dt.astimezone(org_tz)

        d = parse_date(raw)
        if d is not None:
            return datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=org_tz)

        raise ValidationError({"detail": f"Invalid datetime/date: {raw}"})

    from_dt = _parse_one(from_raw)
    to_dt = _parse_one(to_raw)

    # If `to` was provided as a date, interpret as start-of-day; caller treats as exclusive.
    return from_dt, to_dt


def _serialize_booking_list_item(b: Booking):
    svc = getattr(b, "service", None)
    au = getattr(b, "assigned_user", None)
    return {
        "id": b.id,
        "public_ref": getattr(b, "public_ref", None),
        "title": b.title,
        "start": b.start.isoformat() if b.start else None,
        "end": b.end.isoformat() if b.end else None,
        "is_blocking": bool(getattr(b, "is_blocking", False)),
        "client_name": b.client_name,
        "client_email": b.client_email,
        "service": {"id": svc.id, "name": svc.name} if svc else None,
        "assigned_user": {"id": au.id, "username": au.get_username()} if au else None,
        "payment_status": getattr(b, "payment_status", ""),
        "payment_method": getattr(b, "payment_method", ""),
    }


class BookingsListView(APIView):
    """Minimal bookings list for the mobile MVP (org-scoped)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org_param = request.query_params.get("org")
        org, membership = _get_org_and_membership(user=request.user, org_param=org_param)

        try:
            org_tz = ZoneInfo(getattr(org, "timezone", getattr(settings, "TIME_ZONE", "UTC")))
        except Exception:
            org_tz = ZoneInfo(getattr(settings, "TIME_ZONE", "UTC"))

        from_dt, to_dt = _parse_from_to(
            from_raw=request.query_params.get("from"),
            to_raw=request.query_params.get("to"),
            org_tz=org_tz,
        )

        # Default window: next 14 days starting today (org tz)
        if from_dt is None and to_dt is None:
            now = datetime.now(tz=org_tz)
            from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
            to_dt = from_dt + timedelta(days=14)
        elif from_dt is not None and to_dt is None:
            to_dt = from_dt + timedelta(days=14)
        elif from_dt is None and to_dt is not None:
            from_dt = to_dt - timedelta(days=14)

        # Treat `to` as exclusive; if the caller gave a date boundary they likely mean whole-day.
        # (No change needed; our date parser already returns start-of-day.)

        qs = (
            Booking.objects.filter(organization=org)
            .select_related("service", "assigned_user")
            .order_by("start")
        )

        # Exclude internal per-date override markers (not real client bookings).
        qs = qs.exclude(service__isnull=True, client_name__startswith="scope:")

        q = (request.query_params.get("q") or "").strip()
        if q:
            # Lightweight keyword search for the mobile app.
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(client_name__icontains=q)
                | Q(client_email__icontains=q)
                | Q(service__name__icontains=q)
            )

        # Staff users default to seeing their own assignments + unassigned bookings.
        if membership.role == "staff":
            qs = qs.filter(Q(assigned_user__isnull=True) | Q(assigned_user=request.user))

        if from_dt is not None:
            qs = qs.filter(end__gt=from_dt)
        if to_dt is not None:
            qs = qs.filter(start__lt=to_dt)

        try:
            limit = int(request.query_params.get("limit") or 200)
        except Exception:
            limit = 200
        limit = max(1, min(limit, 500))

        items = [_serialize_booking_list_item(b) for b in qs[:limit]]
        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "from": from_dt.isoformat() if from_dt else None,
                "to": to_dt.isoformat() if to_dt else None,
                "count": len(items),
                "bookings": items,
            }
        )


class BookingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, booking_id: int):
        org_param = request.query_params.get("org")
        org, membership = _get_org_and_membership(user=request.user, org_param=org_param)

        qs = Booking.objects.filter(organization=org, id=int(booking_id)).select_related(
            "service", "assigned_user"
        )

        # Exclude internal per-date override markers (not real client bookings).
        qs = qs.exclude(service__isnull=True, client_name__startswith="scope:")

        if membership.role == "staff":
            qs = qs.filter(Q(assigned_user__isnull=True) | Q(assigned_user=request.user))

        b = qs.first()
        if not b:
            raise ValidationError({"detail": "Booking not found."})

        return Response(
            {
                "org": {"id": org.id, "slug": org.slug, "name": org.name},
                "membership": {"role": membership.role},
                "booking": _serialize_booking_list_item(b),
            }
        )

    def delete(self, request, booking_id: int):
        """Cancel/delete a booking (mobile quick actions).

        Query params:
        - org: required
        - action: 'cancel' (default) | 'delete'

        Notes:
        - We implement cancellation by deleting the Booking but marking the audit
          event type as 'cancelled' so history remains visible.
        """

        org_param = request.query_params.get("org")
        org, membership = _get_org_and_membership(user=request.user, org_param=org_param)

        action = (request.query_params.get("action") or "cancel").strip().lower()
        if action not in {"cancel", "delete"}:
            raise ValidationError({"action": "Invalid action. Use 'cancel' or 'delete'."})

        if action == "delete":
            if membership.role not in {"owner", "admin"}:
                raise ValidationError({"detail": "Only owners/GMs can delete bookings."})
        else:
            if membership.role not in {"owner", "admin", "manager"}:
                raise ValidationError({"detail": "Only owners/GMs/managers can cancel bookings."})

        qs = Booking.objects.filter(organization=org, id=int(booking_id)).select_related(
            "service", "assigned_user"
        )
        qs = qs.exclude(service__isnull=True, client_name__startswith="scope:")

        b = qs.first()
        if not b:
            raise ValidationError({"detail": "Booking not found."})

        # Optional reason for audit trail.
        reason = None
        try:
            if isinstance(getattr(request, "data", None), dict):
                reason = request.data.get("reason")
        except Exception:
            reason = None
        if not reason:
            reason = request.query_params.get("reason")
        try:
            reason = (str(reason).strip() if reason is not None else None) or None
        except Exception:
            reason = None

        try:
            # Audit signal reads these attributes.
            b._audit_created_by = request.user
            if reason:
                b._audit_extra = f"mobile:{action} :: {reason}"
            else:
                b._audit_extra = f"mobile:{action}"
            if action == "cancel":
                b._audit_event_type = "cancelled"
        except Exception:
            pass

        b.delete()
        return Response({"detail": "Cancelled." if action == "cancel" else "Deleted."})


class BookingsAuditListView(APIView):
    """Mobile-friendly booking audit list (org-scoped).

    Mirrors the web audit JSON but uses JWT auth.

    Query params:
    - org: required (slug or id)
    - page: optional (default 1)
    - per_page: optional (default 25, max 100)
    - since: optional ISO8601; returns entries with created_at > since
    - include_snapshot: optional (0/1). Default 0 to keep payload small.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        org_param = request.query_params.get("org")
        org, _membership = _get_org_and_membership(user=request.user, org_param=org_param)

        try:
            page = int(request.query_params.get("page") or 1)
        except Exception:
            page = 1
        page = max(1, page)

        try:
            per_page = int(request.query_params.get("per_page") or 25)
        except Exception:
            per_page = 25
        per_page = max(1, min(per_page, 100))

        include_snapshot_raw = str(request.query_params.get("include_snapshot") or "0").strip()
        include_snapshot = include_snapshot_raw in {"1", "true", "True", "yes", "on"}

        qs = AuditBooking.objects.filter(organization=org).select_related("service").order_by("-created_at")

        since_raw = request.query_params.get("since")
        if since_raw:
            try:
                s = since_raw.strip()
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                since_dt = datetime.fromisoformat(s)
                if since_dt.tzinfo is None:
                    since_dt = since_dt.replace(tzinfo=timezone.utc)
                qs = qs.filter(created_at__gt=since_dt)
            except Exception:
                # Ignore parsing errors and return full page.
                pass

        total = qs.count()
        start = (page - 1) * per_page
        end = start + per_page

        items = []
        for a in qs[start:end]:
            snap = a.booking_snapshot if isinstance(a.booking_snapshot, dict) else {}
            public_ref = None
            try:
                public_ref = snap.get("public_ref")
            except Exception:
                public_ref = None

            non_refunded = False
            refund_within_cutoff = False
            try:
                if a.event_type == AuditBooking.EVENT_CANCELLED and snap and (snap.get("refund_forced") or snap.get("refund_id")):
                    non_refunded = False
                    refund_within_cutoff = False
                elif a.event_type == AuditBooking.EVENT_CANCELLED and a.service and a.start and a.created_at:
                    hrs = (a.start - a.created_at).total_seconds() / 3600.0
                    if getattr(a.service, "refunds_allowed", False):
                        cutoff = float(getattr(a.service, "refund_cutoff_hours", 0) or 0)
                        refundable = hrs >= cutoff
                        refund_within_cutoff = hrs < cutoff
                    else:
                        refundable = False
                    non_refunded = not refundable
            except Exception:
                non_refunded = False
                refund_within_cutoff = False

            svc = a.service
            items.append(
                {
                    "id": a.id,
                    "booking_id": a.booking_id,
                    "public_ref": public_ref,
                    "event_type": a.event_type,
                    "service": {
                        "id": svc.id,
                        "name": svc.name,
                        "price": float(svc.price) if getattr(svc, "price", None) is not None else None,
                    }
                    if svc
                    else None,
                    "start": a.start.isoformat() if a.start else None,
                    "end": a.end.isoformat() if a.end else None,
                    "client_name": a.client_name,
                    "client_email": a.client_email,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "extra": a.extra,
                    "non_refunded": non_refunded,
                    "refund_within_cutoff": refund_within_cutoff,
                    **({"snapshot": snap} if include_snapshot else {}),
                }
            )

        return Response({"org": {"id": org.id, "slug": org.slug, "name": org.name}, "total": total, "page": page, "per_page": per_page, "items": items})
