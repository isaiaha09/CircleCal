import { API_BASE_URL } from '../config';
import {
  clearAccessToken,
  clearRefreshToken,
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
} from './auth';

export type ApiError = {
  status: number;
  message: string;
  body?: unknown;
};

let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const refresh = await getRefreshToken();
    if (!refresh) return false;

    try {
      const resp = await fetch(`${API_BASE_URL}/api/v1/auth/token/refresh/`, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ refresh }),
      });

      if (!resp.ok) {
        // Refresh token expired/invalid. Clear tokens so next app start routes to sign-in.
        await Promise.all([clearAccessToken(), clearRefreshToken()]);
        return false;
      }

      const data = (await resp.json()) as { access?: string; refresh?: string };
      if (typeof data.access !== 'string' || !data.access) return false;
      await setAccessToken(data.access);
      if (typeof data.refresh === 'string' && data.refresh) await setRefreshToken(data.refresh);
      return true;
    } catch {
      return false;
    }
  })();

  try {
    return await refreshInFlight;
  } finally {
    refreshInFlight = null;
  }
}

async function readResponseBody(resp: Response): Promise<unknown> {
  // Read text first so we can try JSON parse, but still keep HTML/text for debugging.
  const text = await resp.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return { text };
  }
}

async function buildHeaders(extra?: HeadersInit): Promise<HeadersInit> {
  const token = await getAccessToken();
  return {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : null),
    ...(extra ?? null),
  };
}

async function buildHeadersMultipart(extra?: HeadersInit): Promise<HeadersInit> {
  const token = await getAccessToken();
  // Do NOT set Content-Type for multipart; fetch will add boundary.
  return {
    Accept: 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : null),
    ...(extra ?? null),
  };
}

async function apiRequest<T>(
  path: string,
  buildInit: () => Promise<RequestInit>,
  opts?: { allowRefresh?: boolean }
): Promise<T> {
  const allowRefresh = opts?.allowRefresh !== false;

  const doOnce = async (): Promise<T> => {
    const resp = await fetch(`${API_BASE_URL}${path}`, await buildInit());
    if (!resp.ok) {
      const body = await readResponseBody(resp);
      const err: ApiError = {
        status: resp.status,
        message: `Request failed: ${resp.status}`,
        body,
      };
      throw err;
    }
    return (await resp.json()) as T;
  };

  try {
    return await doOnce();
  } catch (e) {
    const err = e as Partial<ApiError>;
    if (allowRefresh && err.status === 401) {
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        return apiRequest<T>(path, buildInit, { allowRefresh: false });
      }
    }
    throw e;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  return apiRequest<T>(path, async () => ({ method: 'GET', headers: await buildHeaders() }));
}

export async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  return apiRequest<T>(path, async () => ({
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify(payload ?? {}),
  }));
}

export async function apiPatch<T>(path: string, payload: unknown): Promise<T> {
  return apiRequest<T>(path, async () => ({
    method: 'PATCH',
    headers: await buildHeaders(),
    body: JSON.stringify(payload ?? {}),
  }));
}

export async function apiDelete<T>(path: string, payload?: unknown): Promise<T> {
  return apiRequest<T>(path, async () => {
    const init: RequestInit = {
      method: 'DELETE',
      headers: await buildHeaders(),
    };
    if (payload !== undefined) init.body = JSON.stringify(payload);
    return init;
  });
}

export async function apiPostFormData<T>(path: string, form: FormData): Promise<T> {
  return apiRequest<T>(path, async () => ({
    method: 'POST',
    headers: await buildHeadersMultipart(),
    body: form,
  }));
}

export async function apiRegisterPushToken(args: { token: string; platform?: string }): Promise<{ ok: boolean }> {
  return apiPost('/api/v1/push/tokens/', {
    token: args.token,
    platform: args.platform ?? '',
  });
}

export type ApiPushStatus = {
  ok: boolean;
  push_enabled: boolean;
  devices_total: number;
  devices_active: number;
  last_seen_at: string | null;
  server_time: string;
};

export async function apiGetPushStatus(): Promise<ApiPushStatus> {
  return apiGet('/api/v1/push/status/');
}

export async function apiGetMobileSsoLink(args?: { next?: string }): Promise<{ url: string; expires_in: number }> {
  const next = (args?.next ?? '/').trim() || '/';
  // Use GET so we can pass `next` easily; endpoint also supports POST.
  return apiGet(`/api/v1/mobile/sso-link/?next=${encodeURIComponent(next)}`);
}

