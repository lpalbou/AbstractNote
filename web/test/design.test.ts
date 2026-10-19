/**
 * Web 0025 (design pass): the capture acknowledgement's states and line, the milestones of "How it
 * evolved", the review inbox's proposed matches, the run progress lines and "why this surfaced".
 * Each check goes red when the thing it checks is removed.
 */
import { describe, expect, it } from "vitest";
import { ACK_WORDS, ackState, ackSummary, pendingLine } from "../src/lib/ack";

describe("capture acknowledgement", () => {
  it("shows one state at a time: Sending -> Received -> Organizing -> Organized | Needs attention", () => {
    expect(ackState({ state: "uploading" })).toBe("sending");
    expect(ackState({ state: "saving" })).toBe("sending");
    expect(ackState({ state: "saving", answer: "Saved as idea." })).toBe("received");
    expect(ackState({ state: "queued", noteId: "nt_x" })).toBe("organizing");
    expect(ackState({ state: "processed", noteId: "nt_x" })).toBe("organized");
    expect(ackState({ state: "duplicate", noteId: "nt_x" })).toBe("organized");
    expect(ackState({ state: "failed" })).toBe("attention");
    expect(Object.values(ACK_WORDS)).toEqual(["Sending", "Received", "Organizing", "Organized", "Needs attention"]);
  });

  it("the organized line is short and built from the note's fields", () => {
    const note = { kind: "event", topics: ["#lut"], entities: [{ entity_id: "a", name: "Dana", kind: "person" }, { entity_id: "b", name: "LUT", kind: "project" }] };
    expect(ackSummary(note)).toBe("Added to #lut · event · 2 links");
    expect(ackSummary({ ...note, topics: [] })).toBe("Added to Dana · event · 2 links");
    expect(ackSummary({ kind: "idea", topics: [], entities: [] })).toBe("Added to your notebook · idea · no links yet");
    expect(ackSummary(note, true)).toBe("Already in your notebook · event");
    expect(ackSummary(null)).toBe("");
    expect(pendingLine({ state: "uploading", files: [{ name: "a", size: 1, type: "" }, { name: "b", size: 1, type: "" }] })).toBe("Adding your files");
  });
});

import { milestones } from "../src/lib/milestones";
import { localDay } from "../src/components/TimeScrubber";

describe("How it evolved", () => {
  const card = {
    summary_history: [
      { at: "2026-07-23T14:30:00Z", summary: "Led from Lyon.", note_id: "nt_a" },
      { at: "2026-09-02T09:05:00Z", summary: "Moved to Paris.", note_id: "nt_b" },
    ],
  };
  const tn = { earliest: null, latest: null, turning: [{ at: "2026-09-02T09:05:00Z", text: "Dana work location: Lyon → Paris", note_id: "nt_b", claim_id: "cl_1" }] };
  const timeline = [
    { type: "note" as const, at: "2026-07-21T09:00:00Z", note: { note_id: "nt_0", title: "First mention", kind: "idea", occurred_at: "2026-07-21T09:00:00Z" } },
    { type: "note" as const, at: "2026-07-23T14:30:00Z", note: { note_id: "nt_a", title: "Kickoff", kind: "event", occurred_at: "2026-07-23T14:30:00Z" } },
  ];

  it("lists the first note, the dated views and the changes, oldest first, each with its note", () => {
    const ms = milestones(card, tn, timeline);
    expect(ms.map((m) => [m.kind, m.label, m.note_id])).toEqual([
      ["first", "First noted", "nt_0"],
      ["earlier", "Earlier view", "nt_a"],
      ["changed", "Changed", "nt_b"],
      ["latest", "Latest expressed view", "nt_b"],
    ]);
    expect(ms[2].claim_id).toBe("cl_1");
  });

  it("a single dated view is the latest; nothing known gives nothing", () => {
    expect(milestones({ summary_history: [card.summary_history[0]] }, null, []).map((m) => m.kind)).toEqual(["latest"]);
    expect(milestones(null, null, null)).toEqual([]);
  });

  it("the date field works in local days", () => {
    const end = new Date("2026-08-20T23:59:59").toISOString();
    expect(localDay(end)).toBe("2026-08-20");
  });
});

