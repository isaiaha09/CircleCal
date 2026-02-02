import React from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AuthCard } from '../components/AuthCard';
import { theme } from '../ui/theme';

type Props = {
  onPressSignIn: () => void;
};

export function WelcomeScreen({ onPressSignIn }: Props) {
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.container}>
        <AuthCard title="CircleCal" subtitle="Booking + scheduling for teams and businesses.">
          <Pressable style={styles.primaryBtn} onPress={onPressSignIn}>
            <Text style={styles.primaryBtnText}>Sign in</Text>
          </Pressable>

          <Pressable style={styles.secondaryBtn} onPress={() => Linking.openURL('https://circlecal.app')}>
            <Text style={styles.secondaryBtnText}>Open website</Text>
          </Pressable>

          <Text style={styles.hint}>Tip: notifications open directly into bookings.</Text>
        </AuthCard>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.bg },
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 18,
    paddingVertical: 24,
    backgroundColor: theme.colors.bg,
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
    marginTop: 10,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: '#fff',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: theme.radius.md,
    alignItems: 'center',
  },
  secondaryBtnText: { color: theme.colors.text, fontWeight: '800', fontSize: 16 },
  hint: { marginTop: 12, color: theme.colors.muted, fontSize: 12, textAlign: 'center' },
});
