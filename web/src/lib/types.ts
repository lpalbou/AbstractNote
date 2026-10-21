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
export type Task = { task_id: string; note_id: string; text: string; status: string; due: string | null; closed_at: string | null; age_days?: number; stale?: boolean };

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
  /** Where the note's date came from (`client`, `content`, `pdf_created`, `file_mtime`, ...) and whether it is approximate (ADR 0008 §7). */
  date_basis?: string;
  date_approximate?: boolean;
  /** A dated part of a document names its container; a container lists its parts in history order (W30). */
  part_of?: NoteRef | null;
  children?: NoteRef[];
  /** Why the last re-understanding pass could not read it again (held for the day, held by another reading, a failed reading), null when none (W31). */
  reinterpret_error?: string | null;
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
  /** Set when a note understood again withdrew this entry (ADR 0008): `into` = what the note now reads its words as. */
  retired?: { at: string; revision_id: string; note_id: string | null; note_title: string | null; reason: string | null; into: Array<{ entity_id: string; name: string }>; sentence: string } | null;
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
  /** `how`: "captured" (received since the cursor) or "understood" (its processing was recorded since; W24 D16). */
  notes?: Array<NoteRef & { how?: "captured" | "understood" }>;
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
  /** `errors`: the note's last 3 logged failures, newest first (the queue_errors log, W24 D15). */
  failed: Array<{ note_id: string; title: string; occurred_at?: string; last_error: string | null; failed_at?: string; failures?: number; errors?: Array<{ attempt: number; at: string; error: string }> }>;
  waiting: Array<{ note_id: string; title: string; priority: number; state: string; attempts: number }>;
  automations: Automations;
  /** The processor's throughput: up to `max_items` notes every `interval` ("2m"). */
  processor: { max_items: number; interval: string };
};
/** null = the default model. */
export type ModelChoice = { provider: string | null; model: string | null };
/** GET/PUT /note/settings (W1 DEFAULT_SETTINGS). */
export type NoteSettings = { filing_model: ModelChoice; answering_model: ModelChoice; reflect_schedule: { every: string }; processor_schedule: { every: string; max_items: number }; process_inline_default: boolean; consolidate_schedule?: { every: string } };
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

