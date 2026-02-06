import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';

import { AuthCard } from '../components/AuthCard';
import { theme } from '../ui/theme';

type Props = {
  onSelectOwner: () => void;
  onSelectStaff: () => void;
};

export function SignInChoiceScreen({ onSelectOwner, onSelectStaff }: Props) {
  return (
    <LinearGradient
      colors={['#dbeafe', '#e0e7ff', '#ffffff']}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.gradient}
    >
      <SafeAreaView style={styles.safeTransparent}>
        <View style={styles.container}>
          <AuthCard title="Sign in" subtitle="Choose the account type you use for CircleCal.">
            <Pressable style={styles.primaryBtn} onPress={onSelectOwner}>
              <Text style={styles.primaryBtnText}>Business Owner</Text>
            </Pressable>

            <Pressable style={styles.secondaryBtn} onPress={onSelectStaff}>
              <Text style={styles.secondaryBtnText}>Staff | Manager | GM</Text>
            </Pressable>

            <Text style={styles.hint}>You can switch accounts later.</Text>
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
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: theme.radius.md,
    marginBottom: 12,
    alignItems: 'center',
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '800',
    fontSize: 16,
    textAlign: 'center',
  },
  secondaryBtn: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: '#fff',
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: theme.radius.md,
    alignItems: 'center',
  },
  secondaryBtnText: {
    color: theme.colors.text,
    fontWeight: '800',
    fontSize: 16,
    textAlign: 'center',
  },
  hint: { marginTop: 12, color: theme.colors.muted, fontSize: 12, textAlign: 'center' },
});
