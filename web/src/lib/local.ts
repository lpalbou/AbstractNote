/** Per-browser state: the capture session id, the "since you last looked" cursor, the location choice, kept matches. */

export const SESSION_KEY = "abstractnote.session_id.v1";
export const CURSOR_KEY = "abstractnote.changes_cursor.v1";
export const LOCATION_KEY = "abstractnote.location.v1";
export const THREAD_KEY = "abstractnote.thread.v1";
export const LOCATION_CACHE_KEY = "abstractnote.location_cache.v1";
/** Matches kept from the review inbox (this browser only: the note API has no confirm action yet). */
export const REVIEW_KEPT_KEY = "abstractnote.review_kept.v1";

type Store = { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void };

function store(kind: "local" | "session"): Store | null {
  try {
    const s = kind === "local" ? window.localStorage : window.sessionStorage;
    return s ?? null;
  } catch {
    return null;
  }
}

export function readLocal(key: string, kind: "local" | "session" = "local"): string | null {
  try {
    return store(kind)?.getItem(key) ?? null;
  } catch {
    return null;
  }
}
export function writeLocal(key: string, value: string | null, kind: "local" | "session" = "local"): void {
  try {
    if (value === null) store(kind)?.removeItem(key);
    else store(kind)?.setItem(key, value);
  } catch {
    // storage refused (private mode): the value lives for this page only
  }
}

/** `note-web-<uuid>`, made once per browser and kept. */
export function captureSessionId(makeId: () => string, read = readLocal, write = writeLocal): string {
  const existing = read(SESSION_KEY);
  if (existing && /^note-web-[0-9a-f-]{36}$/.test(existing)) return existing;
  const id = `note-web-${makeId()}`;
  write(SESSION_KEY, id);
  return id;
}
