/**
 * The one choke point for every request the app makes (ADR-0039): relative
 * URLs under `api/gateway/` (the app-server session proxy relays them with the
 * browser session), reads retried before they are reported, writes sent once.
 *
 * - A read (GET) that meets a timeout, a lost connection or a server error
 *   (5xx, 408, 429) is tried again: 3 tries in all, 1 s then 3 s apart.
 *   A refusal (401, 403, 404, other 4xx) or an unreadable answer is final at once.
 * - A write (POST, PUT) is sent once; its failure is reported at once.
 * - A 401 anywhere calls `onUnauthorized` (the app returns to sign-in).
 */

export const API_PREFIX = "api/gateway/";
export const CSRF_COOKIE = "abstractnote_gateway_csrf";
export const CSRF_HEADER = "x-abstractnote-csrf";
/** The gateway's attachment limit (25 MiB by default, docs/api.md). */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const READ_BACKOFF_MS = [1000, 3000];
export const READ_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: string;
  constructor(status: number, detail: string, code: string | null = null) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/**
 * A read worth another try (ADR-0039 point 2). Status 0 = no answer (network, timeout).
 * 501 (a route the server does not implement yet) is final: another try cannot meet it.
 */
export function retryable(status: number): boolean {
  return status === 0 || status === 408 || status === 429 || (status >= 500 && status !== 501);
}

export type ApiOptions = {
  fetch?: typeof fetch;
  sleep?: (ms: number) => Promise<void>;
  csrf?: () => string;
  onUnauthorized?: () => void;
  backoffMs?: number[];
  timeoutMs?: number;
};

export function readCookie(name: string, cookieString: string): string {
  for (const part of cookieString.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return decodeURIComponent(rest.join("="));
  }
  return "";
}

function queryString(params?: Record<string, unknown>): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) {
      if (v.length) q.set(k, v.join(","));
    } else q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function parse(resp: Response): Promise<unknown> {
  const text = await resp.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError(resp.status || 502, "The answer could not be read.", "unreadable");
  }
}

/** The gateway's error shape: `{detail: "..."}` or `{detail: {reason_code, message}}` (automations 409). */
function errorFrom(status: number, body: unknown): ApiError {
  const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const d = (b.detail && typeof b.detail === "object" ? b.detail : {}) as Record<string, unknown>;
  const detail = typeof b.detail === "string" ? b.detail : typeof d.message === "string" ? d.message : typeof b.error === "string" ? b.error : `HTTP ${status}`;
  const codeOf = (o: Record<string, unknown>) => (typeof o.reason_code === "string" ? o.reason_code : typeof o.code === "string" ? o.code : null);
  return new ApiError(status, detail, codeOf(b) ?? codeOf(d));
}

export type Api = ReturnType<typeof createApi>;

