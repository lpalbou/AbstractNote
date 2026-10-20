/**
 * The Processing page (operator 2026-10-20): polling with ETag, the time-left words, the steppers, the queue
 * order and "Move to front", the state sentence, consolidation lines, and the mock's GET /note/processing.
 * Each check goes red when the thing it checks is removed.
 */
import { readFileSync } from "node:fs";
import * as http from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApi } from "../src/lib/api";
import {
  CONSOLIDATION_STAGES,
  clockWords,
  consolidationNotice,
  consolidationStageIndex,
  createPoller,
  drainNotes,
  durationWords,
  etaWords,
  groupActive,
  lastConsolidationLine,
  moveToFront,
  orderWaiting,
  paceGeometry,
  paceWords,
  pendingConsolidationLine,
  queueSegments,
  revisionChips,
  consolidationGapsWords,
  reinterpretWords,
  stateLine,
  stepIndex,
  stepperFor,
  type PollAnswer,
} from "../src/lib/processing";
import type { ActiveWork, Processing } from "../src/lib/types";
// @ts-expect-error plain ESM module
import { MOCK_TOKEN, createMockGateway } from "../mock/server.mjs";

/** A manual clock for the poller: timers run only when the test says so. */
function fakeTimers() {
  let seq = 0;
  const pending = new Map<number, { fn: () => void; ms: number }>();
  return {
    setTimer: (fn: () => void, ms: number) => {
      seq += 1;
      pending.set(seq, { fn, ms });
      return seq;
    },
    clearTimer: (h: unknown) => void pending.delete(h as number),
    fire: () => {
      const all = [...pending.entries()];
      pending.clear();
      for (const [, t] of all) t.fn();
      return all.length;
    },
    get count() {
      return pending.size;
    },
    get lastMs() {
      return [...pending.values()].pop()?.ms;
    },
  };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("polling", () => {
  it("reads at start, then every 3 s after the answer, sending the last ETag; a 304 keeps what is shown", async () => {
    const timers = fakeTimers();
    const asked: Array<string | null> = [];
    const answers: Array<PollAnswer<number>> = [{ changed: true, data: 1, etag: '"a"' }, { changed: false }, { changed: true, data: 2, etag: '"b"' }];
    const shown: number[] = [];
    let fresh = 0;
    const p = createPoller<number>({ read: async (etag) => (asked.push(etag), answers.shift()!), hidden: () => false, onData: (d) => shown.push(d), onFresh: () => (fresh += 1), onError: () => undefined, ...timers });
    p.start();
    await flush();
    expect(asked).toEqual([null]);
    expect(timers.count).toBe(1);
    expect(timers.lastMs).toBe(3000);
    timers.fire();
    await flush();
    expect(asked).toEqual([null, '"a"']);
    expect(shown).toEqual([1]); // the 304 changed nothing
    timers.fire();
    await flush();
    expect(asked).toEqual([null, '"a"', '"a"']);
    expect(shown).toEqual([1, 2]);
    expect(p.etag).toBe('"b"');
    expect(fresh).toBe(3);
    p.stop();
    expect(timers.count).toBe(0);
  });

  it("pauses while the page is hidden and reads at once when it is shown again", async () => {
    const timers = fakeTimers();
    let isHidden = false;
    let reads = 0;
    const p = createPoller<number>({ read: async () => (reads++, { changed: true, data: reads, etag: null }), hidden: () => isHidden, onData: () => undefined, onError: () => undefined, ...timers });
    p.start();
    await flush();
    expect(reads).toBe(1);
    isHidden = true;
    p.visibilityChanged();
    expect(timers.count).toBe(0); // no timer while hidden
    timers.fire();
    await flush();
    expect(reads).toBe(1);
    isHidden = false;
    p.visibilityChanged();
    await flush();
    expect(reads).toBe(2);
    expect(timers.count).toBe(1);
    // Hidden without the event reaching us yet: the timer that fires reads nothing.
    isHidden = true;
    timers.fire();
    await flush();
    expect(reads).toBe(2);
    p.stop();
  });

  it("a failure keeps polling and counts; never two reads at once; refresh during a read reads right after", async () => {
    const timers = fakeTimers();
    let release: (() => void) | null = null;
    let reads = 0;
    let concurrent = 0;
    let most = 0;
    const errors: number[] = [];
    const p = createPoller<number>({
      read: async () => {
        reads += 1;
        concurrent += 1;
        most = Math.max(most, concurrent);
        await new Promise<void>((r) => (release = r));
        concurrent -= 1;
        if (reads <= 2) throw new Error("down");
        return { changed: true, data: reads, etag: null };
      },
      hidden: () => false,
      onData: () => undefined,
      onError: (_e, n) => errors.push(n),
      ...timers,
    });
    p.start();
    void p.refresh(); // while the first is in flight
    release!();
    await flush();
    await flush();
    expect(reads).toBe(2); // the refresh ran after the first answer, not beside it
    release!();
    await flush();
    await flush();
    expect(errors).toEqual([1, 2]);
    expect(most).toBe(1);
    expect(timers.count).toBe(1); // still polling after failures
    p.stop();
  });

  it("the conditional read sends If-None-Match, answers unchanged on 304 and keeps the ETag", async () => {
    const seen: Array<Record<string, string>> = [];
    const fetch = async (_url: RequestInfo | URL, init?: RequestInit) => {
      const h = (init?.headers || {}) as Record<string, string>;
      seen.push(h);
      return h["If-None-Match"] === '"x"' ? new Response(null, { status: 304 }) : new Response('{"now":"t"}', { status: 200, headers: { ETag: '"x"' } });
    };
    const api = createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined });
    const first = await api.poll<{ now: string }>("note/processing", null);
    expect(first).toEqual({ changed: true, data: { now: "t" }, etag: '"x"' });
    expect(seen[0]["If-None-Match"]).toBeUndefined();
    expect(await api.poll("note/processing", '"x"')).toEqual({ changed: false });
    expect(seen[1]["If-None-Match"]).toBe('"x"');
  });
});

