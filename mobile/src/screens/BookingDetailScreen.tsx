import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';

import type { ApiError, BookingListItem } from '../lib/api';
import { apiCancelBooking, apiDeleteBooking, apiGetBookingDetail } from '../lib/api';
import { normalizeOrgRole } from '../lib/permissions';

type Props = {
  orgSlug: string;
  bookingId: number;
};

function formatWhen(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function BookingDetailScreen({ orgSlug, bookingId }: Props) {
  const navigation = useNavigation<any>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [booking, setBooking] = useState<BookingListItem | null>(null);
  const [orgName, setOrgName] = useState<string>(orgSlug);
  const [orgRole, setOrgRole] = useState<string>('');
  const [busyAction, setBusyAction] = useState<'cancel' | 'delete' | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetBookingDetail({ org: orgSlug, bookingId });
        if (cancelled) return;
        setOrgName(resp.org?.name ?? orgSlug);
        setOrgRole(normalizeOrgRole(resp.membership?.role));
        setBooking(resp.booking);
      } catch (e) {
        const err = e as Partial<ApiError>;
        const body = err.body as any;
        const msg =
          (typeof body?.detail === 'string' && body.detail) ||
          (typeof err.message === 'string' && err.message) ||
          'Failed to load booking.';
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgSlug, bookingId]);

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Booking</Text>
      <Text style={styles.subtitle}>{orgName}</Text>

      <View style={styles.card}>
        {loading ? (
          <ActivityIndicator />
        ) : error ? (
          <Text style={styles.cardText}>Error: {error}</Text>
        ) : booking ? (
          <>
            <Text style={styles.rowLabel}>When</Text>
            <Text style={styles.rowValue}>
              {formatWhen(booking.start)} — {formatWhen(booking.end)}
            </Text>

            <Text style={styles.rowLabel}>Client</Text>
            <Text style={styles.rowValue}>
              {booking.client_name || '(no name)'}
              {booking.client_email ? ` · ${booking.client_email}` : ''}
            </Text>

            <Text style={styles.rowLabel}>Service</Text>
            <Text style={styles.rowValue}>{booking.service?.name ?? '(none)'}</Text>

            <Text style={styles.rowLabel}>Title</Text>
            <Text style={styles.rowValue}>{booking.title || '(none)'}</Text>

            {orgRole && orgRole !== 'staff' ? (
              <View style={styles.actionsRow}>
                {(orgRole === 'owner' || orgRole === 'admin' || orgRole === 'manager') ? (
                  <Pressable
                    style={[styles.actionBtn, styles.cancelBtn, busyAction ? { opacity: 0.6 } : null]}
                    disabled={busyAction !== null}
                    onPress={() => {
                      Alert.alert(
                        'Cancel booking?',
                        'This will remove the booking from the schedule and record it in the audit history.',
                        [
                          { text: 'Keep', style: 'cancel' },
                          {
                            text: 'Cancel booking',
                            style: 'destructive',
                            onPress: async () => {
                              try {
                                setBusyAction('cancel');
                                await apiCancelBooking({ org: orgSlug, bookingId });
                                Alert.alert('Cancelled', 'Booking cancelled.');
                                navigation.goBack();
                              } catch (e) {
                                const err = e as Partial<ApiError>;
                                const body = err.body as any;
                                const msg =
                                  (typeof body?.detail === 'string' && body.detail) ||
                                  (typeof err.message === 'string' && err.message) ||
                                  'Failed to cancel booking.';
                                Alert.alert('Cancel failed', msg);
                              } finally {
                                setBusyAction(null);
                              }
                            },
                          },
                        ]
                      );
                    }}
                  >
                    <Text style={styles.actionBtnText}>Cancel</Text>
                  </Pressable>
                ) : null}

                {(orgRole === 'owner' || orgRole === 'admin') ? (
                  <Pressable
                    style={[styles.actionBtn, styles.deleteBtn, busyAction ? { opacity: 0.6 } : null]}
                    disabled={busyAction !== null}
                    onPress={() => {
                      Alert.alert(
                        'Delete booking?',
                        'This is permanent (but still recorded in audit history).',
                        [
                          { text: 'Keep', style: 'cancel' },
                          {
                            text: 'Delete',
                            style: 'destructive',
                            onPress: async () => {
                              try {
                                setBusyAction('delete');
                                await apiDeleteBooking({ org: orgSlug, bookingId });
                                Alert.alert('Deleted', 'Booking deleted.');
                                navigation.goBack();
                              } catch (e) {
                                const err = e as Partial<ApiError>;
                                const body = err.body as any;
                                const msg =
                                  (typeof body?.detail === 'string' && body.detail) ||
                                  (typeof err.message === 'string' && err.message) ||
                                  'Failed to delete booking.';
                                Alert.alert('Delete failed', msg);
                              } finally {
                                setBusyAction(null);
                              }
                            },
                          },
                        ]
                      );
                    }}
                  >
                    <Text style={styles.actionBtnText}>Delete</Text>
                  </Pressable>
                ) : null}
              </View>
            ) : null}
          </>
        ) : (
          <Text style={styles.cardText}>Not found</Text>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  content: { padding: 24, paddingTop: 24 },
  title: { fontSize: 28, fontWeight: '700', color: '#111827' },
  subtitle: { marginTop: 6, color: '#6b7280' },
  card: {
    marginTop: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#fff',
    padding: 14,
    borderRadius: 12,
  },
  cardText: { color: '#374151' },
  rowLabel: { marginTop: 10, fontWeight: '700', color: '#111827' },
  rowValue: { marginTop: 4, color: '#374151' },
  actionsRow: {
    marginTop: 16,
    flexDirection: 'row',
    gap: 10,
    flexWrap: 'wrap',
  },
  actionBtn: {
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
  },
  cancelBtn: {
    backgroundColor: '#111827',
  },
  deleteBtn: {
    backgroundColor: '#b91c1c',
  },
  actionBtnText: {
    color: '#fff',
    fontWeight: '700',
  },
});
