import { createNavigationContainerRef, NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import * as Notifications from 'expo-notifications';

import { getAccessToken } from './src/lib/auth';
import { apiGetOrgs } from './src/lib/api';
import { normalizeOrgRole } from './src/lib/permissions';
import { WebAppScreen, webPathFromPushData } from './src/screens/WebAppScreen';
import { HomeScreen } from './src/screens/HomeScreen';
import { BookingDetailScreen } from './src/screens/BookingDetailScreen';
import { BookingAuditScreen } from './src/screens/BookingAuditScreen';
import { BookingsScreen } from './src/screens/BookingsScreen';
import { BusinessesScreen } from './src/screens/BusinessesScreen';
import { BillingScreen } from './src/screens/BillingScreen';
import { CalendarScreen } from './src/screens/CalendarScreen';
import { PricingScreen } from './src/screens/PricingScreen';
import { PortalPlaceholderScreen } from './src/screens/PortalPlaceholderScreen';
import { ProfileScreen } from './src/screens/ProfileScreen';
import { ResourcesScreen } from './src/screens/ResourcesScreen';
import { ScheduleScreen } from './src/screens/ScheduleScreen';
import { StaffScreen } from './src/screens/StaffScreen';
import { ServiceEditScreen } from './src/screens/ServiceEditScreen.tsx';
import { ServicesScreen } from './src/screens/ServicesScreen.tsx';
import { SignInChoiceScreen } from './src/screens/SignInChoiceScreen';
import { SignInScreen } from './src/screens/SignInScreen';
import { WelcomeScreen } from './src/screens/WelcomeScreen';

type RootStackParamList = {
  Welcome: undefined;
  SignInChoice: undefined;
  SignInOwner: undefined;
  SignInStaff: undefined;
  WebApp: { initialPath?: string } | undefined;
  Home: undefined;
  BookingDetail: { orgSlug: string; bookingId: number };
  BookingAudit: { orgSlug: string };
  Calendar: { orgSlug: string };
  Schedule: { orgSlug: string };
  Portal: { title: string };
  Bookings: { orgSlug: string };
  Billing: { orgSlug: string };
  Pricing: { orgSlug: string };
  Resources: { orgSlug: string };
  Staff: { orgSlug: string };
  Businesses: undefined;
  Profile: { forceName?: boolean } | undefined;
  Services: { orgSlug: string };
  ServiceEdit: { orgSlug: string; serviceId: number };
};

const Stack = createNativeStackNavigator<RootStackParamList>();
const navigationRef = createNavigationContainerRef<RootStackParamList>();

function StaffRestrictedScreen(props: {
  orgSlug?: string;
  title?: string;
  onGoHome: () => void;
  onGoBookings?: (orgSlug: string) => void;
}) {
  return (
    <View style={{ flex: 1, padding: 16, justifyContent: 'center' }}>
      <Text style={{ fontSize: 18, fontWeight: '600', marginBottom: 8 }}>Not available</Text>
      <Text style={{ color: '#444', marginBottom: 14 }}>
        {props.title ? `${props.title} is not available for your account.` : 'This section is not available for your account.'}
      </Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
        {props.orgSlug && props.onGoBookings ? (
          <Pressable
            onPress={() => props.onGoBookings && props.orgSlug && props.onGoBookings(props.orgSlug)}
            style={{ backgroundColor: '#2563eb', paddingVertical: 10, paddingHorizontal: 12, borderRadius: 10, marginRight: 10, marginBottom: 10 }}
          >
            <Text style={{ color: 'white', fontWeight: '600' }}>Go to Bookings</Text>
          </Pressable>
        ) : null}
        <Pressable
          onPress={props.onGoHome}
          style={{ backgroundColor: '#111827', paddingVertical: 10, paddingHorizontal: 12, borderRadius: 10, marginBottom: 10 }}
        >
          <Text style={{ color: 'white', fontWeight: '600' }}>Back to Dashboard</Text>
        </Pressable>
      </View>
    </View>
  );
}

function RequireNonStaffOrgRole(props: {
  orgSlug: string;
  title?: string;
  navigation: any;
  children: React.ReactNode;
}) {
  const { orgSlug, navigation } = props;
  const [loading, setLoading] = useState(true);
  const [role, setRole] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetOrgs();
        const found = (resp.orgs ?? []).find((o) => o.slug === orgSlug);
        const r = found?.role ? normalizeOrgRole(found.role) : '';
        if (!cancelled) setRole(r);
      } catch {
        // If we cannot verify, allow screen (backend should still enforce).
        if (!cancelled) setRole('');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator />
      </View>
    );
  }

  if (role === 'staff') {
    return (
      <StaffRestrictedScreen
        orgSlug={orgSlug}
        title={props.title}
        onGoBookings={(slug) => navigation.reset({ index: 0, routes: [{ name: 'Bookings', params: { orgSlug: slug } }] })}
        onGoHome={() => navigation.reset({ index: 0, routes: [{ name: 'Home' }] })}
      />
    );
  }

  return <>{props.children}</>;
}

