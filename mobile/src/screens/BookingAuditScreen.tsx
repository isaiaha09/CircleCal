import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, Linking, Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import * as WebBrowser from 'expo-web-browser';

import type { ApiError, BookingAuditItem } from '../lib/api';
import { apiGetBookingAudit, apiGetMobileSsoLink } from '../lib/api';

type Props = {
  orgSlug: string;
};

function formatWhen(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function labelForEventType(t: string): string {
  if (t === 'deleted') return 'Deleted';
  if (t === 'cancelled') return 'Cancelled';
  return t || 'Event';
}

export function BookingAuditScreen({ orgSlug }: Props) {
  const [items, setItems] = useState<BookingAuditItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState<number | null>(null);
  const [exportingPdf, setExportingPdf] = useState(false);

  const [selected, setSelected] = useState<BookingAuditItem | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const canLoadMore = useMemo(() => {
    if (total == null) return false;
    return items.length < total;
  }, [items.length, total]);

  async function loadFirstPage() {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGetBookingAudit({ org: orgSlug, page: 1, per_page: 50, include_snapshot: true });
      setItems(resp.items ?? []);
      setPage(resp.page ?? 1);
      setTotal(typeof resp.total === 'number' ? resp.total : null);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to load booking audit.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  async function loadNextPage() {
    if (loadingMore || loading) return;
    if (!canLoadMore) return;

    setLoadingMore(true);
    setError(null);
    try {
      const next = page + 1;
      const resp = await apiGetBookingAudit({ org: orgSlug, page: next, per_page: 50, include_snapshot: true });
      const nextItems = resp.items ?? [];
      setItems((prev) => {
        const seen = new Set(prev.map((x) => x.id));
        const merged = [...prev];
        for (const it of nextItems) {
          if (!seen.has(it.id)) merged.push(it);
        }
        return merged;
      });
      setPage(resp.page ?? next);
      setTotal(typeof resp.total === 'number' ? resp.total : null);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to load more audit entries.';
      setError(msg);
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleExportPdf() {
    if (exportingPdf) return;
    if (!items.length) {
      Alert.alert('Nothing to export', 'No audit entries are loaded yet.');
      return;
    }

    const ids = items
      .map((x) => x.id)
      .filter((x) => typeof x === 'number' && Number.isFinite(x))
      .slice(0, 200);

    if (!ids.length) {
      Alert.alert('Nothing to export', 'No valid audit IDs were found.');
      return;
    }

    try {
      setExportingPdf(true);
      // Use a one-time SSO link so the system browser can download the PDF.
      // This avoids WebView download -> blob: URLs (which RN WebView can't open).
      const safeSlug = encodeURIComponent(orgSlug);
      const safeIds = encodeURIComponent(ids.join(','));
      const nextPath = `/bus/${safeSlug}/bookings/audit/export/?ids=${safeIds}`;
      const resp = await apiGetMobileSsoLink({ next: nextPath });
      // Prefer the real external browser for downloads.
      // `openBrowserAsync` uses an in-app browser which can break file downloads (blob: URLs).
      try {
        await Linking.openURL(resp.url);
      } catch {
        await WebBrowser.openBrowserAsync(resp.url);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not open export.';
      Alert.alert('Export failed', msg);
    } finally {
      setExportingPdf(false);
    }
  }

  useEffect(() => {
    loadFirstPage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug]);

  function openDetails(item: BookingAuditItem) {
    setSelected(item);
    setModalOpen(true);
  }

  function closeDetails() {
    setModalOpen(false);
    setSelected(null);
  }

  function renderItem({ item }: { item: BookingAuditItem }) {
    const title = item.service?.name || item.public_ref || (item.booking_id ? `Booking #${item.booking_id}` : 'Booking');
    const who = item.client_name || item.client_email;
    const eventLabel = labelForEventType(item.event_type);

    return (
      <Pressable style={styles.row} onPress={() => openDetails(item)}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 12 }}>
          <Text style={styles.rowTitle} numberOfLines={1}>
            {title}
          </Text>
          <Text style={styles.badge}>{eventLabel}</Text>
        </View>
        <Text style={styles.rowMeta} numberOfLines={2}>
          {formatWhen(item.start)}
          {who ? ` · ${who}` : ''}
        </Text>
        <Text style={styles.rowMetaMuted} numberOfLines={1}>
          Audited {formatWhen(item.created_at)}
        </Text>
      </Pressable>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <View style={{ flex: 1 }}>
            <Text style={styles.title}>Cancelled / Deleted</Text>
            <Text style={styles.subtitle}>Recent booking changes for this business.</Text>
          </View>
          <Pressable
            onPress={() => void handleExportPdf()}
            disabled={loading || !items.length || exportingPdf}
            style={[styles.secondaryBtn, (loading || !items.length || exportingPdf) ? { opacity: 0.7 } : null]}
          >
            <View style={styles.secondaryBtnRow}>
              {exportingPdf ? <ActivityIndicator size="small" color="#ffffff" style={styles.secondaryBtnSpinner} /> : null}
              <Text style={styles.secondaryBtnText}>{exportingPdf ? 'Preparing…' : 'Export PDF'}</Text>
            </View>
          </Pressable>
        </View>
      </View>

      {loading ? (
        <View style={{ paddingTop: 18 }}>
          <ActivityIndicator />
        </View>
      ) : error ? (
        <View style={styles.card}>
          <Text style={styles.cardText}>Error: {error}</Text>
          <Pressable onPress={loadFirstPage} style={styles.primaryBtn}>
            <Text style={styles.primaryBtnText}>Retry</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(x) => String(x.id)}
          renderItem={renderItem}
          onRefresh={loadFirstPage}
          refreshing={loading}
          onEndReached={() => {
            if (canLoadMore) void loadNextPage();
          }}
          onEndReachedThreshold={0.6}
          ListFooterComponent={
            loadingMore ? (
              <View style={{ paddingVertical: 16 }}>
                <ActivityIndicator />
              </View>
            ) : canLoadMore ? (
              <View style={{ paddingVertical: 16, alignItems: 'center' }}>
                <Pressable onPress={() => void loadNextPage()} style={styles.secondaryBtn}>
                  <Text style={styles.secondaryBtnText}>Load more</Text>
                </Pressable>
              </View>
            ) : (
              <View style={{ paddingVertical: 16 }} />
            )
          }
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyText}>No recent cancellations or deletions.</Text>
            </View>
          }
          contentContainerStyle={styles.listContent}
        />
      )}

      <Modal visible={modalOpen} animationType="slide" onRequestClose={closeDetails}>
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Audit details</Text>
            <Pressable onPress={closeDetails} style={styles.modalClose}>
              <Text style={styles.modalCloseText}>Close</Text>
            </Pressable>
          </View>

          {selected ? (
            <View style={styles.modalBody}>
              <Text style={styles.modalMeta}>{labelForEventType(selected.event_type)}</Text>
              <Text style={styles.modalMeta}>{selected.service?.name || ''}</Text>
              <Text style={styles.modalMeta}>{formatWhen(selected.start)}</Text>
              <Text style={styles.modalMetaMuted}>Audited {formatWhen(selected.created_at)}</Text>

              <View style={styles.snapshotBox}>
                <Text style={styles.snapshotText}>
                  {selected.snapshot ? JSON.stringify(selected.snapshot, null, 2) : 'No snapshot available.'}
                </Text>
              </View>

              {selected.extra ? <Text style={styles.modalMetaMuted}>Note: {selected.extra}</Text> : null}
            </View>
          ) : null}
        </View>
      </Modal>
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
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#111827',
  },
  subtitle: {
    marginTop: 8,
    color: '#6b7280',
  },
  listContent: {
    paddingHorizontal: 24,
    paddingBottom: 24,
  },
  row: {
    marginTop: 10,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 12,
    backgroundColor: '#fff',
  },
  rowTitle: {
    fontWeight: '700',
    color: '#111827',
    flex: 1,
  },
  badge: {
    color: '#111827',
    fontWeight: '700',
  },
  rowMeta: {
    marginTop: 4,
    color: '#374151',
  },
  rowMetaMuted: {
    marginTop: 4,
    color: '#6b7280',
    fontSize: 12,
  },
  empty: {
    paddingTop: 18,
  },
  emptyText: {
    color: '#6b7280',
  },
  card: {
    marginTop: 16,
    marginHorizontal: 24,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#fff',
    padding: 14,
    borderRadius: 12,
  },
  cardText: {
    color: '#374151',
  },
  primaryBtn: {
    marginTop: 12,
    backgroundColor: '#2563eb',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    alignSelf: 'flex-start',
  },
  primaryBtnText: {
    color: 'white',
    fontWeight: '700',
  },
  secondaryBtn: {
    backgroundColor: '#111827',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
  },
  secondaryBtnRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  secondaryBtnSpinner: {
    marginRight: 8,
  },
  secondaryBtnText: {
    color: 'white',
    fontWeight: '700',
  },
  modalContainer: {
    flex: 1,
    backgroundColor: '#fff',
  },
  modalHeader: {
    paddingTop: 18,
    paddingHorizontal: 16,
    paddingBottom: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#e5e7eb',
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#111827',
  },
  modalClose: {
    paddingVertical: 8,
    paddingHorizontal: 10,
  },
  modalCloseText: {
    color: '#2563eb',
    fontWeight: '700',
  },
  modalBody: {
    padding: 16,
  },
  modalMeta: {
    color: '#111827',
    fontWeight: '700',
    marginBottom: 6,
  },
  modalMetaMuted: {
    color: '#6b7280',
    marginTop: 8,
  },
  snapshotBox: {
    marginTop: 12,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#f9fafb',
    padding: 12,
    borderRadius: 12,
  },
  snapshotText: {
    fontFamily: 'Courier',
    color: '#111827',
  },
});
