/**
 * Start the note flows and follow them through AbstractGateway's replay view (gateway >= 0.17.0,
 * `GET runs/{id}/view` + `/view/stream`, the runtime's one reconstruction of the run tree; root backlog
 * 0993, runtime 0854): the answer as soon as the view has it, progress from its activities, and a run
 * that waits for a person is reported as waiting instead of being followed forever. The run's terminal
 * `output` (`output.response`, `output.note_id`, ...) is read once from `GET /runs/{id}`.
 *
 * A gateway without the view (its route answers FastAPI's 404 "Not Found") is followed the earlier way,
 * labeled (`RunState.source = "ledger_fold"` with `sourceReason`): the ledger SSE stream shows the
 * `answer_user` message; if the stream fails, the app polls the run and its ledger pages.
 *
 * Ledger frames (abstractgateway/docs/api.md "Stream ledger updates"):
 *   event: step  data: {"cursor": n, "record": {effect: {type, payload}, status, result, ...}}
 *   event: done  data: {"run_id", "cursor", "status": "terminal"}
 */

import { createReplayViewTransport, projectReplayView, ReplayViewFold, type ReplayViewActivity, type ReplayViewInteraction } from "@abstractframework/panel-chat";
import { ApiError, READ_BACKOFF_MS, retryable, type Api } from "./api";

/** A person-facing wait the view names (a question or an approval somewhere in the run tree). */
export type RunWaiting = { kind: ReplayViewInteraction["kind"]; run_id: string; prompt: string | null };

export type RunState = {
  run_id?: string;
  status: string;
  output?: Record<string, unknown> | null;
  error?: unknown;
  waiting?: unknown;
  /** Where the run was followed: the gateway's replay view, or this app's own ledger reads. */
  source?: "replay_view" | "ledger_fold";
  /** Why it was not the replay view (`source: "ledger_fold"`). */
  sourceReason?: string;
};

export const TERMINAL = new Set(["completed", "failed", "cancelled", "canceled", "stopped"]);

/** The message of an `answer_user` ledger record, or null for any other record. */
export function answerFromRecord(record: unknown): string | null {
  if (!record || typeof record !== "object") return null;
  const r = record as Record<string, unknown>;
  const eff = (r.effect && typeof r.effect === "object" ? r.effect : {}) as Record<string, unknown>;
  if (String(eff.type || "").toLowerCase() !== "answer_user") return null;
  if (String(r.status || "").toLowerCase() !== "completed") return null;
  const result = (r.result && typeof r.result === "object" ? r.result : {}) as Record<string, unknown>;
  const payload = (eff.payload && typeof eff.payload === "object" ? eff.payload : {}) as Record<string, unknown>;
  const msg = result.message ?? payload.message ?? payload.text;
  return typeof msg === "string" && msg.trim() ? msg : null;
}

/** The tool names the recall flow calls, as progress a person reads (an unknown tool shows nothing). */
const STEP_TOOLS: Record<string, string> = {
  note_search: "Searched your notes",
  note_expand: "Looked closer at what they connect",
};

/**
 * One completed ledger step as a progress line for Standard and Deep answers, or null. Reads the effect type
 * and the tool names only (structured fields), never the content.
 */
export function stepWords(record: unknown): string | null {
  if (!record || typeof record !== "object") return null;
  const r = record as Record<string, unknown>;
  if (String(r.status || "").toLowerCase() !== "completed") return null;
  const eff = (r.effect && typeof r.effect === "object" ? r.effect : {}) as Record<string, unknown>;
  const type = String(eff.type || "").toLowerCase();
  if (type === "tool_calls") {
    const payload = (eff.payload && typeof eff.payload === "object" ? eff.payload : {}) as Record<string, unknown>;
    const calls = Array.isArray(payload.tool_calls) ? (payload.tool_calls as Array<Record<string, unknown>>) : [];
    const words = calls.map((c) => STEP_TOOLS[String(c?.name || "")]).find(Boolean);
    return words || null;
  }
  if (type === "start_subworkflow") return "Dug through your history";
  if (type === "llm_call") return "Wrote the answer";
  return null;
}