function RequireOwnerOrgRole(props: {
  orgSlug: string;
  title?: string;
  navigation: any;
  children: React.ReactNode;
}) {
  const { orgSlug, navigation } = props;
  const [loading, setLoading] = useState(true);
  const [role, setRole] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetOrgs();
        const found = (resp.orgs ?? []).find((o) => o.slug === orgSlug);
        const r = found?.role ? normalizeOrgRole(found.role) : '';
        if (!cancelled) setRole(r);
      } catch {
        // If we cannot verify, allow screen (backend should still enforce).
        if (!cancelled) setRole('');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator />
      </View>
    );
  }

  if (role && role !== 'owner') {
    return (
      <StaffRestrictedScreen
        orgSlug={orgSlug}
        title={props.title}
        onGoBookings={(slug) =>
          navigation.reset({ index: 0, routes: [{ name: 'Bookings', params: { orgSlug: slug } }] })
        }
        onGoHome={() => navigation.reset({ index: 0, routes: [{ name: 'Home' }] })}
      />
    );
  }

  return <>{props.children}</>;
}

function RequireOwnerOrAdminOrgRole(props: {
  orgSlug: string;
  title?: string;
  navigation: any;
  children: React.ReactNode;
}) {
  const { orgSlug, navigation } = props;
  const [loading, setLoading] = useState(true);
  const [role, setRole] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetOrgs();
        const found = (resp.orgs ?? []).find((o) => o.slug === orgSlug);
        const r = found?.role ? normalizeOrgRole(found.role) : '';
        if (!cancelled) setRole(r);
      } catch {
        if (!cancelled) setRole('');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator />
      </View>
    );
  }

  if (role && role !== 'owner' && role !== 'admin') {
    return (
      <StaffRestrictedScreen
        orgSlug={orgSlug}
        title={props.title}
        onGoBookings={(slug) =>
          navigation.reset({ index: 0, routes: [{ name: 'Bookings', params: { orgSlug: slug } }] })
        }
        onGoHome={() => navigation.reset({ index: 0, routes: [{ name: 'Home' }] })}
      />
    );
  }

  return <>{props.children}</>;
}

function RequireOwnerAnywhere(props: {
  title?: string;
  navigation: any;
  children: React.ReactNode;
}) {
  const { navigation } = props;
  const [loading, setLoading] = useState(true);
  const [allowed, setAllowed] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiGetOrgs();
        const orgs = resp.orgs ?? [];
        const ok = orgs.some((o) => {
          const r = normalizeOrgRole(o.role);
          return r === 'owner' || r === 'admin';
        });
        if (!cancelled) setAllowed(ok);
      } catch {
        // If we cannot verify, allow (backend should still enforce).
        if (!cancelled) setAllowed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator />
      </View>
    );
  }

  if (!allowed) {
    return (
      <StaffRestrictedScreen
        title={props.title ?? 'Businesses'}
        onGoHome={() => navigation.reset({ index: 0, routes: [{ name: 'Home' }] })}
      />
    );
  }

  return <>{props.children}</>;
}

