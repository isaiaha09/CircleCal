import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import type { ApiError } from '../lib/api';
import { apiGet } from '../lib/api';
import { signOut } from '../lib/auth';

type Props = {
  onSignedOut: () => void;
};

export function HomeScreen({ onSignedOut }: Props) {
  const [me, setMe] = useState<{ username: string; email: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGet<{ username: string; email: string }>('/api/v1/me/');
        if (!cancelled) setMe(resp);
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

  async function handleSignOut() {
    await signOut();
    onSignedOut();
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Home</Text>
      <Text style={styles.subtitle}>Next: calendar + bookings screens.</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Auth check</Text>
        {loading ? (
          <ActivityIndicator />
        ) : error ? (
          <Text style={styles.cardText}>Error: {error}</Text>
        ) : me ? (
          <Text style={styles.cardText}>Signed in as {me.username || me.email}</Text>
        ) : (
          <Text style={styles.cardText}>Not signed in</Text>
        )}
      </View>

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
    paddingTop: 72,
    backgroundColor: '#fff',
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#111827',
  },
  subtitle: {
    marginTop: 10,
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
    fontWeight: '700',
    color: '#111827',
    marginBottom: 8,
  },
  cardText: {
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
});
