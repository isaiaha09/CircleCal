import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Image,
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as WebBrowser from 'expo-web-browser';

import { API_BASE_URL } from '../config';
import type { ApiError, ApiProfileOverviewResponse, BillingSummary, OrgOfflinePaymentsResponse } from '../lib/api';
import {
  apiCreateStripeExpressDashboardLink,
  apiGetBillingSummary,
  apiGetMobileSsoLink,
  apiGetOrgOfflinePayments,
  apiGetProfileOverview,
  apiPatch,
  apiPatchOrgOfflinePayments,
  apiPostFormData,
} from '../lib/api';
import { clearActiveOrgSlug, getActiveOrgSlug, signOut } from '../lib/auth';
import { canManageBilling, humanRole, normalizeOrgRole } from '../lib/permissions';
import { contactSupport } from '../lib/support';

type Props = {
  onSignedOut: () => void;
  onOpenBusinesses?: () => void;
  onOpenBilling?: (args: { orgSlug: string }) => void;
  onOpenPlans?: (args: { orgSlug: string }) => void;
  forceNameCompletion?: boolean;
  onRequiredProfileCompleted?: () => void;
};

export function ProfileScreen({
  onSignedOut,
  onOpenBusinesses,
  onOpenBilling,
  onOpenPlans,
  forceNameCompletion,
  onRequiredProfileCompleted,
}: Props) {
  const [stripeFlash, setStripeFlash] = useState<string | null>(null);

  const [resp, setResp] = useState<ApiProfileOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [uploadingAvatar, setUploadingAvatar] = useState(false);

  const [activeOrgSlug, setActiveOrgSlug] = useState<string | null>(null);
  const [billingSummary, setBillingSummary] = useState<BillingSummary | null>(null);
  const [offlinePayments, setOfflinePayments] = useState<OrgOfflinePaymentsResponse | null>(null);
  const [loadingOrg, setLoadingOrg] = useState(false);
  const [savingOffline, setSavingOffline] = useState(false);
  const [openingStripe, setOpeningStripe] = useState(false);

  const initial = useMemo(() => {
    const p = resp?.profile;
    return {
      username: resp?.user?.username ?? '',
      email: resp?.user?.email ?? '',
      firstName: resp?.user?.first_name ?? '',
      lastName: resp?.user?.last_name ?? '',
      displayName: p?.display_name ?? '',
      timezone: p?.timezone ?? 'UTC',
      emailAlerts: !!p?.email_alerts,
      bookingReminders: !!p?.booking_reminders,
      offlineVenmo: resp?.org_overview?.offline_payment?.offline_venmo ?? offlinePayments?.offline_venmo ?? '',
      offlineZelle: resp?.org_overview?.offline_payment?.offline_zelle ?? offlinePayments?.offline_zelle ?? '',
    };
  }, [offlinePayments, resp]);

  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [emailAlerts, setEmailAlerts] = useState(true);
  const [bookingReminders, setBookingReminders] = useState(true);
  const [offlineVenmo, setOfflineVenmo] = useState('');
  const [offlineZelle, setOfflineZelle] = useState('');

  async function reloadProfileAndOrgPanels() {
    try {
      const orgSlug = await getActiveOrgSlug();
      setActiveOrgSlug(orgSlug);

      const next = await apiGetProfileOverview({ org: orgSlug });
      setResp(next);
      setUsername(next.user.username ?? '');
      setEmail(next.user.email ?? '');
      setFirstName((next.user as any).first_name ?? '');
      setLastName((next.user as any).last_name ?? '');
      setDisplayName(next.profile.display_name ?? '');
      setTimezone(next.profile.timezone ?? 'UTC');
      setEmailAlerts(!!next.profile.email_alerts);
      setBookingReminders(!!next.profile.booking_reminders);

      if (orgSlug) {
        setLoadingOrg(true);
        try {
          const [bs, op] = await Promise.all([
            apiGetBillingSummary({ org: orgSlug }).catch(() => null),
            apiGetOrgOfflinePayments({ org: orgSlug }).catch(() => null),
          ]);
          setBillingSummary(bs);
          setOfflinePayments(op);
          if (op) {
            setOfflineVenmo(op.offline_venmo ?? '');
            setOfflineZelle(op.offline_zelle ?? '');
          } else if (next.org_overview?.offline_payment) {
            setOfflineVenmo(next.org_overview.offline_payment.offline_venmo ?? '');
            setOfflineZelle(next.org_overview.offline_payment.offline_zelle ?? '');
          }
        } finally {
          setLoadingOrg(false);
        }
      }
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to load profile.';
      setError(msg);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await reloadProfileAndOrgPanels();
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
    username !== initial.username ||
    email !== initial.email ||
    firstName !== initial.firstName ||
    lastName !== initial.lastName ||
    displayName !== initial.displayName ||
    timezone !== initial.timezone ||
    emailAlerts !== initial.emailAlerts ||
    bookingReminders !== initial.bookingReminders;

  const offlineDirty = offlineVenmo !== initial.offlineVenmo || offlineZelle !== initial.offlineZelle;

  async function handleSave() {
    if (!firstName.trim() || !lastName.trim()) {
      Alert.alert('Missing info', 'Please enter your First Name and Last Name, then tap Save changes.');
      return;
    }
    setSaving(true);
    try {
      const next = await apiPatch<ApiProfileOverviewResponse>('/api/v1/profile/', {
        username,
        email,
        first_name: firstName,
        last_name: lastName,
        display_name: displayName,
        timezone,
        email_alerts: emailAlerts,
        booking_reminders: bookingReminders,
      });
      setResp(next);
      Alert.alert('Saved', 'Your profile settings were updated.');

      if (forceNameCompletion && onRequiredProfileCompleted) {
        onRequiredProfileCompleted();
      }
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.username === 'string' && body.username) ||
        (typeof body?.email === 'string' && body.email) ||
        (typeof body?.timezone === 'string' && body.timezone) ||
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to save profile.';
      Alert.alert('Save failed', msg);
    } finally {
      setSaving(false);
    }
  }

  async function handleSaveOfflinePayments() {
    if (!activeOrgSlug) return;
    setSavingOffline(true);
    try {
      const next = await apiPatchOrgOfflinePayments({
        org: activeOrgSlug,
        patch: {
          offline_venmo: offlineVenmo,
          offline_zelle: offlineZelle,
        },
      });
      setOfflinePayments(next);
      Alert.alert('Saved', 'Offline payment info updated.');
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Failed to save offline payment info.';
      Alert.alert('Save failed', msg);
    } finally {
      setSavingOffline(false);
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

  async function openUrl(url: string) {
    try {
      // Stripe: open in an auth-session style in-app browser so it can deep-link
      // back into the app and automatically close.
      let isStripe = false;
      try {
        const u = new URL(url);
        isStripe = u.hostname === 'stripe.com' || u.hostname.endsWith('.stripe.com');
      } catch {
        isStripe = false;
      }

      if (isStripe) {
        const returnUrl = 'circlecal://stripe-return';
        const res: any = await WebBrowser.openAuthSessionAsync(url, returnUrl);
        if (res?.type === 'success' && typeof res?.url === 'string' && res.url.startsWith(returnUrl)) {
          let msg = 'Returned from Stripe.';
          try {
            const u = new URL(res.url);
            const status = u.searchParams.get('status') || '';
            if (status === 'connected') {
              msg = 'Stripe is connected and ready for payments.';
            } else if (status === 'express_done') {
              msg = 'Stripe Express setup complete.';
            }
          } catch {
            // ignore
          }
          setStripeFlash(msg);
          setTimeout(() => setStripeFlash(null), 8000);
          reloadProfileAndOrgPanels().catch(() => undefined);
        }
        return;
      }

      await Linking.openURL(url);
    } catch {
      Alert.alert('Could not open link', url);
    }
  }

  async function openWebPathWithSso(path: string) {
    try {
      const nextPath = (path && path.trim()) || '/';
      const resp = await apiGetMobileSsoLink({ next: nextPath });
      // Prefer the real external browser so the session cookie persists reliably
      // and form-based flows (like 2FA disable) work as expected.
      try {
        await Linking.openURL(resp.url);
      } catch {
        await WebBrowser.openBrowserAsync(resp.url);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      Alert.alert('Could not open link', msg || 'Please try again.');
    }
  }
              <>
                <Text style={styles.cardText}>Offline payments aren’t available for your plan in this app.</Text>
                <Pressable style={styles.secondaryBtnSmall} onPress={() => contactSupport()}>
                  <Text style={styles.secondaryBtnText}>Contact support</Text>
                </Pressable>
              </>
  const canEditOffline = !!(offlinePayments?.can_edit || resp?.org_overview?.offline_payment?.can_edit);
  const stripeConnected = !!resp?.org_overview?.stripe?.connect_account_id;
  const activeOrgRole = normalizeOrgRole(resp?.org_overview?.membership?.role);
  const canAccessBilling = canManageBilling(activeOrgRole);
  const billingUiEnabled = false;

  async function handleOpenStripeExpressDashboard() {
    if (!activeOrgSlug) return;
    setOpeningStripe(true);
    try {
      const resp2 = await apiCreateStripeExpressDashboardLink({ org: activeOrgSlug });
      const url = (resp2 as any)?.url;
      if (typeof url !== 'string' || !url) throw new Error('No Stripe URL returned.');
      await openUrl(url);
    } catch (e) {
      const err = e as Partial<ApiError>;
      const body = err.body as any;
      const msg =
        (typeof body?.detail === 'string' && body.detail) ||
        (typeof err.message === 'string' && err.message) ||
        'Could not open Stripe Express Dashboard.';
      Alert.alert('Stripe', msg);
    } finally {
      setOpeningStripe(false);
    }
  }

  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={styles.container}>
      <Text style={styles.title}>Profile</Text>
      <Text style={styles.subtitle}>Update your personal info and settings</Text>

      {stripeFlash ? <Text style={styles.successBanner}>{stripeFlash}</Text> : null}

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
                  <Image source={{ uri: resp.profile.avatar_url }} style={styles.avatar} resizeMode="cover" />
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
                {uploadingAvatar ? <ActivityIndicator /> : <Text style={styles.secondaryBtnText}>Change avatar</Text>}
              </Pressable>
            </View>

            <Text style={styles.rowLabel}>First name *</Text>
            <TextInput value={firstName} onChangeText={setFirstName} placeholder="Required" style={styles.input} />

            <Text style={styles.rowLabel}>Last name *</Text>
            <TextInput value={lastName} onChangeText={setLastName} placeholder="Required" style={styles.input} />

            <Text style={styles.rowLabel}>Username</Text>
            <TextInput value={username} onChangeText={setUsername} autoCapitalize="none" style={styles.input} />

            <Text style={styles.rowLabel}>Email</Text>
            <TextInput
              value={email}
              onChangeText={setEmail}
              autoCapitalize="none"
              keyboardType="email-address"
              style={styles.input}
            />

            <Text style={styles.rowLabel}>Display name</Text>
            <TextInput value={displayName} onChangeText={setDisplayName} placeholder="Optional" style={styles.input} />

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

            <Pressable style={[styles.primaryBtn, !dirty || saving ? styles.primaryBtnDisabled : null]} disabled={!dirty || saving} onPress={handleSave}>
              {saving ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Save changes</Text>}
            </Pressable>
          </>
        ) : (
          <Text style={styles.cardText}>Not signed in</Text>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Business</Text>
        <Text style={styles.cardText}>Current: {activeOrgSlug || 'None selected'}</Text>
        {(() => {
          const canSwitchBusiness = (resp?.memberships ?? []).some((m) => {
            const r = normalizeOrgRole(m.role);
            return r === 'owner' || r === 'admin';
          });
          if (!canSwitchBusiness) return null;
          if (!onOpenBusinesses) return null;
          return (
            <Pressable style={styles.secondaryBtn} onPress={onOpenBusinesses}>
              <Text style={styles.secondaryBtnText}>Change business</Text>
            </Pressable>
          );
        })()}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Security</Text>
        <Pressable style={styles.secondaryBtn} onPress={() => openUrl(`${API_BASE_URL}/accounts/password_change/`)}>
          <Text style={styles.secondaryBtnText}>Change password</Text>
        </Pressable>
        <Pressable style={styles.secondaryBtn} onPress={() => void openWebPathWithSso('/accounts/two_factor/')}>
          <Text style={styles.secondaryBtnText}>Two-factor authentication (2FA)</Text>
        </Pressable>
        <Pressable style={[styles.secondaryBtn, { borderColor: '#fecaca' }]} onPress={() => openUrl(`${API_BASE_URL}/accounts/delete/`)}>
          <Text style={[styles.secondaryBtnText, { color: '#991b1b' }]}>Delete account</Text>
        </Pressable>
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Recent logins</Text>
        {resp?.recent_logins?.length ? (
          resp.recent_logins.map((a, idx) => (
            <View key={`login-${idx}`} style={{ marginTop: 10 }}>
              <Text style={styles.rowValue}>{a.timestamp || '—'}</Text>
              <Text style={styles.meta}>IP: {a.ip_address || '—'}</Text>
              {a.user_agent ? <Text style={styles.meta}>UA: {a.user_agent}</Text> : null}
            </View>
          ))
        ) : (
          <Text style={styles.cardText}>No recent activity.</Text>
        )}
      </View>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Team & businesses</Text>
        {resp?.memberships?.length ? (
          resp.memberships.map((m, idx) => (
            <View key={`mem-${idx}`} style={{ marginTop: 10 }}>
              <Text style={styles.rowValue}>{m.org.name}</Text>
              <Text style={styles.meta}>Role: {humanRole(m.role)}</Text>
            </View>
          ))
        ) : (
          <Text style={styles.cardText}>No memberships found.</Text>
        )}

        {resp?.pending_invites?.length ? (
          <>
            <Text style={[styles.cardTitle, { marginTop: 14 }]}>Pending invites</Text>
            {resp.pending_invites.map((inv, idx) => (
              <View key={`inv-${idx}`} style={{ marginTop: 10 }}>
                <Text style={styles.rowValue}>{inv.org.name}</Text>
                <Text style={styles.meta}>Role: {humanRole(inv.role)}</Text>
                {inv.accept_url ? (
                  <Pressable style={styles.secondaryBtn} onPress={() => openUrl(inv.accept_url as string)}>
                    <Text style={styles.secondaryBtnText}>Open invite</Text>
                  </Pressable>
                ) : null}
              </View>
            ))}
          </>
        ) : null}
      </View>

      {canAccessBilling && billingUiEnabled ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Billing</Text>
          {loadingOrg ? <ActivityIndicator /> : billingSummary ? (
            <>
              <Text style={styles.cardText}>
                {billingSummary.plan?.name || billingSummary.plan?.slug || 'Basic'}
                {billingSummary.plan?.billing_period ? ` · ${billingSummary.plan.billing_period}` : ''}
              </Text>
              <Text style={styles.meta}>Status: {billingSummary.subscription?.status || '—'}</Text>
              <View style={{ flexDirection: 'row', gap: 10, marginTop: 10 }}>
                {onOpenBilling && activeOrgSlug ? (
                  <Pressable style={styles.secondaryBtnSmall} onPress={() => onOpenBilling({ orgSlug: activeOrgSlug })}>
                    <Text style={styles.secondaryBtnText}>Open billing</Text>
                  </Pressable>
                ) : null}
                {onOpenPlans && activeOrgSlug ? (
                  <Pressable style={styles.secondaryBtnSmall} onPress={() => onOpenPlans({ orgSlug: activeOrgSlug })}>
                    <Text style={styles.secondaryBtnText}>View plans</Text>
                  </Pressable>
                ) : null}
              </View>
            </>
          ) : (
            <Text style={styles.cardText}>Billing details unavailable for this account.</Text>
          )}
        </View>
      ) : null}

      {canAccessBilling && billingUiEnabled ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Stripe account</Text>
          {stripeConnected ? (
            <>
              <Text style={styles.cardText}>Stripe is connected.</Text>
              <Pressable style={styles.secondaryBtn} onPress={handleOpenStripeExpressDashboard} disabled={openingStripe}>
                {openingStripe ? <ActivityIndicator /> : <Text style={styles.secondaryBtnText}>Open Stripe Express Dashboard</Text>}
              </Pressable>
            </>
          ) : (
            <Text style={styles.cardText}>No Stripe connected account found.</Text>
          )}
        </View>
      ) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Offline payment info (Venmo/Zelle)</Text>
        {activeOrgSlug ? (
          canEditOffline ? (
            <>
              <Text style={styles.rowLabel}>Venmo</Text>
              <TextInput value={offlineVenmo} onChangeText={setOfflineVenmo} placeholder="@yourname" style={styles.input} />
              <Text style={styles.rowLabel}>Zelle</Text>
              <TextInput value={offlineZelle} onChangeText={setOfflineZelle} placeholder="email or phone" style={styles.input} />
              <Pressable
                style={[styles.primaryBtn, !offlineDirty || savingOffline ? styles.primaryBtnDisabled : null]}
                disabled={!offlineDirty || savingOffline}
                onPress={handleSaveOfflinePayments}
              >
                {savingOffline ? <ActivityIndicator color="#fff" /> : <Text style={styles.primaryBtnText}>Save offline info</Text>}
              </Pressable>
            </>
          ) : (
            <Text style={styles.cardText}>Offline payments are available on Pro/Team (owner only).</Text>
          )
        ) : (
          <Text style={styles.cardText}>Select a business to manage offline payments.</Text>
        )}
      </View>

      <Pressable style={styles.secondaryBtn} onPress={handleSignOut}>
        <Text style={styles.secondaryBtnText}>Sign out</Text>
      </Pressable>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
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
  successBanner: {
    marginTop: 10,
    marginBottom: 2,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 12,
    backgroundColor: '#ecfdf5',
    borderWidth: 1,
    borderColor: '#a7f3d0',
    color: '#065f46',
    fontWeight: '700',
    textAlign: 'center',
  },
  card: {
    marginTop: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    backgroundColor: '#fff',
    padding: 14,
    borderRadius: 12,
  },
  cardTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#111827',
  },
  cardText: {
    color: '#374151',
  },
  meta: {
    marginTop: 4,
    color: '#6b7280',
    fontSize: 12,
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
