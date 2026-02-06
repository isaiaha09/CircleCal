import React, { useMemo } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { contactSupport } from '../lib/support';

type Props = {
  orgSlug: string;
};

export function BillingScreen(_props: Props) {
  const platformLabel = useMemo(() => (Platform.OS === 'ios' ? 'iOS' : 'Android'), []);

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Billing changes unavailable</Text>
      <Text style={styles.body}>Billing changes aren’t available in the {platformLabel} app.</Text>

      <Pressable
        style={styles.secondaryBtn}
        onPress={() => {
          contactSupport();
        }}
        accessibilityRole="button"
        accessibilityLabel="Contact support"
      >
        <Text style={styles.secondaryBtnText}>Contact support</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16, justifyContent: 'center' },
  title: { fontSize: 18, fontWeight: '700', marginBottom: 8 },
  body: { color: '#444' },
  secondaryBtn: {
    marginTop: 14,
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#cbd5e1',
    backgroundColor: '#fff',
    alignSelf: 'flex-start',
  },
  secondaryBtnText: { fontWeight: '700', color: '#0f172a' },
});
