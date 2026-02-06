import React, { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { ApiError, TeamInvite, TeamMember } from '../lib/api';
import {
  apiCreateTeamInvite,
  apiDeleteTeamInvite,
  apiGetOrgs,
  apiGetTeamInvites,
  apiGetTeamMembers,
  apiUpdateTeamMember,
} from '../lib/api';
import { canManageBilling, canManageStaff, humanRole, normalizeOrgRole } from '../lib/permissions';
import { contactSupport } from '../lib/support';

type Props = {
  orgSlug: string;
  onOpenPlans: (args: { orgSlug: string }) => void;
};

function humanName(m: TeamMember): string {
  const fn = (m.user.first_name || '').trim();
  const ln = (m.user.last_name || '').trim();
  const full = `${fn} ${ln}`.trim();
  return full || (m.user.username || '').trim() || (m.user.email || '').trim() || 'Member';
}

function normalizeEmail(s: string): string {
  return (s || '').trim().toLowerCase();
}

function errorMessage(e: unknown, fallback: string): string {
  const err = e as Partial<ApiError>;
  const body = err.body as any;
  return (
    (typeof body?.detail === 'string' && body.detail) ||
    (typeof body?.email === 'string' && body.email) ||
    (typeof err.message === 'string' && err.message) ||
    fallback
  );
}

export function StaffScreen({ orgSlug, onOpenPlans }: Props) {

  const [members, setMembers] = useState<TeamMember[]>([]);
  const [invites, setInvites] = useState<TeamInvite[]>([]);
  const [loading, setLoading] = useState(true);
  const [roleLoading, setRoleLoading] = useState(true);
  const [orgRole, setOrgRole] = useState<string>('');
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatingMemberId, setUpdatingMemberId] = useState<number | null>(null);
  const [deletingInviteId, setDeletingInviteId] = useState<number | null>(null);

  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'staff' | 'manager' | 'admin'>('staff');
  const [sendingInvite, setSendingInvite] = useState(false);

  const canInvite = useMemo(() => normalizeEmail(inviteEmail).includes('@'), [inviteEmail]);
  const allowed = useMemo(() => canManageStaff(orgRole), [orgRole]);
  const canSeePricing = useMemo(() => canManageBilling(orgRole), [orgRole]);

  async function loadAll(roleOverride?: string) {
    setError(null);

    const roleToUse = roleOverride ?? orgRole;
    if (!canManageStaff(roleToUse)) {
      setMembers([]);
      setInvites([]);
      setError(`Staff management is restricted to Owners/GMs (you are ${humanRole(roleToUse)}).`);
      return;
    }

    try {
      const [m, i] = await Promise.all([
        apiGetTeamMembers({ org: orgSlug }),
        apiGetTeamInvites({ org: orgSlug }),
      ]);
      setMembers(m.members ?? []);
      setInvites(i.invites ?? []);
    } catch (e) {
      const msg = errorMessage(e, 'Failed to load staff.');
      setError(msg);
      // If this is plan-gated, show a friendly upgrade CTA.
    }
  }

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setRoleLoading(true);
        const orgsResp = await apiGetOrgs();
        const found = (orgsResp.orgs ?? []).find((o) => o.slug === orgSlug);
        const role = normalizeOrgRole(found?.role);
        if (!cancelled) setOrgRole(role);
        if (!cancelled) setRoleLoading(false);

        await loadAll(role);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug]);

  async function onRefresh() {
    setRefreshing(true);
    try {
      await loadAll();
    } finally {
      setRefreshing(false);
    }
  }

  async function handleInvite() {
    const email = normalizeEmail(inviteEmail);
    if (!email || !email.includes('@')) {
      Alert.alert('Invite', 'Enter a valid email.');
      return;
    }

    setSendingInvite(true);
    try {
      const resp = await apiCreateTeamInvite({ org: orgSlug, email, role: inviteRole });
      setInviteEmail('');
      await loadAll();

      const acceptUrl = resp.invite?.accept_url;
      if (resp.sent) {
        Alert.alert('Invite sent', `Invitation emailed to ${email}.`);
      } else if (acceptUrl) {
        Alert.alert(
          'Invite created',
          `We saved the invite for ${email}.\n\nTap OK to copy/share the link from the next screen.`,
          [
            {
              text: 'OK',
              onPress: () => Linking.openURL(acceptUrl).catch(() => null),
            },
          ]
        );
      } else {
        Alert.alert('Invite created', `We saved the invite for ${email}.`);
      }
    } catch (e) {
      Alert.alert('Invite failed', errorMessage(e, 'Could not create invite.'));
    } finally {
      setSendingInvite(false);
    }
  }

  async function handleDeactivate(member: TeamMember) {
    Alert.alert(
      'Remove member',
      `Deactivate ${humanName(member)}?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            try {
              await apiUpdateTeamMember({ org: orgSlug, memberId: member.id, patch: { is_active: false } });
              await loadAll();
            } catch (e) {
              Alert.alert('Remove failed', errorMessage(e, 'Could not remove member.'));
            }
          },
        },
      ]
    );
  }

  async function handleSetRole(member: TeamMember, role: 'staff' | 'manager' | 'admin') {
    if (member.role === 'owner') return;
    if (member.role === role) return;

    setUpdatingMemberId(member.id);
    try {
      await apiUpdateTeamMember({ org: orgSlug, memberId: member.id, patch: { role } });
      await loadAll();
    } catch (e) {
      Alert.alert('Role update failed', errorMessage(e, 'Could not update role.'));
    } finally {
      setUpdatingMemberId(null);
    }
  }

  async function handleDeleteInvite(inv: TeamInvite) {
    Alert.alert('Remove invite', `Remove invite for ${inv.email}?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove',
        style: 'destructive',
        onPress: async () => {
          setDeletingInviteId(inv.id);
          try {
            await apiDeleteTeamInvite({ org: orgSlug, inviteId: inv.id });
            await loadAll();
          } catch (e) {
            Alert.alert('Remove failed', errorMessage(e, 'Could not remove invite.'));
          } finally {
            setDeletingInviteId(null);
          }
        },
      },
    ]);
  }

  const header = (
    <View style={styles.headerPad}>
      <Text style={styles.title}>Staff</Text>
      <Text style={styles.subtitle}>Invite and manage your team</Text>

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
          {canSeePricing ? (
            <Pressable style={styles.primaryBtn} onPress={() => contactSupport()}>
              <Text style={styles.primaryBtnText}>Contact support</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      <View style={styles.card}>
        <Text style={styles.cardTitle}>Invite a team member</Text>
        <TextInput
          value={inviteEmail}
          onChangeText={setInviteEmail}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          placeholder="Email address"
          style={styles.input}
        />

        <View style={styles.roleRow}>
          {(['staff', 'manager', 'admin'] as const).map((r) => {
            const selected = inviteRole === r;
            return (
              <Pressable
                key={r}
                style={[styles.rolePill, selected ? styles.rolePillSelected : null]}
                onPress={() => setInviteRole(r)}
              >
                <Text style={[styles.rolePillText, selected ? styles.rolePillTextSelected : null]}>
                  {r === 'admin' ? 'GM' : r}
                </Text>
              </Pressable>
            );
          })}
        </View>

        <Pressable
          style={[styles.primaryBtn, (!canInvite || sendingInvite) ? styles.primaryBtnDisabled : null]}
          onPress={handleInvite}
          disabled={!canInvite || sendingInvite}
        >
          <Text style={styles.primaryBtnText}>{sendingInvite ? 'Sending…' : 'Send invite'}</Text>
        </Pressable>
      </View>

      <View style={styles.sectionRow}>
        <Text style={styles.sectionTitle}>Pending invites</Text>
        <Text style={styles.sectionMeta}>{invites.length}</Text>
      </View>

      {invites.length === 0 ? (
        <Text style={styles.emptyText}>No pending invites.</Text>
      ) : null}

      {invites.map((inv) => (
        <View key={inv.id} style={styles.rowCard}>
          <View style={styles.rowTextCol}>
            <Text style={styles.rowTitle}>{inv.email}</Text>
            <Text style={styles.rowMeta}>Role: {humanRole(inv.role)}</Text>
          </View>
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
            {inv.accept_url ? (
              <Pressable
                style={styles.secondaryBtn}
                onPress={() => Linking.openURL(inv.accept_url as string).catch(() => null)}
              >
                <Text style={styles.secondaryBtnText}>Open link</Text>
              </Pressable>
            ) : null}
            <Pressable
              style={[styles.dangerBtn, deletingInviteId === inv.id ? { opacity: 0.6 } : null]}
              onPress={() => handleDeleteInvite(inv)}
              disabled={deletingInviteId !== null}
            >
              <Text style={styles.dangerBtnText}>{deletingInviteId === inv.id ? 'Removing…' : 'Remove'}</Text>
            </Pressable>
          </View>
        </View>
      ))}

      <View style={styles.sectionRow}>
        <Text style={styles.sectionTitle}>Active members</Text>
        <Text style={styles.sectionMeta}>{members.length}</Text>
      </View>
    </View>
  );

  if (loading || roleLoading) {
    return (
      <View style={styles.loadingBox}>
        <ActivityIndicator />
        <Text style={styles.loadingText}>Loading staff…</Text>
      </View>
    );
  }

  if (!allowed) {
    return (
      <View style={{ flex: 1, padding: 16, paddingTop: 12, backgroundColor: '#fff' }}>
        <Text style={styles.title}>Staff</Text>
        <Text style={styles.subtitle}>Invite and manage your team</Text>
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error || `Staff management is restricted to Owners/GMs.`}</Text>
        </View>
      </View>
    );
  }

  return (
    <FlatList
      data={members}
      keyExtractor={(m) => String(m.id)}
      ListHeaderComponent={header}
      contentContainerStyle={styles.listContent}
      refreshing={refreshing}
      onRefresh={onRefresh}
      renderItem={({ item }) => {
        const name = humanName(item);
        const role = String(item.role || '').toLowerCase();
        const roleEditable = item.role !== 'owner';
        const isUpdating = updatingMemberId === item.id;
        return (
          <View style={styles.rowCard}>
            <View style={styles.rowTextCol}>
              <Text style={styles.rowTitle}>{name}</Text>
              <Text style={styles.rowMeta}>{item.user.email || item.user.username}</Text>
              <View style={styles.roleEditRow}>
                <Text style={styles.rowMeta}>Role:</Text>
                {item.role === 'owner' ? (
                  <View style={styles.pill}>
                    <Text style={styles.pillText}>Owner</Text>
                  </View>
                ) : (
                  <View style={styles.rolePillsInline}>
                    {(['staff', 'manager', 'admin'] as const).map((r) => {
                      const selected = role === r;
                      return (
                        <Pressable
                          key={r}
                          style={[
                            styles.rolePillSmall,
                            selected ? styles.rolePillSmallSelected : null,
                            (!roleEditable || isUpdating) ? styles.rolePillSmallDisabled : null,
                          ]}
                          disabled={!roleEditable || isUpdating}
                          onPress={() => handleSetRole(item, r)}
                        >
                          <Text
                            style={[
                              styles.rolePillSmallText,
                              selected ? styles.rolePillSmallTextSelected : null,
                            ]}
                          >
                            {r === 'admin' ? 'GM' : r}
                          </Text>
                        </Pressable>
                      );
                    })}
                    {isUpdating ? <ActivityIndicator size="small" /> : null}
                  </View>
                )}
              </View>
            </View>
            {item.role !== 'owner' ? (
              <Pressable style={styles.dangerBtn} onPress={() => handleDeactivate(item)}>
                <Text style={styles.dangerBtnText}>Remove</Text>
              </Pressable>
            ) : (
              <View style={styles.pill}>
                <Text style={styles.pillText}>Owner</Text>
              </View>
            )}
          </View>
        );
      }}
      ListFooterComponent={<View style={styles.footerPad} />}
    />
  );
}

