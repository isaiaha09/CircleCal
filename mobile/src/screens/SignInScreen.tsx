import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { clearActiveOrgSlug, setAccessToken, setRefreshToken } from '../lib/auth';
import type { ApiError } from '../lib/api';
import { apiPost } from '../lib/api';
import { registerPushTokenIfPossible } from '../lib/push';
import { API_BASE_URL } from '../config';

import { AuthCard } from '../components/AuthCard';
import { theme } from '../ui/theme';

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
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={styles.safe}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        // Keep this low; SafeAreaView already accounts for top inset.
        keyboardVerticalOffset={0}
      >
        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          alwaysBounceVertical={false}
        >
          <AuthCard
            title={mode === 'owner' ? 'Business Owner' : 'Staff | Manager | GM'}
            subtitle="Log in to continue"
          >
            <TextInput
              value={identifier}
              onChangeText={setIdentifier}
              placeholder={mode === 'owner' ? 'Username' : 'Email'}
              autoCapitalize="none"
              keyboardType={mode === 'owner' ? 'default' : 'email-address'}
              textContentType={mode === 'owner' ? 'username' : 'emailAddress'}
              autoCorrect={false}
              style={styles.input}
              returnKeyType="next"
            />

            <TextInput
              value={password}
              onChangeText={setPassword}
              placeholder="Password"
              secureTextEntry
              textContentType="password"
              style={styles.input}
              returnKeyType="go"
              onSubmitEditing={() => {
                if (!disabled) handleSignIn();
              }}
            />

            <Pressable
              style={[styles.primaryBtn, disabled ? styles.primaryBtnDisabled : null]}
              disabled={disabled}
              onPress={handleSignIn}
            >
              {submitting ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Login</Text>}
            </Pressable>

            {error ? <Text style={styles.errorText}>{error}</Text> : null}

            <View style={styles.linkRow}>
              <Pressable onPress={() => Linking.openURL(`${API_BASE_URL}/signup/`)}>
                <Text style={styles.link}>Create an account</Text>
              </Pressable>
              <Text style={styles.dot}>•</Text>
              <Pressable onPress={() => Linking.openURL(`${API_BASE_URL}/accounts/password/reset/`)}>
                <Text style={styles.link}>Forgot password</Text>
              </Pressable>
            </View>

            <Text style={styles.hint}>
              {mode === 'owner'
                ? 'Use your owner username and password.'
                : 'Use your staff/manager/GM email and password.'}
            </Text>
          </AuthCard>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.bg },
  scrollContent: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 18,
    paddingVertical: 24,
  },
  input: {
    borderWidth: 2,
    borderColor: '#cfe0ff',
    borderRadius: theme.radius.md,
    paddingVertical: 12,
    paddingHorizontal: 14,
    fontSize: 16,
    marginBottom: 12,
    backgroundColor: '#fbfdff',
  },
  primaryBtn: {
    backgroundColor: theme.colors.primary,
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: theme.radius.md,
    alignItems: 'center',
    marginTop: 6,
  },
  primaryBtnDisabled: {
    opacity: 0.5,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '800',
    fontSize: 16,
  },
  errorText: {
    marginTop: 10,
    color: theme.colors.danger,
    fontSize: 14,
    textAlign: 'center',
  },
  linkRow: {
    marginTop: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    flexWrap: 'wrap',
  },
  link: { color: theme.colors.primaryDark, fontWeight: '800', fontSize: 13, paddingVertical: 6 },
  dot: { color: theme.colors.muted, marginHorizontal: 8 },
  hint: {
    marginTop: 12,
    color: theme.colors.muted,
    fontSize: 12,
    textAlign: 'center',
  },
});
