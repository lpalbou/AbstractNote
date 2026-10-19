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
  /** Names as the note index serves them (W1 claim_dict). */
  subject_name?: string | null;
  object_name?: string | null;
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

/** Entity card relation (W1 entity_card). */
export type Relation = { other_id: string; other_name: string; predicate: string; direction: "in" | "out"; count: number; first_seen: string; last_seen: string; active: boolean };
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

/** Raw then-versus-now answers: see lib/shapes.ts (entity, relation and recall forms). */
export type ThenNowRaw = Record<string, unknown>;
export type TimelineItem = { type: "claim" | "note"; at: string; claim?: Claim; note?: NoteRef };
/** GET /note/relations/{a}/{b} (W1 expand.relation). */
export type RelationTimeline = { a: EntityListItem; b: EntityListItem; claims: Claim[]; supersessions?: unknown[]; then_now?: ThenNowRaw };
/** GET /note/claims/{id} (W1). `superseded_by` may be a claim or an id. */
export type ClaimChain = {
  claim: Claim;
  supersedes: Claim[];
  superseded_by: Claim | string | null;
  notes: Array<NoteRef & { attachments?: Array<Attachment & { excerpt?: string; extracted_text?: string | null }> }>;
  revision?: { summary?: string } | null;
};

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
/** GET /note/day/{date} (W1 NoteStore.day). */
export type Day = { day: string; tz: string; notes: NoteRef[]; revisions: Array<{ revision_id: string; at: string; summary: string; note_id?: string | null }>; entities_touched: EntityListItem[]; entities_created: string[]; claims_superseded: Claim[] };
/** GET /note/timeline (W1 NoteStore.timeline); `bucket` is the day, the Monday of the week, or the month. */
export type TimelineBuckets = { granularity: string; buckets: Array<{ bucket: string; notes: number; revisions: number; entities_created: number; claims_superseded: number }> };
export type Topic = { topic: string; count: number; first_seen?: string; last_seen?: string };
export type Automations = { processor_id: string | null; reflect_id: string | null };
export type QueueStats = { queued: number; processing: number; failed: number; done?: number; by_priority?: Record<string, number>; oldest_enqueued_at?: string | null };
/** GET /note/queue (W1 Queue.summary + W2 automations). */
export type QueueInfo = {
  stats: QueueStats;
  failed: Array<{ note_id: string; title: string; occurred_at?: string; last_error: string | null; failed_at?: string; failures?: number }>;
  waiting: Array<{ note_id: string; title: string; priority: number; state: string; attempts: number }>;
  automations: Automations;
};
/** null = the default model. */
export type ModelChoice = { provider: string | null; model: string | null };
/** GET/PUT /note/settings (W1 DEFAULT_SETTINGS). */
export type NoteSettings = { filing_model: ModelChoice; answering_model: ModelChoice; reflect_schedule: { every: string }; process_inline_default: boolean };
export type NoteStatus = { counts?: Record<string, number>; queue?: QueueStats; settings?: NoteSettings; automations: Automations };
export type ListOf<T> = { items: T[]; next_cursor?: string | null };

/** Pinned: note-capture output. */
export type CaptureOutput = { response: string; note_id: string; status: string; revision_summary?: string };
/** Pinned: note-recall output. */
export type RecallOutput = {
  response?: string;
  answer?: string;
  evidence?: NoteRef[];
  evolution?: unknown;
  open_loops?: Task[];
  follow_ups?: string[];
  dossier_note_id?: string;
};
