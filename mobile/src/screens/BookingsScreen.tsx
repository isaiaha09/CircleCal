import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from 'react-native';

import type { ApiError, BookingListItem } from '../lib/api';
import { apiGetBookings, apiGetOrgs } from '../lib/api';
import { normalizeOrgRole } from '../lib/permissions';

type Props = {
  orgSlug: string;
  onOpenBooking: (args: { orgSlug: string; bookingId: number }) => void;
  onOpenAudit?: (args: { orgSlug: string }) => void;
  setHeaderTitle?: (title: string) => void;
};

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function formatWhen(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function BookingsScreen({ orgSlug, onOpenBooking, onOpenAudit, setHeaderTitle }: Props) {
  const [bookings, setBookings] = useState<BookingListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [orgRole, setOrgRole] = useState<string>('');

  const window = useMemo(() => {
    const from = new Date();
    const to = new Date();
    to.setDate(to.getDate() + 14);
    return { from: isoDate(from), to: isoDate(to) };
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGetBookings({ org: orgSlug, from: window.from, to: window.to, limit: 500 });
      setBookings(resp.bookings ?? []);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to load bookings.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetOrgs();
        const found = (resp.orgs ?? []).find((o) => o.slug === orgSlug);
        const r = found?.role ? normalizeOrgRole(found.role) : '';
        if (!cancelled) setOrgRole(r);
        if (!cancelled && setHeaderTitle) setHeaderTitle(r === 'staff' ? 'My bookings' : 'Bookings');
      } catch {
        if (!cancelled && setHeaderTitle) setHeaderTitle('Bookings');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgSlug, setHeaderTitle]);

  function renderItem({ item }: { item: BookingListItem }) {
    const title = item.service?.name || item.title || 'Booking';
    const who = item.client_name || item.client_email || (item.is_blocking ? 'Blocked' : '');
    return (
      <Pressable
        style={styles.bookingRow}
        onPress={() => onOpenBooking({ orgSlug, bookingId: item.id })}
      >
        <Text style={styles.bookingTitle}>{title}</Text>
        <Text style={styles.bookingMeta}>
          {formatWhen(item.start)}
          {who ? ` · ${who}` : ''}
        </Text>
      </Pressable>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>{orgRole === 'staff' ? 'My bookings' : 'Bookings'}</Text>
        <Text style={styles.subtitle}>
          {window.from} → {window.to}
        </Text>
        {orgRole === 'staff' ? (
          <Text style={styles.staffNote}>Showing only bookings assigned to you.</Text>
        ) : null}

        {onOpenAudit ? (
          <Pressable style={styles.auditBtn} onPress={() => onOpenAudit({ orgSlug })}>
            <Text style={styles.auditBtnText}>Cancelled / Deleted</Text>
          </Pressable>
        ) : null}
      </View>

      {loading ? (
        <View style={{ paddingTop: 18 }}>
          <ActivityIndicator />
        </View>
      ) : error ? (
        <View style={styles.card}>
          <Text style={styles.cardText}>Error: {error}</Text>
        </View>
      ) : (
        <FlatList
          data={bookings}
          keyExtractor={(b) => String(b.id)}
          renderItem={renderItem}
          onRefresh={load}
          refreshing={loading}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyText}>No bookings found in this window.</Text>
            </View>
          }
          contentContainerStyle={styles.listContent}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#fff',
  },
  header: {
    paddingHorizontal: 24,
    paddingTop: 18,
  },
  listContent: {
    paddingHorizontal: 24,
    paddingBottom: 24,
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#111827',
  },
  subtitle: {
    marginTop: 8,
    color: '#6b7280',
  },
  staffNote: {
    marginTop: 8,
    color: '#6b7280',
  },
  auditBtn: {
    marginTop: 12,
    alignSelf: 'flex-start',
    backgroundColor: '#111827',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
  },
  auditBtnText: {
    color: 'white',
    fontWeight: '700',
  },
  card: {
    marginTop: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#fff',
    padding: 14,
    borderRadius: 12,
  },
  cardText: {
    color: '#374151',
  },
  bookingRow: {
    marginTop: 10,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 12,
    backgroundColor: '#fff',
  },
  bookingTitle: {
    fontWeight: '700',
    color: '#111827',
  },
  bookingMeta: {
    marginTop: 4,
    color: '#6b7280',
  },
  empty: {
    paddingTop: 18,
  },
  emptyText: {
    color: '#6b7280',
  },
});
