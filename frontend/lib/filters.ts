"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Filters as the query string carries them: every key holds a list, a single value is a list of one. */
export type Filters = Record<string, string[]>;

export function one(filters: Filters, key: string): string {
  return filters[key]?.[0] ?? "";
}

export function toggled(filters: Filters, key: string, value: string): Filters {
  const list = filters[key] ?? [];
  return { ...filters, [key]: list.includes(value) ? list.filter((v) => v !== value) : [...list, value] };
}

export function fromSearch(search: string, allowed: string[]): Filters {
  const params = new URLSearchParams(search);
  const out: Filters = {};
  for (const key of allowed) {
    const values = params.getAll(key).filter(Boolean);
    if (values.length) out[key] = values;
  }
  return out;
}

export function toSearch(filters: Filters): string {
  const params = new URLSearchParams();
  for (const [key, values] of Object.entries(filters)) values.filter(Boolean).forEach((v) => params.append(key, v));
  const text = params.toString();
  return text ? `?${text}` : "";
}

/**
 * Filters that live in the address bar, so a filtered view can be copied and shared.
 * Read once after the first render (the server cannot see the address), written on every change.
 * `fallback` is what applies when the address carries none of the filters.
 */
export function useUrlFilters(allowed: string[], fallback: Filters = {}): [Filters, (next: Filters) => void, boolean] {
  const [filters, setFilters] = useState<Filters>(fallback);
  const [ready, setReady] = useState(false);
  const keys = useRef(allowed);

  useEffect(() => {
    const found = fromSearch(window.location.search, keys.current);
    // Deferred: the address is only readable in the browser, after hydration.
    queueMicrotask(() => {
      if (Object.keys(found).length) setFilters(found);
      setReady(true);
    });
  }, []);

  const update = useCallback((next: Filters) => {
    setFilters(next);
    try {
      window.history.replaceState(null, "", `${window.location.pathname}${toSearch(next)}`);
    } catch {
      // the address bar is a convenience
    }
  }, []);

  return [filters, update, ready];
}

export interface SavedView {
  name: string;
  filters: Filters;
}

/** Named filter sets, kept in this browser only. Nothing here is sent anywhere. */
export function useSavedViews(page: string): [SavedView[], (name: string, filters: Filters) => void, (name: string) => void] {
  const key = `fraudlens.views.${page}`;
  const [views, setViews] = useState<SavedView[]>([]);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      const parsed = raw ? (JSON.parse(raw) as SavedView[]) : [];
      queueMicrotask(() => setViews(Array.isArray(parsed) ? parsed : []));
    } catch {
      // storage unavailable or damaged: start with none
    }
  }, [key]);

  const store = useCallback(
    (next: SavedView[]) => {
      setViews(next);
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // kept for this visit only
      }
    },
    [key],
  );

  const save = useCallback(
    (name: string, filters: Filters) => {
      const clean = name.trim().slice(0, 40);
      if (!clean) return;
      store([...views.filter((v) => v.name !== clean), { name: clean, filters }].slice(-12));
    },
    [store, views],
  );
  const remove = useCallback((name: string) => store(views.filter((v) => v.name !== name)), [store, views]);
  return [views, save, remove];
}
