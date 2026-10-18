/** Response shapes of the note read API (docs/api.md). Fields api.md leaves open are optional. */

export type EntityRef = { entity_id: string; name: string; kind: string };
/** Pinned (docs/api.md "Pinned response shapes"): {note_id, title, kind, occurred_at, excerpt}. */
export type NoteRef = { note_id: string; title: string; kind: string; occurred_at: string; excerpt?: string; status?: string; topics?: string[] };

export type Claim = {
  claim_id: string;
  subject_id: string;
  predicate: string;
  object_id?: string | null;
  object_literal?: string | null;
  certainty?: string;
  stance?: string;
  observed_at: string;
  valid_from?: string | null;
  valid_until?: string | null;
  status: "active" | "superseded" | "retracted" | string;
  superseded_by?: string | null;
  supersedes?: string | null;
  evidence?: string;
  note_id?: string;
  subject?: EntityRef;
  object?: EntityRef | null;
  note?: { note_id: string; title: string; occurred_at: string } | null;
};

export type Attachment = { attachment_id: string; filename?: string; mime?: string; size?: number; extracted_by?: string; extracted_chars?: number };
/** Pinned: GET /note/tasks items {task_id, note_id, text, status, due, closed_at}. */
export type Task = { task_id: string; note_id: string; text: string; status: string; due: string | null; closed_at: string | null };

export type Note = NoteRef & {
  text: string;
  summary?: string;
  received_at?: string;
  processed_at?: string | null;
  source?: { client?: string; importer?: string; author?: string };
  location?: { lat: number; lon: number; label?: string } | null;
  attachments?: Attachment[];
  entities?: EntityRef[];
  claims?: Claim[];
  revision?: { revision_id: string; summary: string; at: string; recorded_at?: string } | null;
  revisits?: NoteRef[];
  error?: string | null;
};

export type Relation = { other: EntityRef; predicate: string; direction: "in" | "out"; count: number; first_seen: string; last_seen: string; active: boolean };
export type EntityCard = EntityRef & {
  aliases?: string[];
  summary?: string;
  summary_history?: Array<{ at: string; summary: string }>;
  first_seen?: string;
  last_seen?: string;
  note_count?: number;
  beliefs?: Claim[];
  relations?: Relation[];
  notes?: NoteRef[];
  open_tasks?: Task[];
};
export type EntityListItem = EntityRef & { aliases?: string[]; summary?: string; first_seen?: string; last_seen?: string; note_count?: number };

export type ThenNowPoint = { at: string; summary: string; note_id?: string };
export type ThenNow = { earliest: ThenNowPoint | null; latest: ThenNowPoint | null; turning_points: Array<ThenNowPoint & { note_title?: string | null; claim_id?: string }> };
export type TimelineItem = { type: "claim" | "note"; at: string; claim?: Claim; note?: NoteRef };
export type RelationTimeline = { a: EntityRef; b: EntityRef; claims: Claim[]; notes?: NoteRef[]; then_now?: ThenNow };
export type ClaimChain = { claim: Claim; supersedes: Claim[]; superseded_by: Claim | null; notes: Array<{ note_id: string; title: string; occurred_at: string; excerpt?: string }>; attachments: Array<Attachment & { note_id: string; excerpt?: string }> };

export type GraphNode = { id: string; kind: string; name: string; first_seen?: string; last_seen?: string; note_count?: number; summary?: string };
export type GraphEdge = { source: string; target: string; predicate: string; first_seen?: string; last_seen?: string; count: number; active: boolean };
export type Graph = { as_of: string; nodes: GraphNode[]; edges: GraphEdge[] };

/** Pinned: {note_id, title, body, insight_kind, occurred_at, acked_at, evidence_note_ids, entity_ids}. */
export type Insight = { note_id: string; title: string; body: string; insight_kind: string; occurred_at: string; acked_at: string | null; evidence_note_ids: string[]; entity_ids: string[] };
/** What a new note is about (`input_data.context.about` on note-capture). */
export type AboutKind = "insight" | "task" | "cue" | "entity" | "note";
export type About = { kind: AboutKind; id: string; title: string };
/** Pinned: GET /note/cues items {cue_id, text, kind, subject: {kind, id, title}} (cue_id is the cue's note id). */
export type Cue = { cue_id: string; text: string; kind: string; subject: { kind: string; id: string; title: string } };
/** Pinned: GET /note/changes. Absent lists mean empty. */
export type Changes = {
  cursor: string;
  notes?: NoteRef[];
  entities?: Array<EntityRef & { first_seen: string }>;
  superseded_claims?: Array<{ claim_id: string; text: string; superseded_by: string | null; note_id: string }>;
  closed_loops?: Array<{ task_id: string; text: string; closed_at: string }>;
  failed_notes?: Array<{ note_id: string; title: string; error: string }>;
  insights?: Insight[];
};
/** Pinned: GET /note/overview. */
export type Overview = { recent?: NoteRef[]; entities?: Array<EntityRef & { note_count: number; last_seen: string }>; insights?: Insight[]; resurfacing?: NoteRef[]; activity?: Array<{ day: string; notes: number; revisions: number }> };
export type SearchResult = { notes: Array<NoteRef & { score?: number; entities?: EntityRef[] }>; entities: EntityListItem[]; claims: Claim[]; facets?: { kinds?: Record<string, number>; topics?: Record<string, number>; months?: Record<string, number> } };
export type Day = { date: string; notes: NoteRef[]; revisions: Array<{ revision_id: string; at: string; summary: string; note_id?: string | null }>; entities: Array<EntityRef & { change?: string }>; superseded: Claim[] };
export type TimelineBuckets = { granularity: string; buckets: Array<{ start: string; notes: number; revisions: number; new_entities: number; superseded: number }> };
export type Topic = { topic: string; count: number; first_seen?: string; last_seen?: string };
export type QueueInfo = { queued: number; processing: number; failed: number; oldest_enqueued_at?: string | null; failed_notes?: Array<NoteRef & { error?: string; attempts?: number }>; processor?: { automation_id?: string; every?: string; max_items?: number; running?: boolean } };
export type ModelChoice = { provider: string; model: string };
export type NoteSettings = { filing_model: ModelChoice; answering_model: ModelChoice; reflect_schedule: string; process_inline: boolean };
export type NoteStatus = { counts?: Record<string, number>; queue?: QueueInfo; processor_automation_id?: string | null; reflect_automation_id?: string | null; settings?: NoteSettings };
export type ListOf<T> = { items: T[]; next_cursor?: string | null };

/** Pinned: note-capture output. */
export type CaptureOutput = { response: string; note_id: string; status: string; revision_summary?: string };
/** Pinned: note-recall output. */
export type RecallOutput = {
  response?: string;
  answer?: string;
  evidence?: NoteRef[];
  evolution?: ThenNow;
  open_loops?: Task[];
  follow_ups?: string[];
  dossier_note_id?: string;
};
