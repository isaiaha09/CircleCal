import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { ApiError, FacilityResourceListItem } from '../lib/api';
import { apiCreateResource, apiGetOrgs, apiGetResources, apiUpdateResource } from '../lib/api';
import { canManageBilling, canManageResources, humanRole, normalizeOrgRole } from '../lib/permissions';

type Props = {
  orgSlug: string;
  onOpenPricing: (args: { orgSlug: string }) => void;
};

function errorMessage(e: unknown, fallback: string): string {
  const err = e as Partial<ApiError>;
  const body = err.body as any;
  return (
    (typeof body?.detail === 'string' && body.detail) ||
    (typeof body?.name === 'string' && body.name) ||
    (typeof err.message === 'string' && err.message) ||
    fallback
  );
}

export function ResourcesScreen({ orgSlug, onOpenPricing }: Props) {
  const [resources, setResources] = useState<FacilityResourceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [roleLoading, setRoleLoading] = useState(true);
  const [orgRole, setOrgRole] = useState<string>('');
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [maxServices, setMaxServices] = useState('1');
  const [creating, setCreating] = useState(false);

  const canCreate = useMemo(() => (name || '').trim().length > 0, [name]);
  const allowed = useMemo(() => canManageResources(orgRole), [orgRole]);
  const canSeePricing = useMemo(() => canManageBilling(orgRole), [orgRole]);

  async function load() {
    setError(null);
    try {
      const resp = await apiGetResources({ org: orgSlug });
      setResources(resp.resources ?? []);
    } catch (e) {
      setError(errorMessage(e, 'Failed to load resources.'));
    }
  }

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setRoleLoading(true);
        const orgsResp = await apiGetOrgs();
        const found = (orgsResp.orgs ?? []).find((o) => o.slug === orgSlug);
        const role = normalizeOrgRole(found?.role);
        if (!cancelled) setOrgRole(role);
        if (!cancelled) setRoleLoading(false);

        if (!canManageResources(role)) {
          setResources([]);
          setError(`Resources are restricted to Owners/GMs/Managers (you are ${humanRole(role)}).`);
          return;
        }
        await load();
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug]);

  async function onRefresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  async function handleCreate() {
    const nm = (name || '').trim();
    if (!nm) return;

    let ms: number | undefined = undefined;
    const raw = (maxServices || '').trim();
    if (raw !== '') {
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed < 0) {
        Alert.alert('Create resource', 'Max services must be a number ≥ 0.');
        return;
      }
      ms = Math.floor(parsed);
    }

    setCreating(true);
    try {
      await apiCreateResource({ org: orgSlug, name: nm, max_services: ms });
      setName('');
      setMaxServices('1');
      await load();
    } catch (e) {
      Alert.alert('Create failed', errorMessage(e, 'Could not create resource.'));
    } finally {
      setCreating(false);
    }
  }

  async function toggleActive(r: FacilityResourceListItem) {
    if (!r.is_active) {
      try {
        await apiUpdateResource({ org: orgSlug, resourceId: r.id, patch: { is_active: true } });
        await load();
      } catch (e) {
        Alert.alert('Update failed', errorMessage(e, 'Could not update resource.'));
      }
      return;
    }

    if (r.in_use) {
      Alert.alert('Can’t deactivate', 'This resource is linked to a service. Unlink it first.');
      return;
    }

    try {
      await apiUpdateResource({ org: orgSlug, resourceId: r.id, patch: { is_active: false } });
      await load();
    } catch (e) {
      Alert.alert('Update failed', errorMessage(e, 'Could not update resource.'));
    }
  }

  const header = (
    <View style={styles.headerPad}>
      <Text style={styles.title}>Resources</Text>
      <Text style={styles.subtitle}>Manage rooms, cages, fields, and other capacity</Text>

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
          {canSeePricing ? (
            <Pressable style={styles.primaryBtn} onPress={() => onOpenPricing({ orgSlug })}>
              <Text style={styles.primaryBtnText}>View plans</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Create resource</Text>
        <TextInput
          value={name}
          onChangeText={setName}
          placeholder="Resource name (e.g., Cage #1)"
          style={styles.input}
        />
        <TextInput
          value={maxServices}
          onChangeText={setMaxServices}
          placeholder="Max services (0 = unlimited)"
          keyboardType="number-pad"
          style={styles.input}
        />

        <Pressable
          style={[styles.primaryBtn, (!canCreate || creating) ? styles.primaryBtnDisabled : null]}
          onPress={handleCreate}
          disabled={!canCreate || creating}
        >
          <Text style={styles.primaryBtnText}>{creating ? 'Creating…' : 'Create'}</Text>
        </Pressable>
      </View>

      <View style={styles.sectionRow}>
        <Text style={styles.sectionTitle}>Your resources</Text>
        <Text style={styles.sectionMeta}>{resources.length}</Text>
      </View>

      {resources.length === 0 ? <Text style={styles.emptyText}>No resources yet.</Text> : null}
    </View>
  );

  if (loading || roleLoading) {
    return (
      <View style={styles.loadingBox}>
        <ActivityIndicator />
        <Text style={styles.loadingText}>Loading resources…</Text>
      </View>
    );
  }

  if (!allowed) {
    return (
      <View style={{ flex: 1, padding: 16, paddingTop: 12, backgroundColor: '#fff' }}>
        <Text style={styles.title}>Resources</Text>
        <Text style={styles.subtitle}>Manage rooms, cages, fields, and other capacity</Text>
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error || 'Resources are restricted to Owners/GMs/Managers.'}</Text>
        </View>
      </View>
    );
  }

  return (
    <FlatList
      data={resources}
      keyExtractor={(r) => String(r.id)}
      ListHeaderComponent={header}
      contentContainerStyle={styles.listContent}
      refreshing={refreshing}
      onRefresh={onRefresh}
      renderItem={({ item }) => (
        <View style={styles.rowCard}>
          <View style={styles.rowTextCol}>
            <Text style={styles.rowTitle}>{item.name}</Text>
            <Text style={styles.rowMeta}>Slug: {item.slug}</Text>
            <Text style={styles.rowMeta}>Max services: {item.max_services}</Text>
            <Text style={styles.rowMeta}>Status: {item.is_active ? 'Active' : 'Inactive'}</Text>
          </View>
          <Pressable
            style={[styles.secondaryBtn, item.is_active ? styles.secondaryBtnDanger : styles.secondaryBtnOk]}
            onPress={() => toggleActive(item)}
          >
            <Text style={[styles.secondaryBtnText, item.is_active ? styles.secondaryBtnTextDanger : styles.secondaryBtnTextOk]}>
              {item.is_active ? 'Deactivate' : 'Activate'}
            </Text>
          </Pressable>
        </View>
      )}
      ListFooterComponent={<View style={styles.footerPad} />}
    />
  );
}