import * as http from "node:http";
import { fetchReview, reviewAction, runReviewAction } from "../src/lib/review";
import { createApi } from "../src/lib/api";
import { momentParams } from "../src/lib/nav";
import { queueSentence } from "../src/views/Settings";
import { reviewWhy } from "../src/lib/why";
import { stepWords } from "../src/lib/runs";
import { WHY_WORDS, basisLine, whyLine } from "../src/lib/why";
// @ts-expect-error plain ESM module
import { MOCK_TOKEN, createMockGateway } from "../mock/server.mjs";

describe("review inbox", () => {
  const item = {
    review_id: "rq_1", kind: "close_match" as const, why: "unsure_match" as const, reason: null, at: "2026-10-15T00:00:00Z",
    sides: [], evidence_note_ids: [],
    actions: [
      { action: "confirm", method: "POST" as const, path: "/note/review/rq_1/confirm", body: {} },
      { action: "keep_apart", method: "POST" as const, path: "/note/claims/cl_1/retract", body: { reason: null } },
    ],
  };
  it("takes the item's own actions: path under the api, a null reason filled by the person", async () => {
    const sent: Array<{ url: string; body: unknown }> = [];
    const fetch = async (url: RequestInfo | URL, init?: RequestInit) => {
      sent.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null });
      return new Response("{}");
    };
    const api = createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined, csrf: () => "c" });
    await runReviewAction(api, reviewAction(item, "keep_apart")!, "Not the same.");
    await runReviewAction(api, reviewAction(item, "confirm")!);
    expect(sent.map((x) => [x.url.replace(/^.*api\/gateway\//, ""), x.body])).toEqual([["note/claims/cl_1/retract", { reason: "Not the same." }], ["note/review/rq_1/confirm", {}]]);
    expect(reviewAction(item, "unmerge")).toBeNull();
    expect(reviewWhy("merged_by_model")).toMatch(/on its own/);
    expect(reviewWhy("something new")).toBeTruthy();
  });
  it("Explore reads a date as validity or as the archive snapshot", () => {
    expect(momentParams(null, true)).toEqual({});
    expect(momentParams("2026-08-20T21:59:59Z", false)).toEqual({ at: "2026-08-20T21:59:59Z" });
    expect(momentParams("2026-08-20T21:59:59Z", true)).toEqual({ as_received: "2026-08-20T21:59:59Z" });
  });
  it("the queue sentence says what the processor runs with, not a number written in the app", () => {
    const q = { stats: { queued: 0, processing: 0, failed: 0 }, failed: [], waiting: [], automations: { processor_id: "p", reflect_id: "r" }, processor: { max_items: 5, interval: "10m" } };
    expect(queueSentence(q)).toBe("Nothing is waiting. Up to 5 notes are processed every 10 minutes.");
  });
});

describe("progress and why", () => {
  const rec = (type: string, payload: unknown = {}, status = "completed") => ({ status, effect: { type, payload } });
  it("names the steps of an answer from the effect type and the tool names only", () => {
    expect(stepWords(rec("tool_calls", { tool_calls: [{ name: "note_search" }] }))).toBe("Searched your notes");
    expect(stepWords(rec("tool_calls", { tool_calls: [{ name: "note_expand" }] }))).toBe("Looked closer at what they connect");
    expect(stepWords(rec("tool_calls", { tool_calls: [{ name: "something_else" }] }))).toBeNull();
    expect(stepWords(rec("llm_call"))).toBe("Wrote the answer");
    expect(stepWords(rec("start_subworkflow"))).toBe("Dug through your history");
    expect(stepWords(rec("llm_call", {}, "running"))).toBeNull();
    expect(stepWords(rec("answer_user"))).toBeNull();
  });
  it("says why each insight kind surfaced, with a fallback", () => {
    for (const kind of ["warning", "reminder", "connection", "contradiction", "evolution", "dossier", "cue"]) expect(WHY_WORDS[kind], kind).toBeTruthy();
    expect(whyLine("something new")).toMatch(/look back/);
    expect(basisLine(1)).toBe("Based on 1 note");
    expect(basisLine(3)).toBe("Based on 3 notes");
    expect(basisLine(0)).toBe("");
  });
});

