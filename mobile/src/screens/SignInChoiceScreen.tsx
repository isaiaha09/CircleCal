import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

type Props = {
  onSelectOwner: () => void;
  onSelectStaff: () => void;
};

export function SignInChoiceScreen({ onSelectOwner, onSelectStaff }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Choose Sign-In</Text>
      <Text style={styles.subtitle}>Select the account type you use for CircleCal.</Text>

      <Pressable style={styles.primaryBtn} onPress={onSelectOwner}>
        <Text style={styles.primaryBtnText}>Business Owner</Text>
      </Pressable>

      <Pressable style={styles.secondaryBtn} onPress={onSelectStaff}>
        <Text style={styles.secondaryBtnText}>Staff | Manager | GM</Text>
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
    fontSize: 24,
    fontWeight: '700',
    color: '#111827',
    textAlign: 'center',
  },
  subtitle: {
    marginTop: 8,
    color: '#6b7280',
    marginBottom: 18,
    textAlign: 'center',
  },
  primaryBtn: {
    backgroundColor: '#2563eb',
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 12,
    marginBottom: 12,
    textAlign: 'center',
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 16,
    textAlign: 'center',
  },
  secondaryBtn: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 12,
    textAlign: 'center',
  },
  secondaryBtnText: {
    color: '#111827',
    fontWeight: '700',
    fontSize: 16,
    textAlign: 'center',
  },
  btnHint: {
    marginTop: 6,
    color: '#6b7280',
  },
});
