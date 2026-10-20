/**
 * The Processing page's logic (processing contract, 2026-10-19): the processor's state as one sentence,
 * the steps of the work in progress, the pace and the time left, the queue by priority, and the poller that
 * re-reads GET /note/processing every 3 s while the page is visible (ETag / If-None-Match).
 *
 * Every word a person reads is built here from structured fields (flow, step, counts), never from content.
 */

import { randomId } from "@abstractframework/ui-kit";
import type { Api } from "./api";
import { niceMax } from "./progression";
import type { ActiveWork, Consolidation, Processing, RecentRevision } from "./types";

export const POLL_MS = 3000;
/** The queued notes the page lists (the queue's own order). */
export const WAITING_LIMIT = 50;

// ------------------------------------------------------------------ numbers as words

/** "2.1 per minute"; under one a minute "about 1 every 3 minutes"; nothing for no pace. */
export function paceWords(perMinute: number | null | undefined): string {
  const x = Number(perMinute) || 0;
  if (x <= 0) return "";
  if (x >= 0.95) {
    const r = Math.round(x * 10) / 10;
    return `${Number.isInteger(r) ? r.toFixed(0) : r.toFixed(1)} per minute`;
  }
  const every = Math.round(1 / x);
  return every <= 1 ? "about 1 per minute" : `about 1 every ${every} minutes`;
}

/** The time left: "less than a minute left", "about 12 min left", "about 1 h 40 left", "about 2 days left". */
export function etaWords(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) return "";
  const s = Math.round(seconds);
  if (s < 60) return "less than a minute left";
  if (s < 3600) return `about ${Math.max(1, Math.round(s / 60))} min left`;
  if (s < 86400) {
    let h = Math.floor(s / 3600);
    let m = Math.round((s % 3600) / 60 / 5) * 5;
    if (m === 60) {
      h += 1;
      m = 0;
    }
    if (h >= 24) return "about a day left";
    return m === 0 ? `about ${h} h left` : `about ${h} h ${String(m).padStart(2, "0")} left`;
  }
  const d = Math.round(s / 86400);
  return d <= 1 ? "about a day left" : `about ${d} days left`;
}

/** How long a note took: "8 s", "1 min 20 s", "1 h 5 min". */
export function durationWords(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 1) return "under a second";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
  const m = Math.round((s % 3600) / 60);
  return `${Math.floor(s / 3600)} h${m ? ` ${m} min` : ""}`;
}

