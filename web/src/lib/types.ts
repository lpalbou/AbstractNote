/** Response shapes of the note read API (docs/api.md). Fields api.md leaves open are optional. */

/** `name` is the entity's one canonical name (lower case, words joined by `_`); `display_name` is the first way a
 * note wrote it, for a client that wants it. Show `name`. */
export type EntityRef = { entity_id: string; name: string; kind: string; display_name?: string };
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
  /** Set on an insight note (`kind` = its insight_kind), null on every other note. */
  insight?: { kind: string; evidence_note_ids: string[]; entity_ids: string[]; acked_at: string | null } | null;
  error?: string | null;
};

/** Entity card relation (W1 entity_card). */
export type Relation = { other_id: string; other_name: string; predicate: string; direction: "in" | "out"; count: number; first_seen: string; last_seen: string; active: boolean };
export type EntityCard = EntityRef & {
  aliases?: string[];
  summary?: string;
  /** Each dated summary with the note that changed it (W1 expand.entity_card). */
  summary_history?: Array<{ at: string; summary: string; note_id?: string | null; revision_id?: string | null }>;
  first_seen?: string;
  last_seen?: string;
  note_count?: number;
  beliefs?: Claim[];
  relations?: Relation[];
  notes?: NoteRef[];
  open_tasks?: Task[];
  /** Ids of the entries merged into this one (W1 expand.entity_card). */
  merged_from?: string[];
  /** Echoed when the card was built from what the archive held on a date (`as_received`). */
  as_received?: string;
  /** The community the entity belongs to (W17); null when it is in none. */
  community?: { community_id: string; label: string | null } | null;
};
export type EntityListItem = EntityRef & { aliases?: string[]; summary?: string; first_seen?: string; last_seen?: string; note_count?: number; merged_into?: string | null };
/** Pinned: GET /note/entities/{id}?as_merged=true, the entry's own record (not followed to the survivor of a merge). */
export type EntityRecord = EntityRef & {
  aliases: string[];
  summary: string | null;
  first_seen: string | null;
  last_seen: string | null;
  note_count: number;
  merged_into: string | null;
  merged_at: string | null;
  merge: { revision_id: string; at: string; recorded_at: string; reason: string | null; summary: string; note_id: string | null } | null;
  notes: NoteRef[];
};

/** GET /note/revisions items (W1 revision_dict); `ops` are the revision's operations ({op, ...}). */
export type Revision = { revision_id: string; note_id: string | null; at: string; recorded_at?: string; summary: string; ops: Array<Record<string, unknown>>; source?: Record<string, unknown> | null };

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

/** `community_id` (W17): the community the entity belongs to; null when it is in none. */
export type GraphNode = { id: string; kind: string; name: string; display_name?: string; first_seen?: string; last_seen?: string; note_count?: number; summary?: string; community_id?: string | null };
export type GraphEdge = { source: string; target: string; predicate: string; first_seen?: string; last_seen?: string; count: number; active: boolean };
/** `as_received` is echoed when the graph was asked for what the archive held on a date (docs/api.md "Three dates"). */
export type Graph = { as_of: string; as_received?: string; nodes: GraphNode[]; edges: GraphEdge[] };