const styles = StyleSheet.create({
  listContent: {
    paddingBottom: 24,
  },
  headerPad: {
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 8,
  },
  footerPad: {
    height: 12,
  },
  loadingBox: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  loadingText: {
    marginTop: 8,
    color: '#555',
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    marginBottom: 4,
  },
  subtitle: {
    color: '#666',
    marginBottom: 12,
  },
  sectionRow: {
    marginTop: 14,
    marginBottom: 8,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '700',
  },
  sectionMeta: {
    color: '#666',
  },
  emptyText: {
    color: '#666',
    marginBottom: 6,
  },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: '#eee',
  },
  cardTitle: {
    fontWeight: '700',
    marginBottom: 10,
    fontSize: 16,
  },
  input: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 10,
    backgroundColor: '#fafafa',
  },
  primaryBtn: {
    backgroundColor: '#2563eb',
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
  },
  primaryBtnDisabled: {
    opacity: 0.5,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '700',
  },
  rowCard: {
    marginHorizontal: 16,
    marginBottom: 10,
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: '#eee',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  rowTextCol: {
    flex: 1,
  },
  rowTitle: {
    fontWeight: '800',
    fontSize: 15,
    marginBottom: 2,
  },
  rowMeta: {
    color: '#666',
    marginTop: 1,
  },
  secondaryBtn: {
    paddingHorizontal: 10,
    paddingVertical: 10,
    borderRadius: 10,
    borderWidth: 1,
    backgroundColor: '#fff',
  },
  secondaryBtnDanger: {
    borderColor: '#fecaca',
    backgroundColor: '#fef2f2',
  },
  secondaryBtnOk: {
    borderColor: '#bbf7d0',
    backgroundColor: '#f0fdf4',
  },
  secondaryBtnText: {
    fontWeight: '800',
  },
  secondaryBtnTextDanger: {
    color: '#b91c1c',
  },
  secondaryBtnTextOk: {
    color: '#166534',
  },
  errorBox: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: '#fecaca',
    marginBottom: 12,
    gap: 10,
  },
  errorText: {
    color: '#b91c1c',
    fontWeight: '600',
  },
});