/** Elapsed time as a clock: "0:42", "3:05", "1:02:09". */
export function clockWords(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** "just now", "4 min ago", "3 h ago", "2 days ago" (against the server's clock). */
export function agoWords(iso: string | null | undefined, nowMs: number): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, (nowMs - t) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The chips of an understood note: "+3 entities", "+9 claims", "1 replaced" (zeros left out). */
export function revisionChips(r: Pick<RecentRevision, "entities_created" | "claims_added" | "claims_superseded">): string[] {
  const out: string[] = [];
  if (r.entities_created > 0) out.push(`+${plural(r.entities_created, "entity", "entities")}`);
  if (r.claims_added > 0) out.push(`+${plural(r.claims_added, "claim", "claims")}`);
  if (r.claims_superseded > 0) out.push(`${r.claims_superseded} replaced`);
  return out;
}

// ------------------------------------------------------------------ the work in progress

/** The steps a person sees for the work that has them; the server's step names map onto them. */
export const STEPPERS: Record<string, Array<{ label: string; steps: string[] }>> = {
  "note-process": [
    { label: "Reading", steps: ["context"] },
    { label: "Extracting", steps: ["extract"] },
    { label: "Linking", steps: ["resolve", "link"] },
    { label: "Applying", steps: ["apply"] },
  ],
  "note-recall": [
    { label: "Searching", steps: ["search"] },
    { label: "Writing", steps: ["synthesize"] },
  ],
  "note-mine": [
    { label: "Searching", steps: ["search"] },
    { label: "Writing", steps: ["synthesize"] },
  ],
};

/** The stepper of an item: a drain working on a note shows the note's steps. */
export function stepperFor(item: Pick<ActiveWork, "flow" | "note">): Array<{ label: string; steps: string[] }> | null {
  if (item.flow === "note-drain") return item.note ? STEPPERS["note-process"] : null;
  return STEPPERS[item.flow] || null;
}

/** Which step of the item's stepper is current: -1 when the item has no stepper or the step is not one of its steps. */
export function stepIndex(item: Pick<ActiveWork, "flow" | "note" | "step">): number {
  const stepper = stepperFor(item);
  if (!stepper || !item.step) return -1;
  return stepper.findIndex((s) => s.steps.includes(String(item.step)));
}

/** What an item is doing, for a card without a note title. */
export const FLOW_WORDS: Record<string, string> = {
  "note-drain": "Working through the queue",
  "note-process": "Understanding a note",
  "note-capture": "Saving a note",
  "note-recall": "Answering a question",
  "note-mine": "Digging through your history",
  "note-reflect": "Looking back over your notes",
};
export function flowWords(flow: string): string {
  return FLOW_WORDS[flow] || "Working";
}

const FLOW_ORDER = ["note-drain", "note-process", "note-capture", "note-recall", "note-mine", "note-reflect"];

/**
 * The cards of "Right now": a drain carries the notes it is processing nested under it. An item that names its
 * drain (`parent_run_id`, docs/api.md ActiveRun) goes under it; one that does not goes under the latest drain that started before it
 * (a structural rule on the start times: the drain is what processes queued notes). Everything else is a card
 * of its own.
 */
export function groupActive(active: ActiveWork[]): Array<{ item: ActiveWork; children: ActiveWork[] }> {
  const t = (iso: string) => Date.parse(iso) || 0;
  const drains = active.filter((a) => a.flow === "note-drain").sort((a, b) => t(a.started_at) - t(b.started_at));
  const groups = new Map<string, { item: ActiveWork; children: ActiveWork[] }>();
  for (const d of drains) groups.set(d.run_id, { item: d, children: [] });
  const out: Array<{ item: ActiveWork; children: ActiveWork[] }> = [];
  for (const a of active) {
    if (a.flow === "note-drain") continue;
    const named = a.parent_run_id ? groups.get(a.parent_run_id) : undefined;
    const byTime = !a.parent_run_id && a.flow === "note-process" ? [...drains].reverse().find((d) => t(d.started_at) <= t(a.started_at)) : undefined;
    const parent = named || (byTime ? groups.get(byTime.run_id) : undefined);
    if (parent) parent.children.push(a);
    else out.push({ item: a, children: [] });
  }
  const rank = (f: string) => (FLOW_ORDER.indexOf(f) < 0 ? FLOW_ORDER.length : FLOW_ORDER.indexOf(f));
  return [...groups.values(), ...out].sort((a, b) => rank(a.item.flow) - rank(b.item.flow) || t(a.item.started_at) - t(b.item.started_at));
}

/** The note cards a drain shows: its own notes in progress, or (none listed) the note it reports itself. */
export function drainNotes(group: { item: ActiveWork; children: ActiveWork[] }): ActiveWork[] {
  if (group.children.length) return group.children;
  return group.item.note ? [{ ...group.item, flow: "note-process" }] : [];
}

/** Is the notebook understanding notes right now (a drain or a note in progress)? */
export function isUnderstanding(p: Pick<Processing, "active">): boolean {
  return p.active.some((a) => a.flow === "note-drain" || a.flow === "note-process");
}

// ------------------------------------------------------------------ the state sentence

export type StateTone = "live" | "idle" | "paused" | "broken";

function waitingWords(n: number): string {
  return n === 1 ? "1 note waiting" : `${n} notes waiting`;
}

/** "2m" -> "every 2 minutes". */
export function everyWords(every: string | undefined | null): string {
  const m = String(every || "").match(/^(\d+)([smhd])$/);
  if (!m) return "";
  const n = Number(m[1]);
  const unit = { s: "second", m: "minute", h: "hour", d: "day" }[m[2] as "s" | "m" | "h" | "d"];
  return n === 1 ? `every ${unit}` : `every ${n} ${unit}s`;
}

/**
 * The processor's state as one sentence with its tone (the live dot's colour; the sentence carries the
 * meaning): "Understanding your notes · 2.1 per minute · about 1 h 40 left".
 */
export function stateLine(p: Processing): { tone: StateTone; text: string } {
  const queued = p.queue.queued;
  const busy = isUnderstanding(p);
  if (!p.processor.healthy) {
    return { tone: "broken", text: ["Understanding has stopped and needs a repair", queued ? waitingWords(queued) : ""].filter(Boolean).join(" · ") };
  }
  if (p.processor.paused) {
    return { tone: "paused", text: ["Paused", busy ? "finishing the note in progress" : "", queued ? waitingWords(queued) : ""].filter(Boolean).join(" · ") };
  }
  if (busy) {
    const pace = p.pace.per_minute_5m > 0 ? p.pace.per_minute_5m : p.pace.per_minute_1h;
    return { tone: "live", text: ["Understanding your notes", paceWords(pace), etaWords(p.queue.eta_s)].filter(Boolean).join(" · ") };
  }
  const run = p.consolidation?.run;
  if (run && run.stage !== "done") {
    const i = consolidationStageIndex(run.stage);
    const stage = i >= 0 && i < CONSOLIDATION_STAGES.length ? CONSOLIDATION_STAGES[i].label : "";
    const progress = run.stage === "reinterpret" ? reinterpretWords(run.stage_done, run.stage_total) : stage && run.stage_total ? `${stage} ${run.stage_done} of ${run.stage_total}` : stage;
    return { tone: "live", text: ["Consolidating your notebook", progress, etaWords(run.eta_s)].filter(Boolean).join(" · ") };
  }
  const other = groupActive(p.active)[0];
  if (other) return { tone: "live", text: [flowWords(other.item.flow), queued ? waitingWords(queued) : ""].filter(Boolean).join(" · ") };
  if (queued) {
    const every = everyWords(p.processor.schedule?.every);
    return { tone: "idle", text: [waitingWords(queued), every ? `the notebook looks ${every}` : ""].filter(Boolean).join(" · ") };
  }
  const gaps = consolidationGapsWords(p.consolidation?.last);
  if (gaps) return { tone: "idle", text: `Consolidated with gaps · ${gaps}` };
  return { tone: "idle", text: "Up to date · everything you sent is understood" };
}

/** The reinterpret stage in words (ADR 0008): "Re-understanding 12 notes", then "Re-understanding 3 of 12 notes". */
export function reinterpretWords(done: number, total: number): string {
  const t = Math.max(0, Number(total) || 0);
  const d = Math.max(0, Math.min(t, Number(done) || 0));
  if (!t) return "Re-understanding";
  return d ? `Re-understanding ${d} of ${plural(t, "note", "notes")}` : `Re-understanding ${plural(t, "note", "notes")}`;
}

/** What the last consolidation left for the next one, in words ("12 summaries to try again next time"); "" when nothing. */
export function consolidationGapsWords(last: Consolidation["last"] | null | undefined): string {
  if (!last || last.status !== "completed_with_gaps") return "";
  const s = Math.max(0, Number(last.left_out?.summaries) || 0);
  const g = Math.max(0, Number(last.left_out?.labels) || 0);
  const r = Math.max(0, Number(last.left_out?.reinterpret) || 0);
  const parts = [s ? plural(s, "summary", "summaries") : "", g ? plural(g, "theme name", "theme names") : ""].filter(Boolean);
  const words = [parts.length ? `${parts.join(" and ")} to try again next time` : "", r ? `${plural(r, "note", "notes")} to understand again next time` : ""].filter(Boolean);
  return words.length ? words.join(" and ") : "some work to try again next time";
}

/** The last consolidation's end-user notice (only a run that left something out has one). */
export function consolidationNotice(last: Consolidation["last"] | null | undefined): string {
  return last && last.status === "completed_with_gaps" && last.notice ? last.notice : "";
}

// ------------------------------------------------------------------ consolidation

/** The stages of a consolidation, in order, as the stepper names them. */
export const CONSOLIDATION_STAGES: Array<{ stage: string; label: string }> = [
  { stage: "normalize", label: "Normalize" },
  { stage: "reinterpret", label: "Re-understanding" },
  { stage: "communities", label: "Themes" },
  { stage: "labels", label: "Labels" },
  { stage: "summaries", label: "Summaries" },
  { stage: "then_now", label: "Then and now" },
  { stage: "reflect", label: "Reflect" },
];
/** The current stage's place in the stepper; `done` is past the last; -1 for a stage the stepper does not know. */
export function consolidationStageIndex(stage: string | null | undefined): number {
  if (stage === "done") return CONSOLIDATION_STAGES.length;
  return CONSOLIDATION_STAGES.findIndex((s) => s.stage === stage);
}

/** The consolidation schedule choices (settings `consolidate_schedule.every`). */
export const CONSOLIDATE_CHOICES: Array<[string, string]> = [
  ["12h", "Every 12 hours"],
  ["1d", "Every day"],
  ["7d", "Every week"],
  ["off", "Never on its own"],
];

/** "Today", "Yesterday", "3 days ago" (server clock). */
export function dayWords(iso: string | null | undefined, nowMs: number): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return "";
  const day = (ms: number) => {
    const d = new Date(ms);
    return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  };
  const days = Math.round((day(nowMs) - day(t)) / 86400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return `${days} days ago`;
}

/** The last consolidation in one line: "Yesterday: 17 entities joined, 35 themes, 12 named, 140 summaries refreshed, 9 insights". */
export function lastConsolidationLine(last: Consolidation["last"], nowMs: number): string {
  if (!last) return "Not consolidated yet.";
  const parts = [
    last.merges ? `${plural(last.merges, "entity", "entities")} joined` : "",
    last.reinterpreted ? `${plural(last.reinterpreted, "note", "notes")} understood again` : "",
    last.communities ? plural(last.communities, "theme", "themes") : "",
    last.labelled ? `${last.labelled} named` : "",
    last.summaries_refreshed ? `${plural(last.summaries_refreshed, "summary", "summaries")} refreshed` : "",
    last.insights ? plural(last.insights, "insight", "insights") : "",
  ].filter(Boolean);
  const head = dayWords(last.finished_at, nowMs) || "Last time";
  if (last.status !== "completed" && last.status !== "completed_with_gaps") return `${head}: it stopped before the end${parts.length ? ` (${parts.join(", ")})` : ""}.`;
  return `${head}: ${parts.length ? parts.join(", ") : "nothing needed changing"}.`;
}

/** What the next consolidation has waiting: "3 themes to name · 2 themes changed since named · 88 summaries to refresh". */
export function pendingConsolidationLine(p: Consolidation["pending"] | null | undefined): string {
  if (!p) return "";
  return [
    p.unlabelled ? `${plural(p.unlabelled, "theme", "themes")} to name` : "",
    p.stale_labels ? `${plural(p.stale_labels, "theme", "themes")} changed since named` : "",
    p.stale_summaries ? `${plural(p.stale_summaries, "summary", "summaries")} to refresh` : "",
    p.reinterpret ? `${plural(p.reinterpret, "note", "notes")} to understand again` : "",
  ].filter(Boolean).join(" · ");
}

// ------------------------------------------------------------------ the queue

export const PRIORITY_WORDS: Record<string, string> = { "3": "Urgent", "2": "High", "1": "Normal", "0": "Imported" };
export const PRIORITIES = ["3", "2", "1", "0"] as const;

/** The queue by priority, front of the line first, for the stacked bar and its legend. */
export function queueSegments(byPriority: Record<string, number> | null | undefined): Array<{ key: string; label: string; count: number; share: number }> {
  const counts = PRIORITIES.map((k) => Math.max(0, Number(byPriority?.[k]) || 0));
  const total = counts.reduce((a, b) => a + b, 0);
  return PRIORITIES.map((key, i) => ({ key, label: PRIORITY_WORDS[key], count: counts[i], share: total ? counts[i] / total : 0 }));
}

export type WaitingNote = { note_id: string; title: string; occurred_at?: string; priority: number; state?: string; attempts?: number };

/**
 * The queued notes in the queue's order (priority first, then the oldest; docs/cognition.md "The queue"),
 * narrowed to the titles that contain the search (case-folded), the first 50.
 */
export function orderWaiting(waiting: WaitingNote[], search = "", limit = WAITING_LIMIT): WaitingNote[] {
  const q = search.trim().toLocaleLowerCase();
  const t = (n: WaitingNote) => (n.occurred_at ? Date.parse(n.occurred_at) || 0 : 0);
  return waiting
    .filter((n) => n.state === undefined || n.state === "queued")
    .filter((n) => !q || String(n.title || "").toLocaleLowerCase().includes(q))
    .map((n, i) => ({ n, i }))
    .sort((a, b) => (Number(b.n.priority) || 0) - (Number(a.n.priority) || 0) || t(a.n) - t(b.n) || a.i - b.i)
    .slice(0, limit)
    .map((x) => x.n);
}

/** Move a queued note to the front of the line (priority 3). */
export function moveToFront(api: Pick<Api, "post">, noteId: string): Promise<{ note_id: string; priority: number }> {
  return api.post(`note/queue/${encodeURIComponent(noteId)}/priority`, { priority: 3 });
}

/** An automation command (run now, pause, resume). */
export function automationCommand(api: Pick<Api, "post">, automationId: string, type: "automation.run_now" | "automation.pause" | "automation.resume"): Promise<unknown> {
  return api.post(`automations/${encodeURIComponent(automationId)}/commands`, { command_id: randomId(), type });
}

/** Stop a drain: the note in progress goes back to the queue when its lease ends. */
export function cancelWork(api: Pick<Api, "post">, runId: string): Promise<unknown> {
  return api.post("commands", { command_id: randomId(), run_id: runId, type: "cancel" });
}

// ------------------------------------------------------------------ the pace line

export type PacePoint = { t: number; understood: number; x: number; y: number };

/** The 2-hour line's geometry: points, the 2 px line, the 10 % wash, a clean top. */
export function paceGeometry(series: Array<{ t: string; understood: number }>, width: number, height: number, pad = { l: 26, r: 10, t: 8, b: 18 }) {
  const pts = series.map((s) => ({ t: Date.parse(s.t), understood: Math.max(0, Number(s.understood) || 0) })).filter((p) => Number.isFinite(p.t)).sort((a, b) => a.t - b.t);
  const max = niceMax(Math.max(1, ...pts.map((p) => p.understood)));
  const w = Math.max(1, width - pad.l - pad.r);
  const h = Math.max(1, height - pad.t - pad.b);
  const t0 = pts.length ? pts[0].t : 0;
  const t1 = pts.length ? pts[pts.length - 1].t : 1;
  const span = Math.max(1, t1 - t0);
  const points: PacePoint[] = pts.map((p) => ({ ...p, x: pad.l + (pts.length === 1 ? w : ((p.t - t0) / span) * w), y: pad.t + h - (p.understood / max) * h }));
  const path = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const base = (pad.t + h).toFixed(1);
  const area = points.length ? `${path} L${points[points.length - 1].x.toFixed(1)},${base} L${points[0].x.toFixed(1)},${base} Z` : "";
  return { points, path, area, max, baseY: pad.t + h, top: pad.t, left: pad.l, right: pad.l + w };
}

// ------------------------------------------------------------------ the poller

export type PollAnswer<T> = { changed: true; data: T; etag: string | null } | { changed: false };

export type PollerOptions<T> = {
  /** One read; `etag` is the last one the server sent (null at first). */
  read: (etag: string | null) => Promise<PollAnswer<T>>;
  /** Whether the page is hidden now (document.visibilityState). */
  hidden: () => boolean;
  onData: (data: T) => void;
  /** Every answer (changed or not): the page is up to date at this moment. */
  onFresh?: () => void;
  onError: (err: unknown, consecutive: number) => void;
  intervalMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
};

/**
 * Polls while the page is visible: a read at start, then one every `intervalMs` after the previous answer
 * (never two at once). Hidden: no reads; visible again: a read at once. `refresh()` reads now (after the one
 * in flight). A 304 keeps what is shown; a failure keeps it too and counts (the next tick is the retry).
 */
export function createPoller<T>(o: PollerOptions<T>) {
  const interval = o.intervalMs ?? POLL_MS;
  const setTimer = o.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms));
  const clearTimer = o.clearTimer ?? ((h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>));
  let etag: string | null = null;
  let timer: unknown = null;
  let inFlight = false;
  let again = false;
  let stopped = true;
  let failures = 0;

  const schedule = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
    if (stopped || o.hidden()) return;
    timer = setTimer(() => {
      timer = null;
      void tick();
    }, interval);
  };

  async function tick(): Promise<void> {
    if (stopped) return;
    if (o.hidden()) return; // resumed by visibilityChanged()
    if (inFlight) {
      again = true;
      return;
    }
    inFlight = true;
    try {
      const answer = await o.read(etag);
      if (stopped) return;
      failures = 0;
      if (answer.changed) {
        etag = answer.etag;
        o.onData(answer.data);
      }
      o.onFresh?.();
    } catch (e) {
      if (stopped) return;
      failures += 1;
      o.onError(e, failures);
    } finally {
      inFlight = false;
    }
    if (again) {
      again = false;
      return tick();
    }
    schedule();
  }

  return {
    start() {
      if (!stopped) return;
      stopped = false;
      void tick();
    },
    stop() {
      stopped = true;
      if (timer !== null) clearTimer(timer);
      timer = null;
    },
    /** Read now (an action changed something). */
    refresh() {
      if (stopped) return Promise.resolve();
      if (timer !== null) clearTimer(timer);
      timer = null;
      return tick();
    },
    /** The page was hidden or shown. */
    visibilityChanged() {
      if (stopped) return;
      if (o.hidden()) {
        if (timer !== null) clearTimer(timer);
        timer = null;
      } else if (timer === null && !inFlight) void tick();
    },
    get etag() {
      return etag;
    },
    get running() {
      return !stopped;
    },
  };
}