/** Pinned: {note_id, title, body, insight_kind, occurred_at, acked_at, evidence_note_ids, entity_ids}. */
export type Insight = { note_id: string; title: string; body: string; insight_kind: string; occurred_at: string; acked_at: string | null; evidence_note_ids: string[]; entity_ids: string[] };
/** What a new note is about (`input_data.context.about` on note-capture). */
export type AboutKind = "insight" | "task" | "cue" | "entity" | "note";
export type About = { kind: AboutKind; id: string; title: string };
/** Pinned: GET /note/cues items {cue_id, text, kind, subject: {kind, id, title}, evidence_note_ids, entity_ids} (cue_id is the cue's note id). */
export type Cue = { cue_id: string; text: string; kind: string; subject: { kind: string; id: string; title: string }; evidence_note_ids: string[]; entity_ids: string[] };
/** Pinned: GET /note/changes. Absent lists mean empty. */
export type Changes = {
  cursor: string;
  notes?: NoteRef[];
  entities?: Array<EntityRef & { first_seen: string }>;
  /** `kind` superseded (replaced by `superseded_by`) or retracted (withdrawn; `superseded_by` null); `text` is plain words. */
  superseded_claims?: Array<{ claim_id: string; kind: "superseded" | "retracted"; text: string; superseded_by: string | null; note_id: string }>;
  closed_loops?: Array<{ task_id: string; text: string; closed_at: string }>;
  failed_notes?: Array<{ note_id: string; title: string; error: string }>;
  insights?: Insight[];
};
/** Pinned: GET /note/overview. */
export type Overview = { recent?: NoteRef[]; entities?: Array<EntityRef & { note_count: number; last_seen: string }>; insights?: Insight[]; resurfacing?: NoteRef[]; activity?: Array<{ day: string; notes: number; revisions: number }> };
export type SearchResult = { notes: Array<NoteRef & { score?: number; entities?: EntityRef[] }>; entities: EntityListItem[]; claims: Claim[]; facets?: { kinds?: Record<string, number>; topics?: Record<string, number>; months?: Record<string, number> } };
/** GET /note/day/{date} (W1 NoteStore.day). */
export type Day = { day: string; tz: string; as_received?: string; notes: NoteRef[]; revisions: Array<{ revision_id: string; at: string; summary: string; note_id?: string | null }>; entities_touched: EntityListItem[]; entities_created: string[]; claims_superseded: Claim[] };
/** GET /note/timeline (W1 NoteStore.timeline); `bucket` is the day, the Monday of the week, or the month. */
export type TimelineBuckets = { granularity: string; buckets: Array<{ bucket: string; notes: number; revisions: number; entities_created: number; claims_superseded: number }> };
export type Topic = { topic: string; count: number; first_seen?: string; last_seen?: string };
/** One automation's health (GET /note/status and /note/queue, W14): `healthy` false = it cannot run on what the server serves; POST /note/processor/ensure repairs it. */
export type AutomationHealth = { id: string | null; state: string; target: string | null; healthy: boolean; reason: string | null; paused?: boolean };
export type Automations = { processor_id: string | null; reflect_id: string | null; processor?: AutomationHealth; reflect?: AutomationHealth };
/** POST /note/processor/ensure: what it did with each automation. */
export type EnsureAction = { id: string; action: "created" | "kept" | "recreated"; reason: string | null; previous_id?: string };
export type EnsureResult = { processor_id: string; reflect_id: string; created: string[]; processor?: EnsureAction; reflect?: EnsureAction };
export type QueueStats = { queued: number; processing: number; failed: number; done?: number; by_priority?: Record<string, number>; oldest_enqueued_at?: string | null };
/** GET /note/queue (W1 Queue.summary + W2 automations). */
export type QueueInfo = {
  stats: QueueStats;
  failed: Array<{ note_id: string; title: string; occurred_at?: string; last_error: string | null; failed_at?: string; failures?: number }>;
  waiting: Array<{ note_id: string; title: string; priority: number; state: string; attempts: number }>;
  automations: Automations;
  /** The processor's throughput: up to `max_items` notes every `interval` ("2m"). */
  processor: { max_items: number; interval: string };
};
/** null = the default model. */
export type ModelChoice = { provider: string | null; model: string | null };
/** GET/PUT /note/settings (W1 DEFAULT_SETTINGS). */
export type NoteSettings = { filing_model: ModelChoice; answering_model: ModelChoice; reflect_schedule: { every: string }; processor_schedule: { every: string; max_items: number }; process_inline_default: boolean };
export type NoteStatus = { counts?: Record<string, number>; queue?: QueueStats; settings?: NoteSettings; automations: Automations };
export type ListOf<T> = { items: T[]; next_cursor?: string | null };

/** Pinned: ReviewSide {type, id, name, kind, note_ids}: an entity (kind = entity kind) or a statement (kind null). */
export type ReviewSide = { type: "entity" | "claim"; id: string; name: string; kind: string | null; note_ids: string[] };
/** An action on a review item: POST `path` (under /api/gateway) with `body`; a null in `body` is for the person to fill. */
export type ReviewAction = { action: "confirm" | "keep_apart" | "unmerge" | "retract" | string; method: "POST"; path: string; body: Record<string, unknown> };
/** Pinned: GET /note/review items. `why` is the rule: unsure_match, merged_by_model, disputed_pair. */
export type ReviewItem = {
  review_id: string;
  kind: "close_match" | "merge" | "contradiction";
  why: "unsure_match" | "merged_by_model" | "disputed_pair";
  reason: string | null;
  at: string;
  sides: ReviewSide[];
  evidence_note_ids: string[];
  actions: ReviewAction[];
};

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

/** GET /note/communities items (W17): groups of entities the notebook found together, each with a label. */
export type CommunityEntity = { entity_id: string; name: string; kind: string; note_count: number };
/** `label`, `summary`, `kind` and `labelled_at` are null until the notebook names the community (W17 reflect). */
export type Community = { community_id: string; label: string | null; summary: string | null; kind: string | null; size: number; top_entities: CommunityEntity[]; stale_label: boolean; labelled_at: string | null };
export type Communities = { items: Community[] };
/** GET /note/stats (W17): what the notebook has accumulated. `week` is the Monday (YYYY-MM-DD); `month` YYYY-MM. */
export type WeekActivity = { week: string; notes: number; revisions: number; new_entities: number; claims_added: number; claims_superseded: number };
export type NoteStats = {
  totals: { notes_by_kind: Record<string, number>; notes_by_source: Record<string, number>; attachments_by_mime: Record<string, number> };
  activity_weekly: WeekActivity[];
  entities_by_kind: Record<string, number>;
  top_entities: Array<{ entity_id: string; name: string; kind: string; note_count: number; first_seen: string; last_seen: string }>;
  claims_by_predicate: Array<{ predicate: string; count: number }>;
  tasks: { open: number; closed: number };
  insights_by_kind: Record<string, number>;
  communities: number;
  processing: { understood: number; queued: number; failed: number };
  growth_monthly: Array<{ month: string; entities: number; claims: number }>;
  quiet_entities: Array<{ entity_id: string; name: string; last_seen: string }>;
};
