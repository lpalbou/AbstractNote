/**
 * The review inbox reads matches the notebook made on its own from the revision feed
 * (`GET /note/revisions`): every `entity.merge` op ({from_id, into_id, reason}) that no later
 * `entity.unmerge` op ({entity_id, from_id}) undid. Exact op names and ids, never text.
 *
 * The note API has no review endpoint yet (no "unsure" matches, no closeMatch pairs, no way to confirm a
 * match on the server): "Keep" is remembered in this browser only (W9 report, "Endpoint gaps").
 */

import type { Revision } from "./types";

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
