# Web ↔ Mobile parity matrix (CircleCal)

This document inventories the *web app* capabilities and the current *mobile app* screens/APIs, then proposes a parity roadmap that respects plan gating and different business shapes (solo vs multi-staff + facility resources).

## Canonical plan gating (current backend)

Plan slugs: `basic`, `pro`, `team`.

Important product rule (as confirmed): **staff/manager/GM are only users within an owner’s Team subscription**. They do not exist as members of trial/basic/pro orgs; on those plans it’s owner-only.

- **Team plan**
  - Multi-staff (team invites + role management) is Team-only.
  - Facility resources (rooms/cages/fields capacity) are Team-only.
  - Web “Calendar” management view can be used by non-owner roles only on Team (since those roles only exist on Team).
- **Pro + Team**
  - Multiple services (beyond Basic’s 1 active service).
  - Weekly availability customization (but see trial note).
  - Offline payment instructions (Venmo/Zelle/etc.)
  - Per-date overrides (web only today) are Pro/Team only **and not allowed while trialing**.
- **Trialing**
  - Treated like Basic for most feature gates.
  - Weekly availability edits are allowed during trial to improve onboarding.

Source of truth:
- `billing/utils.py`
- Web calendar gating: `calendar_app/views.py::calendar_view`
- API gating: `circlecalproject/api_team.py`, `circlecalproject/api_resources.py`, `circlecalproject/api_billing.py`

## Web features inventory (high level)

### Public (client-facing)
- Public org page: `/bus/<org_slug>/`
- Public service page + booking form: `/bus/<org_slug>/service/<service_slug>/`
- Public availability endpoints + busy intervals
- Public cancel/reschedule flows (signed links)
- Public ICS export (signed link or staff)

### Authenticated (staff-facing)
- Dashboard: `/bus/<org_slug>/dashboard/`
- Calendar management: `/bus/<org_slug>/calendar/` (plan-gated for non-owners)
- Bookings list + bulk delete + recent list
- Booking audit list/export/undo/delete
- Services (CRUD + advanced scheduling/constraints)
- Team dashboard (invites, members, roles) (Team plan)
- Facility resources management (Team plan)
- Org refund settings
- Org custom domain settings

### Billing
- Pricing pages + plan detail pages
- Embedded checkout + Stripe portal
- Payment methods management
- Stripe Connect onboarding + Express dashboard link

## Mobile screens inventory (current)

Screens in `mobile/src/screens/`:

- Auth
  - `WelcomeScreen`, `SignInChoiceScreen`, `SignInScreen`
- Org selection
  - `BusinessesScreen`
- Core
  - `HomeScreen`
  - `ScheduleScreen` (today)
  - `BookingsScreen` (2-week list)
  - `CalendarScreen` (month grid + agenda)
  - `BookingDetailScreen` (includes Cancel/Delete quick actions)
- Management
  - `ServicesScreen`, `ServiceEditScreen`
  - `StaffScreen` (Team plan)
  - `ResourcesScreen` (Team plan)
- Billing
  - `BillingScreen`, `PricingScreen`
- Profile
  - `ProfileScreen` (profile + avatar + offline payment info panel)
- Placeholder
  - `PortalPlaceholderScreen`

## Parity matrix (web ↔ mobile)

Legend for **Parity**:
- ✅ = implemented
- 🟨 = partial / missing some sub-features
- ❌ = missing

