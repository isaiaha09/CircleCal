import React, { useMemo, useState } from 'react';
import { Alert, ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { API_BASE_URL } from '../config';
import { setAccessToken, setRefreshToken } from '../lib/auth';
import type { ApiError } from '../lib/api';
import { apiPost } from '../lib/api';

type Props = {
  mode: 'owner' | 'staff';
  onSignedIn: () => void;
};

export function SignInScreen({ mode, onSignedIn }: Props) {
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const disabled = useMemo(
    () => !identifier.trim() || !password || submitting,
    [identifier, password, submitting]
  );

  async function handleSignIn() {
    setSubmitting(true);
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
      onSignedIn();
    } catch (e) {
      const err = e as Partial<ApiError>;
      const maybeBody = err.body as any;
      const detail =
        (typeof maybeBody?.detail === 'string' && maybeBody.detail) ||
        (typeof maybeBody?.text === 'string' && maybeBody.text.slice(0, 160)) ||
        'Check your credentials and try again.';

      const statusLine = typeof err.status === 'number' ? `HTTP ${err.status}` : 'Network error';
      Alert.alert('Sign in failed', `${statusLine}\n${detail}\n\nAPI: ${API_BASE_URL}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>{mode === 'owner' ? 'Business owner sign in' : 'Staff/manager sign in'}</Text>

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

      <Text style={styles.hint}>
        {mode === 'owner'
          ? 'Use your owner username and password.'
          : 'Use your staff/manager email and password.'}
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
  hint: {
    marginTop: 12,
    color: '#6b7280',
  },
});
