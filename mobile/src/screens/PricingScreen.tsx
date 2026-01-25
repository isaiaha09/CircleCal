import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { ApiError, BillingPlan, BillingSummary } from '../lib/api';
import {
  apiCreateBillingCheckoutSession,
  apiCreateBillingPortalSession,
  apiGetBillingPlans,
  apiGetBillingSummary,
} from '../lib/api';

type Props = {
  orgSlug: string;
};

function safeString(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v == null) return '';
  return String(v);
}

function formatMoney(price: string): string {
  const n = Number(price);
  if (!Number.isFinite(n)) return `$${price}`;
  return `$${n.toFixed(2)}`;
}

export function PricingScreen({ orgSlug }: Props) {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<'portal' | `choose:${number}` | null>(null);
  const [error, setError] = useState<string | null>(null);

  const currentSlug = (summary?.plan?.slug || 'basic').toLowerCase();

  const title = useMemo(() => {
    if (!summary?.org?.name) return 'Pricing';
    return `Pricing · ${summary.org.name}`;
  }, [summary?.org?.name]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [s, p] = await Promise.all([apiGetBillingSummary({ org: orgSlug }), apiGetBillingPlans({ org: orgSlug })]);
        if (cancelled) return;
        setSummary(s);
        setPlans(p.plans ?? []);
      } catch (e) {
        const err = e as Partial<ApiError>;
        const body = err.body as any;
        const msg =
          (typeof body?.detail === 'string' && body.detail) ||
          (typeof err.message === 'string' && err.message) ||
          'Failed to load pricing.';
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  async function openPortal() {
    setBusy('portal');
    setError(null);
    try {
      const resp = await apiCreateBillingPortalSession({ org: orgSlug });
      const url = safeString(resp.url);
      if (!url) throw new Error('No portal URL returned.');
      await Linking.openURL(url);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Could not open billing portal.';
      setError(msg);
    } finally {
      setBusy(null);
    }
  }

  async function choose(planId: number) {
    setBusy(`choose:${planId}`);
    setError(null);
    try {
      const resp = await apiCreateBillingCheckoutSession({ org: orgSlug, planId });
      const url = safeString(resp.url);
      if (!url) throw new Error('No checkout URL returned.');
      await Linking.openURL(url);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Could not start checkout.';
      setError(msg);
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <View style={styles.container}>
        <ActivityIndicator />
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.subtitle}>Choose the plan that fits your business.</Text>

      {error ? (
        <View style={[styles.card, { borderColor: '#fecaca' }]}>
          <Text style={[styles.cardText, { color: '#991b1b' }]}>{error}</Text>
        </View>
      ) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Current</Text>
        <Text style={styles.cardText}>
          {summary?.plan?.name || summary?.plan?.slug || 'Basic'}
          {summary?.plan?.billing_period ? ` · ${summary.plan.billing_period}` : ''}
          {summary?.plan?.price ? ` · ${formatMoney(summary.plan.price)}` : ''}
        </Text>

        <Text style={styles.meta}>Status: {summary?.subscription?.status || '—'}</Text>

        <View style={styles.row}>
          <Pressable style={styles.secondaryBtn} onPress={openPortal} disabled={busy !== null}>
            <Text style={styles.secondaryBtnText}>{busy === 'portal' ? 'Opening…' : 'Manage in Stripe'}</Text>
          </Pressable>
        </View>
      </View>

      <Text style={styles.sectionTitle}>Plans</Text>

      {plans.length === 0 ? (
        <View style={styles.card}>
          <Text style={styles.cardText}>No plans available.</Text>
        </View>
      ) : (
        plans.map((p) => {
          const isCurrent = String(p.slug).toLowerCase() === currentSlug;
          return (
            <View key={p.id} style={[styles.card, isCurrent ? styles.cardActive : null]}>
              <View style={styles.planHeader}>
                <Text style={styles.planName}>{p.name}</Text>
                <Text style={styles.planPrice}>
                  {formatMoney(p.price)} / {p.billing_period}
                </Text>
              </View>
              {p.description ? <Text style={styles.meta}>{p.description}</Text> : null}

              <View style={styles.row}>
                {isCurrent ? (
                  <View style={styles.currentPill}>
                    <Text style={styles.currentPillText}>Current plan</Text>
                  </View>
                ) : (
                  <Pressable
                    style={[styles.primaryBtn, busy ? styles.btnDisabled : null]}
                    disabled={busy !== null}
                    onPress={() => choose(p.id)}
                  >
                    <Text style={styles.primaryBtnText}>
                      {busy === `choose:${p.id}` ? 'Starting…' : 'Choose'}
                    </Text>
                  </Pressable>
                )}
              </View>
            </View>
          );
        })
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    padding: 24,
    paddingTop: 18,
    backgroundColor: '#fff',
  },
  title: {
    fontSize: 24,
    fontWeight: '800',
    color: '#111827',
  },
  subtitle: {
    marginTop: 6,
    color: '#6b7280',
  },
  sectionTitle: {
    marginTop: 16,
    fontSize: 16,
    fontWeight: '800',
    color: '#111827',
  },
  card: {
    marginTop: 14,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 12,
    backgroundColor: '#fff',
  },
  cardActive: {
    borderColor: '#2563eb',
    backgroundColor: '#eff6ff',
  },
  cardTitle: {
    fontWeight: '800',
    color: '#111827',
    marginBottom: 6,
  },
  cardText: {
    color: '#111827',
  },
  meta: {
    marginTop: 6,
    color: '#6b7280',
  },
  row: {
    marginTop: 10,
    flexDirection: 'row',
    gap: 10,
    alignItems: 'center',
  },
  planHeader: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: 12,
  },
  planName: {
    fontSize: 18,
    fontWeight: '900',
    color: '#111827',
  },
  planPrice: {
    fontWeight: '800',
    color: '#111827',
  },
  primaryBtn: {
    backgroundColor: '#2563eb',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '800',
  },
  secondaryBtn: {
    borderWidth: 1,
    borderColor: '#d1d5db',
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderRadius: 10,
    backgroundColor: '#fff',
    alignSelf: 'flex-start',
  },
  secondaryBtnText: {
    color: '#111827',
    fontWeight: '700',
  },
  btnDisabled: {
    opacity: 0.6,
  },
  currentPill: {
    backgroundColor: '#111827',
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderRadius: 999,
  },
  currentPillText: {
    color: '#fff',
    fontWeight: '800',
  },
});
