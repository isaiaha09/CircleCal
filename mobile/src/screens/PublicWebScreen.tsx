import React, { useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Linking, Platform, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { WebView } from 'react-native-webview';
import { LinearGradient } from 'expo-linear-gradient';

import { API_BASE_URL } from '../config';
import { theme } from '../ui/theme';

type Props = {
  initialPath: string;
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
    return '';
  }
}

function withAppModeParam(pathOrUrl: string): string {
  if (!pathOrUrl) return '/?cc_app=1';

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

function isRestrictedBillingUrl(url: string): boolean {
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
  if (p.includes('/pricing/')) return true;
  if (p.startsWith('/billing/') || p.includes('/billing/')) return true;
  if (p.includes('/embedded_checkout') || p.includes('/embedded-checkout')) return true;
  return false;
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

  // Allow Cloudflare Turnstile/challenges inside the WebView.
  if (u.hostname === 'challenges.cloudflare.com') return false;

  const allowedHost = getAllowedHost();
  if (allowedHost && u.host === allowedHost) return false;

  return true;
}

export function PublicWebScreen({ initialPath }: Props) {
  const webviewRef = useRef<WebView>(null);
  const [loading, setLoading] = useState(true);

  const appUaName = Platform.OS === 'ios' ? 'CircleCalApp-iOS' : 'CircleCalApp-Android';
  const iosOpaqueProps =
    Platform.OS === 'ios'
      ? ({
          underPageBackgroundColor: '#ffffff',
        } as any)
      : {};

  const uri = useMemo(() => {
    const p = withAppModeParam(initialPath && initialPath.trim() ? initialPath.trim() : '/');
    return safeJoinUrl(API_BASE_URL, p);
  }, [initialPath]);

  const appModeCookieJs = useMemo(
    () => `
(function(){
  try {
    var secure = '';
    try { if (window.location && String(window.location.protocol) === 'https:') secure = '; Secure'; } catch(e) {}
    document.cookie = 'cc_app=1; Path=/; SameSite=Lax' + secure;
  } catch(e) {}
  return true;
})();
`,
    []
  );

  return (
    <LinearGradient
      colors={['#dbeafe', '#e0e7ff', '#ffffff']}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.gradient}
    >
      <SafeAreaView style={styles.safeTransparent}>
        <View style={styles.body}>
          <WebView
            ref={webviewRef}
            source={{ uri }}
            applicationNameForUserAgent={appUaName}
            javaScriptEnabled
            domStorageEnabled
            sharedCookiesEnabled
            thirdPartyCookiesEnabled={Platform.OS === 'android'}
            androidLayerType={Platform.OS === 'android' ? 'software' : undefined}
            {...iosOpaqueProps}
            allowsBackForwardNavigationGestures
            startInLoadingState
            injectedJavaScriptBeforeContentLoaded={appModeCookieJs}
            containerStyle={styles.webViewContainer}
            style={styles.webView}
            renderLoading={() => (
              <View style={styles.loading}>
                <ActivityIndicator />
              </View>
            )}
            onLoadEnd={() => setLoading(false)}
            onShouldStartLoadWithRequest={(req) => {
              const nextUrl = req.url;

              if (isRestrictedBillingUrl(nextUrl)) {
                const platformLabel = Platform.OS === 'ios' ? 'iOS' : 'Android';
                Alert.alert(
                  'Billing changes unavailable',
                  `Billing changes aren’t available in the ${platformLabel} app.`,
                  [{ text: 'OK', style: 'cancel' }]
                );
                return false;
              }

              if (shouldOpenExternally(nextUrl)) {
                Linking.openURL(nextUrl).catch(() => undefined);
                return false;
              }

              return true;
            }}
          />

          {loading ? <View style={styles.loadingOverlay} /> : null}
        </View>
      </SafeAreaView>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  gradient: { flex: 1 },
  safeTransparent: { flex: 1, backgroundColor: 'transparent' },
  body: { flex: 1, backgroundColor: 'transparent' },
  webView: { flex: 1, backgroundColor: '#fff' },
  webViewContainer: { backgroundColor: '#fff' },
  loading: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  loadingOverlay: { position: 'absolute', left: 0, top: 0, right: 0, bottom: 0 },
});
