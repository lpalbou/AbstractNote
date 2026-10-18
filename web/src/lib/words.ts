/** Plain words for the notebook's vocabulary (kinds, predicates, dates). */

import { formatRelativeTime, formatShortDate } from "@abstractframework/ui-kit";

export const NOTE_KINDS = ["idea", "task", "decision", "fact", "event", "question", "reflection", "reference", "log", "conversation", "personal"] as const;

/** "schema:workLocation" -> "work location"; "ex:led_by" -> "led by". A label, never a decision. */
export function predicateWords(predicate: string): string {
  const local = String(predicate || "").split(":").pop() || "";
  return local.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().trim() || "related to";
}

export function claimObject(c: { object?: { name: string } | null; object_id?: string | null; object_literal?: string | null }): string {
  return c.object?.name || c.object_literal || c.object_id || "";
}

export function claimSentence(c: { subject?: { name: string }; subject_id: string; predicate: string; object?: { name: string } | null; object_id?: string | null; object_literal?: string | null }): string {
  return `${c.subject?.name || c.subject_id} ${predicateWords(c.predicate)} ${claimObject(c)}`.trim();
}

export function shortDate(iso: string | null | undefined): string {
  return iso ? formatShortDate(iso, Date.now()) : "";
}
export function relative(iso: string | null | undefined): string {
  return iso ? formatRelativeTime(iso, Date.now()) : "";
}
export function longDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
}
export function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
/** "#lut" -> "lut" for display. */
export function topicLabel(t: string): string {
  return String(t || "").replace(/^#/, "");
}
