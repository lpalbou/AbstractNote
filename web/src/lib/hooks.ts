import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

export type ReadState<T> = { data: T | null; error: unknown; loading: boolean; reload: () => void; setData: (next: T | null) => void };

/**
 * A read a person waits on (ADR-0039): `loading` until the first answer, the
 * error only after the api's tries are exhausted. A reload while data is on
 * screen keeps the data if it fails (quiet background refresh).
 */
export function useRead<T>(fn: () => Promise<T>, deps: unknown[]): ReadState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const dataRef = useRef<T | null>(null);
  dataRef.current = data;
  useEffect(() => {
    let live = true;
    if (dataRef.current === null) setLoading(true);
    setError(null);
    fn().then(
      (d) => {
        if (!live) return;
        setData(d);
        setLoading(false);
      },
      (e) => {
        if (!live) return;
        if (dataRef.current === null) setError(e);
        setLoading(false);
      },
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => {
    setTick((t) => t + 1);
  }, []);
  const hardReset = useCallback((next: T | null) => setData(next), []);
  return { data, error, loading, reload, setData: hardReset };
}

/** A tiny external store (shared between views). */
export function createStore<T>(initial: T) {
  let value = initial;
  const subs = new Set<() => void>();
  return {
    get: () => value,
    set: (next: T) => {
      if (Object.is(next, value)) return;
      value = next;
      for (const s of subs) s();
    },
    subscribe: (fn: () => void) => {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}

export function useStore<T>(store: ReturnType<typeof createStore<T>>): [T, (next: T) => void] {
  const v = useSyncExternalStore(store.subscribe, store.get, store.get);
  return [v, store.set];
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function useNarrow(maxWidth = 640): boolean {
  const query = `(max-width: ${maxWidth}px)`;
  const get = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false);
  const [narrow, setNarrow] = useState(get);
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(query);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return narrow;
}
