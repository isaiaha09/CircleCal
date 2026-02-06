import * as SecureStore from 'expo-secure-store';

const ACCESS_TOKEN_KEY = 'cc_access_token';
const REFRESH_TOKEN_KEY = 'cc_refresh_token';
const ACTIVE_ORG_SLUG_KEY = 'cc_active_org_slug';
const PUSH_TOKEN_KEY = 'cc_push_token';
const POST_SIGNOUT_MESSAGE_KEY = 'cc_post_signout_message';
const POST_STRIPE_MESSAGE_KEY = 'cc_post_stripe_message';

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

export async function getStoredPushToken(): Promise<string | null> {
  return SecureStore.getItemAsync(PUSH_TOKEN_KEY);
}

export async function setStoredPushToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(PUSH_TOKEN_KEY, token);
}

export async function clearStoredPushToken(): Promise<void> {
  await SecureStore.deleteItemAsync(PUSH_TOKEN_KEY);
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

export async function getPostSignOutMessage(): Promise<string | null> {
  return SecureStore.getItemAsync(POST_SIGNOUT_MESSAGE_KEY);
}

export async function setPostSignOutMessage(message: string): Promise<void> {
  await SecureStore.setItemAsync(POST_SIGNOUT_MESSAGE_KEY, message);
}

export async function clearPostSignOutMessage(): Promise<void> {
  await SecureStore.deleteItemAsync(POST_SIGNOUT_MESSAGE_KEY);
}

export async function getPostStripeMessage(): Promise<string | null> {
  return SecureStore.getItemAsync(POST_STRIPE_MESSAGE_KEY);
}

export async function setPostStripeMessage(message: string): Promise<void> {
  await SecureStore.setItemAsync(POST_STRIPE_MESSAGE_KEY, message);
}

export async function clearPostStripeMessage(): Promise<void> {
  await SecureStore.deleteItemAsync(POST_STRIPE_MESSAGE_KEY);
}
