import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { BillingPlan, BillingSummary } from '../lib/api';
import { apiGetBillingPlans, apiGetBillingSummary } from '../lib/api';
import { contactSupport } from '../lib/support';

type Props = {
  orgSlug: string;
};

function safeText(s: unknown): string {
  return typeof s === 'string' ? s : '';
}

export function PlansScreen({ orgSlug }: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [plans, setPlans] = useState<BillingPlan[]>([]);

  const platformLabel = useMemo(() => 'mobile', []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [s, p] = await Promise.all([
        apiGetBillingSummary({ org: orgSlug }),
        apiGetBillingPlans({ org: orgSlug }),
      ]);
      setSummary(s);
      setPlans(p.plans ?? []);
    } catch {
      setError('Could not load plan details.');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug]);

  const currentPlanLabel = useMemo(() => {
    const name = safeText(summary?.plan?.name).trim();
    const slug = safeText(summary?.plan?.slug).trim();
    return name || slug || '—';
  }, [summary]);

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Plans</Text>
      <Text style={styles.subtitle}>Read-only plan info (no upgrades in-app).</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Important</Text>
        <Text style={styles.cardBody}>Plan changes aren’t available in the {platformLabel} app.</Text>
        <View style={{ flexDirection: 'row', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
          <Pressable style={styles.secondaryBtn} onPress={contactSupport}>
            <Text style={styles.secondaryBtnText}>Contact support</Text>
          </Pressable>
          <Pressable style={styles.secondaryBtn} onPress={load} disabled={loading}>
            {loading ? <ActivityIndicator /> : <Text style={styles.secondaryBtnText}>Refresh</Text>}
          </Pressable>
        </View>
      </View>

      {error ? (
        <View style={[styles.card, styles.cardError]}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Current plan</Text>
        {loading ? (
          <ActivityIndicator />
        ) : (
          <>
            <Text style={styles.cardBody}>{currentPlanLabel}</Text>
            <Text style={styles.meta}>Status: {safeText(summary?.subscription?.status) || '—'}</Text>
          </>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Plan options</Text>
        {loading ? (
          <ActivityIndicator />
        ) : plans.length ? (
          plans.map((p) => {
            const isCurrent = safeText(summary?.plan?.slug) && p.slug === summary?.plan?.slug;
            return (
              <View key={p.id} style={[styles.planRow, isCurrent ? styles.planRowCurrent : null]}>
                <Text style={styles.planName}>{p.name}</Text>
                {p.description ? <Text style={styles.planDesc}>{p.description}</Text> : null}
                {isCurrent ? <Text style={styles.badge}>Current</Text> : null}
              </View>
            );
          })
        ) : (
          <Text style={styles.cardBody}>No plan options available.</Text>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>What your plan enables</Text>
        {loading ? (
          <ActivityIndicator />
        ) : summary ? (
          <>
            <Text style={styles.meta}>Resources: {summary.features.can_use_resources ? 'Enabled' : 'Not enabled'}</Text>
            <Text style={styles.meta}>Add staff: {summary.features.can_add_staff ? 'Enabled' : 'Not enabled'}</Text>
            <Text style={styles.meta}>Add services: {summary.features.can_add_service ? 'Enabled' : 'Not enabled'}</Text>
            <Text style={styles.meta}>Weekly availability: {summary.features.can_edit_weekly_availability ? 'Enabled' : 'Not enabled'}</Text>
            <Text style={styles.meta}>Offline payments: {summary.features.can_use_offline_payment_methods ? 'Enabled' : 'Not enabled'}</Text>
          </>
        ) : (
          <Text style={styles.cardBody}>—</Text>
        )}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: { flex: 1, backgroundColor: '#f8fafc' },
  content: { padding: 16, paddingBottom: 30 },
  title: { fontSize: 22, fontWeight: '800', marginBottom: 4, color: '#0f172a' },
  subtitle: { color: '#475569', marginBottom: 14 },
  card: { backgroundColor: '#fff', borderRadius: 14, padding: 14, borderWidth: 1, borderColor: '#e2e8f0', marginBottom: 12 },
  cardError: { borderColor: '#fecaca', backgroundColor: '#fff1f2' },
  cardTitle: { fontSize: 16, fontWeight: '700', marginBottom: 8, color: '#0f172a' },
  cardBody: { color: '#334155' },
  meta: { color: '#475569', marginTop: 6 },
  errorText: { color: '#991b1b', fontWeight: '600' },
  secondaryBtn: {
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    backgroundColor: '#fff',
  },
  secondaryBtnText: { fontWeight: '700', color: '#0f172a' },
  planRow: { paddingVertical: 10, borderTopWidth: 1, borderTopColor: '#e2e8f0' },
  planRowCurrent: { backgroundColor: '#f1f5f9', marginHorizontal: -14, paddingHorizontal: 14 },
  planName: { fontWeight: '800', color: '#0f172a' },
  planDesc: { color: '#475569', marginTop: 4 },
  badge: { marginTop: 6, alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: '#dbeafe', color: '#1d4ed8', fontWeight: '700' } as any,
});
