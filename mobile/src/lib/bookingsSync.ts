type BookingsChangedPayload = {
  orgSlug?: string;
};

type Listener = (payload: BookingsChangedPayload) => void;

const listeners = new Set<Listener>();

export function onBookingsChanged(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function emitBookingsChanged(orgSlug?: string) {
  const payload: BookingsChangedPayload = { orgSlug };
  for (const listener of Array.from(listeners)) {
    try {
      listener(payload);
    } catch {
      // Ignore listener failures to avoid breaking emitters.
    }
  }
}