export function createApi(options: ApiOptions = {}) {
  const doFetch = options.fetch ?? ((input: RequestInfo | URL, init?: RequestInit) => fetch(input, init));
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const csrf = options.csrf ?? (() => (typeof document === "undefined" ? "" : readCookie(CSRF_COOKIE, document.cookie)));
  const backoff = options.backoffMs ?? READ_BACKOFF_MS;
  const timeoutMs = options.timeoutMs ?? READ_TIMEOUT_MS;

  async function once(method: string, path: string, body?: BodyInit | null, headers: Record<string, string> = {}, timeout = timeoutMs): Promise<unknown> {
    const ctrl = typeof AbortController === "undefined" ? null : new AbortController();
    const timer = ctrl ? setTimeout(() => ctrl.abort(), timeout) : null;
    let resp: Response;
    try {
      const h: Record<string, string> = { Accept: "application/json", ...headers };
      if (method !== "GET") h[CSRF_HEADER] = csrf();
      resp = await doFetch(`${API_PREFIX}${path}`, { method, body: body ?? undefined, headers: h, credentials: "same-origin", signal: ctrl?.signal });
    } catch {
      throw new ApiError(0, "No answer.", "network");
    } finally {
      if (timer) clearTimeout(timer);
    }
    const parsed = await parse(resp).catch((e) => {
      if (resp.ok) throw e;
      return {};
    });
    if (!resp.ok) {
      if (resp.status === 401) options.onUnauthorized?.();
      throw errorFrom(resp.status, parsed);
    }
    return parsed;
  }

  async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
    const full = `${path}${queryString(params)}`;
    let last: ApiError | null = null;
    for (let attempt = 0; attempt <= backoff.length; attempt++) {
      try {
        return (await once("GET", full)) as T;
      } catch (e) {
        last = e instanceof ApiError ? e : new ApiError(0, String(e), "network");
        if (!retryable(last.status) || attempt === backoff.length) break;
        await sleep(backoff[attempt]);
      }
    }
    throw last;
  }

  const json = (data: unknown) => ({ body: JSON.stringify(data ?? {}), headers: { "Content-Type": "application/json" } });

  async function post<T>(path: string, data?: unknown): Promise<T> {
    const j = json(data);
    return (await once("POST", path, j.body, j.headers)) as T;
  }

  async function put<T>(path: string, data: unknown): Promise<T> {
    const j = json(data);
    return (await once("PUT", path, j.body, j.headers)) as T;
  }

  /** Upload one file into the session (refused client-side above the 25 MB limit). */
  async function upload(file: File, sessionId: string): Promise<{ $artifact: string; filename?: string; content_type?: string }> {
    if (file.size > MAX_UPLOAD_BYTES) throw new ApiError(413, "File too large.", "too_large");
    const form = new FormData();
    form.set("session_id", sessionId);
    form.set("filename", file.name);
    form.set("content_type", file.type || "application/octet-stream");
    form.set("file", file, file.name);
    const out = (await once("POST", "attachments/upload", form, {}, 10 * 60_000)) as { artifact?: { $artifact?: string } };
    const ref = out?.artifact;
    if (!ref || typeof ref.$artifact !== "string") throw new ApiError(502, "The upload answer has no file reference.", "unreadable");
    return ref as { $artifact: string };
  }

  return { get, post, put, upload, url: (path: string, params?: Record<string, unknown>) => `${API_PREFIX}${path}${queryString(params)}` };
}

/**
 * The one sentence for a gateway without AbstractNote (no seam, or abstractnote not installed there): its
 * `GET /note/status` answers 404. Worded by the gateway maintainer's review; the one place the app names the
 * gateway, because the fix is on the gateway (install abstractnote there).
 */
export const NOT_INSTALLED = "AbstractNote is not installed on this gateway.";

export type Mount = "mounted" | "missing" | "unknown";

/**
 * Whether this gateway serves the note API: `GET /note/status` answered (mounted), answered 404 (missing:
 * every note read would 404, which is not "a note is missing"), or could not be read (unknown: the views
 * load and report their own errors). A 404 is final at once (never retried).
 */
export async function noteMount(api: Pick<Api, "get">): Promise<Mount> {
  try {
    await api.get("note/status");
    return "mounted";
  } catch (e) {
    return e instanceof ApiError && e.status === 404 ? "missing" : "unknown";
  }
}

/** The one sentence a person reads for a failed call (no codes, no system words). */
export function errorSentence(err: unknown, what = "this"): string {
  const e = err instanceof ApiError ? err : null;
  if (!e) return `Something went wrong while loading ${what}.`;
  if (e.code === "too_large" || e.status === 413) return "This file is larger than 25 MB; add it from the computer that has it with: abstractnote import files <folder>.";
  if (e.status === 0) return `Your notebook could not be reached, so ${what} could not be loaded.`;
  if (e.status === 401) return "Please sign in again.";
  if (e.status === 403) return "You are not allowed to do that.";
  if (e.status === 404) return `${what[0].toUpperCase()}${what.slice(1)} is not in your notebook.`;
  if (e.status === 409 && e.code === "automation_busy") return "Already running.";
  if (e.status === 501) return `Your notebook cannot show ${what} yet.`;
  if (e.status >= 500) return `Your notebook had a problem loading ${what}.`;
  return `${what[0].toUpperCase()}${what.slice(1)} could not be loaded.`;
}