| Area | Web | Mobile | Backend/API support | Plan gating | Parity | Notes |
|---|---|---|---|---|---|---|
| Sign-in | `/accounts/login/*` | `SignInChoiceScreen` + `SignInScreen` | JWT token endpoints | none | ✅ | Web has owner vs staff login choice; mobile mirrors. |
| Org selection | choose business pages | `BusinessesScreen` | `GET /api/v1/orgs/` | none | ✅ | Mobile stores active org slug locally. |
| Dashboard | `/bus/<org>/dashboard/` | `HomeScreen` | (mobile uses org list + basic data) | role-gated | 🟨 | Mobile dashboard is intentionally lighter; web is richer. |
| Bookings list | `/bus/<org>/bookings/` | `BookingsScreen` | `GET /api/v1/bookings/` | staff sees only own/unassigned | ✅ | Mobile list is date-window based. |
| Booking detail | (modal/details on web calendar) | `BookingDetailScreen` | `GET /api/v1/bookings/<id>/` | staff filtered | ✅ | Mobile supports Cancel/Delete (role-gated). |
| Calendar (management) | `/bus/<org>/calendar/` | `CalendarScreen` | `GET /api/v1/bookings/` | non-owner roles only exist on Team | 🟨 | Mobile calendar is *read-only bookings*. Owner access on non-Team plans is expected because there is no staff on those plans. |
| Today schedule | (part of calendar views) | `ScheduleScreen` | `GET /api/v1/bookings/` | staff filtered | ✅ | Good operationally. |
| Services list/edit | `/bus/<org>/services/` | `ServicesScreen` + `ServiceEditScreen` | `GET/POST /api/v1/services/`, `PATCH /api/v1/services/<id>/` | role-gated | ✅ | Web has advanced scheduling (weekly windows, constraints) not on mobile. |
| Staff/team management | `/bus/<org>/team/` | `StaffScreen` | `/api/v1/team/*` | Team plan + role | ✅ | Mobile has invites, role change, deactivate. |
| Facility resources | `/bus/<org>/resources/` | `ResourcesScreen` | `/api/v1/resources/*` | Team plan + role | ✅ | Good parity for CRUD; web likely has linking to services (mobile doesn’t yet). |
| Billing overview | `/billing/bus/<org>/manage/` | `BillingScreen` | `/api/v1/billing/summary/` | owner-only | ✅ | Mobile shows plan, status, payment methods, and portal link. |
| Pricing/upgrade | `/bus/<org>/pricing/` | `PricingScreen` | `/api/v1/billing/plans/`, `/api/v1/billing/checkout/` | owner-only | ✅ | Mobile uses Stripe checkout deep link. |
| Offline payment settings | settings in web (OrgSettings) | `ProfileScreen` panel | `/api/v1/org/offline-payments/` and profile overview | Pro/Team + owner-only | ✅ | Editing is gated by API. |
| Stripe Connect onboarding | `/billing/.../stripe/connect/*` | (none) | web-only | owner/admin | ❌ | Could be mobile-only optional (deep link to web), or keep web-only. |
| Booking audit/history | `/bus/<org>/bookings/audit/*` | (none) | web-only today | role-gated | ❌ | High-value for mobile ops: show recent changes + undo. |
| Audit export PDF/CSV | `/bus/<org>/bookings/audit/export/` | (none) | web-only | owner/admin | ❌ | Likely keep web-only. |
| Bulk delete bookings | `/bus/<org>/bookings/bulk_delete/` | (none) | web-only | owner/admin | ❌ | Probably web-only for safety. |
| Refund settings | `/bus/<org>/settings/refunds/` | (none) | web-only | owner/admin | ❌ | Candidate for mobile “view-only” initially. |
| Custom domain settings | `/bus/<org>/settings/domain/` | (none) | web-only | owner/admin | ❌ | Keep web-only. |
| Public client booking flow | `/bus/<org>/service/<svc>/...` | (none) | server-rendered | n/a | ❌ | Likely stays web. |
| Push notifications | (none historically) | implemented | `/api/v1/push/tokens/` + server sender | n/a | ✅ | Mobile-only advantage. |

## Recommended parity roadmap (by value + safety)

### Phase A (tighten correctness across plans)
1. **Expose an org-scoped “capabilities/permissions” blob to all roles**
  - Since non-owner roles only exist under Team, this endpoint is less about “which plan tier?” and more about **what this member is allowed to do** and whether the subscription is active.
  - Suggested fields: `plan_slug`, `subscription_active`, `is_trialing`, `membership_role`, plus capability booleans like `can_manage_staff`, `can_manage_resources`, `can_manage_services`, `can_manage_billing`.
  - Mobile uses this to hide/show screens and render “ask owner” messaging without relying on owner-only billing endpoints.

### Phase B (high-value mobile parity)
3. **Booking audit feed (mobile)**
   - Read-only audit list with filters (date, service, member).
   - Optional: “undo” for last change type(s), mirroring web’s audit undo.

4. **Service → resource linking (Team plan)**
   - Web likely supports linking facility resources to services; mobile currently only manages resources themselves.

### Phase C (mobile-only advantages)
5. **Notification controls** (mobile only)
   - Quiet hours
   - Per-org notification toggles
   - “Only notify me for assigned bookings” toggle (default already effectively true)

6. **Offline/poor-network friendliness**
   - Cache last N bookings + last org selection.

7. **On-call operational tools**
   - “Who gets notified” preview on reassignment.
   - Quick reschedule / reassign flows (with the same guardrails as web).

---

Next concrete implementation step: add an org-scoped capabilities/permissions endpoint so mobile can render role-gated UI correctly for Team members and avoid confusing 403/402 error flows.
