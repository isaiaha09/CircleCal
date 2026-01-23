import { API_BASE_URL } from '../config';
import { getAccessToken } from './auth';

export type ApiError = {
  status: number;
  message: string;
  body?: unknown;
};

async function readResponseBody(resp: Response): Promise<unknown> {
  // Read text first so we can try JSON parse, but still keep HTML/text for debugging.
  const text = await resp.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return { text };
  }
}

async function buildHeaders(extra?: HeadersInit): Promise<HeadersInit> {
  const token = await getAccessToken();
  return {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : null),
    ...(extra ?? null),
  };
}

export async function apiGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'GET',
    headers: await buildHeaders(),
  });

  if (!resp.ok) {
    const body = await readResponseBody(resp);
    const err: ApiError = {
      status: resp.status,
      message: `Request failed: ${resp.status}`,
      body,
    };
    throw err;
  }

  return (await resp.json()) as T;
}

export async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: await buildHeaders(),
    body: JSON.stringify(payload ?? {}),
  });

  if (!resp.ok) {
    const body = await readResponseBody(resp);
    const err: ApiError = {
      status: resp.status,
      message: `Request failed: ${resp.status}`,
      body,
    };
    throw err;
  }

  return (await resp.json()) as T;
}
