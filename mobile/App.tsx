import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';

import { getAccessToken } from './src/lib/auth';
import { HomeScreen } from './src/screens/HomeScreen';
import { BookingDetailScreen } from './src/screens/BookingDetailScreen';
import { BookingsScreen } from './src/screens/BookingsScreen';
import { BusinessesScreen } from './src/screens/BusinessesScreen';
import { BillingScreen } from './src/screens/BillingScreen';
import { PortalPlaceholderScreen } from './src/screens/PortalPlaceholderScreen';
import { ProfileScreen } from './src/screens/ProfileScreen';
import { ScheduleScreen } from './src/screens/ScheduleScreen';
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
  Home: undefined;
  BookingDetail: { orgSlug: string; bookingId: number };
  Schedule: { orgSlug: string };
  Portal: { title: string };
  Bookings: { orgSlug: string };
  Billing: { orgSlug: string };
  Businesses: undefined;
  Profile: undefined;
  Services: { orgSlug: string };
  ServiceEdit: { orgSlug: string; serviceId: number };
};

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  const [initialRouteName, setInitialRouteName] = useState<keyof RootStackParamList>('Welcome');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await getAccessToken();
        if (!cancelled) setInitialRouteName(token ? 'Home' : 'Welcome');
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) return null;

  return (
    <NavigationContainer>
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
              onSignedIn={() => navigation.reset({ index: 0, routes: [{ name: 'Home' }] })}
            />
          )}
        </Stack.Screen>
        <Stack.Screen name="SignInStaff" options={{ title: 'Staff/manager sign in' }}>
          {({ navigation }) => (
            <SignInScreen
              mode="staff"
              onSignedIn={() => navigation.reset({ index: 0, routes: [{ name: 'Home' }] })}
            />
          )}
        </Stack.Screen>
        <Stack.Screen name="Home" options={{ title: 'Dashboard' }}>
          {({ navigation }) => (
            <HomeScreen
              onSignedOut={() => navigation.reset({ index: 0, routes: [{ name: 'Welcome' }] })}
              onOpenBooking={({ orgSlug, bookingId }: { orgSlug: string; bookingId: number }) =>
                navigation.navigate('BookingDetail', { orgSlug, bookingId })
              }
              onOpenSchedule={({ orgSlug }: { orgSlug: string }) =>
                navigation.navigate('Schedule', { orgSlug })
              }
              onOpenPortal={({ title }: { title: string }) => navigation.navigate('Portal', { title })}
              onOpenBookings={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Bookings', { orgSlug })}
              onOpenBilling={({ orgSlug }: { orgSlug: string }) => navigation.navigate('Billing', { orgSlug })}
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

        <Stack.Screen name="Schedule" options={{ title: 'Today' }}>
          {({ route, navigation }) => (
            <ScheduleScreen
              orgSlug={route.params.orgSlug}
              onOpenBooking={({ orgSlug, bookingId }: { orgSlug: string; bookingId: number }) =>
                navigation.navigate('BookingDetail', { orgSlug, bookingId })
              }
            />
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
            />
          )}
        </Stack.Screen>

        <Stack.Screen name="Billing" options={{ title: 'Billing' }}>
          {({ route }) => <BillingScreen orgSlug={route.params.orgSlug} />}
        </Stack.Screen>

        <Stack.Screen name="Businesses" options={{ title: 'Businesses' }}>
          {({ navigation }) => (
            <BusinessesScreen
              onSelected={({ orgSlug }: { orgSlug: string }) => {
                // Return to dashboard; dashboard will refresh on focus.
                navigation.goBack();
              }}
            />
          )}
        </Stack.Screen>

        <Stack.Screen name="Profile" options={{ title: 'Profile' }}>
          {({ navigation }) => (
            <ProfileScreen
              onSignedOut={() => navigation.reset({ index: 0, routes: [{ name: 'Welcome' }] })}
            />
          )}
        </Stack.Screen>

        <Stack.Screen name="Services" options={{ title: 'Services' }}>
          {({ route, navigation }) => (
            <ServicesScreen
              orgSlug={route.params.orgSlug}
              onOpenEdit={({ orgSlug, serviceId }: { orgSlug: string; serviceId: number }) =>
                navigation.navigate('ServiceEdit', { orgSlug, serviceId })
              }
            />
          )}
        </Stack.Screen>

        <Stack.Screen name="ServiceEdit" options={{ title: 'Edit service' }}>
          {({ route, navigation }) => (
            <ServiceEditScreen
              orgSlug={route.params.orgSlug}
              serviceId={route.params.serviceId}
              onSaved={() => navigation.goBack()}
            />
          )}
        </Stack.Screen>
      </Stack.Navigator>
      <StatusBar style="auto" />
    </NavigationContainer>
  );
}
