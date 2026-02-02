import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Animated, Linking, Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import { Ionicons } from '@expo/vector-icons';

import { API_BASE_URL } from '../config';
import type { ApiError } from '../lib/api';
import { apiGetMobileSsoLink } from '../lib/api';
import { clearActiveOrgSlug, getActiveOrgSlug, setActiveOrgSlug, signOut } from '../lib/auth';
import { unregisterPushTokenBestEffort } from '../lib/push';
import { theme } from '../ui/theme';

type Props = {
  initialPath?: string;
  onSignedOut: () => void;
};

function safeJoinUrl(base: string, pathOrUrl: string): string {
  try {
    // If already absolute, keep.
    // eslint-disable-next-line no-new
    new URL(pathOrUrl);
    return pathOrUrl;
  } catch {
    // treat as path
  }
  const baseUrl = base.replace(/\/$/, '');
  const p = (pathOrUrl || '/').startsWith('/') ? pathOrUrl : `/${pathOrUrl}`;
  return `${baseUrl}${p}`;
}

function getAllowedHost(): string {
  try {
    return new URL(API_BASE_URL).host;
  } catch {
    // Fallback to a permissive empty host; we'll allow only http(s) then.
    return '';
  }
}

function shouldOpenExternally(url: string): boolean {
  if (!url) return false;
  if (url.startsWith('mailto:') || url.startsWith('tel:') || url.startsWith('sms:')) return true;
  if (url.startsWith('itms-apps:') || url.startsWith('itms-services:')) return true;

  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }

  if (u.protocol !== 'http:' && u.protocol !== 'https:') return true;

  const allowedHost = getAllowedHost();
  if (allowedHost && u.host === allowedHost) return false;

  // Stripe/checkout and any other off-domain URLs should open externally.
  return true;
}

function withAppModeParam(pathOrUrl: string): string {
  if (!pathOrUrl) return '/?cc_app=1';

  // Absolute URL: preserve as-is, just append cc_app when same-origin.
  try {
    const u = new URL(pathOrUrl);
    if (u.searchParams.get('cc_app') !== '1') u.searchParams.set('cc_app', '1');
    return u.toString();
  } catch {
    // Path-only
  }

  const base = 'https://app.local';
  const p = pathOrUrl.startsWith('/') ? pathOrUrl : `/${pathOrUrl}`;
  const u = new URL(p, base);
  if (u.searchParams.get('cc_app') !== '1') u.searchParams.set('cc_app', '1');
  return u.pathname + u.search + u.hash;
}

function buildOpenBookingPath(args: { orgSlug: string; bookingId?: number; open?: 'Bookings' }): string {
  const base = `/bus/${encodeURIComponent(args.orgSlug)}/bookings/`;
  if (args.open === 'Bookings' || !args.bookingId) return withAppModeParam(base);
  // Web UI is mostly modal-based; use a query param that we can detect with injected JS.
  return withAppModeParam(`${base}?cc_open_booking=${encodeURIComponent(String(args.bookingId))}`);
}

const AUTO_OPEN_BOOKING_JS = `
(function(){
  try {
    var params = new URLSearchParams(window.location.search || '');
    var id = params.get('cc_open_booking');
    if (!id) return true;

    var bid = parseInt(id, 10);
    if (!isFinite(bid)) return true;

    // Retry for a short period in case page JS defines handlers after load.
    var tries = 0;
    var maxTries = 25; // ~5s at 200ms
    var timer = setInterval(function(){
      tries += 1;
      try {
        if (typeof window.viewBooking === 'function') {
          clearInterval(timer);
          window.viewBooking(bid);
          return;
        }
        if (typeof window.openBookingModal === 'function' && typeof window.getBookingEventById === 'function') {
          var ev = window.getBookingEventById(bid);
          if (ev) {
            clearInterval(timer);
            window.openBookingModal(ev);
            return;
          }
        }
      } catch(e) {
        // ignore
      }
      if (tries >= maxTries) clearInterval(timer);
    }, 200);
  } catch(e) {
    // ignore
  }
  return true;
})();
`;

