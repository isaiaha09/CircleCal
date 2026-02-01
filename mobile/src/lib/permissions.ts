export type OrgRole = 'owner' | 'admin' | 'manager' | 'staff' | string;

export function normalizeOrgRole(role: unknown): OrgRole {
  const r = (typeof role === 'string' ? role : '').trim().toLowerCase();
  return r || 'staff';
}

export function isOwnerOrAdmin(role: unknown): boolean {
  const r = normalizeOrgRole(role);
  return r === 'owner' || r === 'admin';
}

export function canManageStaff(role: unknown): boolean {
  // Matches backend: api_team._require_team_admin
  return isOwnerOrAdmin(role);
}

export function canManageBilling(role: unknown): boolean {
  // Matches backend: api_billing._require_billing_admin
  const r = normalizeOrgRole(role);
  return r === 'owner';
}

export function canManageResources(role: unknown): boolean {
  // Matches backend: api_resources (owner/admin/manager)
  const r = normalizeOrgRole(role);
  return r === 'owner' || r === 'admin' || r === 'manager';
}

export function canManageServices(role: unknown): boolean {
  // Matches backend: api_services (owner/admin/manager)
  return canManageResources(role);
}

export function humanRole(role: unknown): string {
  const r = normalizeOrgRole(role);
  if (r === 'admin') return 'GM';
  return r ? r.charAt(0).toUpperCase() + r.slice(1) : 'Staff';
}