describe("mock gateway shapes the design pass reads", () => {
  let server: http.Server;
  let base = "";
  const H = { Authorization: `Bearer ${MOCK_TOKEN}`, "Content-Type": "application/json" };
  const get = async (p: string) => (await fetch(`${base}/api/gateway/note/${p}`, { headers: H })).json();
  it("cues are unacknowledged insights with their evidence; a merged entry resolves to its survivor; review actions settle items", async () => {
    server = http.createServer(createMockGateway({ processDelayMs: 50 }).handler);
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
    base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    try {
      const cues = await get("cues?limit=3");
      const insights = await get("insights?acked=false");
      const ids = new Set(insights.items.map((i: { note_id: string }) => i.note_id));
      expect(cues.items.length).toBe(3);
      for (const c of cues.items) expect(ids.has(c.cue_id)).toBe(true);
      expect(cues.items.every((c: { evidence_note_ids: string[] }) => c.evidence_note_ids.length > 0)).toBe(true);
      const card = await get("entities/ex%3Aperson-dana-r");
      expect(card).toMatchObject({ entity_id: "ex:person-dana", resolved_from: "ex:person-dana-r" });
      expect(card.summary_history.every((h: { note_id?: string }) => typeof h.note_id === "string")).toBe(true);
      const api = createApi({ fetch: ((u: RequestInfo | URL, init?: RequestInit) => fetch(String(u).replace(/^api\/gateway/, `${base}/api/gateway`), { ...init, headers: H })) as typeof globalThis.fetch, sleep: async () => undefined, csrf: () => "c" });
      const kinds = (await fetchReview(api)).items.map((i) => i.kind).sort();
      expect(kinds).toEqual(["close_match", "contradiction", "merge"]);
      const merge = (await fetchReview(api)).items.find((i) => i.kind === "merge")!;
      expect(merge.sides.map((x) => x.id)).toEqual(["ex:person-dana-r", "ex:person-dana"]);
      const own = await get("entities/ex%3Aperson-dana-r?as_merged=true");
      expect(own).toMatchObject({ name: "Dana R.", merged_into: "ex:person-dana" });
      await runReviewAction(api, reviewAction(merge, "unmerge")!);
      const close = (await fetchReview(api)).items.find((i) => i.kind === "close_match")!;
      await runReviewAction(api, reviewAction(close, "confirm")!);
      expect((await fetchReview(api)).items.map((i) => i.kind)).toEqual(["contradiction"]);
    } finally {
      server.close();
    }
  });
});

import { ApiError, NOT_INSTALLED, errorSentence, noteMount } from "../src/lib/api";

