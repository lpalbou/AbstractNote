import { describe, expect, it } from "vitest";
import { DEFAULT_PANEL, KIND_GROUP, PANELS, THOUGHT_GROUPS, ageWords, changeCount, groupThoughts, keyAction, panelOf, thoughtGroup } from "../src/lib/remember";
import { WHY_WORDS } from "../src/lib/why";
import type { Insight, NoteRef, ReviewItem } from "../src/lib/types";

describe("Remember panels", () => {
  it("seven panels in the operator's order, labels in sentence case", () => {
    expect(PANELS.map((p) => p.label)).toEqual(["What changed", "Ask", "Worth a thought", "Then and now", "Open loops", "To review", "Themes"]);
    for (const p of PANELS) expect(p.label.slice(1)).toBe(p.label.slice(1).toLowerCase());
  });
  it("an unknown or missing panel opens the default", () => {
    expect(panelOf("loops")).toBe("loops");
    expect(panelOf("nope")).toBe(DEFAULT_PANEL);
    expect(panelOf(null)).toBe(DEFAULT_PANEL);
  });
  it("keys 1–7 choose a panel and / goes to the question, never while typing or with a modifier", () => {
    const body = { tagName: "BODY" };
    PANELS.forEach((p, i) => expect(keyAction(String(i + 1), body)).toEqual({ panel: p.id }));
    expect(keyAction("8", body)).toBeNull();
    expect(keyAction("0", body)).toBeNull();
    expect(keyAction("/", body)).toEqual({ focusAsk: true });
    expect(keyAction("3", { tagName: "INPUT" })).toBeNull();
    expect(keyAction("/", { tagName: "TEXTAREA" })).toBeNull();
    expect(keyAction("2", { tagName: "SELECT" })).toBeNull();
    expect(keyAction("2", { tagName: "DIV", isContentEditable: true })).toBeNull();
    expect(keyAction("2", body, { meta: true })).toBeNull();
    expect(keyAction("x", body)).toBeNull();
  });
});

describe("badge counts", () => {
  it("What changed counts one per row", () => {
    expect(changeCount(null)).toBe(0);
    expect(changeCount({ cursor: "c", notes: [{} as NoteRef, {} as NoteRef], entities: [{ entity_id: "e", name: "E", kind: "person", first_seen: "" }], superseded_claims: [], closed_loops: [{ task_id: "t", text: "x", closed_at: "" }], failed_notes: [], insights: [{} as Insight] })).toBe(5);
  });
});

const ins = (id: string, kind: string, at = "2026-10-01T00:00:00Z") => ({ note_id: id, title: id, body: "", insight_kind: kind, occurred_at: at, acked_at: null, evidence_note_ids: [], entity_ids: [] }) as Insight;
const rev = (id: string, kind: ReviewItem["kind"]) => ({ review_id: id, kind, why: "disputed_pair", reason: null, at: "2026-10-02T00:00:00Z", sides: [], evidence_note_ids: [], actions: [] }) as ReviewItem;
const note = (id: string) => ({ note_id: id, title: id, kind: "idea", occurred_at: "2026-08-01T00:00:00Z" }) as NoteRef;

describe("Worth a thought", () => {
  it("every insight kind the data model names has a group (a fixed table, never the text)", () => {
    for (const k of Object.keys(WHY_WORDS)) expect(Object.keys(KIND_GROUP)).toContain(k);
    expect(thoughtGroup("cue")).toBe("questions");
    expect(thoughtGroup("contradiction")).toBe("tensions");
    expect(thoughtGroup("reminder")).toBe("reminders");
    expect(thoughtGroup("evolution")).toBe("ideas");
    expect(thoughtGroup("something-new")).toBe("suggestions");
  });
  it("groups in the operator's order, empty groups left out, contradictions to review are tensions, each thing once", () => {
    const g = groupThoughts([ins("a", "reminder"), ins("b", "cue"), ins("c", "contradiction"), ins("b", "cue")], [rev("r1", "contradiction"), rev("r2", "merge")], [note("n1"), note("a")]);
    expect(g.map((x) => x.group.id)).toEqual(["questions", "suggestions", "tensions", "reminders"]);
    expect(g.find((x) => x.group.id === "tensions")!.items.map((t) => t.id).sort()).toEqual(["c", "r1"]);
    expect(g.flatMap((x) => x.items).map((t) => t.id).sort()).toEqual(["a", "b", "c", "n1", "r1"]);
    expect(THOUGHT_GROUPS.map((x) => x.label)).toEqual(["Questions", "Suggestions", "Tensions", "Ideas to develop", "Reminders"]);
  });
  it("newest first inside a group", () => {
    const g = groupThoughts([ins("old", "reminder", "2026-01-01T00:00:00Z"), ins("new", "warning", "2026-10-01T00:00:00Z")], [], []);
    expect(g[0].items.map((t) => t.id)).toEqual(["new", "old"]);
  });
});

describe("open loops", () => {
  it("their age in words", () => {
    expect(ageWords(0)).toBe("opened today");
    expect(ageWords(1)).toBe("open 1 day");
    expect(ageWords(10)).toBe("open 10 days");
    expect(ageWords(21)).toBe("open 3 weeks");
    expect(ageWords(87)).toBe("open 3 months");
    expect(ageWords(undefined)).toBe("opened today");
  });
});
