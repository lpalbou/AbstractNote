/**
 * Then-versus-now comes in three forms (W1 recall/then_now.py, expand.relation, the recall flow):
 *   entity:   {earliest: {at, summary, note_id} | null, latest, turning_points: [{at, kind, text, note_id, claim_id?}],
 *              earliest_claims: [Claim], current_beliefs: [Claim]}
 *   relation: {earliest: [{claim_id, text, observed_at, note_id}], latest: [...], turning_points: [{at, kind, claim_id, note_id, reason}]}
 *   recall:   {earliest, latest: string | {...}, turning_points: [{note_id, occurred_at, change} | string]}
 * The views render one form: the lines then, the lines now, and the turning points with their notes.
 */

import { claimSentence } from "./words";

export type Side = { at: string; lines: string[] };
export type Turn = { at: string; text: string; note_id?: string; claim_id?: string };
export type ThenNowView = { earliest: Side | null; latest: Side | null; turning: Turn[] };

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

const TURN_WORDS: Record<string, string> = { supersede: "replaced", retract: "withdrawn", dispute: "disputed", merge: "found to be the same", summary: "understanding changed" };

/** A claim-like object as a sentence, from its fields (names), else its `text`. */
function claimLine(c: Obj): string {
  if (typeof c.predicate === "string" && typeof c.subject_id === "string") return claimSentence(c as never);
  return str(c.text) || str(c.summary);
}

function side(v: unknown, claims?: unknown): Side | null {
  if (typeof v === "string") return v.trim() ? { at: "", lines: [v] } : null;
  if (Array.isArray(v)) {
    const lines = v.map((c) => (typeof c === "string" ? c : obj(c) ? claimLine(obj(c)!) : "")).filter(Boolean);
    const at = v.map((c) => str(obj(c)?.observed_at) || str(obj(c)?.at)).find(Boolean) || "";
    return lines.length ? { at, lines } : null;
  }
  const o = obj(v);
  if (o) {
    const line = str(o.summary) || str(o.text);
    if (line) return { at: str(o.at) || str(o.observed_at), lines: [line] };
  }
  return claims !== undefined ? side(claims) : null;
}

export function thenNowView(raw: unknown, claimsById?: Map<string, Obj>): ThenNowView | null {
  const r = obj(raw);
  if (!r) return null;
  const earliest = side(r.earliest, Array.isArray(r.earliest_claims) && r.earliest_claims.length ? r.earliest_claims : undefined);
  const latest = side(r.latest, Array.isArray(r.current_beliefs) && r.current_beliefs.length ? r.current_beliefs : undefined);
  const tps = Array.isArray(r.turning_points) ? r.turning_points : [];
  const turning: Turn[] = tps
    .map((t): Turn | null => {
      if (typeof t === "string") return t.trim() ? { at: "", text: t } : null;
      const o = obj(t);
      if (!o) return null;
      const claim = typeof o.claim_id === "string" ? claimsById?.get(o.claim_id) : undefined;
      const kind = TURN_WORDS[str(o.kind)] || "";
      const base = str(o.text) || str(o.change) || str(o.summary) || (claim ? claimLine(claim) : "");
      const text = base ? (kind && !str(o.text) ? `${base} (${kind})` : base) : kind ? `Something was ${kind}` : "";
      if (!text) return null;
      return { at: str(o.at) || str(o.occurred_at), text, note_id: str(o.note_id) || undefined, claim_id: str(o.claim_id) || undefined };
    })
    .filter((t): t is Turn => Boolean(t));
  if (!earliest && !latest && !turning.length) return null;
  return { earliest, latest, turning };
}

/** Claims by id, for turning points that only name a claim. */
export function claimsById(claims: Array<{ claim_id: string }>): Map<string, Obj> {
  return new Map(claims.map((c) => [c.claim_id, c as unknown as Obj]));
}

/** Entities with one row per entity id (the first one kept): a picker never offers the same entity twice. */
export function uniqueEntities<T extends { entity_id: string }>(entities: T[] | null | undefined): T[] {
  const seen = new Set<string>();
  return (entities || []).filter((e) => (seen.has(e.entity_id) ? false : (seen.add(e.entity_id), true)));
}
