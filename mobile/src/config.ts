import Constants from 'expo-constants';

function getExtraApiBaseUrl(): string | undefined {
  // expo-constants exposes config slightly differently depending on runtime.
  const extra = Constants.expoConfig?.extra as { apiBaseUrl?: string } | undefined;
  return extra?.apiBaseUrl;
}

export const API_BASE_URL: string =
  (process.env.EXPO_PUBLIC_API_BASE_URL as string | undefined) ??
  getExtraApiBaseUrl() ??
  'https://circlecal.app';