export default function App() {
  const [initialRouteName, setInitialRouteName] = useState<keyof RootStackParamList>('Welcome');
  const [ready, setReady] = useState(false);
  const [pendingNav, setPendingNav] = useState<
    | { name: 'WebApp'; params?: { initialPath?: string } }
    | null
  >(null);

  function handleNotificationOpen(response: Notifications.NotificationResponse) {
    const data = (response?.notification?.request?.content?.data ?? {}) as any;
    const orgSlug = typeof data.orgSlug === 'string' ? data.orgSlug : null;
    const open = typeof data.open === 'string' ? data.open : null;
    const kind = typeof data.kind === 'string' ? data.kind : null;

    // Some notifications (e.g. cancellations/reassigned-away) should open a list, not a deleted/forbidden detail.
    if (orgSlug && open === 'Bookings') {
      const initialPath = webPathFromPushData({ orgSlug, open: 'Bookings' });
      const target = { name: 'WebApp' as const, params: { initialPath } };
      if (navigationRef.isReady()) navigationRef.navigate(target.name, target.params as any);
      else setPendingNav(target);
      return;
    }

    const bookingIdRaw = data.bookingId;
    const bookingId = typeof bookingIdRaw === 'number' ? bookingIdRaw : Number(bookingIdRaw);
    if (!orgSlug || !Number.isFinite(bookingId)) return;

    const initialPath = webPathFromPushData({ orgSlug, bookingId });
    const target = { name: 'WebApp' as const, params: { initialPath } };
    if (navigationRef.isReady()) navigationRef.navigate(target.name, target.params as any);
    else setPendingNav(target);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getAccessToken();
        if (!cancelled) setInitialRouteName(token ? 'WebApp' : 'Welcome');
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    // Handle taps while app is backgrounded, and on cold start.
    let subscription: Notifications.Subscription | null = null;
    let cancelled = false;

    (async () => {
      try {
        const last = await Notifications.getLastNotificationResponseAsync();
        if (!cancelled && last) handleNotificationOpen(last);
      } catch {
        // ignore
      }
    })();

    try {
      subscription = Notifications.addNotificationResponseReceivedListener((response) => {
        handleNotificationOpen(response);
      });
    } catch {
      subscription = null;
    }

    return () => {
      cancelled = true;
      if (subscription) subscription.remove();
    };
  }, []);

  if (!ready) return null;

  return (
    <NavigationContainer
      ref={navigationRef}
      onReady={() => {
        if (pendingNav) {
          navigationRef.navigate(pendingNav.name, pendingNav.params as any);
          setPendingNav(null);
        }
      }}
    >
      <Stack.Navigator initialRouteName={initialRouteName}>
        <Stack.Screen name="Welcome" options={{ headerShown: false }}>
          {({ navigation }) => (
            <WelcomeScreen onPressSignIn={() => navigation.navigate('SignInChoice')} />
          )}
        </Stack.Screen>
        <Stack.Screen name="SignInChoice" options={{ title: 'Sign in' }}>
          {({ navigation }) => (
            <SignInChoiceScreen
              onSelectOwner={() => navigation.navigate('SignInOwner')}
              onSelectStaff={() => navigation.navigate('SignInStaff')}
            />
          )}
        </Stack.Screen>
        <Stack.Screen name="SignInOwner" options={{ title: 'Business owner sign in' }}>
          {({ navigation }) => (
            <SignInScreen
              mode="owner"
              onSignedIn={() =>
                navigation.reset({ index: 0, routes: [{ name: 'WebApp', params: { initialPath: '/post-login/' } }] })
              }
            />
          )}
        </Stack.Screen>
        <Stack.Screen name="SignInStaff" options={{ title: 'Staff/manager sign in' }}>
          {({ navigation }) => (
            <SignInScreen
              mode="staff"
              onSignedIn={() =>
                navigation.reset({ index: 0, routes: [{ name: 'WebApp', params: { initialPath: '/post-login/' } }] })
              }
            />
          )}
        </Stack.Screen>

        <Stack.Screen name="WebApp" options={{ headerShown: false }}>
          {({ navigation, route }) => (
            <WebAppScreen
              initialPath={route.params?.initialPath}
              onSignedOut={() => navigation.reset({ index: 0, routes: [{ name: 'Welcome' }] })}
            />
          )}
        </Stack.Screen>
        <Stack.Screen name="Home" options={{ title: 'Dashboard' }}>
          {({ navigation }) => (
            <HomeScreen
              onSignedOut={() => navigation.reset({ index: 0, routes: [{ name: 'Welcome' }] })}
              onForceProfileCompletion={() =>
                navigation.reset({ index: 0, routes: [{ name: 'Profile', params: { forceName: true } }] })
              }
              onOpenBooking={({ orgSlug, bookingId }: { orgSlug: string; bookingId: number }) =>
                navigation.navigate('BookingDetail', { orgSlug, bookingId })
              }
              onOpenCalendar={({ orgSlug }: { orgSlug: string }) =>
                navigation.navigate('Calendar', { orgSlug })
              }
              onOpenSchedule={({ orgSlug }: { orgSlug: string }) =>
                navigation.navigate('Schedule', { orgSlug })
              }
              onOpenPortal={({ title }: { title: string }) => navigation.navigate('Portal', { title })}
              onOpenBookings={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Bookings', { orgSlug })}
              onOpenBilling={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Billing', { orgSlug })}
              onOpenPricing={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Pricing', { orgSlug })}
              onOpenResources={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Resources', { orgSlug })}
              onOpenStaff={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Staff', { orgSlug })}
              onOpenBusinesses={() => navigation.navigate('Businesses')}
              onOpenProfile={() => navigation.navigate('Profile')}
              onOpenServices={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Services', { orgSlug })}
            />
          )}
        </Stack.Screen>

        <Stack.Screen
          name="BookingDetail"
          options={{ title: 'Booking' }}
        >
          {({ route }) => (
            <BookingDetailScreen orgSlug={route.params.orgSlug} bookingId={route.params.bookingId} />
          )}
        </Stack.Screen>

        <Stack.Screen name="BookingAudit" options={{ title: 'Audit' }}>
          {({ route }) => <BookingAuditScreen orgSlug={route.params.orgSlug} />}
        </Stack.Screen>

        <Stack.Screen name="Calendar" options={{ title: 'Calendar' }}>
          {({ route, navigation }) => (
            <RequireNonStaffOrgRole orgSlug={route.params.orgSlug} title="Calendar" navigation={navigation}>
              <CalendarScreen
                orgSlug={route.params.orgSlug}
                onOpenBooking={({ orgSlug, bookingId }: { orgSlug: string; bookingId: number }) =>
                  navigation.navigate('BookingDetail', { orgSlug, bookingId })
                }
              />
            </RequireNonStaffOrgRole>
          )}
        </Stack.Screen>

        <Stack.Screen name="Schedule" options={{ title: 'Today' }}>
          {({ route, navigation }) => (
            <RequireNonStaffOrgRole orgSlug={route.params.orgSlug} title="Schedule" navigation={navigation}>
              <ScheduleScreen
                orgSlug={route.params.orgSlug}
                onOpenBooking={({ orgSlug, bookingId }: { orgSlug: string; bookingId: number }) =>
                  navigation.navigate('BookingDetail', { orgSlug, bookingId })
                }
              />
            </RequireNonStaffOrgRole>
          )}
        </Stack.Screen>

        <Stack.Screen name="Portal" options={{ title: 'Portal' }}>
          {({ route }) => <PortalPlaceholderScreen title={route.params.title} />}
        </Stack.Screen>

        <Stack.Screen name="Bookings" options={{ title: 'Bookings' }}>
          {({ route, navigation }) => (
            <BookingsScreen
              orgSlug={route.params.orgSlug}
              onOpenBooking={({ orgSlug, bookingId }: { orgSlug: string; bookingId: number }) =>
                navigation.navigate('BookingDetail', { orgSlug, bookingId })
              }
              onOpenAudit={({ orgSlug }: { orgSlug: string }) => navigation.navigate('BookingAudit', { orgSlug })}
              setHeaderTitle={(title: string) => navigation.setOptions({ title })}
            />
          )}
        </Stack.Screen>

        <Stack.Screen name="Billing" options={{ title: 'Billing' }}>
          {({ route, navigation }) => (
            <RequireOwnerOrgRole orgSlug={route.params.orgSlug} title="Billing" navigation={navigation}>
              <BillingScreen orgSlug={route.params.orgSlug} />
            </RequireOwnerOrgRole>
          )}
        </Stack.Screen>

        <Stack.Screen name="Pricing" options={{ title: 'Pricing' }}>
          {({ route, navigation }) => (
            <RequireOwnerOrgRole orgSlug={route.params.orgSlug} title="Pricing" navigation={navigation}>
              <PricingScreen orgSlug={route.params.orgSlug} />
            </RequireOwnerOrgRole>
          )}
        </Stack.Screen>

        <Stack.Screen name="Resources" options={{ title: 'Resources' }}>
          {({ route, navigation }) => (
            <RequireNonStaffOrgRole orgSlug={route.params.orgSlug} title="Resources" navigation={navigation}>
              <ResourcesScreen
                orgSlug={route.params.orgSlug}
                onOpenPricing={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Pricing', { orgSlug })}
              />
            </RequireNonStaffOrgRole>
          )}
        </Stack.Screen>

        <Stack.Screen name="Staff" options={{ title: 'Staff' }}>
          {({ route, navigation }) => (
            <RequireOwnerOrAdminOrgRole orgSlug={route.params.orgSlug} title="Staff" navigation={navigation}>
              <StaffScreen
                orgSlug={route.params.orgSlug}
                onOpenPricing={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Pricing', { orgSlug })}
              />
            </RequireOwnerOrAdminOrgRole>
          )}
        </Stack.Screen>

        <Stack.Screen name="Businesses" options={{ title: 'Businesses' }}>
          {({ navigation }) => (
            <RequireOwnerAnywhere title="Businesses" navigation={navigation}>
              <BusinessesScreen
                onSelected={({ orgSlug }: { orgSlug: string }) => {
                  // Return to dashboard; dashboard will refresh on focus.
                  navigation.goBack();
                }}
              />
            </RequireOwnerAnywhere>
          )}
        </Stack.Screen>

        <Stack.Screen name="Profile" options={{ title: 'Profile' }}>
          {({ navigation, route }) => (
            <ProfileScreen
              onSignedOut={() => navigation.reset({ index: 0, routes: [{ name: 'Welcome' }] })}
              forceNameCompletion={!!route.params?.forceName}
              onRequiredProfileCompleted={() =>
                navigation.reset({ index: 0, routes: [{ name: 'Home' }] })
              }
              onOpenBusinesses={() => navigation.navigate('Businesses')}
              onOpenBilling={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Billing', { orgSlug })}
              onOpenPricing={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Pricing', { orgSlug })}
            />
          )}
        </Stack.Screen>

        <Stack.Screen name="Services" options={{ title: 'Services' }}>
          {({ route, navigation }) => (
            <RequireNonStaffOrgRole orgSlug={route.params.orgSlug} title="Services" navigation={navigation}>
              <ServicesScreen
                orgSlug={route.params.orgSlug}
                onOpenEdit={({ orgSlug, serviceId }: { orgSlug: string; serviceId: number }) =>
                  navigation.navigate('ServiceEdit', { orgSlug, serviceId })
                }
              />
            </RequireNonStaffOrgRole>
          )}
        </Stack.Screen>

        <Stack.Screen name="ServiceEdit" options={{ title: 'Edit service' }}>
          {({ route, navigation }) => (
            <RequireNonStaffOrgRole orgSlug={route.params.orgSlug} title="Services" navigation={navigation}>
              <ServiceEditScreen
                orgSlug={route.params.orgSlug}
                serviceId={route.params.serviceId}
                onSaved={() => navigation.goBack()}
              />
            </RequireNonStaffOrgRole>
          )}
        </Stack.Screen>
      </Stack.Navigator>
      <StatusBar style="auto" />
    </NavigationContainer>
  );
}