export async function apiUnregisterPushToken(args: { token: string }): Promise<{ ok: boolean; deleted?: number }> {
  return apiDelete('/api/v1/push/tokens/', { token: args.token });
}

export type ApiProfileResponse = {
  user: {
    id: number;
    username: string;
    email: string;
    first_name?: string;
    last_name?: string;
  };
  profile: {
    display_name: string | null;
    timezone: string;
    email_alerts: boolean;
    booking_reminders: boolean;
    avatar_url: string | null;
    avatar_updated_at: string | null;
    scheduled_account_deletion_at?: string | null;
    scheduled_account_deletion_reason?: string | null;
  };
  recent_logins?: Array<{
    timestamp: string | null;
    ip_address: string | null;
    user_agent: string;
  }>;
  memberships?: Array<{
    org: { id: number; slug: string; name: string };
    role: string;
    is_active: boolean;
  }>;
  pending_invites?: Array<{
    org: { id: number; slug: string; name: string };
    role: string;
    created_at: string | null;
    accept_url: string | null;
  }>;
  org_overview?: {
    org: { id: number; slug: string; name: string };
    membership: { role: string };
    features: {
      can_use_offline_payment_methods: boolean;
    };
    offline_payment: {
      can_edit: boolean;
      offline_venmo: string;
      offline_zelle: string;
    };
    stripe: {
      connect_account_id: boolean;
      connect_details_submitted: boolean;
      connect_charges_enabled: boolean;
      connect_payouts_enabled: boolean;
      connected_account_url: string | null;
    };
  };
};

export type ApiProfileOverviewResponse = ApiProfileResponse;

export type OrgOfflinePaymentsResponse = {
  org: { id: number; slug: string; name: string };
  can_use_offline_payment_methods: boolean;
  can_edit: boolean;
  offline_venmo: string;
  offline_zelle: string;
};

export type OrgListItem = {
  id: number;
  slug: string;
  name: string;
  role: 'owner' | 'admin' | 'manager' | 'staff' | string;
};

export type BookingListItem = {
  id: number;
  public_ref?: string | null;
  title: string;
  start: string | null;
  end: string | null;
  is_blocking: boolean;
  client_name: string;
  client_email: string;
  service?: { id: number; name: string } | null;
  assigned_user?: { id: number; username: string } | null;
  payment_status?: string;
  payment_method?: string;
};

export type BookingAuditItem = {
  id: number;
  booking_id: number | null;
  public_ref?: string | null;
  event_type: 'deleted' | 'cancelled' | string;
  service?: { id: number; name: string; price?: number | null } | null;
  start: string | null;
  end: string | null;
  client_name: string;
  client_email: string;
  created_at: string | null;
  extra?: string;
  non_refunded?: boolean;
  refund_within_cutoff?: boolean;
  snapshot?: any;
};

export type ServiceListItem = {
  id: number;
  name: string;
  slug: string;
  description: string;
  duration: number;
  price: number | string;
  is_active: boolean;
  show_on_public_calendar: boolean;
};

export type BillingSummary = {
  org: { id: number; slug: string; name: string };
  plan: { slug: string; name: string | null; price: string; billing_period: string | null };
  subscription:
    | {
        status: string | null;
        cancel_at_period_end: boolean;
        current_period_end: string | null;
        trial_end: string | null;
        scheduled_plan: { id: number | null; slug: string | null; name: string | null } | null;
        scheduled_change_at: string | null;
      }
    | null;
  features: {
    can_add_service: boolean;
    can_add_staff: boolean;
    can_edit_weekly_availability: boolean;
    can_use_offline_payment_methods: boolean;
    can_use_resources: boolean;
  };
  usage: { active_services_count: number; active_members_count: number };
  stripe: {
    enabled: boolean;
    customer_id: boolean;
    connect_account_id: boolean;
    connect_details_submitted: boolean;
    connect_charges_enabled: boolean;
    connect_payouts_enabled: boolean;
  };
  payment_methods: Array<{
    id: number;
    brand: string | null;
    last4: string | null;
    exp_month: number | null;
    exp_year: number | null;
    is_default: boolean;
  }>;
};

export type BillingPlan = {
  id: number;
  name: string;
  slug: string;
  price: string;
  billing_period: string;
  description: string;
};

