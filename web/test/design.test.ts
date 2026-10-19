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
import { proposedMatches } from "../src/lib/review";
import { stepWords } from "../src/lib/runs";
import { WHY_WORDS, basisLine, whyLine } from "../src/lib/why";
// @ts-expect-error plain ESM module
import { MOCK_TOKEN, createMockGateway } from "../mock/server.mjs";

describe("review inbox", () => {
  const rev = (id: string, at: string, ops: Array<Record<string, unknown>>) => ({ revision_id: id, note_id: `nt_${id}`, at, recorded_at: at, summary: `summary ${id}`, ops });
  it("lists the merges no later unmerge undid, newest first", () => {
    const revs = [
      rev("r1", "2026-10-01T00:00:00Z", [{ op: "entity.merge", from_id: "a", into_id: "b", reason: "same" }]),
      rev("r2", "2026-10-02T00:00:00Z", [{ op: "entity.merge", from_id: "c", into_id: "d", reason: "same too" }, { op: "claim.add", claim_id: "x" }]),
      rev("r3", "2026-10-03T00:00:00Z", [{ op: "entity.unmerge", entity_id: "b", from_id: "a" }]),
    ];
    expect(proposedMatches(revs).map((m) => [m.from_id, m.into_id, m.note_id, m.reason])).toEqual([["c", "d", "nt_r2", "same too"]]);
    expect(proposedMatches(revs.slice(0, 2)).map((m) => m.key)).toEqual(["c>d", "a>b"]);
    expect(proposedMatches([])).toEqual([]);
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
  it("cues are unacknowledged insights; a merged entry resolves to its survivor; a merge can be undone", async () => {
    server = http.createServer(createMockGateway({ processDelayMs: 50 }).handler);
    await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
    base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    try {
      const cues = await get("cues?limit=3");
      const insights = await get("insights?acked=false");
      const ids = new Set(insights.items.map((i: { note_id: string }) => i.note_id));
      expect(cues.items.length).toBe(3);
      for (const c of cues.items) expect(ids.has(c.cue_id)).toBe(true);
      const card = await get("entities/ex%3Aperson-dana-r");
      expect(card).toMatchObject({ entity_id: "ex:person-dana", resolved_from: "ex:person-dana-r" });
      expect(card.summary_history.every((h: { note_id?: string }) => typeof h.note_id === "string")).toBe(true);
      const matches = proposedMatches((await get("revisions?limit=200")).items);
      expect(matches.map((m) => [m.from_id, m.into_id])).toEqual([["ex:person-dana-r", "ex:person-dana"]]);
      const undo = await fetch(`${base}/api/gateway/note/entities/ex%3Aperson-dana/unmerge`, { method: "POST", headers: H, body: JSON.stringify({ from_id: "ex:person-dana-r", reason: "test" }) });
      expect(undo.status).toBe(200);
      expect(proposedMatches((await get("revisions?limit=200")).items)).toEqual([]);
    } finally {
      server.close();
    }
  });
});
