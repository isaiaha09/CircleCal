import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';

import { getAccessToken } from './src/lib/auth';
import { HomeScreen } from './src/screens/HomeScreen';
import { SignInScreen } from './src/screens/SignInScreen';
import { WelcomeScreen } from './src/screens/WelcomeScreen';

type RootStackParamList = {
  Welcome: undefined;
  SignIn: undefined;
  Home: undefined;
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
            <WelcomeScreen onPressSignIn={() => navigation.navigate('SignIn')} />
          )}
        </Stack.Screen>
        <Stack.Screen name="SignIn" options={{ title: 'Sign in' }}>
          {({ navigation }) => (
            <SignInScreen onSignedIn={() => navigation.reset({ index: 0, routes: [{ name: 'Home' }] })} />
          )}
        </Stack.Screen>
        <Stack.Screen name="Home" options={{ title: 'CircleCal' }}>
          {({ navigation }) => (
            <HomeScreen onSignedOut={() => navigation.reset({ index: 0, routes: [{ name: 'Welcome' }] })} />
          )}
        </Stack.Screen>
      </Stack.Navigator>
      <StatusBar style="auto" />
    </NavigationContainer>
  );
}
