import { createNavigationContainerRef } from '@react-navigation/native';

export type RootStackParamList = {
  Welcome: undefined;
  SignInChoice: undefined;
  SignInOwner: undefined;
  SignInStaff: undefined;
  WebApp: { initialPath?: string; skipSso?: boolean } | undefined;
  Home: undefined;
  Notifications: undefined;
  BookingDetail: { orgSlug: string; bookingId: number };
  BookingAudit: { orgSlug: string };
  Calendar: { orgSlug: string };
  Schedule: { orgSlug: string };
  Portal: { title: string };
  Bookings: { orgSlug: string };
  Billing: { orgSlug: string };
  Plans: { orgSlug: string };
  Resources: { orgSlug: string };
  Staff: { orgSlug: string };
  Businesses: undefined;
  Profile: { forceName?: boolean } | undefined;
  Services: { orgSlug: string };
  ServiceEdit: { orgSlug: string; serviceId: number };
};

export const navigationRef = createNavigationContainerRef<RootStackParamList>();

export function openWebAppPath(initialPath: string): void {
  try {
    if (navigationRef.isReady()) {
      navigationRef.navigate('WebApp', { initialPath } as any);
    }
  } catch {
    // ignore
  }
}