/** One activity of the replay view as a progress line (the same words as `stepWords`), or null. */
export function activityWords(activity: ReplayViewActivity): string | null {
  if (activity.kind === "subworkflow") return "Dug through your history";
  if (activity.status !== "completed") return null;
  if (activity.kind === "tool") return (activity.tool_calls || []).map((c) => STEP_TOOLS[String(c?.name || "")]).find(Boolean) || null;
  if (activity.kind === "llm") return "Wrote the answer";
  return null;
}

/** Parse one SSE `step` frame's data into its record (null when unreadable). */
export function recordFromFrame(data: string): { cursor: number; record: unknown } | null {
  try {
    const parsed = JSON.parse(data) as { cursor?: number; record?: unknown };
    if (!parsed || typeof parsed !== "object") return null;
    return { cursor: Number(parsed.cursor) || 0, record: parsed.record ?? parsed };
  } catch {
    return null;
  }
}

/** Start a note flow. `input_data.session_id` always equals the request's session (docs/api.md: the gateway does not forward it). */
export async function startFlow(api: Api, flowId: string, sessionId: string, inputData: Record<string, unknown>): Promise<string> {
  const out = await api.post<{ run_id?: string }>("runs/start", { bundle_id: "abstractnote", flow_id: flowId, session_id: sessionId, input_data: { ...inputData, session_id: sessionId } });
  if (!out || typeof out.run_id !== "string") throw new Error("The start answer has no run id.");
  return out.run_id;
}

const RUNTIME_PREFIX = "Runtime error: ";

/**
 * The sentence of a run that ended without its result: a failed run's `error`, or a completed run whose
 * `output` says `success: false` with an `error` (a tool refusal). The "Runtime error: " prefix is stripped
 * (docs/api.md). Null when the run succeeded.
 */
export function runErrorText(run: RunState): string | null {
  if (run.status === "waiting") {
    const w = (run.waiting && typeof run.waiting === "object" ? run.waiting : {}) as Partial<RunWaiting>;
    const what = w.kind === "tool_approval" ? "an approval" : "an answer";
    const prompt = typeof w.prompt === "string" && w.prompt.trim() ? `: “${w.prompt.trim()}”` : "";
    return `This run is waiting for ${what}${prompt}. Answer it where the gateway's runs are shown (AbstractCode or the Observer); this page does not answer runs.`;
  }
  const out = (run.output && typeof run.output === "object" ? run.output : {}) as Record<string, unknown>;
  const failed = run.status !== "completed" || out.success === false;
  if (!failed) return null;
  const raw = typeof out.error === "string" ? out.error : typeof run.error === "string" ? run.error : "";
  const text = raw.startsWith(RUNTIME_PREFIX) ? raw.slice(RUNTIME_PREFIX.length) : raw;
  return text.trim() || "";
}

export type EventSourceLike = {
  addEventListener(type: string, fn: (ev: MessageEvent) => void): void;
  close(): void;
  onerror: ((ev: Event) => void) | null;
};

export type FollowOptions = {
  onAnswer?: (text: string, source: "view" | "stream" | "ledger") => void;
  /** A progress line as the run advances (`activityWords` from the view, `stepWords` from the ledger). */
  onProgress?: (words: string) => void;
  /** Injected for tests; default `new EventSource(url)`. */
  eventSource?: (url: string) => EventSourceLike;
  sleep?: (ms: number) => Promise<void>;
  /** Poll delays (ms) after the stream fails; the last one repeats. */
  pollMs?: number[];
  /** Stop following (the page went away): an AbortSignal also closes the open view stream at once. */
  signal?: AbortSignal | { aborted: boolean };
  /** View stream reconnects before giving up (default 5). */
  viewRetries?: number;
};

type Settled = { status: "terminal" } | { status: "waiting"; waiting: RunWaiting };

const statusOf = (e: unknown): number => Number((e as { status?: unknown })?.status ?? 0) || 0;
const detailOf = (e: unknown): string => {
  const d = (e as { detail?: unknown })?.detail;
  return typeof d === "string" && d.trim() ? d.trim() : e instanceof Error ? e.message : String(e);
};

