import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Switch, Text, TextInput, View } from 'react-native';

import type { ApiError, OrgListItem, ServiceListItem } from '../lib/api';
import { apiGetOrgs, apiGetServiceDetail, apiPatchService } from '../lib/api';
import { canManageServices, humanRole, normalizeOrgRole } from '../lib/permissions';

type Props = {
  orgSlug: string;
  serviceId: number;
  onSaved: () => void;
};

export function ServiceEditScreen({ orgSlug, serviceId, onSaved }: Props) {
  const [service, setService] = useState<ServiceListItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [role, setRole] = useState<string>('');
  const [roleLoading, setRoleLoading] = useState(true);

  const initial = useMemo(() => {
    return {
      name: service?.name ?? '',
      duration: String(service?.duration ?? 60),
      price: String(service?.price ?? 0),
      description: service?.description ?? '',
      isActive: !!service?.is_active,
      showPublic: !!service?.show_on_public_calendar,
    };
  }, [service]);

  const [name, setName] = useState('');
  const [duration, setDuration] = useState('60');
  const [price, setPrice] = useState('0');
  const [description, setDescription] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [showPublic, setShowPublic] = useState(true);

  const dirty =
    name !== initial.name ||
    duration !== initial.duration ||
    price !== initial.price ||
    description !== initial.description ||
    isActive !== initial.isActive ||
    showPublic !== initial.showPublic;

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGetServiceDetail({ org: orgSlug, serviceId });
      setService(resp.service);
      setName(resp.service.name ?? '');
      setDuration(String(resp.service.duration ?? 60));
      setPrice(String(resp.service.price ?? 0));
      setDescription(resp.service.description ?? '');
      setIsActive(!!resp.service.is_active);
      setShowPublic(!!resp.service.show_on_public_calendar);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to load service.';
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
          setError(`Service editing is restricted to Owners/GMs/Managers (you are ${humanRole(nextRole)}).`);
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
          'Failed to load service.';
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setRoleLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug, serviceId]);

  if (roleLoading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator />
      </View>
    );
  }

  if (!canManageServices(role)) {
    return (
      <View style={styles.container}>
        <Text style={styles.title}>Edit service</Text>
        <View style={styles.card}>
          <Text style={styles.cardText}>{error || 'Service editing is restricted to Owners/GMs/Managers.'}</Text>
        </View>
      </View>
    );
  }

  async function handleSave() {
    setSaving(true);
    try {
      const d = parseInt(duration, 10);
      const p = Number(price);
      const patch = {
        name: name.trim(),
        duration: d,
        price: p,
        description,
        is_active: isActive,
        show_on_public_calendar: showPublic,
      } as any;

      const resp = await apiPatchService({ org: orgSlug, serviceId, patch });
      setService(resp.service);
      Alert.alert('Saved', 'Service updated.');
      onSaved();
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.name === 'string' && body.name) ||
        (typeof body?.duration === 'string' && body.duration) ||
        (typeof body?.price === 'string' && body.price) ||
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to save service.';
      Alert.alert('Save failed', msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Edit service</Text>
      <Text style={styles.subtitle}>{service?.name ?? ''}</Text>

      <View style={styles.card}>
        {loading ? (
          <ActivityIndicator />
        ) : error ? (
          <>
            <Text style={styles.cardText}>Error: {error}</Text>
            <Pressable style={[styles.secondaryBtn, { marginTop: 12 }]} onPress={load}>
              <Text style={styles.secondaryBtnText}>Retry</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.rowLabel}>Name</Text>
            <TextInput value={name} onChangeText={setName} style={styles.input} />

            <View style={styles.inlineRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowLabel}>Duration (min)</Text>
                <TextInput value={duration} onChangeText={setDuration} keyboardType="number-pad" style={styles.input} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowLabel}>Price</Text>
                <TextInput value={price} onChangeText={setPrice} keyboardType="decimal-pad" style={styles.input} />
              </View>
            </View>

            <Text style={styles.rowLabel}>Description</Text>
            <TextInput
              value={description}
              onChangeText={setDescription}
              style={[styles.input, { minHeight: 80 }]}
              multiline
            />

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleLabel}>Active</Text>
                <Text style={styles.toggleHint}>Inactive services won’t be bookable</Text>
              </View>
              <Switch value={isActive} onValueChange={setIsActive} />
            </View>

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleLabel}>Show on public calendar</Text>
                <Text style={styles.toggleHint}>Controls public visibility</Text>
              </View>
              <Switch value={showPublic} onValueChange={setShowPublic} />
            </View>

            <Pressable
              style={[styles.primaryBtn, !dirty || saving ? styles.primaryBtnDisabled : null]}
              disabled={!dirty || saving}
              onPress={handleSave}
            >
              {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Save</Text>}
            </Pressable>
          </>
        )}
      </View>
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
  rowLabel: {
    marginTop: 10,
    fontWeight: '700',
    color: '#111827',
  },
  input: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    fontSize: 16,
    marginTop: 6,
    backgroundColor: '#fff',
  },
  inlineRow: {
    marginTop: 6,
    flexDirection: 'row',
    gap: 10,
  },
  toggleRow: {
    marginTop: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  toggleLabel: {
    fontWeight: '700',
    color: '#111827',
  },
  toggleHint: {
    marginTop: 2,
    color: '#6b7280',
    fontSize: 12,
  },
  primaryBtn: {
    marginTop: 16,
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
});
