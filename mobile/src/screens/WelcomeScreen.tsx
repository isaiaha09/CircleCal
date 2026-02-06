import React, { useEffect, useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';

import { AuthCard } from '../components/AuthCard';
import { API_BASE_URL } from '../config';
import { clearPostSignOutMessage, clearPostStripeMessage, getPostSignOutMessage, getPostStripeMessage } from '../lib/auth';
import { theme } from '../ui/theme';

type Props = {
  onPressSignIn: () => void;
  onPressCreateAccount?: () => void;
};

export function WelcomeScreen({ onPressSignIn, onPressCreateAccount }: Props) {
  const [flashMessage, setFlashMessage] = useState<string | null>(null);
  const [stripeMessage, setStripeMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stripe = await getPostStripeMessage();
        if (!cancelled && stripe) {
          setStripeMessage(stripe);
          await clearPostStripeMessage();
        }

        const msg = await getPostSignOutMessage();
        if (cancelled) return;
        if (msg) {
          setFlashMessage(msg);
          await clearPostSignOutMessage();
        }
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <LinearGradient
      colors={['#dbeafe', '#e0e7ff', '#ffffff']}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.gradient}
    >
      <SafeAreaView style={styles.safeTransparent}>
        <View style={styles.container}>
          <AuthCard title="CircleCal" titleColor={theme.colors.primary} subtitle="Booking + scheduling for teams and businesses.">
            {stripeMessage ? <Text style={styles.successText}>{stripeMessage}</Text> : null}
            {flashMessage ? <Text style={styles.successText}>{flashMessage}</Text> : null}

            <Pressable style={styles.primaryBtn} onPress={onPressSignIn}>
              <Text style={styles.primaryBtnText}>Sign in</Text>
            </Pressable>

            <Pressable
              style={styles.secondaryBtn}
              onPress={() => {
                if (onPressCreateAccount) {
                  onPressCreateAccount();
                  return;
                }
                Linking.openURL(`${API_BASE_URL}/signup/`).catch(() => undefined);
              }}
            >
              <Text style={styles.secondaryBtnText}>Create an account</Text>
            </Pressable>

            <View style={styles.linksRow}>
              <Text
                accessibilityRole="link"
                style={styles.linkText}
                onPress={() => Linking.openURL(`${API_BASE_URL}/privacy/`)}
              >
                Privacy policy
              </Text>
              <Text style={styles.linkDivider}>•</Text>
              <Text
                accessibilityRole="link"
                style={styles.linkText}
                onPress={() => Linking.openURL(`${API_BASE_URL}/terms/`)}
              >
                Terms of service
              </Text>
            </View>

            <Text style={styles.hint}>Tip: notifications open directly into bookings.</Text>
          </AuthCard>
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  gradient: { flex: 1 },
  safeTransparent: { flex: 1, backgroundColor: 'transparent' },
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 18,
    paddingVertical: 24,
    backgroundColor: 'transparent',
  },
  primaryBtn: {
    backgroundColor: theme.colors.primaryDark,
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: theme.radius.md,
    alignItems: 'center',
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '800',
    fontSize: 16,
  },
  secondaryBtn: {
    marginTop: 12,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: '#fff',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: theme.radius.md,
    alignItems: 'center',
  },
  secondaryBtnText: { color: theme.colors.text, fontWeight: '800', fontSize: 16 },
  linksRow: {
    marginTop: 12,
    flexDirection: 'row',
    justifyContent: 'center',
    alignItems: 'center',
  },
  linkText: {
    color: theme.colors.primaryDark,
    fontWeight: '700',
    textDecorationLine: 'underline',
  },
  linkDivider: {
    marginHorizontal: 10,
    color: theme.colors.muted,
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
  hint: { marginTop: 12, color: theme.colors.muted, fontSize: 12, textAlign: 'center' },
});
