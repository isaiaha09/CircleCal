import Constants from 'expo-constants';
import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { apiRegisterPushToken, apiUnregisterPushToken } from './api';
import { clearStoredPushToken, getStoredPushToken, setStoredPushToken } from './auth';

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: false,
    shouldSetBadge: false,
  }),
});

function getProjectId(): string | undefined {
  // In EAS builds, projectId is recommended/required. In Expo Go it can be omitted.
  const anyConstants = Constants as any;
  return (
    anyConstants?.expoConfig?.extra?.eas?.projectId ||
    anyConstants?.easConfig?.projectId ||
    undefined
  );
}

function allowExpoGoPush(): boolean {
  try {
    const anyConstants = Constants as any;
    return Boolean(anyConstants?.expoConfig?.extra?.allowExpoGoPush);
  } catch {
    return false;
  }
}

function isExpoGo(): boolean {
  try {
    const anyConstants = Constants as any;
    return String(anyConstants?.appOwnership || '').toLowerCase() === 'expo';
  } catch {
    return false;
  }
}

function isProjectIdRequired(): boolean {
  // In Expo Go, projectId can be omitted; in dev-client/standalone it is required.
  return !isExpoGo();
}

function formatErr(e: unknown): string {
  try {
    if (!e) return '';
    if (typeof e === 'string') return e;
    const anyE = e as any;
    return String(anyE?.message || anyE?.toString?.() || '');
  } catch {
    return '';
  }
}

async function _getExpoPushTokenDetailed(opts: { prompt: boolean }): Promise<{ token: string | null; permStatus: string; error?: string }> {
  if (!Device.isDevice) return { token: null, permStatus: 'not_device' };

  if (Platform.OS === 'android') {
    try {
      await Notifications.setNotificationChannelAsync('default', {
        name: 'default',
        importance: Notifications.AndroidImportance.DEFAULT,
      });
    } catch {
      // ignore
    }
  }

  const existing = await Notifications.getPermissionsAsync();
  let finalStatus = String((existing as any)?.status || '');

  if (finalStatus !== 'granted' && opts.prompt) {
    try {
      const requested = await Notifications.requestPermissionsAsync();
      finalStatus = String((requested as any)?.status || '');
    } catch {
      // ignore
    }
  }

  if (finalStatus !== 'granted') {
    return { token: null, permStatus: finalStatus || 'denied' };
  }

  try {
    const projectId = getProjectId();
    if (!projectId && isProjectIdRequired()) {
      return {
        token: null,
        permStatus: finalStatus,
        error: 'Missing EAS projectId (expo.extra.eas.projectId). Rebuild app after setting it in app.json.',
      };
    }
    const resp = projectId
      ? await Notifications.getExpoPushTokenAsync({ projectId })
      : await Notifications.getExpoPushTokenAsync();
    return { token: resp?.data || null, permStatus: finalStatus };
  } catch (e) {
    return { token: null, permStatus: finalStatus, error: formatErr(e) || 'Unknown error' };
  }
}

export async function getExpoPushTokenIfPermitted(): Promise<string | null> {
  const r = await _getExpoPushTokenDetailed({ prompt: true });
  return r.token;
}

export async function getExpoPushTokenIfAlreadyGranted(): Promise<string | null> {
  const r = await _getExpoPushTokenDetailed({ prompt: false });
  return r.token;
}

export type PushRegisterResult =
  | { status: 'registered' }
  | { status: 'expo_go_disabled' }
  | { status: 'not_device' }
  | { status: 'permission_denied' }
  | { status: 'token_unavailable' }
  | { status: 'missing_project_id' }
  | { status: 'token_error'; message: string }
  | { status: 'api_failed' };

export async function registerPushTokenWithResult(): Promise<PushRegisterResult> {
  if (isExpoGo() && !allowExpoGoPush()) {
    // If a token was previously registered for this user/device, remove it so
    // they don't keep receiving pushes while testing in Expo Go.
    await unregisterPushTokenBestEffort();
    return { status: 'expo_go_disabled' };
  }
  if (!Device.isDevice) return { status: 'not_device' };

  const projectId = getProjectId();
  if (!projectId && isProjectIdRequired()) {
    return { status: 'missing_project_id' };
  }

  const res = await _getExpoPushTokenDetailed({ prompt: true });
  if (!res.token) {
    if (res.permStatus !== 'granted') return { status: 'permission_denied' };
    if (res.error) return { status: 'token_error', message: res.error };
    return { status: 'token_unavailable' };
  }

  try {
    await apiRegisterPushToken({ token: res.token, platform: Platform.OS });
    await setStoredPushToken(res.token);
    return { status: 'registered' };
  } catch {
    return { status: 'api_failed' };
  }
}

export async function registerPushTokenIfPossible(): Promise<void> {
  if (isExpoGo() && !allowExpoGoPush()) {
    await unregisterPushTokenBestEffort();
    return;
  }
  const token = await getExpoPushTokenIfPermitted();
  if (!token) return;

  try {
    await apiRegisterPushToken({ token, platform: Platform.OS });
    await setStoredPushToken(token);
  } catch {
    // Best-effort: do not block app usage.
  }
}

export async function registerPushTokenIfAlreadyPermitted(): Promise<void> {
  if (isExpoGo() && !allowExpoGoPush()) {
    await unregisterPushTokenBestEffort();
    return;
  }
  const token = await getExpoPushTokenIfAlreadyGranted();
  if (!token) return;

  try {
    await apiRegisterPushToken({ token, platform: Platform.OS });
    await setStoredPushToken(token);
  } catch {
    // Best-effort.
  }
}

export async function unregisterPushTokenBestEffort(): Promise<void> {
  const token = await getStoredPushToken();
  if (!token) return;

  try {
    await apiUnregisterPushToken({ token });
  } catch {
    // ignore
  } finally {
    await clearStoredPushToken();
  }
}
