import React from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

type Props = {
  onPressSignIn: () => void;
};

export function WelcomeScreen({ onPressSignIn }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>CircleCal</Text>
      <Text style={styles.subtitle}>Booking + scheduling, now native.</Text>

      <Pressable style={styles.primaryBtn} onPress={onPressSignIn}>
        <Text style={styles.primaryBtnText}>Sign in</Text>
      </Pressable>

      <Pressable
        style={styles.linkBtn}
        onPress={() => Linking.openURL('https://circlecal.app')}
      >
        <Text style={styles.linkText}>Open website</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    backgroundColor: '#fff',
  },
  title: {
    fontSize: 34,
    fontWeight: '700',
    color: '#111827',
  },
  subtitle: {
    marginTop: 8,
    fontSize: 16,
    color: '#4b5563',
    textAlign: 'center',
  },
  primaryBtn: {
    marginTop: 18,
    backgroundColor: '#2563eb',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 10,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '600',
    fontSize: 16,
  },
  linkBtn: {
    marginTop: 14,
    paddingVertical: 10,
    paddingHorizontal: 10,
  },
  linkText: {
    color: '#2563eb',
    fontWeight: '600',
  },
});
