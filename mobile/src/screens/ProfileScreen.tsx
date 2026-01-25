import React, { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, Alert, Image, Pressable, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';

import type { ApiError } from '../lib/api';
import type { ApiProfileResponse } from '../lib/api';
import { apiGet, apiPatch, apiPostFormData } from '../lib/api';
import { clearActiveOrgSlug, signOut } from '../lib/auth';

type Props = {
  onSignedOut: () => void;
};

export function ProfileScreen({ onSignedOut }: Props) {
  const [resp, setResp] = useState<ApiProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);

  const initial = useMemo(() => {
    const p = resp?.profile;
    return {
      displayName: p?.display_name ?? '',
      timezone: p?.timezone ?? 'UTC',
      emailAlerts: !!p?.email_alerts,
      bookingReminders: !!p?.booking_reminders,
    };
  }, [resp]);

  const [displayName, setDisplayName] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [emailAlerts, setEmailAlerts] = useState(true);
  const [bookingReminders, setBookingReminders] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await apiGet<ApiProfileResponse>('/api/v1/profile/');
        if (cancelled) return;
        setResp(next);
        setDisplayName(next.profile.display_name ?? '');
        setTimezone(next.profile.timezone ?? 'UTC');
        setEmailAlerts(!!next.profile.email_alerts);
        setBookingReminders(!!next.profile.booking_reminders);
      } catch (e) {
        const err = e as Partial<ApiError>;
        const body = err.body as any;
        const msg =
          (typeof body?.detail === 'string' && body.detail) ||
          (typeof err.message === 'string' && err.message) ||
          'Failed to load profile.';
        if (!cancelled) setError(msg);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const dirty =
    displayName !== initial.displayName ||
    timezone !== initial.timezone ||
    emailAlerts !== initial.emailAlerts ||
    bookingReminders !== initial.bookingReminders;

  async function handleSave() {
    setSaving(true);
    try {
      const next = await apiPatch<ApiProfileResponse>('/api/v1/profile/', {
        display_name: displayName,
        timezone,
        email_alerts: emailAlerts,
        booking_reminders: bookingReminders,
      });
      setResp(next);
      Alert.alert('Saved', 'Your profile settings were updated.');
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.timezone === 'string' && body.timezone) ||
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to save profile.';
      Alert.alert('Save failed', msg);
    } finally {
      setSaving(false);
    }
  }

  async function handleChangeAvatar() {
    try {
      setUploadingAvatar(true);

      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (perm.status !== 'granted') {
        Alert.alert('Permission required', 'Please allow photo library access to upload an avatar.');
        return;
      }

      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.9,
      });

      if (result.canceled) return;
      const asset = result.assets?.[0];
      if (!asset?.uri) {
        Alert.alert('Upload failed', 'No image was selected.');
        return;
      }

      const uri = asset.uri;
      const name = uri.split('/').pop() || 'avatar.jpg';
      const type = (asset as any).mimeType || 'image/jpeg';

      const form = new FormData();
      form.append('avatar', { uri, name, type } as any);

      const uploadResp = await apiPostFormData<{ avatar_url: string | null; avatar_updated_at: string | null }>(
        '/api/v1/profile/avatar/',
        form
      );

      // Update local state without forcing a full reload.
      setResp((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          profile: {
            ...prev.profile,
            avatar_url: uploadResp.avatar_url,
            avatar_updated_at: uploadResp.avatar_updated_at,
          },
        };
      });
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.avatar === 'string' && body.avatar) ||
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to upload avatar.';
      Alert.alert('Upload failed', msg);
    } finally {
      setUploadingAvatar(false);
    }
  }

  async function handleSignOut() {
    await Promise.all([signOut(), clearActiveOrgSlug()]);
    onSignedOut();
  }

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Profile</Text>
      <Text style={styles.subtitle}>Update your personal info and settings</Text>

      <View style={styles.card}>
        {loading ? (
          <ActivityIndicator />
        ) : error ? (
          <Text style={styles.cardText}>Error: {error}</Text>
        ) : resp ? (
          <>
            <View style={styles.avatarRow}>
              <View style={styles.avatarWrap}>
                {resp.profile.avatar_url ? (
                  <Image
                    source={{ uri: resp.profile.avatar_url }}
                    style={styles.avatar}
                    resizeMode="cover"
                  />
                ) : (
                  <View style={[styles.avatar, styles.avatarPlaceholder]}>
                    <Text style={styles.avatarPlaceholderText}>
                      {(resp.profile.display_name || resp.user.username || '?').slice(0, 1).toUpperCase()}
                    </Text>
                  </View>
                )}
              </View>
              <Pressable
                style={[styles.secondaryBtnSmall, uploadingAvatar ? styles.secondaryBtnSmallDisabled : null]}
                disabled={uploadingAvatar}
                onPress={handleChangeAvatar}
              >
                {uploadingAvatar ? (
                  <ActivityIndicator />
                ) : (
                  <Text style={styles.secondaryBtnText}>Change avatar</Text>
                )}
              </Pressable>
            </View>

            <Text style={styles.rowLabel}>Username</Text>
            <Text style={styles.rowValue}>{resp.user.username || '—'}</Text>
            <Text style={styles.rowLabel}>Email</Text>
            <Text style={styles.rowValue}>{resp.user.email || '—'}</Text>

            <Text style={styles.rowLabel}>Display name</Text>
            <TextInput
              value={displayName}
              onChangeText={setDisplayName}
              placeholder="Optional"
              style={styles.input}
            />

            <Text style={styles.rowLabel}>Timezone</Text>
            <TextInput
              value={timezone}
              onChangeText={setTimezone}
              placeholder="e.g., America/Los_Angeles"
              autoCapitalize="none"
              style={styles.input}
            />

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleLabel}>Email alerts</Text>
                <Text style={styles.toggleHint}>Receive important account emails</Text>
              </View>
              <Switch value={emailAlerts} onValueChange={setEmailAlerts} />
            </View>

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleLabel}>Booking reminders</Text>
                <Text style={styles.toggleHint}>Send reminders for upcoming bookings</Text>
              </View>
              <Switch value={bookingReminders} onValueChange={setBookingReminders} />
            </View>

            <Pressable
              style={[styles.primaryBtn, !dirty || saving ? styles.primaryBtnDisabled : null]}
              disabled={!dirty || saving}
              onPress={handleSave}
            >
              {saving ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.primaryBtnText}>Save changes</Text>
              )}
            </Pressable>
          </>
        ) : (
          <Text style={styles.cardText}>Not signed in</Text>
        )}
      </View>

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
    paddingTop: 18,
    backgroundColor: '#fff',
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    color: '#111827',
  },
  subtitle: {
    marginTop: 8,
    color: '#6b7280',
  },
  card: {
    marginTop: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#fff',
    padding: 14,
    borderRadius: 12,
  },
  cardText: {
    color: '#374151',
  },
  rowLabel: {
    marginTop: 10,
    fontWeight: '700',
    color: '#111827',
  },
  rowValue: {
    marginTop: 4,
    color: '#374151',
  },
  input: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 12,
    fontSize: 16,
    marginTop: 6,
    backgroundColor: '#fff',
  },
  toggleRow: {
    marginTop: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  toggleLabel: {
    fontWeight: '700',
    color: '#111827',
  },
  toggleHint: {
    marginTop: 2,
    color: '#6b7280',
    fontSize: 12,
  },
  primaryBtn: {
    marginTop: 16,
    backgroundColor: '#2563eb',
    paddingVertical: 12,
    paddingHorizontal: 18,
    borderRadius: 10,
    alignItems: 'center',
  },
  primaryBtnDisabled: {
    opacity: 0.5,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: 16,
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
  secondaryBtnSmall: {
    borderWidth: 1,
    borderColor: '#e5e7eb',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 130,
  },
  secondaryBtnSmallDisabled: {
    opacity: 0.6,
  },
  avatarRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 8,
  },
  avatarWrap: {
    width: 72,
    height: 72,
    borderRadius: 36,
    overflow: 'hidden',
  },
  avatar: {
    width: 72,
    height: 72,
    borderRadius: 36,
  },
  avatarPlaceholder: {
    backgroundColor: '#eff6ff',
    borderWidth: 1,
    borderColor: '#dbeafe',
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarPlaceholderText: {
    fontSize: 26,
    fontWeight: '800',
    color: '#2563eb',
  },
});
