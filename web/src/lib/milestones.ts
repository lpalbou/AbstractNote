/**
 * "How it evolved": the topic portrait's milestone list (an entity's dated views and the changes between
 * them, oldest first), each opening the note it came from. Built only from what the entity card, its
 * then-versus-now and its timeline already carry:
 *   - the summary history (`summary_history[{at, summary, note_id?}]`): earlier view, refinements, latest
 *     expressed view;
 *   - the turning points (a belief replaced or withdrawn), with the note and the claim behind them;
 *   - the first note that mentions it.
 */

import type { ThenNowView } from "./shapes";
import type { EntityCard, TimelineItem } from "./types";

export type MilestoneKind = "first" | "earlier" | "refined" | "latest" | "changed";
export type Milestone = { at: string; kind: MilestoneKind; label: string; text: string; note_id?: string; claim_id?: string };

export const MILESTONE_LABELS: Record<MilestoneKind, string> = {
  first: "First noted",
  earlier: "Earlier view",
  refined: "Refined",
  latest: "Latest expressed view",
  changed: "Changed",
};

const t = (iso: string) => {
  const v = Date.parse(iso);
  return Number.isFinite(v) ? v : 0;
};

export function milestones(card: Pick<EntityCard, "summary_history"> | null | undefined, thenNow: ThenNowView | null | undefined, timeline: TimelineItem[] | null | undefined): Milestone[] {
  const out: Milestone[] = [];
  const history = (card?.summary_history || []).filter((h) => h && h.summary);
  history.forEach((h, i) => {
    const kind: MilestoneKind = i === history.length - 1 ? "latest" : i === 0 ? "earlier" : "refined";
    out.push({ at: h.at, kind, label: MILESTONE_LABELS[kind], text: h.summary, note_id: h.note_id || undefined });
  });
  for (const turn of thenNow?.turning || []) {
    out.push({ at: turn.at, kind: "changed", label: MILESTONE_LABELS.changed, text: turn.text, note_id: turn.note_id, claim_id: turn.claim_id });
  }
  const firstNote = (timeline || []).filter((i) => i.type === "note" && i.note).sort((a, b) => t(a.at) - t(b.at))[0];
  if (firstNote?.note && !out.some((m) => m.note_id === firstNote.note!.note_id)) {
    out.push({ at: firstNote.at, kind: "first", label: MILESTONE_LABELS.first, text: firstNote.note.title, note_id: firstNote.note.note_id });
  }
  // Oldest first; on the same moment the first mention leads and the latest view closes.
  const order: Record<MilestoneKind, number> = { first: 0, earlier: 1, refined: 2, changed: 3, latest: 4 };
  return out.sort((a, b) => t(a.at) - t(b.at) || order[a.kind] - order[b.kind]);
}
