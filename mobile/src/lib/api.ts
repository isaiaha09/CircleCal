import { API_BASE_URL } from '../config';
import { getAccessToken } from './auth';

export type ApiError = {
  status: number;
  message: string;
  body?: unknown;
};

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

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'GET',
    headers: await buildHeaders(),
  });

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
}

export async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify(payload ?? {}),
  });

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
}

export async function apiPatch<T>(path: string, payload: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PATCH',
    headers: await buildHeaders(),
    body: JSON.stringify(payload ?? {}),
  });

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
}

export async function apiPostFormData<T>(path: string, form: FormData): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: await buildHeadersMultipart(),
    body: form,
  });

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
}

export type ApiProfileResponse = {
  user: {
    id: number;
    username: string;
    email: string;
  };
  profile: {
    display_name: string | null;
    timezone: string;
    email_alerts: boolean;
    booking_reminders: boolean;
    avatar_url: string | null;
    avatar_updated_at: string | null;
  };
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

export async function apiGetOrgs(): Promise<{ orgs: OrgListItem[] }> {
  return apiGet('/api/v1/orgs/');
}

export async function apiGetBookings(params: {
  org: string;
  from?: string;
  to?: string;
  limit?: number;
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
  return apiGet(`/api/v1/bookings/?${usp.toString()}`);
}

export async function apiGetBookingDetail(params: {
  org: string;
  bookingId: number;
}): Promise<{
  org: { id: number; slug: string; name: string };
  booking: BookingListItem;
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/bookings/${params.bookingId}/?${usp.toString()}`);
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

export async function apiGetBillingPlans(params: { org: string }): Promise<{
  org: { id: number; slug: string; name: string };
  plans: BillingPlan[];
}> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiGet(`/api/v1/billing/plans/?${usp.toString()}`);
}

export async function apiCreateBillingPortalSession(params: { org: string }): Promise<{ url: string }> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiPost(`/api/v1/billing/portal/?${usp.toString()}`, {});
}

export async function apiCreateBillingCheckoutSession(params: {
  org: string;
  planId: number;
}): Promise<{ url: string }> {
  const usp = new URLSearchParams();
  usp.set('org', params.org);
  return apiPost(`/api/v1/billing/checkout/?${usp.toString()}`, { plan_id: params.planId });
}
