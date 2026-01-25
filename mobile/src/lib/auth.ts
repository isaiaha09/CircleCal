import * as SecureStore from 'expo-secure-store';

const ACCESS_TOKEN_KEY = 'cc_access_token';
const REFRESH_TOKEN_KEY = 'cc_refresh_token';
const ACTIVE_ORG_SLUG_KEY = 'cc_active_org_slug';

export async function getAccessToken(): Promise<string | null> {
  return SecureStore.getItemAsync(ACCESS_TOKEN_KEY);
}

export async function setAccessToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(ACCESS_TOKEN_KEY, token);
}

export async function clearAccessToken(): Promise<void> {
  await SecureStore.deleteItemAsync(ACCESS_TOKEN_KEY);
}

export async function getRefreshToken(): Promise<string | null> {
  return SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
}

export async function setRefreshToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, token);
}

export async function clearRefreshToken(): Promise<void> {
  await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
}

export async function signOut(): Promise<void> {
  await Promise.all([clearAccessToken(), clearRefreshToken()]);
}

export async function getActiveOrgSlug(): Promise<string | null> {
  return SecureStore.getItemAsync(ACTIVE_ORG_SLUG_KEY);
}

export async function setActiveOrgSlug(slug: string): Promise<void> {
  await SecureStore.setItemAsync(ACTIVE_ORG_SLUG_KEY, slug);
}

export async function clearActiveOrgSlug(): Promise<void> {
  await SecureStore.deleteItemAsync(ACTIVE_ORG_SLUG_KEY);
}