/** GET /note/processing (processing contract, 2026-10-19): what the notebook is doing right now. */
export type ProcessingAutomation = {
  id: string | null;
  state: string;
  paused: boolean;
  healthy: boolean;
  reason: string | null;
  schedule?: { every: string; max_items: number } | null;
  target?: string | null;
};
export type ReflectStatus = ProcessingAutomation & {
  last_run_at: string | null;
  last_status: string | null;
  insights_last_run: number | null;
  communities: { count: number; unlabelled: number; stale_label: number; computed_at: string | null } | null;
};
export type ProcessingFlow = "note-drain" | "note-process" | "note-reflect" | "note-recall" | "note-mine" | "note-capture";
/** One thing running now. `run_id` is only ever sent back (cancel), never shown. */
export type ActiveWork = {
  run_id: string;
  flow: ProcessingFlow | string;
  started_at: string;
  note: NoteRef | null;
  step: string | null;
  step_started_at: string | null;
  sections_done: number | null;
  sections_total: number | null;
  thinking: "on" | "off" | null;
  /** docs/api.md ActiveRun: the run that started this one (a drain for its notes). */
  parent_run_id?: string | null;
  status?: string;
};
export type RecentRevision = {
  revision_id: string;
  note: NoteRef;
  at: string;
  recorded_at: string;
  summary: string;
  entities_created: number;
  claims_added: number;
  claims_superseded: number;
  duration_s: number | null;
};
export type FailedWork = { note_id: string; title: string; error: string | null; attempts: number; next_attempt_at: string | null; failed_at: string | null };
export type Processing = {
  now: string;
  processor: ProcessingAutomation;
  reflect: ReflectStatus;
  queue: { queued: number; processing: number; failed: number; done: number; by_priority: Record<string, number>; oldest_enqueued_at: string | null; eta_s: number | null };
  pace: { per_minute_5m: number; per_minute_1h: number; series: Array<{ t: string; understood: number }> };
  active: ActiveWork[];
  recent: RecentRevision[];
  failed: FailedWork[];
  model: { provider: string | null; model: string | null; thinking_bulk: string; thinking_live: string } | null;
  /** null: this server has no consolidation (contract "Consolidation"). */
  consolidation: Consolidation | null;
};
export type ConsolidationStage = "normalize" | "reinterpret" | "communities" | "labels" | "summaries" | "then_now" | "reflect" | "done";
export type Consolidation = {
  automation: ProcessingAutomation & { schedule?: { every: string } | null };
  /** `propagated` (reinterpret stage only): of the notes this pass reads again, those read because an idea they rely on was understood differently, with those ideas' names (ADR 0008 §4a). */
  run: { run_id: string; started_at: string; stage: ConsolidationStage | string; stage_done: number; stage_total: number; eta_s: number | null; elapsed_s: number; propagated?: ConsolidationPropagated | null } | null;
  /**
   * An interrupted run has no finish time and null counts (W20). `status` completed_with_gaps: the run left
   * `left_out` for the next one and `notice` is its one-sentence end-user line (ADR 0007). `reinterpreted` /
   * `unchanged`: the notes the reinterpret stage understood again, or found unchanged (ADR 0008).
   */
  last: { finished_at: string | null; status: string; merges: number | null; communities: number | null; labelled: number | null; summaries_refreshed: number | null; insights: number | null; left_out?: { summaries: number; labels: number; reinterpret?: number; propagated?: number; propagated_from?: { entity_id: string; name: string; notes: number }[]; reasons: string[]; reinterpret_reasons?: { reason: string; notes: number }[] } | null; budget_spent?: boolean; notice?: string | null; why?: string | null; reinterpreted?: number | null; unchanged?: number | null } | null;
  /** `reinterpret`: notes an earlier note changed, waiting to be understood again (ADR 0008). */
  pending: { stale_labels: number; unlabelled: number; stale_summaries: number; reinterpret?: number };
  /** What waits to be understood again and why, before the stage runs (ADR 0008 §5; `GET /note/consolidation`). */
  reinterpret?: {
    pending: number; by_entity: { entity_id: string; name: string; notes: number }[]; reasons: string[]; context_unknown: number; consumed_today: number; last_marked_at: string | null;
    /** The sentences the pending marks keep from the last pass that could not read them again, with how many notes each (W31). */
    errors?: { error: string; notes: number }[];
    /** The notes waiting because an idea they rely on was understood differently, and the upper bound of how many more may follow (ADR 0008 §4a). */
    propagated?: { pending: number; by_source: { entity_id: string; name: string; notes: number }[]; may_follow?: number; may_follow_from?: { entity_id: string; name: string; notes: number }[] };
    /** The activation gate over the notes not read again since an earlier note reached them (AbstractMemory ADR 0001): the candidates, the notes it marked, the ideas whose change weighs most, and how many it would mark with its centre at 0.8 and 1.2 times θ. */
    candidates?: number; marked?: number; sources?: number; top_sources?: GateSource[]; candidate_sources?: GateSource[]; would_mark_at?: Record<string, number>; theta?: number; floor?: number; threshold?: number;
  };
};
/** One idea whose measured change put notes up for re-understanding: `words` says what changed ("changed its kind"). */
export type GateSource = { entity_id: string; name: string; signal: string | null; words: string; change?: Record<string, number | null> | null; mass?: number | null; strength?: number | null; notes: number; contribution: number };
/** The notes of a re-understanding pass read because an idea they rely on was understood differently (ADR 0008 §4a). */
export type ConsolidationPropagated = { notes: number; names: string[]; sources: number };
