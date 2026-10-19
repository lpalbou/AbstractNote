/**
 * Remember, organised by what you want to remember and how (operator, 2026-10-19): seven panels in a rail, one
 * shown at a time, the ask bar always above them. Pure rules here (tested): the panels, the keys that switch
 * them, the badge counts, the grouping of "Worth a thought", and the age of an open loop in words.
 */

import type { Changes, Insight, NoteRef, ReviewItem } from "./types";

export type PanelId = "changed" | "ask" | "thoughts" | "thennow" | "loops" | "review" | "themes";
export type PanelIcon = "activity" | "chat" | "sparkle" | "history" | "loop" | "inbox" | "board";
export const PANELS: Array<{ id: PanelId; label: string; icon: PanelIcon; sub: string }> = [
  { id: "changed", label: "What changed", icon: "activity", sub: "since you last looked" },
  { id: "ask", label: "Ask", icon: "chat", sub: "a question to your notes" },
  { id: "thoughts", label: "Worth a thought", icon: "sparkle", sub: "questions, suggestions, tensions, ideas and reminders" },
  { id: "thennow", label: "Then and now", icon: "history", sub: "how your understanding changed" },
  { id: "loops", label: "Open loops", icon: "loop", sub: "what you said you would do" },
  { id: "review", label: "To review", icon: "inbox", sub: "what your notebook decided on its own" },
  { id: "themes", label: "Themes", icon: "board", sub: "what comes up together" },
];
export const DEFAULT_PANEL: PanelId = "changed";

export function panelOf(value: string | null | undefined): PanelId {
  return (PANELS.find((p) => p.id === value)?.id as PanelId) || DEFAULT_PANEL;
}

/** Keys while Remember is open: 1–7 choose a panel, "/" goes to the question. Never while typing in a field. */
export function keyAction(key: string, target: { tagName?: string; isContentEditable?: boolean } | null, mods: { ctrl?: boolean; meta?: boolean; alt?: boolean } = {}): { panel: PanelId } | { focusAsk: true } | null {
  if (mods.ctrl || mods.meta || mods.alt) return null;
  const tag = String(target?.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) return null;
  if (key === "/") return { focusAsk: true };
  const n = Number(key);
  if (Number.isInteger(n) && n >= 1 && n <= PANELS.length && key.length === 1) return { panel: PANELS[n - 1].id };
  return null;
}

/** How many things "What changed" lists: one row per item (the new notes, entities, beliefs replaced, loops closed,
 * notes needing attention, insights not shown in "Worth a thought"). */
export function changeCount(c: Changes | null | undefined): number {
  if (!c) return 0;
  return (c.notes?.length || 0) + (c.entities?.length || 0) + (c.superseded_claims?.length || 0) + (c.closed_loops?.length || 0) + (c.failed_notes?.length || 0) + (c.insights?.length || 0);
}

export type ThoughtGroup = "questions" | "suggestions" | "tensions" | "ideas" | "reminders";
export const THOUGHT_GROUPS: Array<{ id: ThoughtGroup; label: string; pill: string }> = [
  { id: "questions", label: "Questions", pill: "Question" },
  { id: "suggestions", label: "Suggestions", pill: "Suggestion" },
  { id: "tensions", label: "Tensions", pill: "Tension" },
  { id: "ideas", label: "Ideas to develop", pill: "Idea" },
  { id: "reminders", label: "Reminders", pill: "Reminder" },
];
/**
 * Where an insight goes, from its stored `insight_kind` (docs/data-model.md): a fixed table, never its text.
 * cue (someone or something went quiet) asks a question; connection suggests; contradiction is a tension;
 * evolution and dossier are ideas to develop; reminder and warning remind.
 */
export const KIND_GROUP: Record<string, ThoughtGroup> = {
  cue: "questions",
  connection: "suggestions",
  contradiction: "tensions",
  evolution: "ideas",
  dossier: "ideas",
  reminder: "reminders",
  warning: "reminders",
};
export function thoughtGroup(kind: string | null | undefined): ThoughtGroup {
  return KIND_GROUP[String(kind || "")] || "suggestions";
}

export type Thought =
  | { type: "insight"; id: string; group: ThoughtGroup; insight: Insight }
  | { type: "tension"; id: string; group: "tensions"; item: ReviewItem }
  | { type: "resurface"; id: string; group: "suggestions"; note: NoteRef };

/** "Worth a thought": unread insights by kind, the disagreements the notebook kept (tensions), and notes worth
 * another look (suggestions); each thing once, in the order of THOUGHT_GROUPS, newest first inside a group. */
export function groupThoughts(insights: Insight[], review: ReviewItem[], resurfacing: NoteRef[]): Array<{ group: (typeof THOUGHT_GROUPS)[number]; items: Thought[] }> {
  const seen = new Set<string>();
  const all: Array<Thought & { at: string }> = [];
  for (const i of insights) {
    if (seen.has(i.note_id)) continue;
    seen.add(i.note_id);
    all.push({ type: "insight", id: i.note_id, group: thoughtGroup(i.insight_kind), insight: i, at: i.occurred_at });
  }
  for (const r of review) {
    if (r.kind !== "contradiction" || seen.has(r.review_id)) continue;
    seen.add(r.review_id);
    all.push({ type: "tension", id: r.review_id, group: "tensions", item: r, at: r.at });
  }
  for (const n of resurfacing) {
    if (seen.has(n.note_id)) continue;
    seen.add(n.note_id);
    all.push({ type: "resurface", id: n.note_id, group: "suggestions", note: n, at: n.occurred_at });
  }
  return THOUGHT_GROUPS.map((group) => ({
    group,
    items: all.filter((t) => t.group === group.id).sort((a, b) => Date.parse(b.at) - Date.parse(a.at)),
  })).filter((g) => g.items.length);
}

/** "open 3 days", "open 5 weeks", "open 4 months"; from the server's age_days, else from when it was opened. */
export function ageWords(ageDays: number | null | undefined): string {
  const d = Math.max(0, Math.floor(Number(ageDays) || 0));
  if (d < 1) return "opened today";
  if (d < 14) return `open ${d} ${d === 1 ? "day" : "days"}`;
  if (d < 60) return `open ${Math.round(d / 7)} weeks`;
  return `open ${Math.round(d / 30)} months`;
}
