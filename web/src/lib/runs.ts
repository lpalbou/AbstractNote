/**
 * Start the note flows and follow them: the ledger SSE stream shows the
 * `answer_user` message as soon as it exists; the run's terminal `output`
 * (`output.response`, `output.note_id`, ...) comes from `GET /runs/{id}`. If the
 * stream fails, the app polls the run and its ledger pages instead.
 *
 * Ledger frames (abstractgateway/docs/api.md "Stream ledger updates"):
 *   event: step  data: {"cursor": n, "record": {effect: {type, payload}, status, result, ...}}
 *   event: done  data: {"run_id", "cursor", "status": "terminal"}
 */

import type { Api } from "./api";

export type RunState = { run_id?: string; status: string; output?: Record<string, unknown> | null; error?: unknown; waiting?: unknown };

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
  onAnswer?: (text: string, source: "stream" | "ledger") => void;
  /** Every ledger record as it arrives (progress lines: see `stepWords`). */
  onStep?: (record: unknown) => void;
  /** Injected for tests; default `new EventSource(url)`. */
  eventSource?: (url: string) => EventSourceLike;
  sleep?: (ms: number) => Promise<void>;
  /** Poll delays (ms) after the stream fails; the last one repeats. */
  pollMs?: number[];
  signal?: { aborted: boolean };
};

/**
 * Follow a run to its end. Resolves with the terminal run (from `GET /runs/{id}`).
 * Rejects only when the run cannot be read (the api's retries are exhausted).
 */
export async function followRun(api: Api, runId: string, opts: FollowOptions = {}): Promise<RunState> {
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const pollMs = opts.pollMs ?? [700, 1000, 1500, 2500, 4000];
  let answered = false;
  let cursor = 0;
  const seen = (text: string, source: "stream" | "ledger") => {
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
        opts.onStep?.(frame.record);
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
    if (!answered || opts.onStep) {
      const page = await api.get<{ items?: Array<{ cursor?: number; record?: unknown }>; next_after?: number }>(`runs/${encodeURIComponent(runId)}/ledger`, { after: cursor, limit: 200 });
      for (const item of page.items || []) {
        opts.onStep?.(item.record ?? item);
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
