/**
 * Capture: one note per send. Upload the files, start `note-capture` with a
 * fresh idempotency key (kept for a retry, so a retry never duplicates the
 * note), show the answer as soon as the ledger has it, then read the note and
 * re-read it with back-off until it is processed (review 01 M8).
 */

import { ApiError, MAX_UPLOAD_BYTES, errorSentence, type Api } from "./api";
import { followRun, runErrorText, startFlow, type FollowOptions } from "./runs";
import type { About, Note } from "./types";

export type CaptureState = "uploading" | "saving" | "queued" | "processed" | "duplicate" | "failed";
/** `lastModified`: the file's modification time (File.lastModified, epoch ms), sent with its upload so an upload
 * without a date of its own is dated by it (docs/api.md "Capture a note", ADR 0008 §7). */
export type CaptureFile = { name: string; size: number; type: string; lastModified?: number };
/** An uploaded file's reference in `context.attachments`: `last_modified` is the file's modification time. */
export type ArtifactRef = { $artifact: string; last_modified?: number };

export type CaptureItem = {
  id: string;
  key: string;
  text: string;
  files: CaptureFile[];
  artifacts: ArtifactRef[];
  occurred_at: string;
  /** The text was pasted (not typed now): its own stated date may date the note (`source.origin.pasted`). */
  pasted?: boolean;
  location: { lat: number; lon: number; accuracy_m?: number } | null;
  /** What the note answers or comments (sent as `context.about`). */
  about?: About | null;
  state: CaptureState;
  answer?: string;
  answerSource?: "view" | "stream" | "ledger" | "output";
  runId?: string;
  noteId?: string;
  note?: Pick<Note, "note_id" | "kind" | "title" | "topics" | "status" | "entities" | "revisits" | "revision" | "occurred_at"> | null;
  revisionSummary?: string;
  error?: string;
};

/** Back-off for re-reading a queued note: 2 s, 4 s, 8 s, 15 s, 30 s, then every 60 s. */
export const NOTE_BACKOFF_MS = [2000, 4000, 8000, 15000, 30000, 60000];
export function noteBackoff(attempt: number): number {
  return NOTE_BACKOFF_MS[Math.min(attempt, NOTE_BACKOFF_MS.length - 1)];
}

export function tooLarge(files: Array<{ size: number }>): boolean {
  return files.some((f) => f.size > MAX_UPLOAD_BYTES);
}

/** The `input_data` of a `note-capture` start (docs/api.md "Capture a note"). */
export function captureInput(item: CaptureItem, processInline: boolean, sessionId: string): Record<string, unknown> {
  const input: Record<string, unknown> = {
    text: item.text,
    // Also added by startFlow for every run (docs/api.md); kept here so the capture input is complete on its own.
    session_id: sessionId,
    title: null,
    // A note of files only is dated by the files (their own date, else their last change), not by the moment of
    // the upload; a note with words keeps the moment they were written (docs/api.md "Capture a note").
    occurred_at: !item.text.trim() && item.files.length ? null : item.occurred_at,
    source: item.pasted ? { client: "web", origin: { pasted: true } } : { client: "web" },
    idempotency_key: item.key,
    priority: null,
    process_inline: processInline,
    context: { attachments: item.artifacts },
  };
  if (item.location) input.location = item.location;
  if (item.about) (input.context as Record<string, unknown>).about = item.about;
  return input;
}

export function pickNote(n: Note): CaptureItem["note"] {
  return { note_id: n.note_id, kind: n.kind, title: n.title, topics: n.topics, status: n.status, entities: n.entities, revisits: n.revisits, revision: n.revision, occurred_at: n.occurred_at };
}

export type CaptureDeps = {
  api: Api;
  sessionId: string;
  processInline: () => boolean;
  update: (id: string, patch: Partial<CaptureItem>) => void;
  sleep?: (ms: number) => Promise<void>;
  follow?: Omit<FollowOptions, "onAnswer">;
  alive?: () => boolean;
  /** The page's leave signal: the run's live view closes when it aborts. */
  signal?: () => AbortSignal | undefined;
};

