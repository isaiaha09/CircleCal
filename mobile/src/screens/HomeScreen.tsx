import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { signOut } from '../lib/auth';

type Props = {
  onSignedOut: () => void;
};

export function HomeScreen({ onSignedOut }: Props) {
  async function handleSignOut() {
    await signOut();
    onSignedOut();
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Home</Text>
      <Text style={styles.subtitle}>Next: calendar + bookings screens.</Text>

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
