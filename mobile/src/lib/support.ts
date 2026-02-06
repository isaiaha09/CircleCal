import { Alert, Linking, Platform } from 'react-native';

import { API_BASE_URL } from '../config';
import { navigationRef, openWebAppPath } from './navigation';

export function getSupportUrl(): string {
  return `${API_BASE_URL.replace(/\/$/, '')}/contact/`;
}

export function contactSupport(): void {
  // Prefer in-app WebView so the app UA + app-mode styling applies.
  // This also avoids Cloudflare/Turnstile issues that can occur in external browsers or embedded contexts.
  if (navigationRef.isReady()) {
    openWebAppPath('/contact/');
    return;
  }

  Linking.openURL(getSupportUrl()).catch(() => undefined);
}

export function showPlanGatedAlert(args?: { title?: string; message?: string }): void {
  const platformLabel = Platform.OS === 'ios' ? 'iOS' : 'Android';
  const title = args?.title || 'Feature unavailable';
  const message =
    args?.message || `This feature isn’t available in the ${platformLabel} app.`;

  Alert.alert(title, message, [
    { text: 'Contact support', onPress: () => contactSupport() },
    { text: 'OK', style: 'cancel' },
  ]);
}
