import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { ApiError, BillingPlan, BillingSummary } from '../lib/api';
import { apiCreateBillingCheckoutSession, apiCreateBillingPortalSession, apiGetBillingPlans, apiGetBillingSummary } from '../lib/api';

type Props = {
  orgSlug: string;
};

function safeString(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v == null) return '';
  return String(v);
}

function formatDateMaybe(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function formatMoney(price: string): string {
  // Server returns strings like "19.99"; keep it simple.
  const n = Number(price);
  if (!Number.isFinite(n)) return `$${price}`;
  return `$${n.toFixed(2)}`;
}

export function BillingScreen({ orgSlug }: Props) {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<'portal' | `checkout:${number}` | null>(null);
  const [error, setError] = useState<string | null>(null);

  const title = useMemo(() => {
    if (!summary?.org?.name) return 'Billing';
    return `Billing · ${summary.org.name}`;
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
          'Failed to load billing.';
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

  async function checkout(planId: number) {
    setBusy(`checkout:${planId}`);
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

      {error ? (
        <View style={[styles.card, { borderColor: '#fecaca' }]}>
          <Text style={[styles.cardText, { color: '#991b1b' }]}>{error}</Text>
        </View>
      ) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Current plan</Text>

        <Text style={styles.cardText}>
          {summary?.plan?.name || summary?.plan?.slug || 'Basic'}
          {summary?.plan?.billing_period ? ` · ${summary.plan.billing_period}` : ''}
          {summary?.plan?.price ? ` · ${formatMoney(summary.plan.price)}` : ''}
        </Text>

        <Text style={styles.meta}>
          Status: {summary?.subscription?.status || '—'}
          {summary?.subscription?.cancel_at_period_end ? ' (cancels at period end)' : ''}
        </Text>
        <Text style={styles.meta}>Trial ends: {formatDateMaybe(summary?.subscription?.trial_end ?? null)}</Text>
        <Text style={styles.meta}>Renews/ends: {formatDateMaybe(summary?.subscription?.current_period_end ?? null)}</Text>

        <View style={styles.row}>
          <Pressable style={styles.primaryBtn} onPress={openPortal} disabled={busy !== null}>
            <Text style={styles.primaryBtnText}>{busy === 'portal' ? 'Opening…' : 'Open billing portal'}</Text>
          </Pressable>
        </View>

        <Text style={[styles.meta, { marginTop: 10 }]}>
          Services: {summary?.usage?.active_services_count ?? 0} · Team members: {summary?.usage?.active_members_count ?? 0}
        </Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Payment methods</Text>
        {summary?.payment_methods?.length ? (
          summary.payment_methods.map((pm) => (
            <View key={pm.id} style={styles.pmRow}>
              <Text style={styles.cardText}>
                {pm.brand ? pm.brand.toUpperCase() : 'CARD'} •••• {pm.last4 || '—'}
              </Text>
              <Text style={styles.meta}>
                {pm.exp_month && pm.exp_year ? `Exp ${pm.exp_month}/${pm.exp_year}` : 'Exp —'}
                {pm.is_default ? ' · Default' : ''}
              </Text>
            </View>
          ))
        ) : (
          <Text style={styles.cardText}>No saved payment methods yet.</Text>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Plans</Text>
        {plans.length === 0 ? (
          <Text style={styles.cardText}>No plans available.</Text>
        ) : (
          plans.map((p) => (
            <View key={p.id} style={styles.planRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.planName}>
                  {p.name} · {p.billing_period} · {formatMoney(p.price)}
                </Text>
                {p.description ? <Text style={styles.meta}>{p.description}</Text> : null}
              </View>
              <Pressable
                style={[styles.secondaryBtn, busy ? styles.secondaryBtnDisabled : null]}
                disabled={busy !== null}
                onPress={() => checkout(p.id)}
              >
                <Text style={styles.secondaryBtnText}>
                  {busy === `checkout:${p.id}` ? 'Starting…' : 'Choose'}
                </Text>
              </Pressable>
            </View>
          ))
        )}
      </View>
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
  card: {
    marginTop: 14,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 12,
    paddingVertical: 12,
    paddingHorizontal: 12,
    backgroundColor: '#fff',
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
    marginTop: 4,
    color: '#6b7280',
  },
  row: {
    marginTop: 10,
    flexDirection: 'row',
    gap: 10,
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
  secondaryBtnDisabled: {
    opacity: 0.6,
  },
  secondaryBtnText: {
    color: '#111827',
    fontWeight: '700',
  },
  pmRow: {
    paddingVertical: 8,
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  planRow: {
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
    flexDirection: 'row',
    gap: 10,
    alignItems: 'center',
  },
  planName: {
    fontWeight: '800',
    color: '#111827',
  },
});
