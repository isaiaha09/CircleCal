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

export async function getExpoPushTokenIfPermitted(): Promise<string | null> {
  if (!Device.isDevice) return null;

  // Android requires a channel for notifications to show.
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
  let finalStatus = existing.status;

  if (finalStatus !== 'granted') {
    const requested = await Notifications.requestPermissionsAsync();
    finalStatus = requested.status;
  }

  if (finalStatus !== 'granted') return null;

  try {
    const projectId = getProjectId();
    const resp = projectId
      ? await Notifications.getExpoPushTokenAsync({ projectId })
      : await Notifications.getExpoPushTokenAsync();

    return resp?.data || null;
  } catch {
    return null;
  }
}

export async function registerPushTokenIfPossible(): Promise<void> {
  const token = await getExpoPushTokenIfPermitted();
  if (!token) return;

  try {
    await apiRegisterPushToken({ token, platform: Platform.OS });
    await setStoredPushToken(token);
  } catch {
    // Best-effort: do not block app usage.
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
