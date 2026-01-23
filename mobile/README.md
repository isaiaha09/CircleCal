# CircleCal Mobile (Expo + TypeScript)

This folder contains the React Native mobile app for CircleCal.

## Prereqs

- Node.js (already installed)
- A real iPhone (for iOS testing from Windows) and/or an Android device/emulator
- Expo Go app installed on your phone (for quick dev testing)

## Run locally (Expo Go)

From the repo root:

- `cd mobile`
- `npm run start`

Then:

- On iPhone: open **Expo Go** → scan the QR code.
- If you get "Request timed out" in Expo Go, your Wi‑Fi/firewall is usually blocking LAN access.
  Use tunnel mode instead (works even when phone is on cellular):
  - `npm run start:tunnel`

## Troubleshooting

### Expo Go shows “Request timed out” after scanning QR

- Use tunnel mode: `npm run start:tunnel`
- Disable VPN on phone
- Ensure Expo Go has Local Network permission (iOS Settings → Expo Go)

### “iOS Bundling failed” / missing file under npm-cache/_npx

If you started Expo via `npx expo ...`, the temporary `npx` cache can get corrupted on Windows.

Fix:

- Stop the dev server (Ctrl+C)
- Use the project scripts (they use the local `expo` dependency instead of `npx`):
  - `npm run start:tunnel:clear`

Optional cache reset (PowerShell):

- `Remove-Item -Recurse -Force "$env:LOCALAPPDATA\npm-cache\_npx\*"`

## Configure API base URL

Default API base is `https://circlecal.app` (from `app.json`).

You can override it during development by creating a `.env` file (copy from `.env.example`):

- `EXPO_PUBLIC_API_BASE_URL=https://circlecal.app`

The app reads the value from:

- `process.env.EXPO_PUBLIC_API_BASE_URL` or
- `app.json` → `expo.extra.apiBaseUrl`

## EAS Build (cloud builds; no Mac needed)

Install EAS CLI:

- `npm i -g eas-cli`

Login:

- `eas login`

Initialize (first time only):

- `cd mobile`
- `eas init`

Build:

- Development build (good for internal testing when you need native modules):
  - `eas build -p ios --profile development`
  - `eas build -p android --profile development`

- Production build (App Store / Play Store):
  - `eas build -p ios --profile production`
  - `eas build -p android --profile production`

## TestFlight (iOS beta testing)

TestFlight is Apple’s distribution channel for installing iOS builds on real devices.

Typical flow:

1) Build iOS with EAS:
   - `eas build -p ios --profile production`

2) Submit to App Store Connect:
   - `eas submit -p ios --profile production`

3) In App Store Connect:
   - Create/select the app
   - Add testers
   - They install via **TestFlight**

Notes:

- You need an Apple Developer account.
- The first App Store Connect setup is a one-time step.

## What’s implemented so far

- Basic navigation (Welcome → Sign In → Home)
- Centralized API base URL config: `src/config.ts`
- Secure token storage scaffolding using `expo-secure-store`: `src/lib/auth.ts`
- Minimal fetch client scaffolding: `src/lib/api.ts`

Next step is adding real auth + JSON APIs in the Django backend (JWT + DRF) and then wiring the Sign In screen to it.
