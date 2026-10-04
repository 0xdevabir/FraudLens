"use client";

import { useCallback, useEffect, useState } from "react";

/** Where the FraudLens API lives. Inlined at build time (NEXT_PUBLIC_*). */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");

const TOKEN_KEY = "fraudlens.token";

/** The session token lives in sessionStorage: gone when the tab closes, never in a URL. */
export function getToken(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // storage unavailable: the session simply does not survive a reload
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

function toLogin() {
  setToken(null);
  if (!window.location.pathname.startsWith("/login")) {
    const next = window.location.pathname + window.location.search;
    // Outside React, and a full page load clears what the expired session had in memory.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/login?next=${encodeURIComponent(next)}`);
  }
}

async function failure(response: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `The server answered ${response.status}`;
  let extra: Record<string, unknown> = {};
  try {
    const body = await response.json();
    if (body?.error) {
      ({ code = code, message = message, ...extra } = body.error);
    } else if (Array.isArray(body?.detail)) {
      code = "invalid_request";
      message = body.detail.map((d: { loc?: string[]; msg: string }) => `${d.loc?.at(-1) ?? ""} ${d.msg}`).join("; ");
    }
  } catch {
    // not JSON: keep the status line
  }
  return new ApiError(response.status, code, message, extra);
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** GET `path`, or POST `body` to it. Throws ApiError; a 401 ends the session. */
export async function api<T = unknown>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { ...authHeaders(), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "unreachable", `Cannot reach the FraudLens API at ${API_URL}`);
  }
  if (response.status === 401 && !path.startsWith("/v1/auth/login")) {
    toLogin();
    throw new ApiError(401, "signed_out", "Your session has ended. Please sign in again.");
  }
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

/** Save a CSV the API builds for the current filters. The token goes in a header, so this is a fetch, not a link. */
export async function download(path: string, fallbackName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(API_URL + path, { headers: authHeaders(), cache: "no-store" });
  } catch {
    throw new ApiError(0, "unreachable", `Cannot reach the FraudLens API at ${API_URL}`);
  }
  if (response.status === 401) {
    toLogin();
    throw new ApiError(401, "signed_out", "Your session has ended. Please sign in again.");
  }
  if (!response.ok) throw await failure(response);
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export interface Loaded<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch `path` and keep it fresh. While a new path loads, the previous data stays on screen. */
export function useApi<T>(path: string | null, refreshMs?: number): Loaded<T> {
  const [state, setState] = useState<{ path: string | null; data?: T; error: ApiError | null }>({
    path: null,
    error: null,
  });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    api<T>(path, undefined, controller.signal).then(
      (data) => setState({ path, data, error: null }),
      (error) => {
        if (error?.name === "AbortError") return;
        setState((old) => ({ path, data: old.path === path ? old.data : undefined, error }));
      },
    );
    return () => controller.abort();
  }, [path, tick]);

  useEffect(() => {
    if (!refreshMs) return;
    const timer = window.setInterval(reload, refreshMs);
    return () => window.clearInterval(timer);
  }, [refreshMs, reload]);

  return {
    data: path ? state.data : undefined,
    error: state.path === path ? state.error : null,
    loading: path !== null && state.path !== path,
    reload,
  };
}

/** Query string from a plain object; arrays repeat the key, empty values are dropped. */
export function qs(params: Record<string, string | number | boolean | null | undefined | string[]>): string {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((v) => out.append(key, v));
    else if (value !== null && value !== undefined && value !== "") out.set(key, String(value));
  }
  const text = out.toString();
  return text ? `?${text}` : "";
}
