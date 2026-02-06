import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import {
  ActivityIndicator,
  Alert,
  Animated,
  Image,
  Keyboard,
  Linking,
  PanResponder,
  Platform,
  Pressable,
  StyleSheet,
  TextInput,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import { Ionicons } from '@expo/vector-icons';
import * as WebBrowser from 'expo-web-browser';
import { LinearGradient } from 'expo-linear-gradient';

import { API_BASE_URL } from '../config';
import type { ApiError } from '../lib/api';
import { apiGetBookings, apiGetMobileSsoLink, apiGetProfileOverview } from '../lib/api';

const CC_LOGO = require('../../assets/cc-header-logo.png');
import {
  clearActiveOrgSlug,
  clearPostStripeMessage,
  getActiveOrgSlug,
  getPostStripeMessage,
  setActiveOrgSlug,
  setPostSignOutMessage,
  signOut,
} from '../lib/auth';
import { unregisterPushTokenBestEffort } from '../lib/push';
import {
  getInboxNotifications,
  getInboxUnreadCount,
  markInboxRead,
  subscribeInboxChanges,
  type InboxNotification,
} from '../lib/notificationStore';
import { navigationRef } from '../lib/navigation';
import { theme } from '../ui/theme';

type BillingSummaryPayload = {
  plan?: {
    slug?: string | null;
    name?: string | null;
    billing_period?: string | null;
  } | null;
  subscription?: {
    status?: string | null;
    trial_end?: string | null;
    current_period_end?: string | null;
    cancel_at_period_end?: boolean | null;
  } | null;
} | null;

type WebMessageBillingSummary = {
  type: 'CC_BILLING_SUMMARY';
  ok: boolean;
  status?: number;
  json?: any;
  error?: string;
};

type Props = {
  initialPath?: string;
  skipSso?: boolean;
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

function buildAvatarImageUrl(avatarUrl: string | null, avatarUpdatedAt: string | null): string | null {
  if (!avatarUrl) return null;
  const abs = safeJoinUrl(API_BASE_URL, avatarUrl);
  const v = (avatarUpdatedAt || '').trim();
  if (!v) return abs;
  try {
    const u = new URL(abs);
    u.searchParams.set('v', v);
    return u.toString();
  } catch {
    const sep = abs.includes('?') ? '&' : '?';
    return `${abs}${sep}v=${encodeURIComponent(v)}`;
  }
}

function isRestrictedBillingUrl(url: string): boolean {
  // CircleCal policy: prevent subscription/upgrade surfaces from
  // being shown inside the mobile app WebView (iOS + Android).
  if (!url) return false;

  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }

  const allowedHost = getAllowedHost();
  if (allowedHost && u.host !== allowedHost) return false;

  const p = u.pathname || '';
  // Allow Stripe Connect onboarding + Express dashboard. These are not subscription purchase/upgrade flows.
  if (p.includes('/stripe/connect/')) return false;
  // Org pricing page + billing routes + embedded checkout pages.
  if (p.includes('/pricing/')) return true;
  if (p.startsWith('/billing/') || p.includes('/billing/')) return true;
  if (p.includes('/embedded_checkout') || p.includes('/embedded-checkout')) return true;
  return false;
}

function shouldOpenExternally(url: string): boolean {
  if (!url) return false;
  if (url.startsWith('mailto:') || url.startsWith('tel:') || url.startsWith('sms:')) return true;
  if (url.startsWith('itms-apps:') || url.startsWith('itms-services:')) return true;
  // WebView cannot open blob: URLs and neither can Linking.openURL().
  // If we try to open these externally, users see "Can't open url: blob:...".
  // Prefer blocking external open attempts; app-mode export avoids blob URLs.
  if (url.startsWith('blob:')) return false;

  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }

  if (u.protocol !== 'http:' && u.protocol !== 'https:') return true;

  // Cloudflare Turnstile loads inside an iframe from challenges.cloudflare.com.
  // iOS/Android WebView can route iframe navigations through onShouldStartLoadWithRequest;
  // if we treat these as external, the app will bounce out to the browser.
  if (u.hostname === 'challenges.cloudflare.com') return false;

  const allowedHost = getAllowedHost();
  if (allowedHost && u.host === allowedHost) {
    // Some same-origin endpoints produce file downloads that WebView can't handle well.
    // Open these in the external browser so the OS can save/share the file.
    const p = u.pathname || '';
    if (p.includes('/bookings/audit/export/')) return true;
    return false;
  }

  // Stripe/checkout and any other off-domain URLs should open externally.
  return true;
}

function isStripeUrl(url: string): boolean {
  try {
    const u = new URL(url);
    return u.hostname === 'stripe.com' || u.hostname.endsWith('.stripe.com');
  } catch {
    return false;
  }
}

function isSameOriginAuditExportUrl(url: string): { ok: true; nextPath: string } | { ok: false } {
  if (!url) return { ok: false };
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return { ok: false };
  }

  const allowedHost = getAllowedHost();
  if (allowedHost && u.host !== allowedHost) return { ok: false };

  const p = u.pathname || '';
  if (!p.includes('/bookings/audit/export/')) return { ok: false };

  const nextPath = `${p}${u.search || ''}`;
  return { ok: true, nextPath };
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

const APP_MODE_COOKIE_JS = `
(function(){
  try {
    // Best-effort: persist app-mode for server-side detection.
    // Some navigations may lose the cc_app=1 query param; cookies are more stable.
    var secure = '';
    try { if (window.location && String(window.location.protocol) === 'https:') secure = '; Secure'; } catch(e) {}
    document.cookie = 'cc_app=1; Path=/; SameSite=Lax' + secure;
  } catch(e) {
    // ignore
  }
  return true;
})();
`;

const APP_BG_GRADIENT = {
  // Slightly stronger than the website's default blue-50 so it's noticeable behind cards.
  colors: ['#dbeafe', '#e0e7ff', '#ffffff'] as const,
  start: { x: 0, y: 0 } as const,
  end: { x: 1, y: 1 } as const,
};

function buildBillingSummaryFetchJs(orgSlug: string): string {
  const safeSlug = String(orgSlug || '');
  return `
(function(){
  try {
    var slug = ${JSON.stringify(safeSlug)};
    if (!slug) return true;
    var url = '/api/v1/billing/summary/?org=' + encodeURIComponent(slug);
    fetch(url, { credentials: 'include', headers: { 'Accept': 'application/json' } })
      .then(function(r){
        return r.json().then(function(j){
          return { ok: r.ok, status: r.status, json: j };
        }).catch(function(){
          return { ok: r.ok, status: r.status, json: null };
        });
      })
      .then(function(res){
        if (window.ReactNativeWebView && window.ReactNativeWebView.postMessage) {
          window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'CC_BILLING_SUMMARY', ok: !!res.ok, status: res.status, json: res.json }));
        }
      })
      .catch(function(e){
        if (window.ReactNativeWebView && window.ReactNativeWebView.postMessage) {
          window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'CC_BILLING_SUMMARY', ok: false, status: 0, error: String((e && e.message) ? e.message : e) }));
        }
      });
  } catch(e) {}
  return true;
})();
`;
}

