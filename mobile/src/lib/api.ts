import { API_BASE_URL } from '../config';
import { getAccessToken } from './auth';

export type ApiError = {
  status: number;
  message: string;
  body?: unknown;
};

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
    let body: unknown = undefined;
    try {
      body = await resp.json();
    } catch {
      // ignore
    }
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
    let body: unknown = undefined;
    try {
      body = await resp.json();
    } catch {
      // ignore
    }
    const err: ApiError = {
      status: resp.status,
      message: `Request failed: ${resp.status}`,
      body,
    };
    throw err;
  }

  return (await resp.json()) as T;
}