const styles = StyleSheet.create({
  listContent: {
    paddingBottom: 24,
  },
  headerPad: {
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 8,
  },
  footerPad: {
    height: 12,
  },
  loadingBox: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  loadingText: {
    marginTop: 8,
    color: '#555',
  },
  title: {
    fontSize: 28,
    fontWeight: '700',
    marginBottom: 4,
  },
  subtitle: {
    color: '#666',
    marginBottom: 12,
  },
  sectionRow: {
    marginTop: 14,
    marginBottom: 8,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '700',
  },
  sectionMeta: {
    color: '#666',
  },
  emptyText: {
    color: '#666',
    marginBottom: 6,
  },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: '#eee',
  },
  cardTitle: {
    fontWeight: '700',
    marginBottom: 10,
    fontSize: 16,
  },
  input: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 10,
    backgroundColor: '#fafafa',
  },
  roleRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 10,
  },
  rolePill: {
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#ddd',
    backgroundColor: '#fff',
  },
  rolePillSelected: {
    borderColor: '#2563eb',
    backgroundColor: '#eff6ff',
  },
  rolePillText: {
    color: '#444',
    fontWeight: '600',
    textTransform: 'capitalize',
  },
  rolePillTextSelected: {
    color: '#1d4ed8',
  },
  roleEditRow: {
    marginTop: 6,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  rolePillsInline: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    flexWrap: 'wrap',
  },
  rolePillSmall: {
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#ddd',
    backgroundColor: '#fff',
  },
  rolePillSmallSelected: {
    borderColor: '#2563eb',
    backgroundColor: '#eff6ff',
  },
  rolePillSmallDisabled: {
    opacity: 0.5,
  },
  rolePillSmallText: {
    color: '#444',
    fontWeight: '700',
    textTransform: 'capitalize',
    fontSize: 12,
  },
  rolePillSmallTextSelected: {
    color: '#1d4ed8',
  },
  primaryBtn: {
    backgroundColor: '#2563eb',
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: 'center',
  },
  primaryBtnDisabled: {
    opacity: 0.5,
  },
  primaryBtnText: {
    color: '#fff',
    fontWeight: '700',
  },
  secondaryBtn: {
    paddingHorizontal: 10,
    paddingVertical: 10,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#ddd',
    backgroundColor: '#fff',
  },
  secondaryBtnText: {
    fontWeight: '700',
    color: '#111',
  },
  rowCard: {
    marginHorizontal: 16,
    marginBottom: 10,
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: '#eee',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  rowTextCol: {
    flex: 1,
  },
  rowTitle: {
    fontWeight: '800',
    fontSize: 15,
    marginBottom: 2,
  },
  rowMeta: {
    color: '#666',
    marginTop: 1,
  },
  dangerBtn: {
    paddingHorizontal: 10,
    paddingVertical: 10,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#fecaca',
    backgroundColor: '#fef2f2',
  },
  dangerBtnText: {
    color: '#b91c1c',
    fontWeight: '800',
  },
  pill: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: '#f3f4f6',
  },
  pillText: {
    color: '#111',
    fontWeight: '800',
  },
  errorBox: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: '#fecaca',
    marginBottom: 12,
    gap: 10,
  },
  errorText: {
    color: '#b91c1c',
    fontWeight: '600',
  },
});