/** Run one capture to its answer (and, when queued, until processed). Never throws. */
export async function runCapture(item: CaptureItem, files: File[], deps: CaptureDeps): Promise<void> {
  const { api, update } = deps;
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const alive = deps.alive ?? (() => true);
  let artifacts = item.artifacts;
  try {
    if (files.length && artifacts.length < files.length) {
      update(item.id, { state: "uploading", error: undefined });
      const refs = [...artifacts];
      for (const f of files.slice(refs.length)) refs.push(await api.upload(f, deps.sessionId));
      artifacts = refs.map((r, i) => {
        const modified = item.files[i]?.lastModified;
        return typeof modified === "number" && modified > 0 ? { $artifact: r.$artifact, last_modified: modified } : { $artifact: r.$artifact };
      });
      update(item.id, { artifacts });
    }
    update(item.id, { state: "saving", error: undefined });
    const runId = await startFlow(api, "note-capture", deps.sessionId, captureInput({ ...item, artifacts }, deps.processInline(), deps.sessionId));
    update(item.id, { runId });
    let answered = false;
    const leave = deps.signal?.();
    const run = await followRun(api, runId, {
      ...deps.follow,
      ...(leave ? { signal: leave } : {}),
      onAnswer: (text, source) => {
        answered = true;
        update(item.id, { answer: text, answerSource: source });
      },
    });
    const out = (run.output || {}) as Record<string, unknown>;
    const refused = runErrorText(run);
    if (refused !== null) {
      update(item.id, { state: "failed", error: refused || "This note could not be saved." });
      return;
    }
    if (!answered && typeof out.response === "string") update(item.id, { answer: out.response, answerSource: "output" });
    const noteId = typeof out.note_id === "string" ? out.note_id : "";
    if (!noteId) {
      update(item.id, { state: "failed", error: "The note was saved without a reference, so its links cannot be shown." });
      return;
    }
    const duplicate = out.status === "duplicate";
    const revisionSummary = typeof out.revision_summary === "string" ? out.revision_summary : undefined;
    update(item.id, { noteId, revisionSummary, state: duplicate ? "duplicate" : "queued" });
    await watchNote(item.id, noteId, deps, sleep, alive, duplicate);
  } catch (e) {
    update(item.id, { state: "failed", error: e instanceof ApiError && e.code === "too_large" ? errorSentence(e) : saveSentence(e) });
  }
}

function saveSentence(e: unknown): string {
  if (e instanceof ApiError && e.status === 0) return "Your notebook could not be reached; the note is not saved yet.";
  if (e instanceof ApiError && e.status === 401) return "Please sign in again; the note is not saved yet.";
  return "This note could not be saved.";
}

/** Read the note, then re-read it with back-off until processed (or failed). */
export async function watchNote(id: string, noteId: string, deps: CaptureDeps, sleep: (ms: number) => Promise<void>, alive: () => boolean, duplicate = false): Promise<void> {
  for (let attempt = 0; alive(); attempt++) {
    let note: Note;
    try {
      note = await deps.api.get<Note>(`note/notes/${encodeURIComponent(noteId)}`);
    } catch {
      // A background re-read keeps what is on screen and tries again later.
      await sleep(noteBackoff(attempt));
      continue;
    }
    const status = String(note.status || "");
    if (status === "processed" || status === "archived") {
      deps.update(id, { note: pickNote(note), state: duplicate ? "duplicate" : "processed" });
      return;
    }
    if (status === "failed") {
      deps.update(id, { note: pickNote(note), state: "failed", error: "The note is saved, but it could not be processed; retry it from Settings." });
      return;
    }
    deps.update(id, { note: pickNote(note) });
    if (duplicate) {
      deps.update(id, { state: "duplicate" });
      return;
    }
    await sleep(noteBackoff(attempt));
  }
}