export type TeamMember = {
  id: number;
  role: 'owner' | 'admin' | 'manager' | 'staff' | string;
  is_active: boolean;
  created_at: string | null;
  user: {
    id: number | null;
    username: string;
    email: string;
    first_name: string;
    last_name: string;
  };
};

export type TeamInvite = {
  id: number;
  email: string;
  role: 'admin' | 'manager' | 'staff' | string;
  accepted: boolean;
  created_at: string | null;
  accept_url: string | null;
};

export type FacilityResourceListItem = {
  id: number;
  name: string;
  slug: string;
  is_active: boolean;
  max_services: number;
  in_use: boolean;
};

export async function apiGetOrgs(): Promise<{ orgs: OrgListItem[] }> {
  return apiGet('/api/v1/orgs/');
}

export async function apiGetTeamMembers(params: { org: string }): Promise<{
  org: { id: number; slug: string; name: string };
  count: number;
  members: TeamMember[];
}> {
  return apiGet(`/api/v1/team/members/?org=${encodeURIComponent(params.org)}`);
}

export async function apiUpdateTeamMember(params: {
  org: string;
  memberId: number;
  patch: { role?: string; is_active?: boolean };
}): Promise<{ member: TeamMember }> {
  return apiPatch(`/api/v1/team/members/${params.memberId}/?org=${encodeURIComponent(params.org)}`, params.patch);
}

export async function apiGetTeamInvites(params: { org: string }): Promise<{
  org: { id: number; slug: string; name: string };
  count: number;
  invites: TeamInvite[];
}> {
  return apiGet(`/api/v1/team/invites/?org=${encodeURIComponent(params.org)}`);
}

export async function apiCreateTeamInvite(params: {
  org: string;
  email: string;
  role: 'admin' | 'manager' | 'staff';
}): Promise<{ invite: TeamInvite; sent: boolean; send_error?: string }> {
  return apiPost(`/api/v1/team/invites/?org=${encodeURIComponent(params.org)}`, {
    email: params.email,
    role: params.role,
  });
}

export async function apiDeleteTeamInvite(params: { org: string; inviteId: number }): Promise<{ deleted: boolean }> {
  try {
    // Preferred (new) endpoint.
    return await apiRequest(
      `/api/v1/team/invites/${params.inviteId}/?org=${encodeURIComponent(params.org)}`,
      async () => ({
        method: 'DELETE',
        headers: await buildHeaders(),
      })
    );
  } catch (e) {
    const err = e as Partial<ApiError>;
    if (err.status !== 404) throw e;

    // Fallback (compat): some deployments may not have the detail route yet.
    return apiRequest(
      `/api/v1/team/invites/?org=${encodeURIComponent(params.org)}&invite_id=${encodeURIComponent(
        String(params.inviteId)
      )}`,
      async () => ({
        method: 'DELETE',
        headers: await buildHeaders(),
      })
    );
  }
}

export async function apiGetResources(params: { org: string }): Promise<{
  org: { id: number; slug: string; name: string };
  count: number;
  resources: FacilityResourceListItem[];
}> {
  return apiGet(`/api/v1/resources/?org=${encodeURIComponent(params.org)}`);
}

export async function apiCreateResource(params: {
  org: string;
  name: string;
  max_services?: number;
}): Promise<{ resource: FacilityResourceListItem }> {
  return apiPost(`/api/v1/resources/?org=${encodeURIComponent(params.org)}`, {
    name: params.name,
    max_services: params.max_services,
  });
}

export async function apiUpdateResource(params: {
  org: string;
  resourceId: number;
  patch: { name?: string; is_active?: boolean; max_services?: number };
}): Promise<{ resource: FacilityResourceListItem }> {
  return apiPatch(`/api/v1/resources/${params.resourceId}/?org=${encodeURIComponent(params.org)}`, params.patch);
}

export async function apiGetBookings(params: {
  org: string;
  from?: string;
  to?: string;
  limit?: number;
  q?: string;
}): Promise<{
  org: { id: number; slug: string; name: string };
  from: string | null;
  to: string | null;
  count: number;
  bookings: BookingListItem[];
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  if (params.from) usp.set('from', params.from);
  if (params.to) usp.set('to', params.to);
  if (typeof params.limit === 'number') usp.set('limit', String(params.limit));
  if (params.q) usp.set('q', params.q);
  return apiGet(`/api/v1/bookings/?${usp.toString()}`);
}