/**
 * Follow the run through the replay view. Resolves with the terminal run (its summary read until it says
 * terminal: the runtime appends the completion record before it saves the run) or a waiting one;
 * `{unavailable}` when the gateway has no view route.
 */
async function followView(api: Api, runId: string, opts: FollowOptions): Promise<RunState | { unavailable: string }> {
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const external = opts.signal;
  const transport = createReplayViewTransport({ fetch: (path, init) => api.raw(path, init) });
  // The first read is retried like every other read (ADR-0039): a blip is not a failed run.
  let read;
  for (let attempt = 0; ; attempt++) {
    try {
      read = await transport.getView(runId, external instanceof AbortSignal ? { signal: external } : {});
      break;
    } catch (e) {
      if (e && typeof e === "object" && (e as Error).name === "ReplayViewUnavailableError") return { unavailable: (e as Error).message };
      if (external?.aborted) return { status: "cancelled", source: "replay_view" };
      if (!retryable(statusOf(e)) || attempt >= READ_BACKOFF_MS.length) throw e instanceof ApiError ? e : new ApiError(statusOf(e), detailOf(e));
      await sleep(READ_BACKOFF_MS[attempt]);
    }
  }
  if (read.notModified) throw new ApiError(502, "The gateway answered 304 to a first read of the run.");
  const fold = new ReplayViewFold();
  fold.adopt(read.view, read.etag);
  const root = read.view.root_run_id || runId;
  const shown = new Set<string>();
  let answered = false;
  const settle = (): Settled | null => {
    const view = fold.view!;
    const p = projectReplayView(view);
    for (const a of view.activities || []) {
      const words = activityWords(a);
      if (words && !shown.has(words)) {
        shown.add(words);
        opts.onProgress?.(words);
      }
    }
    if (!answered) {
      // The early answer is the root run's own `answer_user` (a sub-run's reply is not the flow's answer).
      const answer = (view.messages || []).find((m) => m.run_id === root && m.source === "answer_user" && m.role === "assistant" && m.text.trim());
      if (answer) {
        answered = true;
        opts.onAnswer?.(answer.text, "view");
      }
    }
    if (TERMINAL.has(p.status)) return { status: "terminal" };
    if (view.interaction) return { status: "waiting", waiting: { kind: view.interaction.kind, run_id: view.interaction.run_id, prompt: view.interaction.prompt ?? null } };
    return null;
  };
  let settled = settle();
  const retries = opts.viewRetries ?? 5;
  // A stream that ends without `done` is reconnected from the last applied frame; one that delivers
  // nothing counts toward the limit (an accept-then-close never loops for ever). A refusal (401, 403,
  // 404) is final.
  let failures = 0;
  let lastError: unknown = null;
  while (!settled && !fold.terminal) {
    if (external?.aborted) return { status: "cancelled", source: "replay_view" };
    const ctrl = new AbortController();
    const stop = () => ctrl.abort();
    if (external instanceof AbortSignal) external.addEventListener("abort", stop, { once: true });
    let delivered = false;
    try {
      await transport.streamView(runId, {
        lastEventId: fold.lastEventId,
        signal: ctrl.signal,
        onFrame: (frame) => {
          const outcome = fold.apply(frame);
          if (outcome !== "view" && outcome !== "delta") return;
          delivered = true;
          settled = settle();
          if (settled) ctrl.abort();
        },
      });
    } catch (e) {
      if (!ctrl.signal.aborted) {
        if ([401, 403, 404].includes(statusOf(e))) throw e instanceof ApiError ? e : new ApiError(statusOf(e), detailOf(e));
        lastError = e;
      }
    } finally {
      if (external instanceof AbortSignal) external.removeEventListener("abort", stop);
    }
    if (external?.aborted) return { status: "cancelled", source: "replay_view" };
    if (settled || fold.terminal) break;
    failures = delivered ? 0 : failures + 1;
    if (failures >= retries) {
      const why = lastError ? `: ${detailOf(lastError)}` : " (it closed without sending anything)";
      throw new ApiError(statusOf(lastError), `The run's live view stopped answering${why}.`, "network");
    }
    await sleep([300, 700, 1500, 2500, 4000][Math.min(failures, 4)]);
  }
  if (settled?.status === "waiting") return { run_id: runId, status: "waiting", waiting: settled.waiting, source: "replay_view" };
  // The view is terminal; the summary (the run's `output`) can lag by one save. Bounded.
  const pollMs = opts.pollMs ?? [300, 700, 1000, 1500, 2500, 4000];
  for (let i = 0; ; i++) {
    const run = await api.get<RunState>(`runs/${encodeURIComponent(runId)}`);
    if (TERMINAL.has(String(run.status))) return { ...run, source: "replay_view" };
    if (external?.aborted) return { status: "cancelled", source: "replay_view" };
    if (i >= 8) throw new ApiError(0, `The run ended (${fold.view?.status}) but its result was not saved yet; try again in a moment.`, "lagging");
    await sleep(pollMs[Math.min(i, pollMs.length - 1)]);
  }
}

