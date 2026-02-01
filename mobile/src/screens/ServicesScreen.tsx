import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Alert, FlatList, Pressable, StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import type { ApiError, OrgListItem, ServiceListItem } from '../lib/api';
import { apiCreateService, apiGetOrgs, apiGetServices, apiPatchService } from '../lib/api';
import { canManageServices, humanRole, normalizeOrgRole } from '../lib/permissions';

type Props = {
  orgSlug: string;
  onOpenEdit: (args: { orgSlug: string; serviceId: number }) => void;
};

function toPriceDisplay(v: number | string): string {
  if (typeof v === 'number') return `$${v.toFixed(2)}`;
  const s = String(v);
  if (!s) return '$0.00';
  // If backend returns a string decimal, keep it stable.
  return s.startsWith('$') ? s : `$${s}`;
}

export function ServicesScreen({ orgSlug, onOpenEdit }: Props) {
  const [services, setServices] = useState<ServiceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [role, setRole] = useState<string>('');
  const [roleLoading, setRoleLoading] = useState(true);

  const [name, setName] = useState('');
  const [duration, setDuration] = useState('60');
  const [price, setPrice] = useState('0');

  const canCreate = useMemo(() => {
    const n = name.trim();
    const d = parseInt(duration, 10);
    const p = Number(price);
    return n.length > 0 && Number.isFinite(d) && d > 0 && Number.isFinite(p) && p >= 0;
  }, [name, duration, price]);

  const allowed = useMemo(() => canManageServices(role), [role]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGetServices({ org: orgSlug });
      setServices(resp.services ?? []);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to load services.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setRoleLoading(true);
        const orgsResp = await apiGetOrgs();
        const org = (orgsResp.orgs ?? []).find((o: OrgListItem) => o.slug === orgSlug);
        const nextRole = normalizeOrgRole(org?.role);
        if (cancelled) return;
        setRole(nextRole);
        setRoleLoading(false);
        if (!canManageServices(nextRole)) {
          setError(`Services are restricted to Owners/GMs/Managers (you are ${humanRole(nextRole)}).`);
          setServices([]);
          setLoading(false);
          return;
        }
        await load();
      } catch (e) {
        const err = e as Partial<ApiError>;
        const body = err.body as any;
        const msg =
          (typeof body?.detail === 'string' && body.detail) ||
          (typeof err.message === 'string' && err.message) ||
          'Failed to load services.';
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setRoleLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug]);

  if (roleLoading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator />
      </View>
    );
  }

  if (!allowed) {
    return (
      <View style={styles.container}>
        <Text style={styles.title}>Services</Text>
        <Text style={styles.subtitle}>Manage your service offerings</Text>
        <View style={styles.card}>
          <Text style={styles.cardText}>{error || 'Services are restricted to Owners/GMs/Managers.'}</Text>
        </View>
      </View>
    );
  }

  async function handleCreate() {
    if (!canCreate) return;
    setSaving(true);
    try {
      const d = parseInt(duration, 10);
      const p = Number(price);
      const created = await apiCreateService({ org: orgSlug, name: name.trim(), duration: d, price: p });
      setServices((prev) => [created.service, ...prev]);
      setName('');
      setDuration('60');
      setPrice('0');
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.name === 'string' && body.name) ||
        (typeof body?.duration === 'string' && body.duration) ||
        (typeof body?.price === 'string' && body.price) ||
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to create service.';
      Alert.alert('Create failed', msg);
    } finally {
      setSaving(false);
    }
  }

  async function handleToggle(serviceId: number, nextActive: boolean) {
    // optimistic
    setServices((prev) => prev.map((s) => (s.id === serviceId ? { ...s, is_active: nextActive } : s)));
    try {
      await apiPatchService({ org: orgSlug, serviceId, patch: { is_active: nextActive } });
    } catch (e) {
      // rollback
      setServices((prev) => prev.map((s) => (s.id === serviceId ? { ...s, is_active: !nextActive } : s)));
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to update service.';
      Alert.alert('Update failed', msg);
    }
  }

  function renderItem({ item }: { item: ServiceListItem }) {
    return (
      <View style={styles.row}>
        <Pressable style={{ flex: 1 }} onPress={() => onOpenEdit({ orgSlug, serviceId: item.id })}>
          <Text style={styles.rowTitle}>{item.name}</Text>
          <Text style={styles.rowMeta}>
            {item.duration} min · {toPriceDisplay(item.price)}
          </Text>
        </Pressable>

        <Switch value={!!item.is_active} onValueChange={(v) => handleToggle(item.id, v)} />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Services</Text>
        <Text style={styles.subtitle}>Manage your service offerings</Text>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Create a service</Text>

          <TextInput value={name} onChangeText={setName} placeholder="Service name" style={styles.input} />

          <View style={styles.inlineRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.inlineLabel}>Duration (min)</Text>
              <TextInput
                value={duration}
                onChangeText={setDuration}
                keyboardType="number-pad"
                style={styles.input}
              />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.inlineLabel}>Price</Text>
              <TextInput value={price} onChangeText={setPrice} keyboardType="decimal-pad" style={styles.input} />
            </View>
          </View>

          <Pressable
            style={[styles.primaryBtn, !canCreate || saving ? styles.primaryBtnDisabled : null]}
            disabled={!canCreate || saving}
            onPress={handleCreate}
          >
            {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Create</Text>}
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
          <Pressable style={[styles.secondaryBtn, { marginTop: 12 }]} onPress={load}>
            <Text style={styles.secondaryBtnText}>Retry</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={services}
          keyExtractor={(s) => String(s.id)}
          renderItem={renderItem}
          onRefresh={load}
          refreshing={loading}
          ListEmptyComponent={
            <View style={styles.empty}>
              <Text style={styles.emptyText}>No services yet. Create your first one above.</Text>
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
  card: {
    marginTop: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#fff',
    padding: 14,
    borderRadius: 12,
  },
  cardTitle: {
    fontWeight: '800',
    color: '#111827',
  },
  cardText: {
    marginTop: 8,
    color: '#374151',
  },
  input: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    fontSize: 16,
    marginTop: 10,
    backgroundColor: '#fff',
  },
  inlineRow: {
    marginTop: 6,
    flexDirection: 'row',
    gap: 10,
  },
  inlineLabel: {
    marginTop: 10,
    fontWeight: '700',
    color: '#111827',
  },
  primaryBtn: {
    marginTop: 14,
    backgroundColor: '#2563eb',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 10,
    alignItems: 'center',
  },
  primaryBtnDisabled: {
    opacity: 0.5,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 16,
  },
  secondaryBtn: {
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
  row: {
    marginTop: 10,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 12,
    backgroundColor: '#fff',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  rowTitle: {
    fontWeight: '700',
    color: '#111827',
  },
  rowMeta: {
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
