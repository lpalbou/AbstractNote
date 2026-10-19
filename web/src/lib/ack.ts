/**
 * The capture acknowledgement: one short line per note that expands into what was filed.
 * One state at a time, in words and a symbol (never colour alone):
 *
 *   Sending -> Received -> Organizing -> Organized | Needs attention
 *
 * "Received" = the notebook has the note (its answer arrived); "Organizing" = it is saved and waiting
 * for, or going through, processing; "Organized" = processed (topics and links are known).
 */

import type { CaptureItem } from "./capture";
import { topicLabel } from "./words";

export type AckState = "sending" | "received" | "organizing" | "organized" | "attention";

/** The words a person reads for each state (the copy-rule test pins them). */
export const ACK_WORDS: Record<AckState, string> = {
  sending: "Sending",
  received: "Received",
  organizing: "Organizing",
  organized: "Organized",
  attention: "Needs attention",
};

/** The ui-kit icon each state shows beside its word. */
export const ACK_ICONS: Record<AckState, "loader" | "check" | "clock" | "sparkle" | "warning"> = {
  sending: "loader",
  received: "check",
  organizing: "clock",
  organized: "sparkle",
  attention: "warning",
};

export function ackState(item: Pick<CaptureItem, "state" | "answer" | "noteId">): AckState {
  switch (item.state) {
    case "failed":
      return "attention";
    case "processed":
    case "duplicate":
      return "organized";
    case "queued":
      return "organizing";
    case "uploading":
      return "sending";
    case "saving":
      return item.answer || item.noteId ? "received" : "sending";
  }
}

type Filed = NonNullable<CaptureItem["note"]>;

/**
 * The collapsed line once organized: "Added to #lut · event · 2 links". The place is the first topic, else
 * the first linked entity, else the notebook itself. Built from the note's fields, never from the reply text.
 */
export function ackSummary(note: Pick<Filed, "kind" | "topics" | "entities"> | null | undefined, duplicate = false): string {
  if (!note) return "";
  const topic = (note.topics || [])[0];
  const first = (note.entities || [])[0];
  const where = topic ? `#${topicLabel(topic)}` : first ? first.name : "your notebook";
  const links = (note.entities || []).length;
  const parts = [duplicate ? "Already in your notebook" : `Added to ${where}`];
  if (note.kind) parts.push(note.kind);
  if (!duplicate) parts.push(links === 0 ? "no links yet" : links === 1 ? "1 link" : `${links} links`);
  return parts.join(" · ");
}

/** The collapsed line before the note is organized. */
export function pendingLine(item: Pick<CaptureItem, "state" | "files">): string {
  if (item.state === "uploading") return item.files.length > 1 ? "Adding your files" : "Adding your file";
  if (item.state === "queued") return "Topics and links come next";
  return "";
}
