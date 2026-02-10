import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View, FlatList, Switch } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Notifications from 'expo-notifications';

import {
  clearInboxNotifications,
  getInboxNotifications,
  markAllInboxRead,
  markInboxRead,
  type InboxNotification,
} from '../lib/notificationStore';
import { theme } from '../ui/theme';
import { webPathFromPushData } from './WebAppScreen';
import { apiGetPushStatus, type ApiPushStatus } from '../lib/api';
import { registerPushTokenWithResult, unregisterPushTokenBestEffort } from '../lib/push';

type Props = {
  navigation: any;
};

function formatWhen(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch {
    return '';
  }
}

export function NotificationsScreen({ navigation }: Props) {
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<InboxNotification[]>([]);
  const [pushStatus, setPushStatus] = useState<ApiPushStatus | null>(null);
  const [pushStatusLoading, setPushStatusLoading] = useState(false);
  const [pushHint, setPushHint] = useState<string | null>(null);
  const [permStatus, setPermStatus] = useState<string>('');
  const [pushBusy, setPushBusy] = useState(false);

  const permGranted = useMemo(() => (permStatus || '').toLowerCase() === 'granted', [permStatus]);
  const permDenied = useMemo(() => (permStatus || '').toLowerCase() === 'denied', [permStatus]);
  const deviceConnected = useMemo(() => {
    const n = Number(pushStatus?.devices_active ?? 0);
    return Number.isFinite(n) && n > 0;
  }, [pushStatus]);
  const pushSwitchValue = permGranted && deviceConnected;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const x = await getInboxNotifications();
      setItems(x);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const refreshPushStatus = useCallback(async () => {
    setPushStatusLoading(true);
    try {
      try {
        const perm = await Notifications.getPermissionsAsync();
        setPermStatus(String((perm as any)?.status || ''));
      } catch {
        setPermStatus('');
      }
      const st = await apiGetPushStatus();
      setPushStatus(st);
    } catch {
      setPushStatus(null);
    } finally {
      setPushStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshPushStatus().catch(() => undefined);
  }, [refreshPushStatus]);

  useEffect(() => {
    // When arriving on this screen, treat inbox as viewed.
    const unsub = navigation?.addListener?.('focus', () => {
      markAllInboxRead()
        .then(load)
        .catch(() => undefined);
    });
    return unsub;
  }, [load, navigation]);

  const hasAny = items.length > 0;

  const headerRight = useMemo(() => {
    if (!hasAny) return null;
    return (
      <Pressable
        onPress={() => {
          clearInboxNotifications()
            .then(load)
            .catch(() => undefined);
        }}
        style={styles.headerBtn}
        accessibilityRole="button"
        accessibilityLabel="Clear notifications"
      >
        <Text style={styles.headerBtnText}>Clear</Text>
      </Pressable>
    );
  }, [hasAny, load]);

  useEffect(() => {
    try {
      navigation?.setOptions?.({ headerRight: () => headerRight });
    } catch {
      // ignore
    }
  }, [headerRight, navigation]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.colors.primary} />
        <Text style={styles.muted}>Loading…</Text>
      </View>
    );
  }

  if (!hasAny) {
    return (
      <View style={styles.center}>
        <Ionicons name="notifications-outline" size={30} color={theme.colors.muted} />
        <Text style={styles.title}>No notifications yet</Text>
        <Text style={styles.muted}>When you get a push, it will show here.</Text>

        <View style={styles.pushCard}>
          <Text style={styles.pushTitle}>Push notifications</Text>

          <View style={styles.pushToggleRow}>
            <Text style={styles.pushRowText}>Enable push notifications</Text>
            <Switch
              value={pushSwitchValue}
              onValueChange={(next) => {
                (async () => {
                  if (pushBusy) return;
                  setPushBusy(true);
                  setPushHint(null);
                  try {
                    if (next) {
                      setPushHint('Enabling…');
                      const res = await registerPushTokenWithResult();
                      if (res.status === 'registered') setPushHint('Enabled.');
                      else if (res.status === 'expo_go_disabled') setPushHint('Push is disabled in Expo Go. Test in TestFlight.');
                      else if (res.status === 'permission_denied') setPushHint('Turn on notifications in Settings.');
                      else if (res.status === 'missing_project_id') setPushHint('Notifications aren’t available in this build yet.');
                      else if (res.status === 'not_device') setPushHint('Push notifications require a real device.');
                      else setPushHint('Could not enable. Please try again.');
                    } else {
                      setPushHint('Turning off…');
                      await unregisterPushTokenBestEffort();
                      setPushHint('Off for this device.');
                    }
                    await refreshPushStatus();
                  } finally {
                    setPushBusy(false);
                  }
                })().catch(() => undefined);
              }}
              disabled={pushStatusLoading || pushBusy}
            />
          </View>

          <Text style={styles.pushSubtle}>Get booking reminders and updates.</Text>

          {pushHint ? <Text style={styles.pushHint}>{pushHint}</Text> : null}

          {permDenied ? (
            <View style={{ marginTop: 10, width: '100%' }}>
              <Pressable
                hitSlop={8}
                onPress={() => {
                  setPushHint(null);
                  try {
                    Linking.openSettings();
                  } catch {
                    setPushHint('Could not open Settings.');
                  }
                }}
                style={[styles.pushBtn, { backgroundColor: '#111827', alignSelf: 'flex-start' }]}
                accessibilityRole="button"
                accessibilityLabel="Open notification settings"
              >
                <Text style={[styles.pushBtnText, { color: '#fff' }]}>Open Settings</Text>
              </Pressable>
            </View>
          ) : null}
        </View>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <FlatList
        data={items}
        keyExtractor={(it) => it.id}
        contentContainerStyle={styles.listContent}
        renderItem={({ item }) => {
          const data: any = item.data || {};
          const orgSlug = typeof data.orgSlug === 'string' ? data.orgSlug : null;
          const open = typeof data.open === 'string' ? data.open : null;
          const bookingIdRaw = data.bookingId;
          const bookingId = typeof bookingIdRaw === 'number' ? bookingIdRaw : Number(bookingIdRaw);
          const canOpenBookingsList = Boolean(orgSlug && open === 'Bookings');
          const canOpenBooking = Boolean(orgSlug && Number.isFinite(bookingId));
          const canOpen = canOpenBookingsList || canOpenBooking;

          return (
            <Pressable
              style={styles.row}
              onPress={() => {
                if (!canOpen) return;
                markInboxRead(item.id).catch(() => undefined);
                const initialPath = canOpenBookingsList
                  ? webPathFromPushData({ orgSlug: orgSlug as string, open: 'Bookings' })
                  : webPathFromPushData({ orgSlug: orgSlug as string, bookingId: bookingId as number });
                navigation?.navigate?.('WebApp', { initialPath });
              }}
            >
              <View style={styles.rowLeft}>
                <Text style={styles.rowTitle} numberOfLines={1}>
                  {item.title || 'Notification'}
                </Text>
                {item.body ? (
                  <Text style={styles.rowBody} numberOfLines={2}>
                    {item.body}
                  </Text>
                ) : null}
                <Text style={styles.rowWhen}>{formatWhen(item.receivedAt)}</Text>
              </View>
              <Ionicons
                name={canOpen ? 'chevron-forward' : 'notifications'}
                size={18}
                color={theme.colors.muted}
              />
            </Pressable>
          );
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  listContent: { padding: 16 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, backgroundColor: '#fff' },
  title: { marginTop: 10, fontSize: 18, fontWeight: '900', color: '#111827' },
  muted: { marginTop: 6, fontSize: 13, fontWeight: '700', color: theme.colors.muted, textAlign: 'center' },
  pushCard: {
    marginTop: 18,
    width: '100%',
    maxWidth: 420,
    padding: 14,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.06)',
    backgroundColor: '#fff',
  },
  pushTitle: { fontSize: 14, fontWeight: '900', color: '#111827', marginBottom: 8 },
  pushToggleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  pushRowText: { flex: 1, fontSize: 13, fontWeight: '900', color: '#111827' },
  pushSubtle: { marginTop: 6, fontSize: 12, fontWeight: '700', color: theme.colors.muted },
  pushHint: { marginTop: 8, fontSize: 12, fontWeight: '800', color: theme.colors.primaryDark },
  pushBtn: { paddingHorizontal: 12, paddingVertical: 9, borderRadius: 12, marginRight: 10, marginBottom: 10 },
  pushBtnText: { fontSize: 13, fontWeight: '900' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderRadius: 14,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.06)',
    marginBottom: 10,
  },
  rowLeft: { flex: 1, paddingRight: 10 },
  rowTitle: { fontSize: 14, fontWeight: '900', color: '#111827' },
  rowBody: { marginTop: 2, fontSize: 12, fontWeight: '700', color: theme.colors.muted },
  rowWhen: { marginTop: 6, fontSize: 11, fontWeight: '900', color: theme.colors.primaryDark },
  headerBtn: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 10, backgroundColor: '#eff6ff' },
  headerBtnText: { fontSize: 13, fontWeight: '900', color: theme.colors.primaryDark },
});
