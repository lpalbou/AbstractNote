/**
 * The review inbox: `GET /note/review` (docs/api.md) lists what the notebook proposed or did on its own and a
 * person should look at: close matches (`unsure_match`), merges it made (`merged_by_model`) and disagreements it
 * kept (`disputed_pair`), each with the two sides, the notes behind them and the actions to take. "Keep" is
 * `POST /note/review/{id}/confirm` (recorded on the server for the signed-in account), "Undo" the item's
 * `unmerge` action. Exact fields and ids, never text.
 */

import type { Api } from "./api";
import type { ListOf, ReviewAction, ReviewItem } from "./types";

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
