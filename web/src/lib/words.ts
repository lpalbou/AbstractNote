/** Plain words for the notebook's vocabulary (kinds, predicates, dates). */

import { formatExactTime, formatRelativeTime, formatShortDate } from "@abstractframework/ui-kit";

export const NOTE_KINDS = ["idea", "task", "decision", "fact", "event", "question", "reflection", "reference", "log", "conversation", "personal"] as const;

/** "schema:workLocation" -> "work location"; "ex:led_by" -> "led by". A label, never a decision. */
export function predicateWords(predicate: string): string {
  const local = String(predicate || "").split(":").pop() || "";
  return local.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase().trim() || "related to";
}

type ClaimWords = { subject_name?: string | null; subject_id: string; predicate: string; object_name?: string | null; object_id?: string | null; object_literal?: string | null };

export function claimObject(c: Omit<ClaimWords, "subject_id" | "predicate" | "subject_name">): string {
  return c.object_name || c.object_literal || "";
}

/** "Dana work location Paris". Names come with the claim; an entity without a name reads "someone". */
export function claimSentence(c: ClaimWords): string {
  return `${c.subject_name || "someone"} ${predicateWords(c.predicate)} ${claimObject(c) || "something"}`.trim();
}

type ClaimWindow = { status: string; observed_at: string; valid_from?: string | null; valid_until?: string | null };

/** The claim card's status line. A withdrawn claim was never true, so it shows no window; an end
 * is shown only when it is after the start (never "since 19 October until 18 October"). */
export function claimStatusLine(c: ClaimWindow): string {
  if (c.status === "retracted") return `Withdrawn · noted ${longDate(c.observed_at)}`;
  const start = c.valid_from || c.observed_at;
  const head = c.status === "active" ? "Believed" : "No longer believed";
  const end = c.valid_until && start && Date.parse(c.valid_until) > Date.parse(start) ? ` until ${longDate(c.valid_until)}` : "";
  return `${head} · since ${longDate(start)}${end}`;
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

/** When a captured note was written: "14:05" today, "Oct 2 · 14:05" before. */
export function entryTime(iso: string | null | undefined, nowMs = Date.now()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return new Date(nowMs).toDateString() === d.toDateString() ? hm : `${formatShortDate(iso, nowMs)} · ${hm}`;
}
export function exactTime(iso: string | null | undefined): string {
  return iso ? formatExactTime(iso) : "";
}
/** "12 KB", "1.4 MB". */
export function sizeWords(bytes: number | null | undefined): string {
  const b = Math.max(0, Number(bytes) || 0);
  if (b < 1024 * 1024) return `${Math.max(1, Math.round(b / 1024))} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}
