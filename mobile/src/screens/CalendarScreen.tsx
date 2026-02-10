import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from 'react-native';
import { Calendar, DateData } from 'react-native-calendars';
import { useFocusEffect } from '@react-navigation/native';

import type { ApiError, BookingListItem } from '../lib/api';
import { apiGetBookings } from '../lib/api';
import { onBookingsChanged } from '../lib/bookingsSync';

type Props = {
  orgSlug: string;
  onOpenBooking: (args: { orgSlug: string; bookingId: number }) => void;
};

function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function monthWindow(yyyyMmDd: string): { from: string; toExclusive: string } {
  const [y, m] = yyyyMmDd.split('-').map((x) => Number(x));
  const first = new Date(y, (m || 1) - 1, 1);
  const nextMonth = new Date(y, (m || 1), 1);
  return { from: isoDate(first), toExclusive: isoDate(nextMonth) };
}

function monthAnchorForDate(dateString: string): string {
  return `${dateString.slice(0, 7)}-01`;
}

function shiftMonth(anchor: string, deltaMonths: number): string {
  const [y, m] = anchor.split('-').map((x) => Number(x));
  const d = new Date(y, (m || 1) - 1, 1);
  d.setMonth(d.getMonth() + deltaMonths);
  return isoDate(new Date(d.getFullYear(), d.getMonth(), 1));
}

function localDayKey(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return isoDate(d);
}

function formatWhen(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function CalendarScreen({ orgSlug, onOpenBooking }: Props) {
  const today = useMemo(() => isoDate(new Date()), []);
  const [selected, setSelected] = useState<string>(today);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bookingsByDay, setBookingsByDay] = useState<Record<string, BookingListItem[]>>({});
  const [activeMonthAnchor, setActiveMonthAnchor] = useState<string>(monthAnchorForDate(today));
  const didFirstFocus = useRef(false);

  async function loadMonth(anchor: string) {
    setLoading(true);
    setError(null);
    try {
      const w = monthWindow(anchor);
      const resp = await apiGetBookings({ org: orgSlug, from: w.from, to: w.toExclusive, limit: 2000 });
      const next: Record<string, BookingListItem[]> = {};
      for (const b of resp.bookings ?? []) {
        const k = localDayKey(b.start);
        if (!k) continue;
        if (!next[k]) next[k] = [];
        next[k].push(b);
      }
      // Sort each day's bookings by start time.
      for (const k of Object.keys(next)) {
        next[k].sort((a, b) => {
          const ta = new Date(a.start ?? 0).getTime();
          const tb = new Date(b.start ?? 0).getTime();
          return ta - tb;
        });
      }
      setBookingsByDay(next);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to load calendar.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadMonth(activeMonthAnchor);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug, activeMonthAnchor]);

  // Refresh when returning to this screen (e.g., after cancelling/deleting a booking).
  useFocusEffect(
    React.useCallback(() => {
      if (!didFirstFocus.current) {
        didFirstFocus.current = true;
        return () => undefined;
      }
      loadMonth(activeMonthAnchor);
      return () => undefined;
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [orgSlug, activeMonthAnchor])
  );

  useEffect(() => {
    return onBookingsChanged(({ orgSlug: changedOrgSlug }) => {
      if (changedOrgSlug && changedOrgSlug !== orgSlug) return;
      loadMonth(activeMonthAnchor);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug, activeMonthAnchor]);

  const markedDates = useMemo(() => {
    const marked: Record<string, any> = {};
    for (const day of Object.keys(bookingsByDay)) {
      marked[day] = { marked: true, dotColor: '#2563eb' };
    }
    marked[selected] = {
      ...(marked[selected] ?? null),
      selected: true,
      selectedColor: '#111827',
    };
    return marked;
  }, [bookingsByDay, selected]);

  const bookingsForSelected = bookingsByDay[selected] ?? [];

  function onDayPress(d: DateData) {
    setSelected(d.dateString);
    const monthAnchor = monthAnchorForDate(d.dateString);
    if (monthAnchor !== activeMonthAnchor) setActiveMonthAnchor(monthAnchor);
  }

  function onMonthChange(m: DateData) {
    if (!m?.dateString) return;
    const nextAnchor = monthAnchorForDate(m.dateString);
    if (nextAnchor !== activeMonthAnchor) setActiveMonthAnchor(nextAnchor);
    // Keep the selected day visible within the month.
    if (selected.slice(0, 7) !== nextAnchor.slice(0, 7)) setSelected(nextAnchor);
  }

  function renderBooking({ item }: { item: BookingListItem }) {
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
        <Text style={styles.title}>Calendar</Text>
        <Text style={styles.subtitle}>Tap a day to see bookings.</Text>

        <View style={styles.calendarCard}>
          <Calendar
            current={activeMonthAnchor}
            enableSwipeMonths
            onDayPress={onDayPress}
            onMonthChange={onMonthChange}
            markedDates={markedDates}
            theme={({
              todayTextColor: '#2563eb',
              arrowColor: '#111827',
              textDayFontWeight: '600',
              textMonthFontWeight: '800',
              textDayHeaderFontWeight: '700',
              // Center the month/year header ("showing calendar for" dropdown area).
              'stylesheet.calendar.header': {
                header: {
                  paddingLeft: 0,
                  paddingRight: 0,
                },
                monthText: {
                  flex: 1,
                  textAlign: 'center',
                },
              },
            } as any)}
          />
        </View>

        <View style={styles.agendaHeader}>
          <Text style={styles.sectionTitle}>Agenda</Text>
          <View style={styles.agendaRight}>
            <Text style={styles.sectionSubtitle}>{selected}</Text>
            <Pressable
              style={[styles.refreshBtn, loading ? styles.refreshBtnDisabled : null]}
              disabled={loading}
              onPress={() => loadMonth(activeMonthAnchor)}
              accessibilityRole="button"
              accessibilityLabel="Refresh agenda"
            >
              <Text style={styles.refreshBtnText}>{loading ? 'Refreshing…' : 'Refresh'}</Text>
            </Pressable>
          </View>
        </View>
      </View>

      {loading ? (
        <View style={{ paddingTop: 12 }}>
          <ActivityIndicator />
        </View>
      ) : error ? (
        <View style={styles.card}>
          <Text style={styles.cardText}>Error: {error}</Text>
        </View>
      ) : (
        <FlatList
          data={bookingsForSelected}
          keyExtractor={(b) => String(b.id)}
          renderItem={renderBooking}
          onRefresh={() => loadMonth(activeMonthAnchor)}
          refreshing={loading}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyText}>No bookings on this day.</Text>
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
    fontWeight: '800',
    color: '#111827',
  },
  subtitle: {
    marginTop: 6,
    color: '#6b7280',
  },
  calendarCard: {
    marginTop: 12,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 12,
    overflow: 'hidden',
  },
  agendaHeader: {
    marginTop: 14,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
  },
  agendaRight: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '900',
    color: '#111827',
  },
  sectionSubtitle: {
    color: '#6b7280',
    fontWeight: '600',
  },
  refreshBtn: {
    backgroundColor: '#111827',
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 10,
  },
  refreshBtnDisabled: {
    opacity: 0.6,
  },
  refreshBtnText: {
    color: '#fff',
    fontWeight: '800',
  },
  card: {
    marginTop: 12,
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
    fontWeight: '800',
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