describe("a gateway without AbstractNote", () => {
  /** A fake gateway: path -> status (200 answers {ok: true, principal}). */
  const gatewayWith = (routes: Record<string, number>) => {
    const calls: string[] = [];
    const fetch = async (url: RequestInfo | URL) => {
      const path = String(url).replace(/^api\/gateway\//, "");
      calls.push(path);
      const status = routes[path] ?? 404;
      return new Response(JSON.stringify(status === 200 ? { ok: true, principal: { user_id: "u" } } : { detail: status === 404 ? "Not Found" : "x" }), { status });
    };
    return { calls, api: createApi({ fetch: fetch as unknown as typeof globalThis.fetch, sleep: async () => undefined, csrf: () => "c" }) };
  };
  it("404 on note/status while signed in (GET /me answers) is not installed, decided at once", async () => {
    const g = gatewayWith({ "note/status": 404, me: 200 });
    expect(await noteMount(g.api)).toBe("missing");
    expect(g.calls).toEqual(["note/status", "me"]);
  });
  it("401 is signed out, never not installed (the standalone server proxies without a session)", async () => {
    const g = gatewayWith({ "note/status": 401 });
    expect(await noteMount(g.api)).toBe("signed_out");
    expect(g.calls).toEqual(["note/status"]);
    expect(await noteMount(gatewayWith({ "note/status": 404, me: 401 }).api)).toBe("signed_out");
  });
  it("mounted, or unknown when it cannot be told", async () => {
    expect(await noteMount(gatewayWith({ "note/status": 200 }).api)).toBe("mounted");
    expect(await noteMount(gatewayWith({ "note/status": 503 }).api)).toBe("unknown");
    expect(await noteMount(gatewayWith({ "note/status": 404, me: 503 }).api)).toBe("unknown");
  });
  it("is one sentence, distinct from a note that is really missing", () => {
    expect(NOT_INSTALLED).toBe("AbstractNote is not installed on this gateway.");
    expect(errorSentence(new ApiError(404, "Not found."), "this note")).toBe("This note is not in your notebook.");
    expect(errorSentence(new ApiError(404, "Not found."), "this note")).not.toBe(NOT_INSTALLED);
  });
});

import { MAX_FIT_ZOOM, boundsOf, fitView, settleFrames, toWorld, zoomAt } from "../src/lib/graph_layout";
import { initialSignIn, readRemembered, signInSentence } from "../src/lib/signin";

describe("Explore canvas geometry", () => {
  const four = [
    { id: "a", x: 480, y: 330, r: 12 },
    { id: "b", x: 560, y: 350, r: 12 },
    { id: "c", x: 500, y: 400, r: 12 },
    { id: "d", x: 540, y: 300, r: 12 },
  ];
  it("four nodes fill the view: the fitted box spans the width or the height, centred", () => {
    const b = boundsOf(four)!;
    const v = fitView(b, 900, 600, 28, 10);
    const left = b.minX * v.k + v.x;
    const right = b.maxX * v.k + v.x;
    const top = b.minY * v.k + v.y;
    const bottom = b.maxY * v.k + v.y;
    expect(Math.min(left, 900 - right, top, 600 - bottom)).toBeCloseTo(28, 0);
    expect((left + right) / 2).toBeCloseTo(450, 0);
    expect((top + bottom) / 2).toBeCloseTo(300, 0);
    expect(v.k).toBeGreaterThan(3);
    // The default cap keeps a tiny graph from filling the screen with one circle.
    expect(fitView(b, 900, 600).k).toBe(MAX_FIT_ZOOM);
  });
  it("zooming keeps the point under the pointer still, within the zoom limits", () => {
    const v = { k: 1.5, x: 40, y: -20 };
    const before = toWorld(v, 300, 200);
    const z = zoomAt(v, 1.7, 300, 200);
    const after = toWorld(z, 300, 200);
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
    expect(zoomAt(v, 100, 0, 0).k).toBe(4);
    expect(zoomAt(v, 0.0001, 0, 0).k).toBe(0.2);
  });
  it("a dropped node stays where it was put while the others settle", () => {
    const frames = settleFrames(four, [{ source: "a", target: "b" }], { id: "a", x: 100, y: 100 }, 4, 10);
    expect(frames.length).toBe(4);
    for (const f of frames) expect(f.find((n) => n.id === "a")).toMatchObject({ x: 100, y: 100 });
    expect(frames[3].find((n) => n.id === "b")).not.toEqual(four[1]);
  });
});

describe("sign-in dialog defaults", () => {
  it("opens on the server's own address and the remembered account", () => {
    expect(initialSignIn(null, "http://127.0.0.1:18080/")).toEqual({ gatewayUrl: "http://127.0.0.1:18080", user: "" });
    const r = { gateway_url: "http://127.0.0.1:8080", user: "laurent", server_url: "http://127.0.0.1:8080" };
    // The server moved: never default to the stale address (the server would refuse it), keep the account.
    expect(initialSignIn(r, "http://127.0.0.1:18080")).toEqual({ gatewayUrl: "http://127.0.0.1:18080", user: "laurent" });
    expect(initialSignIn(r, "http://127.0.0.1:8080")).toEqual({ gatewayUrl: "http://127.0.0.1:8080", user: "laurent" });
    expect(initialSignIn(null, null, "http://h:1")).toEqual({ gatewayUrl: "http://h:1", user: "" });
    expect(readRemembered("{broken")).toBeNull();
  });
  it("says one sentence on a refusal", () => {
    for (const status of [400, 401, 403, 0, 502, 500]) {
      const s = signInSentence(status, "http://x:1", "http://127.0.0.1:18080");
      expect(s.split(/[.!?](\s|$)/).filter((x) => x && x.trim()).length, s).toBe(1);
      expect(/HTTP|\d{3}\b|detail/.test(s.replace("127.0.0.1:18080", "").replace("x:1", "")), s).toBe(false);
    }
    expect(signInSentence(401, "", "")).toMatch(/not accepted/);
  });
});