export async function apiGetBookingDetail(params: {
  org: string;
  bookingId: number;
}): Promise<{
  org: { id: number; slug: string; name: string };
  membership?: { role: string };
  booking: BookingListItem;
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/bookings/${params.bookingId}/?${usp.toString()}`);
}

export async function apiGetBookingAudit(params: {
  org: string;
  page?: number;
  per_page?: number;
  since?: string;
  include_snapshot?: boolean;
}): Promise<{
  org: { id: number; slug: string; name: string };
  total: number;
  page: number;
  per_page: number;
  items: BookingAuditItem[];
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  if (typeof params.page === 'number') usp.set('page', String(params.page));
  if (typeof params.per_page === 'number') usp.set('per_page', String(params.per_page));
  if (params.since) usp.set('since', params.since);
  if (params.include_snapshot) usp.set('include_snapshot', '1');
  return apiGet(`/api/v1/bookings/audit/?${usp.toString()}`);
}

export async function apiCancelBooking(params: {
  org: string;
  bookingId: number;
  reason?: string;
}): Promise<{ detail: string }> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  usp.set('action', 'cancel');
  return apiDelete(`/api/v1/bookings/${params.bookingId}/?${usp.toString()}`, params.reason ? { reason: params.reason } : undefined);
}

export async function apiDeleteBooking(params: {
  org: string;
  bookingId: number;
  reason?: string;
}): Promise<{ detail: string }> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  usp.set('action', 'delete');
  return apiDelete(`/api/v1/bookings/${params.bookingId}/?${usp.toString()}`, params.reason ? { reason: params.reason } : undefined);
}

export async function apiGetServices(params: { org: string }): Promise<{
  org: { id: number; slug: string; name: string };
  count: number;
  services: ServiceListItem[];
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/services/?${usp.toString()}`);
}

export async function apiCreateService(params: {
  org: string;
  name: string;
  duration: number;
  price?: number | string;
  description?: string;
}): Promise<{
  org: { id: number; slug: string; name: string };
  service: ServiceListItem;
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiPost(`/api/v1/services/?${usp.toString()}`, {
    name: params.name,
    duration: params.duration,
    price: params.price ?? 0,
    description: params.description ?? '',
  });
}

export async function apiGetServiceDetail(params: {
  org: string;
  serviceId: number;
}): Promise<{
  org: { id: number; slug: string; name: string };
  service: ServiceListItem;
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/services/${params.serviceId}/?${usp.toString()}`);
}

export async function apiPatchService(params: {
  org: string;
  serviceId: number;
  patch: Partial<Pick<ServiceListItem, 'name' | 'description' | 'duration' | 'price' | 'is_active' | 'show_on_public_calendar'>>;
}): Promise<{
  org: { id: number; slug: string; name: string };
  service: ServiceListItem;
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiPatch(`/api/v1/services/${params.serviceId}/?${usp.toString()}`, params.patch);
}

export async function apiGetBillingSummary(params: { org: string }): Promise<BillingSummary> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/billing/summary/?${usp.toString()}`);
}

export async function apiGetProfileOverview(params?: { org?: string | null }): Promise<ApiProfileOverviewResponse> {
  const org = params?.org;
  if (org) {
    const usp = new URLSearchParams();
    usp.set('org', org);
    return apiGet(`/api/v1/profile/overview/?${usp.toString()}`);
  }
  return apiGet(`/api/v1/profile/overview/`);
}

export async function apiGetOrgOfflinePayments(params: { org: string }): Promise<OrgOfflinePaymentsResponse> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/org/offline-payments/?${usp.toString()}`);
}

export async function apiPatchOrgOfflinePayments(params: {
  org: string;
  patch: Partial<Pick<OrgOfflinePaymentsResponse, 'offline_venmo' | 'offline_zelle'>>;
}): Promise<OrgOfflinePaymentsResponse> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiPatch(`/api/v1/org/offline-payments/?${usp.toString()}`, params.patch);
}

export async function apiGetBillingPlans(params: { org: string }): Promise<{
  org: { id: number; slug: string; name: string };
  plans: BillingPlan[];
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/billing/plans/?${usp.toString()}`);
}

export async function apiCreateStripeExpressDashboardLink(params: { org: string }): Promise<{ url: string }> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiPost(`/api/v1/billing/stripe/express-dashboard/?${usp.toString()}`, {});
}
