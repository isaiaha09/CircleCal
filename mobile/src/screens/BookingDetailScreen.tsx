import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { ApiError, BookingListItem } from '../lib/api';
import { apiGetBookingDetail } from '../lib/api';

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [booking, setBooking] = useState<BookingListItem | null>(null);
  const [orgName, setOrgName] = useState<string>(orgSlug);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetBookingDetail({ org: orgSlug, bookingId });
        if (cancelled) return;
        setOrgName(resp.org?.name ?? orgSlug);
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
          </>
        ) : (
          <Text style={styles.cardText}>Not found</Text>
        )}
      </View>

      <View style={styles.hintBox}>
        <Text style={styles.hint}>
          Next step: add quick actions here (cancel/reschedule) once the backend endpoints are ready.
        </Text>
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
  hintBox: { marginTop: 14 },
  hint: { color: '#6b7280' },
});