describe("numbers as words", () => {
  it("time left", () => {
    expect(etaWords(null)).toBe("");
    expect(etaWords(0)).toBe("");
    expect(etaWords(40)).toBe("less than a minute left");
    expect(etaWords(12 * 60 + 10)).toBe("about 12 min left");
    expect(etaWords(6000)).toBe("about 1 h 40 left");
    expect(etaWords(3600 + 3 * 60)).toBe("about 1 h 05 left");
    expect(etaWords(2 * 3600 + 58 * 60)).toBe("about 3 h left");
    expect(etaWords(23 * 3600 + 59 * 60)).toBe("about a day left");
    expect(etaWords(3 * 86400)).toBe("about 3 days left");
  });
  it("pace, durations and clocks", () => {
    expect(paceWords(2.13)).toBe("2.1 per minute");
    expect(paceWords(2)).toBe("2 per minute");
    expect(paceWords(0.34)).toBe("about 1 every 3 minutes");
    expect(paceWords(0)).toBe("");
    expect(durationWords(8.4)).toBe("8 s");
    expect(durationWords(80)).toBe("1 min 20 s");
    expect(durationWords(3900)).toBe("1 h 5 min");
    expect(clockWords(185_000)).toBe("3:05");
    expect(clockWords(3_729_000)).toBe("1:02:09");
  });
  it("revision chips leave zeros out", () => {
    expect(revisionChips({ entities_created: 3, claims_added: 9, claims_superseded: 1 })).toEqual(["+3 entities", "+9 claims", "1 replaced"]);
    expect(revisionChips({ entities_created: 1, claims_added: 0, claims_superseded: 0 })).toEqual(["+1 entity"]);
  });
});

const work = (over: Partial<ActiveWork>): ActiveWork => ({ run_id: "r", flow: "note-process", started_at: "2026-10-20T10:00:00Z", note: null, step: null, step_started_at: null, sections_done: null, sections_total: null, thinking: null, ...over });
const ref = { note_id: "nt_1", title: "Weekly sync", kind: "log", occurred_at: "2026-10-01T00:00:00Z" };

