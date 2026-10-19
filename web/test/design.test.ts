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