export function WebAppScreen({ initialPath, onSignedOut }: Props) {
  const webviewRef = useRef<WebView>(null);
  const progressAnim = useRef(new Animated.Value(0)).current;

  const [loading, setLoading] = useState(true);
  const [pageLoading, setPageLoading] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeOrgSlug, setActiveOrgSlugState] = useState<string | null>(null);
  const [currentUrl, setCurrentUrl] = useState<string>('');
  const [moreOpen, setMoreOpen] = useState(false);

  const startPath = useMemo(
    () => withAppModeParam(initialPath && initialPath.trim() ? initialPath.trim() : '/post-login/'),
    [initialPath]
  );

  const hardSignOut = useCallback(async () => {
    await unregisterPushTokenBestEffort();
    await Promise.all([signOut(), clearActiveOrgSlug()]);
    onSignedOut();
  }, [onSignedOut]);

  const logoutViaWebThenNative = useCallback(() => {
    Alert.alert('Log out?', 'You will be signed out of CircleCal.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Log out',
        style: 'destructive',
        onPress: () => {
          setMoreOpen(false);
          // Clear the Django session cookie inside the WebView first.
          // The endpoint is app-UA only and supports GET.
          const logoutPath = '/accounts/mobile/logout/?next=/';
          const p = withAppModeParam(logoutPath);
          const escaped = p.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
          webviewRef.current?.injectJavaScript(`window.location.href='${escaped}'; true;`);
        },
      },
    ]);
  }, []);

  const loadWithSso = useCallback(async (nextPath: string) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await apiGetMobileSsoLink({ next: nextPath });
      setUrl(resp.url);
    } catch (e) {
      const err = e as Partial<ApiError>;
      if (err.status === 401) {
        await hardSignOut();
        return;
      }

      // If the backend you're pointing at doesn't include the new endpoint yet,
      // fall back to regular web login so Expo Go remains usable.
      if (err.status === 404) {
        const loginUrl = safeJoinUrl(API_BASE_URL, '/accounts/login/');
        setUrl(loginUrl);
        if (__DEV__) {
          setError(
            `Your backend at ${API_BASE_URL} doesn't have /api/v1/mobile/sso-link/ yet (404). ` +
              'Falling back to web login in the WebView.'
          );
        }
        return;
      }

      if (__DEV__) {
        setError(
          `Failed to open the web app via SSO (API base: ${API_BASE_URL}). ` +
            `HTTP ${typeof err.status === 'number' ? err.status : 'unknown'}.`
        );
      } else {
        setError('Failed to open the web app. Please try again.');
      }
      setUrl(null);
    } finally {
      setLoading(false);
    }
  }, [hardSignOut]);

  useEffect(() => {
    loadWithSso(startPath);
  }, [loadWithSso, startPath]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stored = await getActiveOrgSlug();
        if (!cancelled) setActiveOrgSlugState(stored);
      } catch {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const navigateInWebView = useCallback((path: string) => {
    const p = withAppModeParam((path || '/').startsWith('/') ? (path || '/') : `/${path}`);
    const escaped = p.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    webviewRef.current?.injectJavaScript(`window.location.href='${escaped}'; true;`);
  }, []);

  const bottomNavItems = useMemo(() => {
    const slug = activeOrgSlug;
    return [
      {
        key: 'dashboard',
        label: 'Dashboard',
        path: withAppModeParam(slug ? `/bus/${encodeURIComponent(slug)}/dashboard/` : '/post-login/'),
        active: currentUrl.includes('/dashboard/'),
        icon: 'home-outline' as const,
        iconActive: 'home' as const,
      },
      {
        key: 'bookings',
        label: 'Bookings',
        path: withAppModeParam(slug ? `/bus/${encodeURIComponent(slug)}/bookings/` : '/choose-business/'),
        active: currentUrl.includes('/bookings/'),
        icon: 'list-outline' as const,
        iconActive: 'list' as const,
      },
      {
        key: 'calendar',
        label: 'Calendar',
        path: withAppModeParam(slug ? `/bus/${encodeURIComponent(slug)}/calendar/` : '/choose-business/'),
        active: currentUrl.includes('/calendar/'),
        icon: 'calendar-outline' as const,
        iconActive: 'calendar' as const,
      },
      {
        key: 'profile',
        label: 'Profile',
        path: withAppModeParam('/accounts/profile/'),
        active: currentUrl.includes('/accounts/profile/'),
        icon: 'person-outline' as const,
        iconActive: 'person' as const,
      },
    ];
  }, [activeOrgSlug, currentUrl]);

  const moreItems = useMemo(() => {
    const slug = activeOrgSlug;
    return [
      {
        key: 'switch',
        label: 'Switch Business',
        icon: 'swap-horizontal-outline' as const,
        onPress: () => {
          setMoreOpen(false);
          navigateInWebView(withAppModeParam('/choose-business/'));
        },
      },
      {
        key: 'logout',
        label: 'Logout',
        icon: 'log-out-outline' as const,
        danger: true,
        onPress: () => {
          // If org is null this still logs out fine.
          void slug;
          logoutViaWebThenNative();
        },
      },
    ];
  }, [activeOrgSlug, logoutViaWebThenNative, navigateInWebView]);

  if (loading && !url) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.centerBody}>
          <ActivityIndicator />
          <Text style={styles.loadingText}>Opening CircleCal…</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (error) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.centerBody}>
          <Text style={styles.errorTitle}>Could not load</Text>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.primaryBtn} onPress={() => loadWithSso(startPath)}>
            <Text style={styles.primaryBtnText}>Try again</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  if (!url) return null;

  const startProgress = () => {
    setPageLoading(true);
    progressAnim.setValue(0);

    // Kick off a small visible start so it doesn't look stuck.
    Animated.timing(progressAnim, {
      toValue: 0.12,
      duration: 140,
      useNativeDriver: false,
    }).start();
  };

  const updateProgress = (p: number) => {
    const clamped = Math.max(0, Math.min(1, p));
    Animated.timing(progressAnim, {
      toValue: clamped,
      duration: 120,
      useNativeDriver: false,
    }).start();
  };

  const finishProgress = () => {
    // Mark not-loading immediately so late progress events can't shrink the bar.
    setPageLoading(false);

    // Animate to full width and keep it there.
    Animated.timing(progressAnim, {
      toValue: 1,
      duration: 160,
      useNativeDriver: false,
    }).start();
  };

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.webWrap}>
        <View style={styles.progressTrack}>
          <Animated.View
            style={[
              styles.progressBar,
              {
                width: progressAnim.interpolate({
                  inputRange: [0, 1],
                  outputRange: ['0%', '100%'],
                }),
              },
            ]}
          />
        </View>

        <WebView
          ref={webviewRef}
          source={{ uri: url }}
          applicationNameForUserAgent="CircleCalApp"
          javaScriptEnabled
          domStorageEnabled
          sharedCookiesEnabled
          thirdPartyCookiesEnabled={Platform.OS === 'android'}
          allowsBackForwardNavigationGestures
          injectedJavaScript={AUTO_OPEN_BOOKING_JS}
          onLoadStart={startProgress}
          onLoadProgress={({ nativeEvent }) => {
            if (!pageLoading) return;
            // nativeEvent.progress is 0..1 on most platforms.
            const p = typeof nativeEvent?.progress === 'number' ? nativeEvent.progress : 0;
            // Avoid jumping to 1.0 early; wait for onLoadEnd to finish.
            if (p >= 0 && p < 1) updateProgress(Math.max(0.12, p));
          }}
          onLoadEnd={finishProgress}
          onShouldStartLoadWithRequest={(req) => {
            const nextUrl = req.url;
            if (shouldOpenExternally(nextUrl)) {
              Linking.openURL(nextUrl).catch(() => undefined);
              return false;
            }
            return true;
          }}
          onNavigationStateChange={(nav) => {
            setCurrentUrl(nav.url || '');

            // If user logs out from the web UI, reflect it in native.
            try {
              const u = new URL(nav.url);
              if (u.pathname.startsWith('/accounts/logout') || u.pathname.startsWith('/accounts/mobile/logout')) {
                setTimeout(() => {
                  hardSignOut().catch(() => undefined);
                }, 50);
                return;
              }

              // Track org slug so the native bottom nav and push routing can target the active org.
              const match = u.pathname.match(/\/bus\/([^\/]+)\//);
              if (match && match[1]) {
                const slug = decodeURIComponent(match[1]);
                if (slug && slug !== activeOrgSlug) {
                  setActiveOrgSlugState(slug);
                  setActiveOrgSlug(slug).catch(() => undefined);
                }
              }
            } catch {
              // ignore
            }
          }}
        />

        <View style={styles.bottomNav}>
          {bottomNavItems.map((it) => (
            <Pressable
              key={it.key}
              style={styles.bottomNavItem}
              onPress={() => {
                navigateInWebView(it.path);
              }}
            >
              <Ionicons
                name={it.active ? it.iconActive : it.icon}
                size={22}
                color={it.active ? theme.colors.primaryDark : theme.colors.muted}
              />
              <Text style={[styles.bottomNavText, it.active ? styles.bottomNavTextActive : null]}>
                {it.label}
              </Text>
            </Pressable>
          ))}

          <Pressable
            key="more"
            style={styles.bottomNavItem}
            onPress={() => setMoreOpen(true)}
            accessibilityRole="button"
            accessibilityLabel="More"
          >
            <Ionicons name="menu" size={22} color={theme.colors.muted} />
            <Text style={styles.bottomNavText}>More</Text>
          </Pressable>
        </View>

        {moreOpen ? (
          <View style={styles.moreOverlay}>
            <Pressable style={styles.moreBackdrop} onPress={() => setMoreOpen(false)} />
            <View style={styles.moreSheet}>
              <View style={styles.moreHandle} />
              {moreItems.map((it) => (
                <Pressable
                  key={it.key}
                  style={[styles.moreRow, it.danger ? styles.moreRowDanger : null]}
                  onPress={it.onPress}
                >
                  <Ionicons
                    name={it.icon}
                    size={20}
                    color={it.danger ? '#b91c1c' : '#111827'}
                    style={styles.moreIcon}
                  />
                  <Text style={[styles.moreText, it.danger ? styles.moreTextDanger : null]}>
                    {it.label}
                  </Text>
                </Pressable>
              ))}
            </View>
          </View>
        ) : null}
      </View>
    </SafeAreaView>
  );
}

