import React from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';

import { theme } from '../ui/theme';

type Props = {
  title: string;
  subtitle?: string;
  titleColor?: string;
  children: React.ReactNode;
};

export function AuthCard({ title, subtitle, titleColor, children }: Props) {
  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Image
          source={require('../../assets/cc-auth-logo.png')}
          style={styles.logo}
          resizeMode="contain"
        />
        <Text style={[styles.title, titleColor ? { color: titleColor } : null]}>{title}</Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
      </View>
      <View style={styles.content}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    width: '100%',
    maxWidth: 420,
    backgroundColor: theme.colors.card,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.lg,
    overflow: 'hidden',
    ...theme.shadow.card,
  },
  header: {
    paddingTop: 22,
    paddingHorizontal: 22,
    paddingBottom: 12,
    alignItems: 'center',
  },
  logo: { width: 72, height: 72, marginBottom: 10 },
  title: { fontSize: 22, fontWeight: '800', color: theme.colors.text },
  subtitle: { marginTop: 6, fontSize: 13, color: theme.colors.muted, textAlign: 'center' },
  content: { padding: 22, paddingTop: 10 },
});