export function WebAppScreen({ initialPath, skipSso, onSignedOut }: Props) {
  const insets = useSafeAreaInsets();
  const { width: windowWidth, height: windowHeight } = useWindowDimensions();
  const webviewRef = useRef<WebView>(null);
  const progressAnim = useRef(new Animated.Value(0)).current;
  const moreAnim = useRef(new Animated.Value(0)).current;
  const morePanY = useRef(new Animated.Value(0)).current;

  const [loading, setLoading] = useState(true);
  const [pageLoading, setPageLoading] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeOrgSlug, setActiveOrgSlugState] = useState<string | null>(null);
  const [currentUrl, setCurrentUrl] = useState<string>('');
  const [moreOpen, setMoreOpen] = useState(false);
  const [stripeFlash, setStripeFlash] = useState<string | null>(null);
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);
  const [userInitials, setUserInitials] = useState<string>('CC');
  const [searchText, setSearchText] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const searchAnim = useRef(new Animated.Value(0)).current;
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<Array<{ id: number; title: string; start: string | null; client_name: string; service_name: string | null }>>(
    []
  );

  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerAnim = useRef(new Animated.Value(0)).current;
  const [todayLoading, setTodayLoading] = useState(false);
  const [todayError, setTodayError] = useState<string | null>(null);
  const [todayBookings, setTodayBookings] = useState<Array<{ id: number; title: string; start: string | null; client_name: string; service_name: string | null }>>(
    []
  );

  const [billingLoading, setBillingLoading] = useState(false);
  const [billingError, setBillingError] = useState<string | null>(null);
  const [billingSummary, setBillingSummary] = useState<BillingSummaryPayload>(null);

  const [drawerNotifLoading, setDrawerNotifLoading] = useState(false);
  const [drawerNotifItems, setDrawerNotifItems] = useState<InboxNotification[]>([]);
  const [inboxUnreadCount, setInboxUnreadCount] = useState(0);

  const refreshUnreadCount = useCallback(async () => {
    try {
      const n = await getInboxUnreadCount();
      setInboxUnreadCount(n);
    } catch {
      // ignore
    }
  }, []);

  const openMore = useCallback(() => {
    setMoreOpen(true);
    morePanY.setValue(0);
    moreAnim.setValue(0);
    Animated.timing(moreAnim, {
      toValue: 1,
      duration: 200,
      useNativeDriver: true,
    }).start();
  }, [moreAnim, morePanY]);

  const closeMore = useCallback(() => {
    Animated.timing(moreAnim, {
      toValue: 0,
      duration: 180,
      useNativeDriver: true,
    }).start(({ finished }) => {
      if (finished) {
        morePanY.setValue(0);
        setMoreOpen(false);
      }
    });
  }, [moreAnim, morePanY]);

  const morePanResponder = useMemo(() => {
    return PanResponder.create({
      onMoveShouldSetPanResponder: (_evt, gesture) => {
        if (!moreOpen) return false;
        if (Math.abs(gesture.dx) > Math.abs(gesture.dy)) return false;
        return gesture.dy > 6;
      },
      onPanResponderMove: (_evt, gesture) => {
        const dy = Math.max(0, gesture.dy);
        morePanY.setValue(dy);
      },
      onPanResponderRelease: (_evt, gesture) => {
        const dy = Math.max(0, gesture.dy);
        const shouldClose = dy > 110 || gesture.vy > 1.25;
        if (shouldClose) {
          closeMore();
          return;
        }
        Animated.spring(morePanY, {
          toValue: 0,
          tension: 160,
          friction: 20,
          useNativeDriver: true,
        }).start();
      },
      onPanResponderTerminate: () => {
        Animated.spring(morePanY, {
          toValue: 0,
          tension: 160,
          friction: 20,
          useNativeDriver: true,
        }).start();
      },
    });
  }, [closeMore, moreOpen, morePanY]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const msg = await getPostStripeMessage();
        if (cancelled || !msg) return;
        setStripeFlash(msg);
        setTimeout(() => setStripeFlash(null), 8000);
      } finally {
        // Always clear so it doesn't reappear.
        await clearPostStripeMessage();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
    if (skipSso) {
      // Fallback path: load directly and rely on any existing WebView cookies/session.
      // This is especially important for Stripe-return deep links when native API tokens
      // may be missing/expired but the web session is still valid.
      setLoading(true);
      setError(null);
      try {
        const directUrl = safeJoinUrl(API_BASE_URL, startPath);
        setUrl(directUrl);
      } finally {
        setLoading(false);
      }
      return;
    }

    loadWithSso(startPath);
  }, [loadWithSso, startPath, skipSso]);

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

  const refreshProfileBadge = useCallback(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetProfileOverview({ org: activeOrgSlug });
        if (cancelled) return;

        const first = String(resp?.user?.first_name || '').trim();
        const last = String(resp?.user?.last_name || '').trim();
        const display = String(resp?.profile?.display_name || '').trim();
        const fromName = display || `${first} ${last}`.trim();
        const parts = fromName.split(/\s+/).filter(Boolean);
        const i1 = parts[0]?.[0] || (resp?.user?.username ? String(resp.user.username)[0] : 'C');
        const i2 = parts.length > 1 ? parts[parts.length - 1]?.[0] : '';
        const initials = `${String(i1).toUpperCase()}${String(i2).toUpperCase()}`.slice(0, 2) || 'CC';
        setUserInitials(initials);

        setAvatarUrl(buildAvatarImageUrl(resp?.profile?.avatar_url ?? null, resp?.profile?.avatar_updated_at ?? null));
      } catch {
        // ignore (no token / endpoint missing / network)
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeOrgSlug]);

  useEffect(() => {
    return refreshProfileBadge();
  }, [refreshProfileBadge]);

  useFocusEffect(
    useCallback(() => {
      return refreshProfileBadge();
    }, [refreshProfileBadge])
  );

  const navigateInWebView = useCallback((path: string) => {
    const p = withAppModeParam((path || '/').startsWith('/') ? (path || '/') : `/${path}`);
    const escaped = p.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    webviewRef.current?.injectJavaScript(`window.location.href='${escaped}'; true;`);
  }, []);

  const runSearch = useCallback(() => {
    const q = searchText.trim();
    const slug = activeOrgSlug;
    if (!slug) {
      // If no org selected yet, take them to choose-business.
      navigateInWebView('/choose-business/');
      return;
    }
    const base = `/bus/${encodeURIComponent(slug)}/bookings/`;
    const path = q ? `${base}?q=${encodeURIComponent(q)}` : base;
    navigateInWebView(path);
  }, [activeOrgSlug, navigateInWebView, searchText]);

  const formatLongDate = useCallback((d: Date) => {
    try {
      return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
    } catch {
      const months = [
        'January',
        'February',
        'March',
        'April',
        'May',
        'June',
        'July',
        'August',
        'September',
        'October',
        'November',
        'December',
      ];
      return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    }
  }, []);

  const formatYmdLocal = useCallback((d: Date) => {
    const yyyy = String(d.getFullYear());
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  }, []);

  const addDaysLocal = useCallback((d: Date, days: number) => {
    const x = new Date(d);
    x.setDate(x.getDate() + days);
    return x;
  }, []);

  const openDrawer = useCallback(() => {
    setDrawerOpen(true);
    drawerAnim.setValue(0);
    Animated.timing(drawerAnim, {
      toValue: 1,
      duration: 220,
      useNativeDriver: true,
    }).start();
  }, [drawerAnim]);

  const closeDrawer = useCallback(() => {
    Animated.timing(drawerAnim, {
      toValue: 0,
      duration: 180,
      useNativeDriver: true,
    }).start(({ finished }) => {
      if (finished) setDrawerOpen(false);
    });
  }, [drawerAnim]);

  useEffect(() => {
    let cancelled = false;
    if (!drawerOpen) return;
    if (!activeOrgSlug) {
      setTodayBookings([]);
      setTodayError(null);
      setTodayLoading(false);
      return;
    }

    (async () => {
      try {
        setTodayLoading(true);
        setTodayError(null);
        const today = new Date();
        const from = formatYmdLocal(today);
        const to = formatYmdLocal(addDaysLocal(today, 1));
        const resp = await apiGetBookings({ org: activeOrgSlug, from, to, limit: 3 });
        if (cancelled) return;
        const items = (resp?.bookings ?? []).map((b) => ({
          id: b.id,
          title: b.title,
          start: b.start,
          client_name: b.client_name,
          service_name: b.service?.name ?? null,
        }));
        setTodayBookings(items);
      } catch (e) {
        if (cancelled) return;
        const msg = (e as any)?.message ? String((e as any).message) : 'Failed to load schedule';
        setTodayError(msg);
      } finally {
        if (!cancelled) setTodayLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeOrgSlug, addDaysLocal, drawerOpen, formatYmdLocal]);

  useEffect(() => {
    let cancelled = false;
    if (!drawerOpen) return;
    (async () => {
      try {
        setDrawerNotifLoading(true);
        const all = await getInboxNotifications();
        if (cancelled) return;
        setDrawerNotifItems(all.slice(0, 3));
      } finally {
        if (!cancelled) setDrawerNotifLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [drawerOpen]);

  useEffect(() => {
    if (!drawerOpen) return;
    if (!activeOrgSlug) {
      setBillingLoading(false);
      setBillingError(null);
      setBillingSummary(null);
      return;
    }

    setBillingLoading(true);
    setBillingError(null);
    try {
      webviewRef.current?.injectJavaScript(buildBillingSummaryFetchJs(activeOrgSlug));
    } catch {
      setBillingLoading(false);
      setBillingError('Unable to load plan status');
    }
  }, [activeOrgSlug, drawerOpen]);

  const billingDisplay = useMemo(() => {
    if (!activeOrgSlug) {
      return { title: 'Plan & trial', sub: 'Choose a business to see status' };
    }
    if (billingLoading) {
      return { title: 'Plan & trial', sub: 'Loading…' };
    }
    if (billingError) {
      return { title: 'Plan & trial', sub: billingError };
    }

    const planSlug = billingSummary?.plan?.slug ? String(billingSummary.plan.slug) : '';
    const planName = billingSummary?.plan?.name ? String(billingSummary.plan.name) : '';
    const status = billingSummary?.subscription?.status ? String(billingSummary.subscription.status) : '';
    const trialEndRaw = billingSummary?.subscription?.trial_end ? String(billingSummary.subscription.trial_end) : '';

    const planLabel = (planName || planSlug || 'Basic').trim();

    let sub = `Plan: ${planLabel}`;
    if (status) sub = `${sub} • ${status}`;

    if (status === 'trialing' && trialEndRaw) {
      const end = new Date(trialEndRaw);
      const now = new Date();
      if (!isNaN(end.getTime())) {
        const msLeft = end.getTime() - now.getTime();
        const daysLeft = Math.ceil(msLeft / (24 * 60 * 60 * 1000));
        if (daysLeft > 1) sub = `Trial: ${daysLeft} days left • Ends ${end.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
        else if (daysLeft === 1) sub = `Trial: 1 day left • Ends ${end.toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
        else sub = `Trial ended • Plan: ${planLabel}`;
      }
    }

    return { title: 'Plan & trial', sub };
  }, [activeOrgSlug, billingError, billingLoading, billingSummary]);

  const showHowToPayPopup = useCallback(() => {
    Alert.alert(
      'Plan changes unavailable',
      'Plan changes and payments aren’t available in the app. If you need help with billing, contact support.',
      [
        {
          text: 'Contact support',
          onPress: () => {
            closeDrawer();
            navigateInWebView(withAppModeParam('/contact/'));
          },
        },
        { text: 'OK', style: 'cancel' },
      ]
    );
  }, [closeDrawer, navigateInWebView]);

  useEffect(() => {
    refreshUnreadCount().catch(() => undefined);
    const unsub = subscribeInboxChanges(() => {
      refreshUnreadCount().catch(() => undefined);
    });
    return unsub;
  }, [refreshUnreadCount]);

  const openSearch = useCallback(() => {
    setSearchOpen(true);
    searchAnim.setValue(0);
    setSearchError(null);
    setSearchResults([]);
    Animated.timing(searchAnim, {
      toValue: 1,
      duration: 180,
      useNativeDriver: true,
    }).start();
  }, [searchAnim]);

  const closeSearch = useCallback(() => {
    Keyboard.dismiss();
    Animated.timing(searchAnim, {
      toValue: 0,
      duration: 160,
      useNativeDriver: true,
    }).start(({ finished }) => {
      if (finished) setSearchOpen(false);
    });
  }, [searchAnim]);

  useEffect(() => {
    let cancelled = false;
    if (!searchOpen) return;
    const q = searchText.trim();
    if (!q) {
      setSearchResults([]);
      setSearchError(null);
      setSearchLoading(false);
      return;
    }

    // Debounce typing.
    const t = setTimeout(() => {
      (async () => {
        const slug = activeOrgSlug;
        if (!slug) return;
        try {
          setSearchLoading(true);
          setSearchError(null);
          const today = new Date();
          const from = formatYmdLocal(today);
          const to = formatYmdLocal(addDaysLocal(today, 14));
          const resp = await apiGetBookings({ org: slug, from, to, limit: 20, q });
          if (cancelled) return;
          const items = (resp?.bookings ?? []).map((b) => ({
            id: b.id,
            title: b.title,
            start: b.start,
            client_name: b.client_name,
            service_name: b.service?.name ?? null,
          }));
          setSearchResults(items);
        } catch (e) {
          if (cancelled) return;
          const msg = (e as any)?.message ? String((e as any).message) : 'Search failed';
          setSearchError(msg);
        } finally {
          if (!cancelled) setSearchLoading(false);
        }
      })();
    }, 220);

    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [activeOrgSlug, addDaysLocal, formatYmdLocal, searchOpen, searchText]);

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

  const supportPath = useMemo(() => withAppModeParam('/contact/'), []);
  const privacyPath = useMemo(() => withAppModeParam('/privacy/'), []);
  const termsPath = useMemo(() => withAppModeParam('/terms/'), []);
  const platformLabel = useMemo(() => (Platform.OS === 'ios' ? 'iOS' : 'Android'), []);

  const searchQuickLinks = useMemo(() => {
    const slug = activeOrgSlug;
    const bus = (p: string) => (slug ? withAppModeParam(`/bus/${encodeURIComponent(slug)}${p}`) : '/choose-business/');
    return [
      {
        key: 'dashboard',
        label: 'Dashboard',
        icon: 'home' as const,
        keywords: ['home', 'dash'],
        path: bus('/dashboard/'),
      },
      {
        key: 'calendar',
        label: 'Calendar',
        icon: 'calendar' as const,
        keywords: ['schedule', 'today', 'week', 'month'],
        path: bus('/calendar/'),
      },
      {
        key: 'bookings',
        label: 'Bookings',
        icon: 'list' as const,
        keywords: ['appointments', 'appt', 'clients', 'payments', 'charges'],
        path: bus('/bookings/'),
      },
      {
        key: 'profile',
        label: 'Profile',
        icon: 'person' as const,
        keywords: ['account', 'settings', 'avatar'],
        path: withAppModeParam('/accounts/profile/'),
      },
      {
        key: 'switch',
        label: 'Switch business',
        icon: 'swap-horizontal' as const,
        keywords: ['org', 'organization', 'business'],
        path: withAppModeParam('/choose-business/'),
      },
      {
        key: 'support',
        label: 'Contact support',
        icon: 'help-circle' as const,
        keywords: ['help', 'support', 'email'],
        path: supportPath,
      },
      {
        key: 'privacy',
        label: 'Privacy policy',
        icon: 'document-text' as const,
        keywords: ['privacy'],
        path: privacyPath,
      },
      {
        key: 'terms',
        label: 'Terms of service',
        icon: 'reader' as const,
        keywords: ['terms'],
        path: termsPath,
      },
    ];
  }, [activeOrgSlug, privacyPath, supportPath, termsPath]);

  const filteredQuickLinks = useMemo(() => {
    const q = searchText.trim().toLowerCase();
    if (!q) return [];
    return searchQuickLinks.filter((x) => {
      if (x.label.toLowerCase().includes(q)) return true;
      return (x.keywords || []).some((k) => k.toLowerCase().includes(q));
    });
  }, [searchQuickLinks, searchText]);

  const moreItems = useMemo(() => {
    const slug = activeOrgSlug;
    return [
      {
        key: 'switch',
        label: 'Switch Business',
        icon: 'swap-horizontal-outline' as const,
        onPress: () => {
          closeMore();
          navigateInWebView(withAppModeParam('/choose-business/'));
        },
      },
      {
        key: 'support',
        label: 'Contact support',
        icon: 'help-circle-outline' as const,
        onPress: () => {
          closeMore();
          navigateInWebView(supportPath);
        },
      },
      {
        key: 'privacy',
        label: 'Privacy policy',
        icon: 'document-text-outline' as const,
        onPress: () => {
          closeMore();
          navigateInWebView(privacyPath);
        },
      },
      {
        key: 'terms',
        label: 'Terms of service',
        icon: 'reader-outline' as const,
        onPress: () => {
          closeMore();
          navigateInWebView(termsPath);
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
  }, [activeOrgSlug, closeMore, logoutViaWebThenNative, navigateInWebView, privacyPath, supportPath, termsPath]);

  if (loading && !url) {
    return (
      <LinearGradient colors={APP_BG_GRADIENT.colors} start={APP_BG_GRADIENT.start} end={APP_BG_GRADIENT.end} style={styles.gradient}>
        <SafeAreaView edges={['left', 'right']} style={styles.safeTransparent}>
          <View style={[styles.topSafeFill, { height: insets.top }]} />
          <View style={styles.centerBody}>
            <ActivityIndicator />
            <Text style={styles.loadingText}>Opening CircleCal…</Text>
          </View>
        </SafeAreaView>
      </LinearGradient>
    );
  }

  if (error) {
    return (
      <LinearGradient colors={APP_BG_GRADIENT.colors} start={APP_BG_GRADIENT.start} end={APP_BG_GRADIENT.end} style={styles.gradient}>
        <SafeAreaView edges={['left', 'right']} style={styles.safeTransparent}>
          <View style={[styles.topSafeFill, { height: insets.top }]} />
          <View style={styles.centerBody}>
            <Text style={styles.errorTitle}>Could not load</Text>
            <Text style={styles.errorText}>{error}</Text>
            <Pressable style={styles.primaryBtn} onPress={() => loadWithSso(startPath)}>
              <Text style={styles.primaryBtnText}>Try again</Text>
            </Pressable>
          </View>
        </SafeAreaView>
      </LinearGradient>
    );
  }

  if (!url) return null;

  const appUaName = Platform.OS === 'ios' ? 'CircleCalApp-iOS' : 'CircleCalApp-Android';
  const iosOpaqueProps = Platform.OS === 'ios' ? ({ underPageBackgroundColor: '#ffffff' } as any) : {};

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

  const holdProgressForBlockedNav = () => {
    // When we block a navigation (e.g. show a native alert), WebView may never
    // call onLoadEnd. Keep the bar in a “nearly done” state until the user
    // chooses an option, then complete it.
    setPageLoading(true);
    Animated.timing(progressAnim, {
      toValue: 0.92,
      duration: 140,
      useNativeDriver: false,
    }).start();
  };

  const completeProgressAfterDecision = () => {
    // Complete to 100% so it feels intentional, and keep it there
    // (same behavior as normal page loads).
    setPageLoading(false);
    Animated.timing(progressAnim, {
      toValue: 1,
      duration: 160,
      useNativeDriver: false,
    }).start();
  };

  return (
    <LinearGradient colors={APP_BG_GRADIENT.colors} start={APP_BG_GRADIENT.start} end={APP_BG_GRADIENT.end} style={styles.gradient}>
      <SafeAreaView edges={['left', 'right']} style={styles.safeTransparent}>
        <View style={[styles.topChrome, { paddingTop: insets.top }]}>
          <View style={styles.topBar}>
            <View style={styles.topLeftBtns}>
              <Pressable
                style={styles.topIconBtn}
                onPress={openSearch}
                accessibilityRole="button"
                accessibilityLabel="Search"
              >
                <Ionicons name="search" size={20} color={theme.colors.muted} />
              </Pressable>
            </View>

            <View style={styles.topCenter} pointerEvents="none">
              <Image source={CC_LOGO} style={styles.topLogo} />
            </View>

            <View style={styles.topRightBtns}>
              <Pressable
                style={styles.topAvatarBtn}
                onPress={() => navigateInWebView('/accounts/profile/')}
                accessibilityRole="button"
                accessibilityLabel="Profile"
              >
                {avatarUrl ? (
                  <Image source={{ uri: avatarUrl }} style={styles.topAvatarImg} />
                ) : (
                  <Text style={styles.topAvatarInitials}>{userInitials}</Text>
                )}
              </Pressable>
              <Pressable
                style={[styles.topIconBtn, styles.topRightIconSpacing]}
                onPress={openDrawer}
                accessibilityRole="button"
                accessibilityLabel="Menu"
              >
                <Ionicons name="menu" size={22} color={theme.colors.muted} />
                {inboxUnreadCount > 0 ? (
                  <View style={styles.hamburgerBadge} pointerEvents="none">
                    <Text style={styles.hamburgerBadgeText}>
                      {inboxUnreadCount > 9 ? '9+' : String(inboxUnreadCount)}
                    </Text>
                  </View>
                ) : null}
              </Pressable>
            </View>
          </View>
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
        </View>

        {searchOpen ? (
          <Animated.View
            style={[
              styles.searchOverlay,
              {
                opacity: searchAnim,
                transform: [
                  {
                    translateY: searchAnim.interpolate({ inputRange: [0, 1], outputRange: [12, 0] }),
                  },
                ],
              },
            ]}
          >
            <Pressable style={styles.searchBackdrop} onPress={closeSearch}>
              <View style={[styles.searchPanel, { paddingTop: insets.top }]} pointerEvents="box-none">
                <View style={styles.searchHeader}>
                  <View style={styles.searchHeaderLeft}>
                    <Image source={CC_LOGO} style={styles.searchLogo} />
                    <Text style={styles.searchTitle}>Search</Text>
                  </View>
                  <Pressable
                    style={styles.searchCloseBtn}
                    onPress={closeSearch}
                    accessibilityRole="button"
                    accessibilityLabel="Close search"
                  >
                    <Ionicons name="close" size={24} color={theme.colors.muted} />
                  </Pressable>
                </View>

                <Pressable style={styles.searchInputWrap} onPress={() => {}}>
                  <Ionicons name="search" size={18} color={theme.colors.muted} style={styles.searchInputIcon} />
                  <TextInput
                    autoFocus
                    value={searchText}
                    onChangeText={setSearchText}
                    placeholder="Search…"
                    placeholderTextColor={theme.colors.muted}
                    style={styles.searchInput}
                    returnKeyType="search"
                    onSubmitEditing={() => {
                      // If org is selected, prefer native results panel.
                      if (!activeOrgSlug) {
                        runSearch();
                        closeSearch();
                        return;
                      }
                      // Otherwise keep focus and let results list handle navigation.
                    }}
                  />
                </Pressable>

                {searchText.trim() ? (
                  filteredQuickLinks.length ? (
                    <View style={styles.searchSection}>
                      <Text style={styles.searchSectionTitle}>Pages</Text>
                      <View style={styles.searchResults}>
                        {filteredQuickLinks.slice(0, 5).map((it, idx) => (
                          <Pressable
                            key={it.key}
                            style={[
                              styles.searchResultRow,
                              idx === Math.min(filteredQuickLinks.length, 5) - 1 ? styles.searchResultRowLast : null,
                            ]}
                            onPress={() => {
                              closeSearch();
                              navigateInWebView(it.path);
                            }}
                          >
                            <View style={styles.searchResultLeft}>
                              <Text style={styles.searchResultTitle} numberOfLines={1}>
                                {it.label}
                              </Text>
                            </View>
                            <Ionicons name={it.icon} size={18} color={theme.colors.muted} />
                          </Pressable>
                        ))}
                      </View>
                    </View>
                  ) : null
                ) : null}

                {activeOrgSlug ? (
                  searchLoading ? (
                    <View style={styles.searchLoadingRow}>
                      <ActivityIndicator size="small" color={theme.colors.primary} />
                      <Text style={styles.searchHint}>Searching…</Text>
                    </View>
                  ) : searchError ? (
                    <Text style={styles.searchHint}>{searchError}</Text>
                  ) : searchText.trim() ? (
                    searchResults.length ? (
                      <View style={styles.searchSection}>
                        <Text style={styles.searchSectionTitle}>Bookings</Text>
                        <View style={styles.searchResults}>
                          {searchResults.map((b, idx) => (
                          <Pressable
                            key={b.id}
                            style={[styles.searchResultRow, idx === searchResults.length - 1 ? styles.searchResultRowLast : null]}
                            onPress={() => {
                              const slug = activeOrgSlug;
                              if (!slug) return;
                              closeSearch();
                              // Navigate to bookings list with highlight.
                              // (This is safe even if detail route doesn't exist.)
                              navigateInWebView(`/bus/${encodeURIComponent(slug)}/bookings/?q=${encodeURIComponent(searchText.trim())}`);
                            }}
                          >
                            <View style={styles.searchResultLeft}>
                              <Text style={styles.searchResultTitle} numberOfLines={1}>
                                {b.service_name || b.title || 'Booking'}
                              </Text>
                              <Text style={styles.searchResultSub} numberOfLines={1}>
                                {b.client_name || 'Client'}
                              </Text>
                            </View>
                            <Text style={styles.searchResultTime}>
                              {b.start
                                ? new Date(b.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
                                : ''}
                            </Text>
                          </Pressable>
                          ))}
                        </View>
                      </View>
                    ) : (
                      <Text style={styles.searchHint}>No results in the next 14 days.</Text>
                    )
                  ) : (
                    <Text style={styles.searchHint}>Type to search anything.</Text>
                  )
                ) : (
                  <Text style={styles.searchHint}>Tap outside to close</Text>
                )}
              </View>
            </Pressable>
          </Animated.View>
        ) : null}

        {drawerOpen ? (
          <Animated.View
            style={[
              styles.drawerOverlay,
              {
                opacity: drawerAnim,
              },
            ]}
          >
            <Pressable style={styles.drawerBackdrop} onPress={closeDrawer}>
              <Animated.View
                style={[
                  styles.drawerPanel,
                  {
                    height: windowHeight,
                    transform: [
                      {
                        translateX: drawerAnim.interpolate({
                          inputRange: [0, 1],
                          outputRange: [Math.min(380, windowWidth * 0.9), 0],
                        }),
                      },
                    ],
                  },
                ]}
              >
                <View style={[styles.drawerHeader, { paddingTop: insets.top + 8 }]}>
                  <View style={styles.drawerHeaderRow}>
                    <Text style={styles.drawerTitle}>Today</Text>
                    <Pressable
                      style={styles.drawerCloseBtn}
                      onPress={closeDrawer}
                      accessibilityRole="button"
                      accessibilityLabel="Close menu"
                    >
                      <Ionicons name="close" size={22} color={theme.colors.muted} />
                    </Pressable>
                  </View>
                  <Text style={styles.drawerDate}>{formatLongDate(new Date())}</Text>
                </View>

                <View style={styles.drawerSection}>
                  <Pressable
                    style={styles.planBanner}
                    onPress={showHowToPayPopup}
                    accessibilityRole="button"
                    accessibilityLabel="Plan and trial information"
                  >
                    <View style={styles.planBannerLeft}>
                      <Text style={styles.planBannerTitle}>{billingDisplay.title}</Text>
                      <Text style={styles.planBannerSub} numberOfLines={2}>
                        {billingDisplay.sub}
                      </Text>
                    </View>
                    <View style={styles.planBannerRight}>
                      {billingLoading ? <ActivityIndicator size="small" color={theme.colors.primary} /> : null}
                      <Ionicons name="chevron-forward" size={18} color={theme.colors.muted} />
                    </View>
                  </Pressable>
                </View>

                <View style={styles.drawerSection}>
                  {activeOrgSlug ? null : (
                    <Text style={styles.drawerMuted}>Choose a business to see today’s schedule.</Text>
                  )}

                  {activeOrgSlug ? (
                    todayLoading ? (
                      <View style={styles.drawerLoadingRow}>
                        <ActivityIndicator size="small" color={theme.colors.primary} />
                        <Text style={styles.drawerMuted}>Loading schedule…</Text>
                      </View>
                    ) : todayError ? (
                      <Text style={styles.drawerMuted}>{todayError}</Text>
                    ) : todayBookings.length ? (
                      <View style={styles.drawerList}>
                        {todayBookings.map((b) => (
                          <View key={b.id} style={styles.drawerListItem}>
                            <View style={styles.drawerListLeft}>
                              <Text style={styles.drawerListTitle} numberOfLines={1}>
                                {b.service_name || b.title || 'Booking'}
                              </Text>
                              <Text style={styles.drawerListSub} numberOfLines={1}>
                                {b.client_name || 'Client'}
                              </Text>
                            </View>
                            <Text style={styles.drawerListTime}>
                              {b.start
                                ? new Date(b.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
                                : ''}
                            </Text>
                          </View>
                        ))}
                      </View>
                    ) : (
                      <Text style={styles.drawerMuted}>No bookings found for today.</Text>
                    )
                  ) : null}

                  <Pressable
                    style={styles.drawerPrimaryBtn}
                    onPress={() => {
                      const slug = activeOrgSlug;
                      if (!slug) {
                        closeDrawer();
                        navigateInWebView('/choose-business/');
                        return;
                      }
                      closeDrawer();
                      navigateInWebView(`/bus/${encodeURIComponent(slug)}/calendar/`);
                    }}
                  >
                    <Ionicons name="calendar" size={18} color="#fff" style={styles.drawerBtnIcon} />
                    <Text style={styles.drawerPrimaryBtnText}>View full day</Text>
                  </Pressable>
                </View>

                <View style={styles.drawerDivider} />

                <View style={styles.drawerSection}>
                  <View style={styles.drawerSectionHeaderRow}>
                    <Text style={styles.drawerSectionTitle}>Notifications</Text>
                    <Pressable
                      style={styles.drawerSectionLink}
                      onPress={() => {
                        closeDrawer();
                        try {
                          if (navigationRef.isReady()) navigationRef.navigate('Notifications');
                        } catch {
                          // ignore
                        }
                      }}
                      accessibilityRole="button"
                      accessibilityLabel="View all notifications"
                    >
                      <Text style={styles.drawerSectionLinkText}>View all</Text>
                    </Pressable>
                  </View>

                  {drawerNotifLoading ? (
                    <View style={styles.drawerLoadingRow}>
                      <ActivityIndicator size="small" color={theme.colors.primary} />
                      <Text style={styles.drawerMuted}>Loading notifications…</Text>
                    </View>
                  ) : drawerNotifItems.length ? (
                    <View style={styles.drawerList}>
                      {drawerNotifItems.map((n) => {
                        const data: any = n.data || {};
                        const orgSlug = typeof data.orgSlug === 'string' ? data.orgSlug : null;
                        const open = typeof data.open === 'string' ? data.open : null;
                        const bookingIdRaw = data.bookingId;
                        const bookingId = typeof bookingIdRaw === 'number' ? bookingIdRaw : Number(bookingIdRaw);
                        const canOpenBookingsList = Boolean(orgSlug && open === 'Bookings');
                        const canOpenBooking = Boolean(orgSlug && Number.isFinite(bookingId));
                        const canOpen = canOpenBookingsList || canOpenBooking;
                        return (
                          <Pressable
                            key={n.id}
                            style={styles.drawerNotifRow}
                            onPress={() => {
                              closeDrawer();
                              markInboxRead(n.id).catch(() => undefined);
                              if (canOpen) {
                                navigateInWebView(
                                  canOpenBookingsList
                                    ? buildOpenBookingPath({ orgSlug: orgSlug as string, open: 'Bookings' })
                                    : buildOpenBookingPath({ orgSlug: orgSlug as string, bookingId: bookingId as number })
                                );
                                return;
                              }
                              try {
                                if (navigationRef.isReady()) navigationRef.navigate('Notifications');
                              } catch {
                                // ignore
                              }
                            }}
                          >
                            <View style={styles.drawerListLeft}>
                              <Text style={styles.drawerListTitle} numberOfLines={1}>
                                {n.title || 'Notification'}
                              </Text>
                              {n.body ? (
                                <Text style={styles.drawerListSub} numberOfLines={1}>
                                  {n.body}
                                </Text>
                              ) : null}
                            </View>
                            <Ionicons name={canOpen ? 'chevron-forward' : 'notifications'} size={16} color={theme.colors.muted} />
                          </Pressable>
                        );
                      })}
                    </View>
                  ) : (
                    <Text style={styles.drawerMuted}>No notifications yet.</Text>
                  )}
                </View>

                <View style={styles.drawerDivider} />

                <View style={styles.drawerSection}>
                  <Pressable
                    style={styles.drawerRowBtn}
                    onPress={() => {
                      closeDrawer();
                      navigateInWebView('/choose-business/');
                    }}
                  >
                    <Ionicons name="swap-horizontal" size={18} color={theme.colors.primaryDark} style={styles.drawerRowIcon} />
                    <Text style={styles.drawerRowText}>Switch business</Text>
                  </Pressable>
                  <Pressable
                    style={styles.drawerRowBtn}
                    onPress={() => {
                      closeDrawer();
                      navigateInWebView(supportPath);
                    }}
                  >
                    <Ionicons name="help-circle" size={18} color={theme.colors.primaryDark} style={styles.drawerRowIcon} />
                    <Text style={styles.drawerRowText}>Contact support</Text>
                  </Pressable>
                  <Pressable
                    style={styles.drawerRowBtn}
                    onPress={() => {
                      closeDrawer();
                      logoutViaWebThenNative();
                    }}
                  >
                    <Ionicons name="log-out" size={18} color={theme.colors.primaryDark} style={styles.drawerRowIcon} />
                    <Text style={styles.drawerRowText}>Logout</Text>
                  </Pressable>
                </View>

                <View style={{ height: Math.max(16, insets.bottom) }} />
              </Animated.View>
            </Pressable>
          </Animated.View>
        ) : null}

      <View style={styles.webWrap}>

        {stripeFlash ? <Text style={styles.successBanner}>{stripeFlash}</Text> : null}

        <WebView
          ref={webviewRef}
          source={{ uri: url }}
          applicationNameForUserAgent={appUaName}
          javaScriptEnabled
          domStorageEnabled
          sharedCookiesEnabled
          thirdPartyCookiesEnabled={Platform.OS === 'android'}
          androidLayerType={Platform.OS === 'android' ? 'software' : undefined}
          {...iosOpaqueProps}
          allowsBackForwardNavigationGestures
          injectedJavaScriptBeforeContentLoaded={APP_MODE_COOKIE_JS}
          injectedJavaScript={AUTO_OPEN_BOOKING_JS}
          containerStyle={styles.webViewContainer}
          style={styles.webView}
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

            // If the web session logs out (or we redirect to the app-logout endpoint),
            // immediately sign out natively and stop the WebView from navigating to
            // the post-logout website home.
            try {
              const u = new URL(nextUrl);
              if (u.pathname.startsWith('/accounts/logout') || u.pathname.startsWith('/accounts/mobile/logout')) {
                completeProgressAfterDecision();
                const flash = u.searchParams.get('cc_flash') || '';
                if (flash === 'deleted') {
                  setPostSignOutMessage('Your account was deleted successfully.').catch(() => undefined);
                } else if (flash === 'deactivated') {
                  setPostSignOutMessage(
                    'Your account was deactivated successfully. To reactivate, please use a web browser on the CircleCal website.'
                  ).catch(() => undefined);
                }
                hardSignOut().catch(() => undefined);
                return false;
              }
            } catch {
              // ignore
            }

            if (isRestrictedBillingUrl(nextUrl)) {
              holdProgressForBlockedNav();
              let alertTitle = 'Billing changes unavailable';
              let alertMsg = `Billing changes aren’t available in the ${platformLabel} app.`;
              try {
                const u = new URL(nextUrl);
                const gateMsg = u.searchParams.get('cc_gate_msg') || '';
                if (gateMsg) {
                  alertTitle = 'Upgrade required';
                  alertMsg = gateMsg;
                }
              } catch {
                // ignore
              }

              Alert.alert(alertTitle, alertMsg, [
                {
                  text: 'Contact support',
                  onPress: () => {
                    completeProgressAfterDecision();
                    navigateInWebView(supportPath);
                  },
                },
                {
                  text: 'OK',
                  style: 'cancel',
                  onPress: () => {
                    completeProgressAfterDecision();
                  },
                },
              ]);
              return false;
            }

            if (shouldOpenExternally(nextUrl)) {
              completeProgressAfterDecision();
              // Prefer an in-app auth-session style browser for Stripe so it can return
              // to the app and automatically close when Stripe redirects back.
              if (isStripeUrl(nextUrl)) {
                const returnUrl = 'circlecal://stripe-return';
                WebBrowser.openAuthSessionAsync(nextUrl, returnUrl)
                  .then((res: any) => {
                    if (res?.type === 'success' && typeof res?.url === 'string' && res.url.startsWith(returnUrl)) {
                      let msg = 'Returned from Stripe.';
                      try {
                        const u = new URL(res.url);
                        const status = u.searchParams.get('status') || '';
                        if (status === 'connected') msg = 'Stripe is connected and ready for payments.';
                        else if (status === 'express_done') msg = 'Stripe Express setup complete.';
                      } catch {
                        // ignore
                      }
                      setStripeFlash(msg);
                      setTimeout(() => setStripeFlash(null), 8000);
                    }
                  })
                  .catch(() => {
                    Linking.openURL(nextUrl).catch(() => undefined);
                  });
              } else {
                const auditExport = isSameOriginAuditExportUrl(nextUrl);
                if (auditExport.ok) {
                  // System browser doesn't share WebView cookies, so open via mobile SSO.
                  // This matches the native export flow and avoids a CircleCal login prompt.
                  (async () => {
                    try {
                      const sso = await apiGetMobileSsoLink({ next: auditExport.nextPath });
                      if (sso?.url) {
                        await Linking.openURL(String(sso.url));
                        return;
                      }
                    } catch {
                      // fall back
                    }
                    Linking.openURL(nextUrl).catch(() => undefined);
                  })().catch(() => undefined);
                } else {
                  Linking.openURL(nextUrl).catch(() => undefined);
                }
              }
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
                const flash = u.searchParams.get('cc_flash') || '';
                if (flash === 'deleted') {
                  setPostSignOutMessage('Your account was deleted successfully.').catch(() => undefined);
                } else if (flash === 'deactivated') {
                  setPostSignOutMessage(
                    'Your account was deactivated successfully. To reactivate, please use a web browser on the CircleCal website.'
                  ).catch(() => undefined);
                }
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
          onMessage={(ev) => {
            const raw = ev?.nativeEvent?.data;
            if (!raw || typeof raw !== 'string') return;
            let msg: any;
            try {
              msg = JSON.parse(raw);
            } catch {
              return;
            }
            const m = msg as WebMessageBillingSummary;
            if (m && m.type === 'CC_BILLING_SUMMARY') {
              setBillingLoading(false);
              if (!m.ok) {
                const status = typeof m.status === 'number' ? m.status : 0;
                if (status === 400 || status === 401 || status === 403) {
                  setBillingError('Ask the business owner for plan status');
                } else {
                  setBillingError(m.error ? String(m.error) : 'Unable to load plan status');
                }
                setBillingSummary(null);
                return;
              }
              setBillingError(null);
              const json = m.json || null;
              setBillingSummary(json as BillingSummaryPayload);
            }
          }}
        />

        <View style={[styles.bottomNavContainer, { paddingBottom: insets.bottom }]}>
          <View style={styles.bottomNavRow}>
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
              onPress={openMore}
              accessibilityRole="button"
              accessibilityLabel="More"
            >
              <Ionicons name="add" size={24} color={theme.colors.muted} />
              <Text style={styles.bottomNavText}>More</Text>
            </Pressable>
          </View>
        </View>

        {moreOpen ? (
          <View style={styles.moreOverlay}>
            <Pressable style={StyleSheet.absoluteFill} onPress={closeMore}>
              <Animated.View
                pointerEvents="none"
                style={[
                  styles.moreBackdrop,
                  {
                    opacity: moreAnim.interpolate({
                      inputRange: [0, 1],
                      outputRange: [0, 0.35],
                    }),
                  },
                ]}
              />
            </Pressable>

            <Animated.View
              style={[
                styles.moreSheet,
                {
                  paddingBottom: 18 + insets.bottom,
                  transform: [
                    {
                      translateY: Animated.add(
                        moreAnim.interpolate({
                          inputRange: [0, 1],
                          outputRange: [420, 0],
                        }),
                        morePanY
                      ),
                    },
                  ],
                },
              ]}
              {...morePanResponder.panHandlers}
            >
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
            </Animated.View>
          </View>
        ) : null}
      </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

// Export helpers for push routing.
export const webPathFromPushData = buildOpenBookingPath;

const styles = StyleSheet.create({
  gradient: { flex: 1 },
  safeTransparent: { flex: 1, backgroundColor: 'transparent' },
  topSafeFill: { width: '100%', backgroundColor: '#fff' },
  topChrome: { width: '100%', backgroundColor: '#fff' },
  topBar: {
    height: 50,
    paddingHorizontal: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    position: 'relative',
  },
  topLeftBtns: { width: 48, flexDirection: 'row', alignItems: 'center' },
  topCenter: {
    position: 'absolute',
    left: 0,
    right: 0,
    alignItems: 'center',
    justifyContent: 'center',
  },
  topLogo: { width: 34, height: 34, resizeMode: 'contain' },
  topRightBtns: { minWidth: 96, flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end' },
  topIconBtn: {
    width: 38,
    height: 38,
    borderRadius: 999,
    backgroundColor: '#f3f4f6',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.06)',
  },
  hamburgerBadge: {
    position: 'absolute',
    top: -4,
    right: -4,
    minWidth: 18,
    height: 18,
    paddingHorizontal: 5,
    borderRadius: 999,
    backgroundColor: '#ef4444',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: '#fff',
  },
  hamburgerBadgeText: { color: '#fff', fontSize: 10, fontWeight: '900' },
  topRightIconSpacing: { marginLeft: 10 },
  topAvatarBtn: {
    width: 38,
    height: 38,
    borderRadius: 999,
    backgroundColor: '#eff6ff',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: theme.colors.primary,
  },
  topAvatarImg: { width: 38, height: 38, borderRadius: 999 },
  topAvatarInitials: { color: theme.colors.primary, fontWeight: '900', fontSize: 13 },
  searchOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 50,
    elevation: 20,
  },
  searchBackdrop: { flex: 1, backgroundColor: 'rgba(255,255,255,0.98)' },
  searchPanel: { flex: 1, paddingHorizontal: 16 },
  searchHeader: {
    height: 56,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  searchHeaderLeft: { flexDirection: 'row', alignItems: 'center' },
  searchLogo: { width: 32, height: 32, resizeMode: 'contain', marginRight: 10 },
  searchTitle: { fontSize: 18, fontWeight: '900', color: '#111827' },
  searchCloseBtn: {
    width: 42,
    height: 42,
    borderRadius: 999,
    alignItems: 'center',
    justifyContent: 'center',
  },
  searchInputWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    height: 52,
    borderRadius: 14,
    backgroundColor: '#f3f4f6',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.06)',
    paddingHorizontal: 12,
  },
  searchInputIcon: { marginRight: 10 },
  searchInput: { flex: 1, fontSize: 16, fontWeight: '700', color: '#111827', paddingVertical: 0 },
  searchHint: { marginTop: 14, fontSize: 12, fontWeight: '700', color: theme.colors.muted },
  searchLoadingRow: { flexDirection: 'row', alignItems: 'center', marginTop: 14 },
  searchSection: { marginTop: 14 },
  searchSectionTitle: { fontSize: 12, fontWeight: '900', color: theme.colors.muted, marginBottom: 8 },
  searchResults: {
    marginTop: 12,
    borderRadius: 14,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.06)',
    overflow: 'hidden',
  },
  searchResultRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.06)',
  },
  searchResultRowLast: { borderBottomWidth: 0 },
  searchResultLeft: { flex: 1, paddingRight: 10 },
  searchResultTitle: { fontSize: 14, fontWeight: '900', color: '#111827' },
  searchResultSub: { marginTop: 2, fontSize: 12, fontWeight: '700', color: theme.colors.muted },
  searchResultTime: { fontSize: 12, fontWeight: '900', color: theme.colors.primaryDark },

  drawerOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 70,
    elevation: 30,
  },
  drawerBackdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' },
  drawerPanel: {
    position: 'absolute',
    top: 0,
    right: 0,
    width: 340,
    backgroundColor: '#fff',
    borderTopLeftRadius: 18,
    borderBottomLeftRadius: 18,
    overflow: 'hidden',
  },
  drawerHeader: {
    paddingHorizontal: 16,
    paddingBottom: 14,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.06)',
  },
  drawerHeaderRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  drawerTitle: { fontSize: 20, fontWeight: '900', color: '#111827' },
  drawerCloseBtn: { width: 42, height: 42, borderRadius: 999, alignItems: 'center', justifyContent: 'center' },
  drawerDate: { marginTop: 4, fontSize: 13, fontWeight: '700', color: theme.colors.muted },
  drawerSection: { paddingHorizontal: 16, paddingTop: 14 },
  planBanner: {
    minHeight: 56,
    borderRadius: 16,
    paddingHorizontal: 14,
    paddingVertical: 12,
    backgroundColor: '#eff6ff',
    borderWidth: 1,
    borderColor: 'rgba(37, 99, 235, 0.18)',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  planBannerLeft: { flex: 1, paddingRight: 10 },
  planBannerTitle: { fontSize: 12, fontWeight: '900', color: theme.colors.primaryDark },
  planBannerSub: { marginTop: 4, fontSize: 13, fontWeight: '800', color: '#111827' },
  planBannerRight: { flexDirection: 'row', alignItems: 'center' },
  drawerSectionHeaderRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  drawerSectionTitle: { fontSize: 12, fontWeight: '900', color: theme.colors.muted },
  drawerSectionLink: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, backgroundColor: '#eff6ff' },
  drawerSectionLinkText: { fontSize: 12, fontWeight: '900', color: theme.colors.primaryDark },
  drawerMuted: { fontSize: 13, fontWeight: '700', color: theme.colors.muted },
  drawerLoadingRow: { flexDirection: 'row', alignItems: 'center', marginTop: 10 },
  drawerList: { marginTop: 10 },
  drawerListItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.06)',
  },
  drawerListLeft: { flex: 1, paddingRight: 10 },
  drawerListTitle: { fontSize: 14, fontWeight: '900', color: '#111827' },
  drawerListSub: { marginTop: 2, fontSize: 12, fontWeight: '700', color: theme.colors.muted },
  drawerListTime: { fontSize: 12, fontWeight: '900', color: theme.colors.primaryDark },
  drawerNotifRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(0,0,0,0.06)',
  },
  drawerPrimaryBtn: {
    marginTop: 14,
    height: 46,
    borderRadius: 14,
    backgroundColor: theme.colors.primary,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  drawerBtnIcon: { marginRight: 8 },
  drawerPrimaryBtnText: { color: '#fff', fontSize: 14, fontWeight: '900' },
  drawerDivider: { marginTop: 18, height: 1, backgroundColor: 'rgba(0,0,0,0.06)' },
  drawerRowBtn: { height: 46, borderRadius: 14, flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12 },
  drawerRowIcon: { marginRight: 10 },
  drawerRowText: { fontSize: 14, fontWeight: '900', color: '#111827' },
  webWrap: { flex: 1, backgroundColor: 'transparent' },
  // Keep the WebView itself opaque to avoid iOS swipe-back revealing a black under-layer.
  // The gradient background is rendered by the web app in app-mode CSS.
  webView: { flex: 1, backgroundColor: '#fff' },
  webViewContainer: { backgroundColor: '#fff' },
  centerBody: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 16 },
  loadingText: { marginTop: 10, color: theme.colors.muted, fontSize: 13 },
  progressTrack: { height: 3, width: '100%', backgroundColor: '#fff' },
  progressBar: { height: 3, backgroundColor: theme.colors.primary },
  successBanner: {
    marginHorizontal: 12,
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
    backgroundColor: '#fff',
  },
  bottomNavContainer: {
    borderTopWidth: 3,
    borderTopColor: theme.colors.primary,
    backgroundColor: '#fff',
  },
  bottomNavRow: {
    height: 64,
    flexDirection: 'row',
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
    backgroundColor: '#000',
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
