import React, { useMemo, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { clearActiveOrgSlug, setAccessToken, setRefreshToken } from '../lib/auth';
import type { ApiError } from '../lib/api';
import { apiPost } from '../lib/api';
import { registerPushTokenIfPossible } from '../lib/push';

type Props = {
  mode: 'owner' | 'staff';
  onSignedIn: () => void;
};

export function SignInScreen({ mode, onSignedIn }: Props) {
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const disabled = useMemo(
    () => !identifier.trim() || !password || submitting,
    [identifier, password, submitting]
  );

  async function handleSignIn() {
    setSubmitting(true);
    setError(null);
    try {
      // SimpleJWT's TokenObtainPairView expects "username" + "password".
      // We pass email/username into "username"; your Django auth backend supports either.
      const token = await apiPost<{ access: string; refresh: string }>(
        '/api/v1/auth/token/',
        {
          username: identifier.trim(),
          password,
        }
      );

      await Promise.all([setAccessToken(token.access), setRefreshToken(token.refresh)]);
      // If a different user signs in, the previously selected org might not be valid.
      await clearActiveOrgSlug();
      // Best-effort: register device token for push notifications.
      await registerPushTokenIfPossible();
      onSignedIn();
    } catch (e) {
      const err = e as Partial<ApiError>;
      const status = typeof err.status === 'number' ? err.status : null;

      if (status === 401) {
        setError(
          mode === 'owner'
            ? 'Incorrect username or password. Please try again.'
            : 'Incorrect email or password. Please try again.'
        );
        return;
      }

      // Keep other errors generic so we don't surface raw API error text.
      setError('Sign in failed. Please try again.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>
        {mode === 'owner' ? 'Business Owner Sign-In' : 'Staff | Manager | GM Sign-In'}
      </Text>

      <TextInput
        value={identifier}
        onChangeText={setIdentifier}
        placeholder={mode === 'owner' ? 'Username' : 'Email'}
        autoCapitalize="none"
        keyboardType={mode === 'owner' ? 'default' : 'email-address'}
        style={styles.input}
      />

      <TextInput
        value={password}
        onChangeText={setPassword}
        placeholder="Password"
        secureTextEntry
        style={styles.input}
      />

      <Pressable
        style={[styles.primaryBtn, disabled ? styles.primaryBtnDisabled : null]}
        disabled={disabled}
        onPress={handleSignIn}
      >
        {submitting ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.primaryBtnText}>Continue</Text>
        )}
      </Pressable>

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <Text style={styles.hint}>
        {mode === 'owner'
          ? 'Use your owner username and password.'
          : 'Use your staff/manager/GM email and password.'}
      </Text>
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
    marginBottom: 18,
    textAlign: 'center',
  },
  input: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    paddingVertical: 12,
    paddingHorizontal: 14,
    fontSize: 16,
    marginBottom: 12,
    backgroundColor: '#fff',
  },
  primaryBtn: {
    backgroundColor: '#2563eb',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 10,
    alignItems: 'center',
    marginTop: 6,
  },
  primaryBtnDisabled: {
    opacity: 0.5,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 16,
  },
  errorText: {
    marginTop: 10,
    color: '#b91c1c',
    fontSize: 14,
    textAlign: 'center',
  },
  hint: {
    marginTop: 12,
    color: '#6b7280',
  },
});
