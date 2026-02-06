import React, { useCallback, useMemo, useState, useEffect } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';
import { LinearGradient } from 'expo-linear-gradient';
import {
  clearActiveOrgSlug,
  signOut,
  setAccessToken,
  setRefreshToken,
  setPostStripeMessage,
  getPostStripeMessage,
  clearPostStripeMessage,
} from '../lib/auth';
import type { ApiError } from '../lib/api';
import { apiGetMobileSsoLink, apiGetOrgs, apiGetProfileOverview, apiPost } from '../lib/api';
import { registerPushTokenIfPossible } from '../lib/push';
import { API_BASE_URL } from '../config';

import { AuthCard } from '../components/AuthCard';
import { theme } from '../ui/theme';

type Props = {
  mode: 'owner' | 'staff';
  onSignedIn: () => void;
};

export function SignInScreen({ mode, onSignedIn }: Props) {
  const openBrowserFullScreen = useCallback((url: string) => {
    return WebBrowser.openBrowserAsync(url, {
      createTask: true,
      // iOS: avoid the "sheet" look so it fills the screen.
      presentationStyle: WebBrowser.WebBrowserPresentationStyle.FULL_SCREEN,
      // Android: allow the top bar to collapse on scroll where supported.
      enableBarCollapsing: true,
      showTitle: false,
    } as any);
  }, []);
  const [identifier, setIdentifier] = useState('');
  const [password, setPassword] = useState('');
  const [otpRequired, setOtpRequired] = useState(false);
  const [otpCode, setOtpCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showOnboardingOverlay, setShowOnboardingOverlay] = useState(false);
  const [stripeMessage, setStripeMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const msg = await getPostStripeMessage();
        if (cancelled) return;
        if (msg) {
          setStripeMessage(msg);
          await clearPostStripeMessage();
        }
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const disabled = useMemo(
    () => !identifier.trim() || !password || submitting || (otpRequired && !otpCode.trim()),
    [identifier, password, submitting, otpCode, otpRequired]
  );

  async function handleSignIn() {
    setSubmitting(true);
    setError(null);
    try {
      // 2FA-aware mobile endpoint.
      // Step 1: username/password. If user has 2FA enabled, server returns otp_required.
      // Step 2: include otp to receive JWT tokens.
      const token = await apiPost<{ access: string; refresh: string; otp_required?: boolean }>(
        '/api/v1/auth/mobile/token/',
        {
          username: identifier.trim(),
          password,
          ...(otpRequired ? { otp: otpCode.trim() } : null),
        }
      );

      await Promise.all([setAccessToken(token.access), setRefreshToken(token.refresh)]);
      // If a different user signs in, the previously selected org might not be valid.
      await clearActiveOrgSlug();
      // Best-effort: register device token for push notifications.
      await registerPushTokenIfPossible();

      // Owners: keep the onboarding inside an in-app browser until Stripe is connected.
      // This mirrors the web experience where /post-login/ routes the user to the next
      // onboarding step (create business, connect Stripe, etc.).
      if (mode === 'owner') {
        let needsStripe = false;
        try {
          const orgsResp = await apiGetOrgs();
          const orgs = orgsResp?.orgs ?? [];
          const isOwnerAnywhere = orgs.length === 0 || orgs.some((o) => String((o as any)?.role || '').toLowerCase() === 'owner');

          // Correction: only force Stripe/onboarding for true owners.
          // If this account is not an owner anywhere (e.g. admin/staff), allow normal sign-in.
          if (!isOwnerAnywhere) {
            onSignedIn();
            return;
          }

          const orgSlug = orgs[0]?.slug ?? null;
          if (!orgSlug) {
            needsStripe = true; // no business yet -> onboarding needed
          } else {
            const overview = await apiGetProfileOverview({ org: orgSlug });
            const stripe = (overview as any)?.org_overview?.stripe;
            const connected = !!(stripe?.connect_charges_enabled || stripe?.connect_payouts_enabled);
            needsStripe = !connected;
          }
        } catch {
          // If we can't determine status, do not block login.
          needsStripe = false;
        }

        if (needsStripe) {
          try {
            const sso = await apiGetMobileSsoLink({ next: '/post-login/?cc_app=1' });
            const startUrl = (sso as any)?.url;
            if (typeof startUrl !== 'string' || !startUrl) throw new Error('No SSO URL');

            const returnUrl = 'circlecal://stripe-return';
            setShowOnboardingOverlay(true);
            const res: any = await WebBrowser.openAuthSessionAsync(startUrl, returnUrl);

            // If the user completed Stripe and got redirected back, stash the banner
            // message (App.tsx also handles this, but doing it here makes the flow robust).
            if (res?.type === 'success' && typeof res?.url === 'string' && res.url.startsWith(returnUrl)) {
              let msg = 'Stripe setup complete.';
              try {
                const u = new URL(res.url);
                const status = u.searchParams.get('status') || '';
                if (status === 'connected') msg = 'Stripe is connected and ready for payments.';
                else if (status === 'express_done') msg = 'Stripe Express setup complete.';
              } catch {
                // ignore
              }
              setPostStripeMessage(msg).catch(() => undefined);
              onSignedIn();
              return;
            }

            // If they exited before finishing onboarding, require login again next time.
            await signOut();

            // Also clear any Django session cookies that may have been created inside
            // the in-app browser during SSO, so "Create account" doesn't appear to resume.
            try {
              const cancelReturn = 'circlecal://auth-cancel';
              const logoutUrl = `${API_BASE_URL}/accounts/mobile/logout/?next=${encodeURIComponent(cancelReturn)}`;
              await WebBrowser.openAuthSessionAsync(logoutUrl, cancelReturn);
            } catch {
              // ignore
            }
            setError('Please finish setup (create business + connect Stripe) to continue.');
            return;
          } catch {
            await signOut();
            setError('Setup could not be completed. Please try again.');
            return;
          }
        }
      }

      onSignedIn();
    } catch (e) {
      const err = e as Partial<ApiError>;
      const status = typeof err.status === 'number' ? err.status : null;

      // Server indicates OTP is required for this user.
      if (status === 400) {
        const body: any = (err as any)?.body;
        const needsOtp = Boolean(body && (body.otp_required === true || body.detail === 'otp_required'));
        if (needsOtp) {
          setOtpRequired(true);
          setError('Enter your 2FA code to continue.');
          return;
        }
      }

      if (status === 401) {
        if (otpRequired) {
          setError('Incorrect 2FA code. Please try again.');
        } else {
          setError(
            mode === 'owner'
              ? 'Incorrect username or password. Please try again.'
              : 'Incorrect email or password. Please try again.'
          );
        }
        return;
      }

      // Keep other errors generic so we don't surface raw API error text.
      setError('Sign in failed. Please try again.');
    } finally {
      setSubmitting(false);
      setShowOnboardingOverlay(false);
    }
  }

  return (
    <LinearGradient
      colors={['#dbeafe', '#e0e7ff', '#ffffff']}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.gradient}
    >
      <SafeAreaView style={styles.safeTransparent}>
        <KeyboardAvoidingView
          style={styles.safeTransparent}
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
            {stripeMessage ? <Text style={styles.successText}>{stripeMessage}</Text> : null}
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

            {otpRequired ? (
              <TextInput
                value={otpCode}
                onChangeText={setOtpCode}
                placeholder="2FA code"
                keyboardType={Platform.OS === 'ios' ? 'number-pad' : 'numeric'}
                textContentType="oneTimeCode"
                autoCapitalize="none"
                autoCorrect={false}
                style={styles.input}
                returnKeyType="go"
                onSubmitEditing={() => {
                  if (!disabled) handleSignIn();
                }}
              />
            ) : null}

            <Pressable
              style={[styles.primaryBtn, disabled ? styles.primaryBtnDisabled : null]}
              disabled={disabled}
              onPress={handleSignIn}
            >
              {submitting ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.primaryBtnText}>{otpRequired ? 'Verify & Login' : 'Login'}</Text>
              )}
            </Pressable>

            {otpRequired ? (
              <Pressable
                onPress={() => {
                  setOtpRequired(false);
                  setOtpCode('');
                  setError(null);
                }}
                style={{ marginTop: 10 }}
              >
                <Text style={styles.link}>Use a different account</Text>
              </Pressable>
            ) : null}

            {error ? <Text style={styles.errorText}>{error}</Text> : null}

            <View style={styles.linkRow}>
              <Pressable
                onPress={() =>
                  (async () => {
                    try {
                      WebBrowser.dismissBrowser();
                    } catch {
                      // ignore
                    }
                    const ts = Date.now();
                    try {
                      const startUrl = `${API_BASE_URL}/signup/?cc_app=1&cc_ts=${ts}`;
                      const returnUrl = 'circlecal://auth-cancel';
                      await WebBrowser.openAuthSessionAsync(startUrl, returnUrl, {
                        createTask: true,
                        preferEphemeralSession: true,
                      });
                    } catch {
                      openBrowserFullScreen(`${API_BASE_URL}/signup/?cc_app=1&cc_ts=${ts}`).catch(() => undefined);
                    }
                  })()
                }
              >
                <Text style={styles.link}>Create an account</Text>
              </Pressable>
              <Text style={styles.dot}>•</Text>
              <Pressable onPress={() => openBrowserFullScreen(`${API_BASE_URL}/accounts/password/reset/?cc_app=1`).catch(() => undefined)}>
                <Text style={styles.link}>Forgot password</Text>
              </Pressable>
            </View>

            <Text style={styles.hint}>
              {mode === 'owner'
                ? 'Use your owner username and password.'
                : 'Use your staff/manager/GM email and password.'}
            </Text>
          </AuthCard>

          {showOnboardingOverlay ? (
            <View style={styles.onboardingOverlay} pointerEvents="none">
              <View style={styles.onboardingOverlayCard}>
                <ActivityIndicator />
                <Text style={styles.onboardingOverlayTitle}>Resuming setup…</Text>
                <Text style={styles.onboardingOverlaySubtitle}>
                  Complete business setup and connect Stripe to continue.
                </Text>
              </View>
            </View>
          ) : null}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  gradient: { flex: 1 },
  safeTransparent: { flex: 1, backgroundColor: 'transparent' },
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

  successText: {
    marginBottom: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: theme.radius.md,
    backgroundColor: '#ecfdf5',
    borderWidth: 1,
    borderColor: '#a7f3d0',
    color: '#065f46',
    fontWeight: '700',
    textAlign: 'center',
  },

  onboardingOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(255,255,255,0.7)',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 18,
  },
  onboardingOverlayCard: {
    width: '100%',
    maxWidth: 420,
    paddingHorizontal: 18,
    paddingVertical: 20,
    borderRadius: theme.radius.lg,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#e5e7eb',
    alignItems: 'center',
  },
  onboardingOverlayTitle: {
    marginTop: 12,
    fontSize: 16,
    fontWeight: '800',
    color: theme.colors.text,
    textAlign: 'center',
  },
  onboardingOverlaySubtitle: {
    marginTop: 6,
    fontSize: 13,
    color: theme.colors.muted,
    textAlign: 'center',
  },
});