describe("steppers", () => {
  it("maps the server's steps to Reading, Extracting, Linking, Applying", () => {
    expect(stepperFor(work({}))!.map((s) => s.label)).toEqual(["Reading", "Extracting", "Linking", "Applying"]);
    expect(["context", "extract", "resolve", "link", "apply", "filing", null].map((step) => stepIndex(work({ step })))).toEqual([0, 1, 2, 2, 3, -1, -1]);
    expect(stepIndex(work({ flow: "note-drain", note: ref, step: "extract" }))).toBe(1);
    expect(stepperFor(work({ flow: "note-drain" }))).toBeNull();
    expect(stepIndex(work({ flow: "note-recall", step: "synthesize" }))).toBe(1);
    expect(stepperFor(work({ flow: "note-reflect" }))).toBeNull();
  });
  it("nests a drain's notes under it, by its name or by time, without showing a note twice", () => {
    const drain = work({ run_id: "d", flow: "note-drain", started_at: "2026-10-20T10:00:00Z", note: ref, step: "link" });
    const child = work({ run_id: "c", started_at: "2026-10-20T10:01:00Z", note: ref, step: "link", parent_run_id: "d" });
    const reflect = work({ run_id: "f", flow: "note-reflect" });
    const g = groupActive([reflect, child, drain]);
    expect(g.map((x) => [x.item.run_id, x.children.map((c) => c.run_id)])).toEqual([["d", ["c"]], ["f", []]]);
    expect(drainNotes(g[0]).map((n) => n.run_id)).toEqual(["c"]);
    const byTime = groupActive([drain, work({ run_id: "t", started_at: "2026-10-20T10:02:00Z" })]);
    expect(byTime[0].children.map((c) => c.run_id)).toEqual(["t"]);
    const alone = groupActive([drain]);
    expect(drainNotes(alone[0]).map((n) => [n.flow, n.note?.note_id])).toEqual([["note-process", "nt_1"]]);
    const early = groupActive([drain, work({ run_id: "e", started_at: "2026-10-20T09:00:00Z" })]);
    expect(early.map((x) => x.item.run_id)).toEqual(["d", "e"]);
  });
  it("consolidation stages", () => {
    expect(CONSOLIDATION_STAGES.map((s) => s.label)).toEqual(["Normalize", "Re-understanding", "Themes", "Labels", "Summaries", "Then and now", "Reflect"]);
    expect(consolidationStageIndex("summaries")).toBe(4);
    expect(consolidationStageIndex("reinterpret")).toBe(1);
    expect(consolidationStageIndex("done")).toBe(7);
    expect(consolidationStageIndex("other")).toBe(-1);
  });
});

describe("queue and the front of the line", () => {
  const waiting = [
    { note_id: "a", title: "Old import", priority: 0, occurred_at: "2025-01-01T00:00:00Z" },
    { note_id: "b", title: "Live note", priority: 1, occurred_at: "2026-10-01T00:00:00Z" },
    { note_id: "c", title: "Urgent task", priority: 3, occurred_at: "2026-10-02T00:00:00Z" },
    { note_id: "d", title: "Older live note", priority: 1, occurred_at: "2026-09-01T00:00:00Z" },
  ];
  it("orders by priority then age, searches titles, keeps 50", () => {
    expect(orderWaiting(waiting).map((n) => n.note_id)).toEqual(["c", "d", "b", "a"]);
    expect(orderWaiting(waiting, "LIVE").map((n) => n.note_id)).toEqual(["d", "b"]);
    const many = Array.from({ length: 80 }, (_, i) => ({ note_id: `n${i}`, title: `n${i}`, priority: 0 }));
    expect(orderWaiting(many)).toHaveLength(50);
  });
  it("Move to front asks for priority 3 on that note", async () => {
    const sent: Array<[string, unknown]> = [];
    await moveToFront({ post: async (path: string, body?: unknown) => (sent.push([path, body]), { note_id: "c", priority: 3 }) } as never, "nt x");
    expect(sent).toEqual([["note/queue/nt%20x/priority", { priority: 3 }]]);
  });
  it("the stacked bar goes front of the line first", () => {
    expect(queueSegments({ "0": 6, "1": 2, "3": 2 }).map((s) => [s.label, s.count, s.share])).toEqual([["Urgent", 2, 0.2], ["High", 0, 0], ["Normal", 2, 0.2], ["Imported", 6, 0.6]]);
  });
  it("the pace line fills the box", () => {
    const g = paceGeometry([{ t: "2026-10-20T10:00:00Z", understood: 3 }, { t: "2026-10-20T10:05:00Z", understood: 9 }], 300, 100);
    expect(g.max).toBe(10);
    expect(g.points[0].x).toBe(g.left);
    expect(g.points[1].x).toBe(g.right);
    expect(g.path.startsWith("M")).toBe(true);
  });
});