/**
 * Follow a run to its end. Resolves with the terminal run (from `GET /runs/{id}`).
 * Rejects only when the run cannot be read (the api's retries are exhausted).
 */
export async function followRun(api: Api, runId: string, opts: FollowOptions = {}): Promise<RunState> {
  const viewed = await followView(api, runId, opts);
  if (!("unavailable" in viewed)) return viewed;
  const legacy = await followLedger(api, runId, opts);
  return { ...legacy, source: "ledger_fold", sourceReason: viewed.unavailable };
}

/** The earlier way (gateways before the replay view): the ledger stream, else polling. */
async function followLedger(api: Api, runId: string, opts: FollowOptions): Promise<RunState> {
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const pollMs = opts.pollMs ?? [700, 1000, 1500, 2500, 4000];
  let answered = false;
  let cursor = 0;
  const seen = (text: string, source: "view" | "stream" | "ledger") => {
    if (answered) return;
    answered = true;
    opts.onAnswer?.(text, source);
  };

  const factory = opts.eventSource ?? (typeof EventSource === "undefined" ? null : (url: string) => new EventSource(url) as unknown as EventSourceLike);
  if (factory) {
    const streamed = await new Promise<boolean>((resolve) => {
      let es: EventSourceLike;
      try {
        es = factory(api.url(`runs/${encodeURIComponent(runId)}/ledger/stream`, { after: 0 }));
      } catch {
        resolve(false);
        return;
      }
      es.addEventListener("step", (ev) => {
        const frame = recordFromFrame(String(ev.data));
        if (!frame) return;
        cursor = Math.max(cursor, frame.cursor);
        const words = stepWords(frame.record);
        if (words) opts.onProgress?.(words);
        const text = answerFromRecord(frame.record);
        if (text) seen(text, "stream");
      });
      es.addEventListener("done", () => {
        es.close();
        resolve(true);
      });
      es.onerror = () => {
        es.close();
        resolve(false);
      };
    });
    if (streamed) {
      // `done` = everything was sent and the run is finished; read its output.
      for (let i = 0; ; i++) {
        const run = await api.get<RunState>(`runs/${encodeURIComponent(runId)}`);
        if (TERMINAL.has(String(run.status))) return run;
        await sleep(pollMs[Math.min(i, pollMs.length - 1)]);
      }
    }
  }
  // Poll: the run for its status, its ledger pages for the early answer and the progress.
  for (let i = 0; ; i++) {
    if (opts.signal?.aborted) return { status: "cancelled" };
    if (!answered || opts.onProgress) {
      const page = await api.get<{ items?: Array<{ cursor?: number; record?: unknown }>; next_after?: number }>(`runs/${encodeURIComponent(runId)}/ledger`, { after: cursor, limit: 200 });
      for (const item of page.items || []) {
        const words = stepWords(item.record ?? item);
        if (words) opts.onProgress?.(words);
        const text = answerFromRecord(item.record ?? item);
        if (text) seen(text, "ledger");
      }
      if (typeof page.next_after === "number") cursor = page.next_after;
    }
    const run = await api.get<RunState>(`runs/${encodeURIComponent(runId)}`);
    if (TERMINAL.has(String(run.status))) return run;
    await sleep(pollMs[Math.min(i, pollMs.length - 1)]);
  }
}
