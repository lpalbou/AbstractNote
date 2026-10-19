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
