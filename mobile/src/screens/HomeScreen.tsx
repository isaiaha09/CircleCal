import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';

import type { ApiError } from '../lib/api';
import type { BookingListItem, OrgListItem } from '../lib/api';
import { apiGet, apiGetBookings, apiGetOrgs } from '../lib/api';
import { clearActiveOrgSlug, getActiveOrgSlug, setActiveOrgSlug, signOut } from '../lib/auth';

type Props = {
  onSignedOut: () => void;
  onOpenBooking: (args: { orgSlug: string; bookingId: number }) => void;
  onOpenCalendar: (args: { orgSlug: string }) => void;
  onOpenSchedule: (args: { orgSlug: string }) => void;
  onOpenPortal: (args: { title: string }) => void;
  onOpenBookings: (args: { orgSlug: string }) => void;
  onOpenBilling: (args: { orgSlug: string }) => void;
  onOpenPricing: (args: { orgSlug: string }) => void;
  onOpenBusinesses: () => void;
  onOpenProfile: () => void;
  onOpenServices: (args: { orgSlug: string }) => void;
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

function isSameLocalDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function safeParseDate(iso: string | null): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function HomeScreen({
  onSignedOut,
  onOpenBooking,
  onOpenCalendar,
  onOpenSchedule,
  onOpenPortal,
  onOpenBookings,
  onOpenBilling,
  onOpenPricing,
  onOpenBusinesses,
  onOpenProfile,
  onOpenServices,
}: Props) {
  const [me, setMe] = useState<{ username: string; email: string } | null>(null);
  const [orgs, setOrgs] = useState<OrgListItem[]>([]);
  const [activeOrg, setActiveOrg] = useState<OrgListItem | null>(null);
  const [bookings, setBookings] = useState<BookingListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingBookings, setLoadingBookings] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const window = useMemo(() => {
    const from = new Date();
    const to = new Date();
    to.setDate(to.getDate() + 14);
    return { from: isoDate(from), to: isoDate(to) };
  }, []);

  async function loadBookings(orgSlug: string) {
    setLoadingBookings(true);
    try {
      const resp = await apiGetBookings({ org: orgSlug, from: window.from, to: window.to, limit: 200 });
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
      setLoadingBookings(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [meResp, orgsResp] = await Promise.all([
          apiGet<{ username: string; email: string }>('/api/v1/me/'),
          apiGetOrgs(),
        ]);

        if (cancelled) return;
        setMe(meResp);
        const nextOrgs = orgsResp.orgs ?? [];
        setOrgs(nextOrgs);

        const storedSlug = await getActiveOrgSlug();
        if (cancelled) return;

        const chosen =
          (storedSlug && nextOrgs.find((o) => o.slug === storedSlug)) || nextOrgs[0] || null;
        setActiveOrg(chosen);
        if (chosen) {
          await setActiveOrgSlug(chosen.slug);
          if (!cancelled) await loadBookings(chosen.slug);
        }
      } catch (e) {
        const err = e as Partial<ApiError>;
        const body = err.body as any;
        const msg =
          (typeof body?.detail === 'string' && body.detail) ||
          (typeof err.message === 'string' && err.message) ||
          'Failed to load profile.';
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // When returning from other screens (like Businesses), refresh selected org and bookings.
  useFocusEffect(
    React.useCallback(() => {
      let cancelled = false;
      (async () => {
        if (loading) return;
        try {
          const storedSlug = await getActiveOrgSlug();
          if (cancelled) return;
          if (!storedSlug || storedSlug === activeOrg?.slug) return;
          const chosen = orgs.find((o) => o.slug === storedSlug) || null;
          if (!chosen) return;
          setActiveOrg(chosen);
          await loadBookings(chosen.slug);
        } catch {
          // ignore
        }
      })();
      return () => {
        cancelled = true;
      };
    }, [loading, activeOrg?.slug, orgs])
  );

  async function handleSignOut() {
    await Promise.all([signOut(), clearActiveOrgSlug()]);
    onSignedOut();
  }

  async function handleSelectOrg(o: OrgListItem) {
    setActiveOrg(o);
    await setActiveOrgSlug(o.slug);
    await loadBookings(o.slug);
  }

  const header = (
    <>
      <Text style={styles.title}>Dashboard</Text>
      <Text style={styles.subtitle}>{activeOrg ? activeOrg.name : 'Select a business'}</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Business</Text>
        {orgs.length === 0 ? (
          <Text style={styles.cardText}>No businesses found for this account.</Text>
        ) : orgs.length === 1 && activeOrg ? (
          <Text style={styles.cardText}>{activeOrg.name}</Text>
        ) : (
          <View style={styles.orgList}>
            {orgs.map((o) => {
              const selected = activeOrg?.slug === o.slug;
              return (
                <Pressable
                  key={o.slug}
                  style={[styles.orgBtn, selected ? styles.orgBtnSelected : null]}
                  onPress={() => handleSelectOrg(o)}
                >
                  <Text style={[styles.orgBtnText, selected ? styles.orgBtnTextSelected : null]}>
                    {o.name}
                  </Text>
                  <Text style={[styles.orgBtnRole, selected ? styles.orgBtnRoleSelected : null]}>
                    {o.role}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Quick glance</Text>

        <Text style={styles.cardText}>
          {me ? `Signed in as ${me.username || me.email}` : 'Signed in'}
        </Text>

        <View style={styles.statsRow}>
          {(() => {
            const now = new Date();
            const upcoming = bookings
              .map((b) => ({ b, start: safeParseDate(b.start) }))
              .filter((x) => x.start && x.start.getTime() >= now.getTime())
              .sort((a, b) => a.start!.getTime() - b.start!.getTime());

            const todayCount = bookings
              .map((b) => safeParseDate(b.start))
              .filter((d) => d && isSameLocalDay(d, now)).length;

            const next = upcoming[0]?.b ?? null;
            const nextTitle = next ? next.service?.name || next.title || 'Booking' : 'None';
            const nextWhen = next ? formatWhen(next.start) : '';

            return (
              <>
                <View style={styles.statCard}>
                  <Text style={styles.statLabel}>Today</Text>
                  <Text style={styles.statValue}>{todayCount}</Text>
                </View>
                <View style={styles.statCardWide}>
                  <Text style={styles.statLabel}>Next booking</Text>
                  <Text style={styles.statValueSmall}>{nextTitle}</Text>
                  <Text style={styles.statMeta}>{nextWhen || '—'}</Text>
                </View>
              </>
            );
          })()}
        </View>

        <View style={styles.windowRow}>
          <Text style={styles.windowLabel}>Upcoming window</Text>
          <Text style={styles.windowValue}>
            {window.from} → {window.to}
          </Text>
        </View>

        {loadingBookings ? <ActivityIndicator style={{ marginTop: 10 }} /> : null}
        {error ? <Text style={[styles.cardText, { marginTop: 10 }]}>Error: {error}</Text> : null}
      </View>

      <Text style={styles.sectionTitle}>Portals</Text>
      <View style={styles.portalGrid}>
        <Pressable
          style={[styles.portalTile, !activeOrg ? styles.portalTileDisabled : null]}
          disabled={!activeOrg}
          onPress={() => (activeOrg ? onOpenBilling({ orgSlug: activeOrg.slug }) : null)}
        >
          <Text style={[styles.portalTitle, !activeOrg ? styles.portalTitleDisabled : null]}>Billing</Text>
          <Text style={[styles.portalSubtitle, !activeOrg ? styles.portalSubtitleDisabled : null]}>
            Plan, trial, payment methods
          </Text>
        </Pressable>

        <Pressable
          style={[styles.portalTile, !activeOrg ? styles.portalTileDisabled : null]}
          disabled={!activeOrg}
          onPress={() => (activeOrg ? onOpenBookings({ orgSlug: activeOrg.slug }) : null)}
        >
          <Text style={[styles.portalTitle, !activeOrg ? styles.portalTitleDisabled : null]}>Bookings</Text>
          <Text style={[styles.portalSubtitle, !activeOrg ? styles.portalSubtitleDisabled : null]}>
            View all client bookings
          </Text>
        </Pressable>

        <Pressable style={styles.portalTile} onPress={onOpenBusinesses}>
          <Text style={styles.portalTitle}>Businesses</Text>
          <Text style={styles.portalSubtitle}>View and manage your businesses</Text>
        </Pressable>

        <Pressable
          style={[styles.portalTile, !activeOrg ? styles.portalTileDisabled : null]}
          disabled={!activeOrg}
          onPress={() => (activeOrg ? onOpenCalendar({ orgSlug: activeOrg.slug }) : null)}
        >
          <Text style={[styles.portalTitle, !activeOrg ? styles.portalTitleDisabled : null]}>Calendar</Text>
          <Text style={[styles.portalSubtitle, !activeOrg ? styles.portalSubtitleDisabled : null]}>
            Month view & daily agenda
          </Text>
        </Pressable>

        <Pressable style={[styles.portalTile, styles.portalTileDisabled]} disabled>
          <Text style={[styles.portalTitle, styles.portalTitleDisabled]}>Resources</Text>
          <Text style={[styles.portalSubtitle, styles.portalSubtitleDisabled]}>
            Manage space and capacity (Team plan only)
          </Text>
        </Pressable>

        <Pressable
          style={[styles.portalTile, !activeOrg ? styles.portalTileDisabled : null]}
          disabled={!activeOrg}
          onPress={() => (activeOrg ? onOpenPricing({ orgSlug: activeOrg.slug }) : null)}
        >
          <Text style={[styles.portalTitle, !activeOrg ? styles.portalTitleDisabled : null]}>Pricing</Text>
          <Text style={[styles.portalSubtitle, !activeOrg ? styles.portalSubtitleDisabled : null]}>
            {activeOrg ? 'Plans & upgrades' : 'Select a business first'}
          </Text>
        </Pressable>

        <Pressable style={styles.portalTile} onPress={onOpenProfile}>
          <Text style={styles.portalTitle}>Profile</Text>
          <Text style={styles.portalSubtitle}>Update your personal info and settings</Text>
        </Pressable>

        <Pressable
          style={[styles.portalTile, !activeOrg ? styles.portalTileDisabled : null]}
          disabled={!activeOrg}
          onPress={() => (activeOrg ? onOpenServices({ orgSlug: activeOrg.slug }) : null)}
        >
          <Text style={[styles.portalTitle, !activeOrg ? styles.portalTitleDisabled : null]}>Services</Text>
          <Text style={[styles.portalSubtitle, !activeOrg ? styles.portalSubtitleDisabled : null]}>
            Manage your service offerings
          </Text>
        </Pressable>

        <Pressable style={[styles.portalTile, styles.portalTileDisabled]} disabled>
          <Text style={[styles.portalTitle, styles.portalTitleDisabled]}>Staff</Text>
          <Text style={[styles.portalSubtitle, styles.portalSubtitleDisabled]}>
            Manage staff roles & invitations (Team plan only)
          </Text>
        </Pressable>
      </View>

      <Text style={styles.sectionTitle}>Upcoming</Text>
    </>
  );

  function renderItem({ item }: { item: BookingListItem }) {
    const title = item.service?.name || item.title || 'Booking';
    const who = item.client_name || item.client_email || (item.is_blocking ? 'Blocked' : '');
    return (
      <Pressable
        style={styles.bookingRow}
        onPress={() => {
          if (!activeOrg) return;
          onOpenBooking({ orgSlug: activeOrg.slug, bookingId: item.id });
        }}
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
      {loading ? (
        <View style={{ paddingTop: 64 }}>
          <ActivityIndicator />
        </View>
      ) : (
        <FlatList
          data={bookings}
          keyExtractor={(b) => String(b.id)}
          renderItem={renderItem}
          ListHeaderComponent={header}
          contentContainerStyle={{ paddingBottom: 24 }}
          onRefresh={() => (activeOrg ? loadBookings(activeOrg.slug) : Promise.resolve())}
          refreshing={loadingBookings}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyText}>
                {activeOrg
                  ? 'No bookings found in this window.'
                  : 'Select a business to view bookings.'}
              </Text>
            </View>
          }
        />
      )}

      <Pressable style={styles.secondaryBtn} onPress={handleSignOut}>
        <Text style={styles.secondaryBtnText}>Sign out</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 24,
    paddingTop: 18,
    backgroundColor: '#fff',
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
  sectionTitle: {
    marginTop: 16,
    fontSize: 16,
    fontWeight: '700',
    color: '#111827',
  },
  portalGrid: {
    marginTop: 10,
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 10,
  },
  portalTile: {
    width: '48%',
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 12,
    backgroundColor: '#fff',
  },
  portalTileDisabled: {
    backgroundColor: '#f8fafc',
    borderColor: '#e5e7eb',
    opacity: 0.7,
  },
  portalTitle: {
    fontWeight: '800',
    color: '#2563eb',
  },
  portalTitleDisabled: {
    color: '#9ca3af',
  },
  portalSubtitle: {
    marginTop: 4,
    color: '#6b7280',
  },
  portalSubtitleDisabled: {
    color: '#9ca3af',
  },
  card: {
    marginTop: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#fff',
    padding: 14,
    borderRadius: 12,
  },
  cardTitle: {
    fontWeight: '700',
    color: '#111827',
    marginBottom: 8,
  },
  cardText: {
    color: '#374151',
  },
  statsRow: {
    marginTop: 12,
    flexDirection: 'row',
    gap: 10,
  },
  statCard: {
    flex: 1,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: '#fff',
  },
  statCardWide: {
    flex: 2,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
    backgroundColor: '#fff',
  },
  statLabel: {
    color: '#6b7280',
    fontWeight: '600',
  },
  statValue: {
    marginTop: 6,
    fontSize: 26,
    fontWeight: '800',
    color: '#111827',
  },
  statValueSmall: {
    marginTop: 6,
    fontSize: 16,
    fontWeight: '800',
    color: '#111827',
  },
  statMeta: {
    marginTop: 2,
    color: '#6b7280',
  },
  windowRow: {
    marginTop: 12,
  },
  windowLabel: {
    color: '#6b7280',
    fontWeight: '600',
  },
  windowValue: {
    marginTop: 4,
    color: '#374151',
  },
  secondaryBtn: {
    marginTop: 18,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 10,
    alignItems: 'center',
  },
  secondaryBtnText: {
    color: '#111827',
    fontWeight: '600',
  },
  orgList: {
    marginTop: 10,
    gap: 10,
  },
  orgBtn: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    backgroundColor: '#fff',
  },
  orgBtnSelected: {
    borderColor: '#2563eb',
    backgroundColor: '#eff6ff',
  },
  orgBtnText: {
    fontWeight: '700',
    color: '#111827',
  },
  orgBtnTextSelected: {
    color: '#1d4ed8',
  },
  orgBtnRole: {
    marginTop: 2,
    color: '#6b7280',
  },
  orgBtnRoleSelected: {
    color: '#1d4ed8',
  },
  bookingRow: {
    marginTop: 10,
    marginHorizontal: 0,
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