function processing(over: Partial<Processing> = {}): Processing {
  const auto = { id: "p", state: "active", paused: false, healthy: true, reason: null, schedule: { every: "2m", max_items: 20 } };
  return {
    now: "2026-10-20T10:00:00Z",
    processor: auto,
    reflect: { ...auto, id: "r", last_run_at: null, last_status: null, insights_last_run: null, communities: null },
    queue: { queued: 128, processing: 1, failed: 0, done: 10, by_priority: { "0": 128 }, oldest_enqueued_at: null, eta_s: 6000 },
    pace: { per_minute_5m: 2.1, per_minute_1h: 1.6, series: [] },
    active: [work({ flow: "note-drain" })],
    recent: [],
    failed: [],
    model: null,
    consolidation: null,
    ...over,
  };
}

describe("the state sentence", () => {
  it("live, paused, broken, waiting and up to date", () => {
    expect(stateLine(processing())).toEqual({ tone: "live", text: "Understanding your notes · 2.1 per minute · about 1 h 40 left" });
    expect(stateLine(processing({ processor: { ...processing().processor, paused: true } })).text).toBe("Paused · finishing the note in progress · 128 notes waiting");
    expect(stateLine(processing({ processor: { ...processing().processor, healthy: false } })).tone).toBe("broken");
    expect(stateLine(processing({ active: [] }))).toEqual({ tone: "idle", text: "128 notes waiting · the notebook looks every 2 minutes" });
    expect(stateLine(processing({ active: [], queue: { ...processing().queue, queued: 0 } })).text).toBe("Up to date · everything you sent is understood");
  });
  it("consolidation in progress when nothing else runs", () => {
    const c = { automation: { id: "c", state: "active", paused: false, healthy: true, reason: null }, run: { run_id: "x", started_at: "2026-10-20T09:56:00Z", stage: "summaries", stage_done: 48, stage_total: 140, eta_s: 400, elapsed_s: 240 }, last: null, pending: { stale_labels: 0, unlabelled: 0, stale_summaries: 0 } };
    expect(stateLine(processing({ active: [], consolidation: c })).text).toBe("Consolidating your notebook · Summaries 48 of 140 · about 7 min left");
  });
  it("the re-understanding stage in words (ADR 0008)", () => {
    const run = { run_id: "x", started_at: "2026-10-20T09:56:00Z", stage: "reinterpret", stage_done: 0, stage_total: 12, eta_s: 400, elapsed_s: 10 };
    const c = { automation: { id: "c", state: "active", paused: false, healthy: true, reason: null }, run, last: null, pending: { stale_labels: 0, unlabelled: 0, stale_summaries: 0, reinterpret: 12 } };
    expect(stateLine(processing({ active: [], consolidation: c })).text).toBe("Consolidating your notebook · Re-understanding 12 notes · about 7 min left");
    expect(stateLine(processing({ active: [], consolidation: { ...c, run: { ...run, stage_done: 3 } } })).text).toBe("Consolidating your notebook · Re-understanding 3 of 12 notes · about 7 min left");
    expect(reinterpretWords(0, 1)).toBe("Re-understanding 1 note");
    expect(pendingConsolidationLine({ unlabelled: 0, stale_labels: 0, stale_summaries: 0, reinterpret: 4 })).toBe("4 notes to understand again");
    // ADR 0008 §4a: how many of them follow from an idea understood differently, in plain words, never an impact
    expect(reinterpretWords(0, 12, { notes: 5, names: ["Zorn"], sources: 1 })).toBe("Re-understanding 12 notes (5 because Zorn changed)");
    expect(reinterpretWords(3, 12, { notes: 5, names: ["Zorn", "Mira"], sources: 3 })).toBe("Re-understanding 3 of 12 notes (5 because Zorn and 2 other ideas changed)");
    expect(reinterpretWords(0, 12, { notes: 0, names: [], sources: 0 })).toBe("Re-understanding 12 notes");
    expect(stateLine(processing({ active: [], consolidation: { ...c, run: { ...run, propagated: { notes: 5, names: ["Zorn"], sources: 1 } } } })).text).toBe("Consolidating your notebook · Re-understanding 12 notes (5 because Zorn changed) · about 7 min left");
    expect(pendingConsolidationLine({ unlabelled: 0, stale_labels: 0, stale_summaries: 0, reinterpret: 12 }, { pending: 12, by_entity: [], reasons: [], context_unknown: 0, consumed_today: 0, last_marked_at: null, propagated: { pending: 5, by_source: [{ entity_id: "e1", name: "Zorn", notes: 5 }], may_follow: 9 } })).toBe("12 notes to understand again (5 because Zorn changed)");
    const now = Date.parse("2026-10-20T10:00:00");
    const last = { finished_at: new Date(Date.parse("2026-10-19T03:12:00")).toISOString(), status: "completed_with_gaps", merges: 0, communities: 3, labelled: 0, summaries_refreshed: 0, insights: 0, reinterpreted: 5, unchanged: 2, left_out: { summaries: 0, labels: 0, reinterpret: 3, reasons: ["x"] }, budget_spent: true, notice: "n" };
    expect(lastConsolidationLine(last, now)).toBe("Yesterday: 5 notes understood again, 3 themes.");
    expect(consolidationGapsWords(last)).toBe("3 notes to understand again next time");
    expect(consolidationGapsWords({ ...last, left_out: { summaries: 2, labels: 0, reinterpret: 3, reasons: [] } })).toBe("2 summaries to try again next time and 3 notes to understand again next time");
  });
  it("the last consolidation in one line, and what is waiting", () => {
    const now = Date.parse("2026-10-20T10:00:00");
    const last = { finished_at: new Date(Date.parse("2026-10-19T03:12:00")).toISOString(), status: "completed", merges: 17, communities: 35, labelled: 12, summaries_refreshed: 140, insights: 9 };
    expect(lastConsolidationLine(last, now)).toBe("Yesterday: 17 entities joined, 35 themes, 12 named, 140 summaries refreshed, 9 insights.");
    expect(lastConsolidationLine(null, now)).toBe("Not consolidated yet.");
    expect(lastConsolidationLine({ ...last, status: "failed" }, now)).toMatch(/^Yesterday: it stopped before the end/);
    expect(lastConsolidationLine({ finished_at: null, status: "interrupted", merges: null, communities: null, labelled: null, summaries_refreshed: null, insights: null }, now)).toBe("Last time: it stopped before the end.");
    expect(pendingConsolidationLine({ unlabelled: 1, stale_labels: 2, stale_summaries: 88 })).toBe("1 theme to name · 2 themes changed since named · 88 summaries to refresh");
  });
  it("a consolidation that left something out says so, subtly (ADR 0007)", () => {
    const now = Date.parse("2026-10-20T10:00:00");
    const notice = "12 summaries could not be refreshed this time; they will be tried again next time.";
    const last = { finished_at: new Date(Date.parse("2026-10-19T03:12:00")).toISOString(), status: "completed_with_gaps", merges: 0, communities: 3, labelled: 0, summaries_refreshed: 80, insights: 0, left_out: { summaries: 12, labels: 0, reasons: ["x"] }, budget_spent: false, notice };
    expect(lastConsolidationLine(last, now)).toBe("Yesterday: 3 themes, 80 summaries refreshed.");
    expect(consolidationNotice(last)).toBe(notice);
    expect(consolidationNotice({ ...last, status: "completed" })).toBe("");
    const c = { automation: { id: "c", state: "active", paused: false, healthy: true, reason: null }, run: null, last, pending: { stale_labels: 0, unlabelled: 0, stale_summaries: 12 } };
    expect(stateLine(processing({ active: [], queue: { ...processing().queue, queued: 0 }, consolidation: c })).text).toBe("Consolidated with gaps · 12 summaries to try again next time");
    expect(stateLine(processing({ active: [], queue: { ...processing().queue, queued: 0 }, consolidation: { ...c, last: { ...last, status: "completed" } } })).text).toBe("Up to date · everything you sent is understood");
  });
  it("the Processing page shows the notice under the last-run line", () => {
    const src = readFileSync(new URL("../src/views/Processing.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/lastConsolidationLine\(c\.last, now\)\}<\/p>\n\s*\{consolidationNotice\(c\.last\) \? <p className="nt-muted[^"]*">\{consolidationNotice\(c\.last\)\}<\/p> : null\}/);
  });
});

describe("the mock's GET /note/processing", () => {
  let server: http.Server;
  let base = "";
  let gw: { stop: () => void; state: { proc: { drain: unknown } } };
  const H = { Authorization: `Bearer ${MOCK_TOKEN}`, "Content-Type": "application/json" };
  beforeAll(async () => {
    gw = createMockGateway({ processDelayMs: 50, liveProcessing: true, stepMs: 120 });
    server = http.createServer((gw as unknown as { handler: http.RequestListener }).handler);
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
    base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/gateway/`;
  });
  afterAll(() => {
    gw.stop();
    server.close();
  });

  it("serves the contract's keys with an ETag and answers 304 when nothing changed", async () => {
    gw.state.proc.drain = null; // hold the simulation still for the 304
    const r = await fetch(`${base}note/processing`, { headers: H });
    const body = await r.json();
    expect(Object.keys(body).sort()).toEqual(["active", "consolidation", "failed", "model", "now", "pace", "processor", "queue", "recent", "reflect"]);
    expect(Object.keys(body.queue).sort()).toEqual(["by_priority", "done", "eta_s", "failed", "oldest_enqueued_at", "processing", "queued"]);
    expect(body.pace.series).toHaveLength(24);
    expect(body.failed).toHaveLength(2);
    expect(Object.keys(body.consolidation).sort()).toEqual(["automation", "last", "pending", "run"]);
    const etag = r.headers.get("etag");
    expect(etag).toBeTruthy();
    const again = await fetch(`${base}note/processing`, { headers: { ...H, "If-None-Match": etag! } });
    expect(again.status).toBe(304);
  });

  it("the live simulation advances a note through its steps; Move to front puts a note first", async () => {
    await fetch(`${base}automations/au_note_processor/commands`, { method: "POST", headers: H, body: JSON.stringify({ command_id: "x1", type: "automation.run_now" }) });
    const steps = new Set<string>();
    for (let i = 0; i < 30 && steps.size < 3; i++) {
      const b = await (await fetch(`${base}note/processing`, { headers: H })).json();
      for (const a of b.active) if (a.flow === "note-process" && a.step) steps.add(a.step);
      await new Promise((r) => setTimeout(r, 60));
    }
    expect(steps.size).toBeGreaterThanOrEqual(3);
    const q = await (await fetch(`${base}note/queue`, { headers: H })).json();
    const last = q.waiting[q.waiting.length - 1];
    const moved = await fetch(`${base}note/queue/${last.note_id}/priority`, { method: "POST", headers: H, body: JSON.stringify({ priority: 3 }) });
    expect(await moved.json()).toEqual({ note_id: last.note_id, priority: 3 });
    const after = await (await fetch(`${base}note/queue`, { headers: H })).json();
    expect(after.waiting.findIndex((w: { note_id: string }) => w.note_id === last.note_id)).toBeLessThan(2);
  });
});