// Export helpers for push routing.
export const webPathFromPushData = buildOpenBookingPath;

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: theme.colors.bg },
  webWrap: { flex: 1, backgroundColor: theme.colors.bg },
  centerBody: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 16 },
  loadingText: { marginTop: 10, color: theme.colors.muted, fontSize: 13 },
  progressTrack: { height: 3, width: '100%', backgroundColor: 'transparent' },
  progressBar: { height: 3, backgroundColor: theme.colors.primary },
  errorTitle: { fontSize: 18, fontWeight: '700', marginBottom: 6, color: '#111827' },
  errorText: { color: '#374151', textAlign: 'center', marginBottom: 14 },
  primaryBtn: {
    backgroundColor: '#2563eb',
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 10,
  },
  primaryBtnText: { color: '#fff', fontWeight: '700' },

  bottomNav: {
    height: 64,
    flexDirection: 'row',
    borderTopWidth: 3,
    borderTopColor: theme.colors.primary,
    backgroundColor: '#fff',
  },
  bottomNavItem: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  bottomNavText: { fontSize: 11, fontWeight: '800', color: theme.colors.muted, marginTop: 2 },
  bottomNavTextActive: { color: theme.colors.primaryDark },

  moreOverlay: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: 0,
    bottom: 0,
    justifyContent: 'flex-end',
  },
  moreBackdrop: {
    position: 'absolute',
    left: 0,
    right: 0,
    top: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.35)',
  },
  moreSheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    paddingTop: 10,
    paddingBottom: 18,
    paddingHorizontal: 16,
    borderTopWidth: 1,
    borderTopColor: 'rgba(0,0,0,0.08)',
  },
  moreHandle: {
    width: 44,
    height: 5,
    borderRadius: 999,
    backgroundColor: '#e5e7eb',
    alignSelf: 'center',
    marginBottom: 10,
  },
  moreRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    borderRadius: 12,
  },
  moreRowDanger: {
    backgroundColor: 'rgba(185,28,28,0.04)',
  },
  moreIcon: {
    width: 28,
    textAlign: 'center',
    marginRight: 10,
  },
  moreText: {
    fontSize: 15,
    fontWeight: '700',
    color: '#111827',
  },
  moreTextDanger: {
    color: '#b91c1c',
  },
});
