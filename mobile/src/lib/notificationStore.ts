import * as SecureStore from 'expo-secure-store';

const INBOX_KEY = 'cc_notification_inbox_v1';
const MAX_ITEMS = 50;

type InboxListener = () => void;
const listeners = new Set<InboxListener>();

function emitChange(): void {
  try {
    listeners.forEach((fn) => {
      try {
        fn();
      } catch {
        // ignore
      }
    });
  } catch {
    // ignore
  }
}

export function subscribeInboxChanges(listener: InboxListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export type InboxNotification = {
  id: string;
  title: string;
  body: string;
  receivedAt: string; // ISO string
  data?: Record<string, any>;
  read?: boolean;
};

function safeParse(json: string | null): InboxNotification[] {
  if (!json) return [];
  try {
    const v = JSON.parse(json);
    if (!Array.isArray(v)) return [];
    return v
      .filter(Boolean)
      .map((x: any) => {
        const id = typeof x?.id === 'string' ? x.id : '';
        const title = typeof x?.title === 'string' ? x.title : '';
        const body = typeof x?.body === 'string' ? x.body : '';
        const receivedAt = typeof x?.receivedAt === 'string' ? x.receivedAt : '';
        const data = x?.data && typeof x.data === 'object' ? x.data : undefined;
        const read = typeof x?.read === 'boolean' ? x.read : undefined;
        if (!id || !receivedAt) return null;
        return { id, title, body, receivedAt, data, read } satisfies InboxNotification;
      })
      .filter(Boolean) as InboxNotification[];
  } catch {
    return [];
  }
}

async function write(items: InboxNotification[]): Promise<void> {
  try {
    await SecureStore.setItemAsync(INBOX_KEY, JSON.stringify(items.slice(0, MAX_ITEMS)));
  } catch {
    // ignore (best-effort)
  }

  emitChange();
}

export async function getInboxNotifications(): Promise<InboxNotification[]> {
  try {
    const raw = await SecureStore.getItemAsync(INBOX_KEY);
    const parsed = safeParse(raw);
    // newest first
    return parsed.sort((a, b) => (a.receivedAt < b.receivedAt ? 1 : a.receivedAt > b.receivedAt ? -1 : 0));
  } catch {
    return [];
  }
}

export async function addInboxNotification(item: InboxNotification): Promise<void> {
  if (!item?.id || !item?.receivedAt) return;
  const existing = await getInboxNotifications();
  const withoutDup = existing.filter((x) => x.id !== item.id);
  const next = [{ ...item }, ...withoutDup].slice(0, MAX_ITEMS);
  await write(next);
}

export async function markInboxRead(id: string): Promise<void> {
  if (!id) return;
  const existing = await getInboxNotifications();
  const next = existing.map((x) => (x.id === id ? { ...x, read: true } : x));
  await write(next);
}

export async function markAllInboxRead(): Promise<void> {
  const existing = await getInboxNotifications();
  const next = existing.map((x) => ({ ...x, read: true }));
  await write(next);
}

export async function getInboxUnreadCount(): Promise<number> {
  const items = await getInboxNotifications();
  return items.filter((x) => !x.read).length;
}

export async function clearInboxNotifications(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(INBOX_KEY);
  } catch {
    // ignore
  }

  emitChange();
}
