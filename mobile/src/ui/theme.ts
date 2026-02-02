export const theme = {
  colors: {
    bg: '#f8fafc', // slate-50
    card: '#ffffff',
    text: '#0f172a', // slate-900
    muted: '#475569', // slate-600
    border: '#e2e8f0', // slate-200
    primary: '#3b82f6', // blue-500 (matches web theme-color)
    primaryDark: '#2563eb', // blue-600
    danger: '#ef4444', // red-500
  },
  radius: {
    md: 12,
    lg: 16,
  },
  shadow: {
    card: {
      shadowColor: '#000',
      shadowOpacity: 0.08,
      shadowRadius: 14,
      shadowOffset: { width: 0, height: 8 },
      elevation: 3,
    },
  },
} as const;
