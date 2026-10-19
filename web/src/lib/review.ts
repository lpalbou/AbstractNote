/**
 * The review inbox: `GET /note/review` (docs/api.md) lists what the notebook proposed or did on its own and a
 * person should look at: close matches (`unsure_match`), merges it made (`merged_by_model`) and disagreements it
 * kept (`disputed_pair`), each with the two sides, the notes behind them and the actions to take. "Keep" is
 * `POST /note/review/{id}/confirm` (recorded on the server for the signed-in account), "Undo" the item's
 * `unmerge` action. Exact fields and ids, never text.
 *
 * `proposedMatches` (merges read from the revision feed, "Keep" per browser) is the earlier workaround; it
 * stays until the view reads `fetchReview` (W10 report).
 */

import type { Api } from "./api";
import type { ListOf, Revision, ReviewAction, ReviewItem } from "./types";

/** `GET /note/review` (newest first). */
export function fetchReview(api: Api, limit = 50): Promise<ListOf<ReviewItem>> {
  return api.get<ListOf<ReviewItem>>("note/review", { limit });
}

/** The item's action by name (`confirm`, `keep_apart`, `unmerge`, `retract`), or null when it has none. */
export function reviewAction(item: ReviewItem, name: string): ReviewAction | null {
  return item.actions.find((a) => a.action === name) || null;
}

/**
 * Take one of the item's actions: its `path` is under `/api/gateway` (`/note/...`), its `body` as served, with
 * `reason` filled in where the server left it null (a retraction says why).
 */
export function runReviewAction<T = unknown>(api: Api, action: ReviewAction, reason?: string): Promise<T> {
  const body: Record<string, unknown> = { ...action.body };
  if ("reason" in body && body.reason === null) body.reason = reason ?? "";
  return api.post<T>(action.path.replace(/^\//, ""), body);
}

/** A merge item in the earlier `ProposedMatch` form (the view's current shape). */
export function asProposedMatch(item: ReviewItem): ProposedMatch | null {
  const unmerge = reviewAction(item, "unmerge");
  if (item.kind !== "merge" || !unmerge || item.sides.length !== 2) return null;
  const [from, into] = item.sides;
  return { key: item.review_id, from_id: from.id, into_id: into.id, reason: item.reason || "", revision_id: "", note_id: item.evidence_note_ids[0] ?? null, at: item.at, summary: "" };
}

export type ProposedMatch = { key: string; from_id: string; into_id: string; reason: string; revision_id: string; note_id: string | null; at: string; summary: string };

type Op = Record<string, unknown>;
const s = (v: unknown) => (typeof v === "string" ? v : "");

export function proposedMatches(revisions: Revision[]): ProposedMatch[] {
  const byTime = [...revisions].sort((a, b) => Date.parse(a.recorded_at || a.at) - Date.parse(b.recorded_at || b.at));
  const live = new Map<string, ProposedMatch>();
  for (const r of byTime) {
    for (const op of (r.ops || []) as Op[]) {
      if (op.op === "entity.merge" && s(op.from_id) && s(op.into_id)) {
        const key = `${s(op.from_id)}>${s(op.into_id)}`;
        live.set(key, { key, from_id: s(op.from_id), into_id: s(op.into_id), reason: s(op.reason), revision_id: r.revision_id, note_id: r.note_id ?? null, at: r.at, summary: r.summary || "" });
      } else if (op.op === "entity.unmerge" && s(op.from_id) && s(op.entity_id)) {
        live.delete(`${s(op.from_id)}>${s(op.entity_id)}`);
      }
    }
  }
  return [...live.values()].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}
