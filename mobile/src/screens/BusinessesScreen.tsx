import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import type { ApiError, OrgListItem } from '../lib/api';
import { apiGetOrgs } from '../lib/api';
import { getActiveOrgSlug, setActiveOrgSlug } from '../lib/auth';

type Props = {
  onSelected: (args: { orgSlug: string }) => void;
};

export function BusinessesScreen({ onSelected }: Props) {
  const [orgs, setOrgs] = useState<OrgListItem[]>([]);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [orgsResp, stored] = await Promise.all([apiGetOrgs(), getActiveOrgSlug()]);
        if (cancelled) return;
        setOrgs(orgsResp.orgs ?? []);
        setActiveSlug(stored);
      } catch (e) {
        const err = e as Partial<ApiError>;
        const body = err.body as any;
        const msg =
          (typeof body?.detail === 'string' && body.detail) ||
          (typeof err.message === 'string' && err.message) ||
          'Failed to load businesses.';
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSelect(orgSlug: string) {
    setActiveSlug(orgSlug);
    await setActiveOrgSlug(orgSlug);
    onSelected({ orgSlug });
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Businesses</Text>
      <Text style={styles.subtitle}>View and manage your businesses</Text>

      {loading ? (
        <View style={{ paddingTop: 18 }}>
          <ActivityIndicator />
        </View>
      ) : error ? (
        <View style={styles.card}>
          <Text style={styles.cardText}>Error: {error}</Text>
        </View>
      ) : orgs.length === 0 ? (
        <View style={styles.card}>
          <Text style={styles.cardText}>No businesses found for this account.</Text>
        </View>
      ) : (
        <View style={{ marginTop: 12, gap: 10 }}>
          {orgs.map((o) => {
            const selected = activeSlug === o.slug;
            return (
              <Pressable
                key={o.slug}
                style={[styles.orgBtn, selected ? styles.orgBtnSelected : null]}
                onPress={() => handleSelect(o.slug)}
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
});
