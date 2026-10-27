"""SQLite schema of the note index (``note.sqlite3``).

Truth tables (kept by a rebuild): ``meta``, ``notes``, ``attachments``,
``note_links``, ``queue``, ``revisions``, ``vectors`` (the embedding cache) and
``context_packets``. ``KEPT_TABLES`` (the graph communities, backlog 0030) are derived but hold the
model's labels, so a rebuild keeps them too, with the consolidation rows (bookkeeping, backlog 0032). Everything else is a projection of notes plus revisions
and is recreated by ``Notebook.rebuild``.

Vectors are float32 blobs; cosine search runs in Python over the filtered rows.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 4  # 2: entities.name_key/display_name/key_clash_with, entity_aliases.alias_key;
#                    3: ADR 0008 (revisions.context_json, reinterpret_marks, note_dependencies, soft-removed mentions,
#                    topics and tasks of a superseded interpretation, retired entities);
#                    4: ADR 0008 §4a (idea_changes, the propagated fields of reinterpret_marks)
# Not bumped for backlog 0037 (the ledger address of a note and of a revision, the note's sources, the body
# artifact): those are nullable columns added on open (``_ADDED_COLUMNS``), NULL on older rows, read by nothing
# that changes the behaviour of existing data (memory transition: the baseline is this schema-4 line, operator
# decision 1).

TRUTH_TABLES = ("meta", "notes", "attachments", "note_links", "queue", "revisions", "vectors", "context_packets",
                "reinterpret_marks", "idea_changes", "reading_confirmations")
#: Derived tables a rebuild copies (entity ids are stable, and the community labels are the model's work), and the
#: consolidation bookkeeping (its progress rows and the measured seconds per item the next ETA starts from), and the
#: ``refile --missing`` repairs' progress rows.
KEPT_TABLES = ("communities", "entity_communities", "consolidation_runs", "refile_runs")

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS notes (
  note_id TEXT PRIMARY KEY,
  kind TEXT,
  title TEXT,
  text TEXT NOT NULL DEFAULT '',
  text_norm TEXT NOT NULL DEFAULT '',
  text_hash TEXT,
  one_line TEXT,
  summary TEXT,
  occurred_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  processed_at TEXT,
  source_json TEXT NOT NULL,
  client TEXT,
  location_json TEXT,
  language TEXT,
  filing_topics_json TEXT NOT NULL DEFAULT '[]',
  priority INTEGER NOT NULL DEFAULT 1,
  priority_explicit INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'received',
  duplicate_of TEXT,
  session_id TEXT,
  run_id TEXT,
  archived_at TEXT,
  idempotency_key TEXT,
  size INTEGER NOT NULL DEFAULT 0,
  vector_key TEXT,
  insight_kind TEXT,
  evidence_note_ids_json TEXT,
  entity_ids_json TEXT,
  acked_at TEXT,
  about_json TEXT,
  seq INTEGER,
  ledger_seq INTEGER,
  ledger_step_id TEXT,
  body_artifact_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_occurred ON notes(occurred_at);
CREATE INDEX IF NOT EXISTS idx_notes_received ON notes(received_at);
CREATE INDEX IF NOT EXISTS idx_notes_text_hash ON notes(text_hash);
CREATE INDEX IF NOT EXISTS idx_notes_kind ON notes(kind);

CREATE TABLE IF NOT EXISTS attachments (
  attachment_id TEXT PRIMARY KEY,
  note_id TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  mime TEXT NOT NULL,
  filename TEXT,
  artifact_ref_json TEXT,
  blob_path TEXT NOT NULL,
  extracted_by TEXT NOT NULL DEFAULT 'none',
  extracted_chars INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_attachments_note ON attachments(note_id);
CREATE INDEX IF NOT EXISTS idx_attachments_sha ON attachments(sha256);

CREATE TABLE IF NOT EXISTS note_links (
  src_note_id TEXT NOT NULL,
  dst_note_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  score REAL,
  matched_by TEXT,
  detail_json TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (src_note_id, dst_note_id, predicate)
);
CREATE INDEX IF NOT EXISTS idx_note_links_dst ON note_links(dst_note_id);

CREATE TABLE IF NOT EXISTS queue (
  note_id TEXT PRIMARY KEY,
  priority INTEGER NOT NULL,
  occurred_at TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  enqueued_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued',
  leased_by TEXT,
  lease_until TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  last_error TEXT,
  failed_at TEXT,
  done_at TEXT,
  inline_run_id TEXT,
  inline_until TEXT,
  reinterpret_run_id TEXT,
  reinterpret_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_order ON queue(state, priority DESC, occurred_at ASC, size ASC);

-- every failed attempt (an ack with an error, an expired lease), kept for good: queue.last_error holds only the
-- latest and processing clears it; this log is truth (a rebuild copies it) and nothing deletes from it
CREATE TABLE IF NOT EXISTS queue_errors (
  note_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  at TEXT NOT NULL,
  error TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_errors_note ON queue_errors(note_id, at);

CREATE TABLE IF NOT EXISTS revisions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  revision_id TEXT NOT NULL UNIQUE,
  note_id TEXT,
  attempt INTEGER,
  action TEXT,
  target TEXT,
  at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  run_id TEXT,
  ops_json TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  source_json TEXT,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  result_json TEXT,
  applied_index INTEGER NOT NULL DEFAULT 0,
  applied_kg INTEGER NOT NULL DEFAULT 0,
  abandoned INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  ledger_run_id TEXT,
  ledger_seq INTEGER,
  ledger_step_id TEXT,
  sources_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_revisions_note ON revisions(note_id);
CREATE INDEX IF NOT EXISTS idx_revisions_at ON revisions(at);
CREATE INDEX IF NOT EXISTS idx_revisions_recorded ON revisions(recorded_at);

-- ADR 0008: notes marked for re-understanding (truth: an "unchanged" outcome writes no revision, so only this table
-- says it was consumed). One untaken pending mark per note; a mark arriving while one is taken by a pass is a new row
-- for the next pass. ``pass_id`` = the consolidation that took it, ``outcome`` reinterpreted | unchanged.
CREATE TABLE IF NOT EXISTS reinterpret_marks (
  mark_id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  by_note_id TEXT,
  by_revision_id TEXT,
  entities_json TEXT NOT NULL DEFAULT '[]',
  from_at TEXT,
  marked_at TEXT NOT NULL,
  pass_id TEXT,
  taken_at TEXT,
  consumed_at TEXT,
  outcome TEXT,
  consumed_revision_id TEXT,
  calls INTEGER,
  error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reinterpret_marks_untaken ON reinterpret_marks(note_id)
  WHERE consumed_at IS NULL AND pass_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_reinterpret_marks_note ON reinterpret_marks(note_id, consumed_at);

-- ADR 0008 §4a: how deeply one re-understanding (``revision_id``, a ``note.reinterpret``) changed each entity it
-- touched, measured by comparing the note's old and new reading (recall/propagation.py), and where a deep change
-- spread (``spread_json``). Truth: written once when the re-understanding is applied, copied by a rebuild, never
-- recomputed by a replay (the embedder or the graph may have moved since). ``pass_id`` = the consolidation pass
-- whose mark the re-understanding consumed (each entity spreads at most once per pass).
CREATE TABLE IF NOT EXISTS idea_changes (
  revision_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  note_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  pass_id TEXT,
  kind_change REAL NOT NULL DEFAULT 0,
  summary_distance REAL,
  summary_basis TEXT NOT NULL DEFAULT 'none',
  claim_share REAL NOT NULL DEFAULT 0,
  relation_share REAL NOT NULL DEFAULT 0,
  support_claims INTEGER NOT NULL DEFAULT 0,
  support_relations INTEGER NOT NULL DEFAULT 0,
  magnitude REAL NOT NULL DEFAULT 0,
  deep INTEGER NOT NULL DEFAULT 0,
  summary_only INTEGER NOT NULL DEFAULT 0,
  carried REAL NOT NULL DEFAULT 1.0,
  hops INTEGER NOT NULL DEFAULT 0,
  embedder_model TEXT,
  spread INTEGER NOT NULL DEFAULT 0,
  spread_json TEXT,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY (revision_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_idea_changes_pass ON idea_changes(pass_id, entity_id);

-- ADR 0008 §4a with ADR 0003 (INT2): a re-reading that came out unchanged writes no revision; what it was read
-- against (the stored packet's dependency record, ``related`` included) is appended here for the reading it confirmed
-- (``revision_id``). The no-call check and the next wave read the newest row of the CURRENT reading, else the
-- revision's own ``context_json``, which stays exactly as written at apply time. Truth: append-only, copied by a
-- rebuild.
CREATE TABLE IF NOT EXISTS reading_confirmations (
  confirmation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  note_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  context_json TEXT NOT NULL,
  pass_id TEXT,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reading_confirmations_note ON reading_confirmations(note_id, revision_id);

CREATE TABLE IF NOT EXISTS vectors (
  vector_key TEXT PRIMARY KEY,
  model_id TEXT NOT NULL,
  dimension INTEGER NOT NULL,
  vector BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS context_packets (
  note_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  packet_json TEXT NOT NULL
);

-- ---------------------------------------------------------------- projections

CREATE TABLE IF NOT EXISTS entities (
  entity_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  summary TEXT,
  created_at TEXT NOT NULL,
  created_revision_id TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  first_seen TEXT,
  last_seen TEXT,
  note_count INTEGER NOT NULL DEFAULT 0,
  merged_into TEXT,
  merged_at TEXT,
  vector_key TEXT,
  revision_count INTEGER NOT NULL DEFAULT 0,
  name_key TEXT,
  display_name TEXT,
  key_clash_with TEXT
);
CREATE INDEX IF NOT EXISTS idx_entities_created ON entities(created_at);
CREATE INDEX IF NOT EXISTS idx_entities_last_seen ON entities(last_seen);

CREATE TABLE IF NOT EXISTS entity_aliases (
  entity_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  alias_fold TEXT NOT NULL,
  added_at TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  alias_key TEXT,
  PRIMARY KEY (entity_id, alias_fold)
);
CREATE INDEX IF NOT EXISTS idx_alias_fold ON entity_aliases(alias_fold);

CREATE TABLE IF NOT EXISTS entity_names (
  entity_id TEXT NOT NULL,
  name TEXT NOT NULL,
  at TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  PRIMARY KEY (entity_id, revision_id)
);

CREATE TABLE IF NOT EXISTS entity_summaries (
  entity_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  at TEXT NOT NULL,
  note_id TEXT,
  summary TEXT NOT NULL,
  previous TEXT,
  PRIMARY KEY (entity_id, revision_id)
);

CREATE TABLE IF NOT EXISTS entity_notes (
  entity_id TEXT NOT NULL,
  note_id TEXT NOT NULL,
  at TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  PRIMARY KEY (entity_id, note_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_notes_note ON entity_notes(note_id);

-- the mentions of a superseded interpretation (ADR 0008 soft removal: the live reads never see them, the archive as
-- received before the re-understanding does)
CREATE TABLE IF NOT EXISTS entity_notes_removed (
  entity_id TEXT NOT NULL,
  note_id TEXT NOT NULL,
  at TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  removed_revision_id TEXT NOT NULL,
  PRIMARY KEY (entity_id, note_id, revision_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_notes_removed_note ON entity_notes_removed(note_id);

CREATE TABLE IF NOT EXISTS merges (
  from_id TEXT NOT NULL,
  into_id TEXT NOT NULL,
  at TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  reason TEXT,
  undone_at TEXT,
  undone_revision_id TEXT,
  assertion_id TEXT NOT NULL,
  PRIMARY KEY (from_id, revision_id)
);

CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object_id TEXT,
  object_literal TEXT,
  certainty TEXT NOT NULL,
  stance TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  valid_from TEXT,
  valid_until TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  superseded_by TEXT,
  closed_reason TEXT,
  closed_revision_id TEXT,
  evidence TEXT,
  note_id TEXT,
  revision_id TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  assertion_id TEXT NOT NULL,
  vector_key TEXT,
  disputed_with_json TEXT NOT NULL DEFAULT '[]',
  reinforced_count INTEGER NOT NULL DEFAULT 0,
  seq INTEGER
);
CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_id, predicate);
CREATE INDEX IF NOT EXISTS idx_claims_object ON claims(object_id);
CREATE INDEX IF NOT EXISTS idx_claims_observed ON claims(observed_at);
CREATE INDEX IF NOT EXISTS idx_claims_note ON claims(note_id);

CREATE TABLE IF NOT EXISTS claim_reinforcements (
  claim_id TEXT NOT NULL,
  note_id TEXT,
  revision_id TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY (claim_id, revision_id)
);

CREATE TABLE IF NOT EXISTS relations (
  subject_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  count INTEGER NOT NULL,
  active_claim_ids_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (subject_id, object_id, predicate)
);
CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_id);

CREATE TABLE IF NOT EXISTS topics (
  topic TEXT PRIMARY KEY,
  first_seen TEXT,
  last_seen TEXT,
  note_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS note_topics (
  note_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  assigned_by TEXT NOT NULL,
  revision_id TEXT,
  at TEXT NOT NULL,
  PRIMARY KEY (note_id, topic)
);
CREATE INDEX IF NOT EXISTS idx_note_topics_topic ON note_topics(topic);

-- the topics a superseded interpretation assigned (ADR 0008 soft removal)
CREATE TABLE IF NOT EXISTS note_topics_removed (
  note_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  assigned_by TEXT NOT NULL,
  revision_id TEXT,
  at TEXT NOT NULL,
  removed_revision_id TEXT NOT NULL,
  PRIMARY KEY (note_id, topic, removed_revision_id)
);

-- what each note's CURRENT interpretation used (ADR 0008 §4, the invalidation key: entities it reused, created or
-- made the subject or object of a statement, statements it related to, tasks it opened or closed); kind entity |
-- claim | task_opened | task_closed. Derived from the revision's ops (a rebuild recomputes it).
CREATE TABLE IF NOT EXISTS note_dependencies (
  note_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  PRIMARY KEY (note_id, kind, key)
);
CREATE INDEX IF NOT EXISTS idx_note_dependencies_key ON note_dependencies(kind, key);

-- interpretation revisions a later note.reinterpret superseded (ADR 0008 §1)
CREATE TABLE IF NOT EXISTS revision_supersessions (
  revision_id TEXT PRIMARY KEY,
  superseded_by TEXT NOT NULL,
  note_id TEXT,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  note_id TEXT,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  due TEXT,
  opened_at TEXT NOT NULL,
  opened_revision_id TEXT NOT NULL,
  closed_at TEXT,
  closed_by_note_id TEXT,
  closed_revision_id TEXT,
  recorded_closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

-- communities of the entity graph (abstractnote.recall.communities); kept by a rebuild
CREATE TABLE IF NOT EXISTS communities (
  community_id TEXT PRIMARY KEY,
  label TEXT,
  summary TEXT,
  kind TEXT,
  size INTEGER NOT NULL,
  top_entity_ids_json TEXT NOT NULL DEFAULT '[]',
  member_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  labelled_at TEXT,
  labelled_member_ids_json TEXT,
  stale_label INTEGER NOT NULL DEFAULT 0,
  revision_count_at_compute INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entity_communities (
  entity_id TEXT PRIMARY KEY,
  community_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entity_communities_community ON entity_communities(community_id);

-- one row per memory consolidation (abstractnote.recall.consolidation); the open row has finished_at NULL
CREATE TABLE IF NOT EXISTS consolidation_runs (
  consolidation_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  started_ts REAL NOT NULL,
  updated_ts REAL NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  stage TEXT,
  stage_done INTEGER NOT NULL DEFAULT 0,
  stage_total INTEGER NOT NULL DEFAULT 0,
  stages_json TEXT NOT NULL DEFAULT '{}',
  offered_json TEXT NOT NULL DEFAULT '[]',
  touched_json TEXT NOT NULL DEFAULT '[]',
  results_json TEXT,
  error TEXT,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_consolidation_runs_started ON consolidation_runs(started_ts);

-- one row per ``refile --missing`` repair (abstractnote.ingest.refile): updated after every note, closed with its status
CREATE TABLE IF NOT EXISTS refile_runs (
  refile_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  filed_json TEXT NOT NULL DEFAULT '[]',
  skipped_json TEXT NOT NULL DEFAULT '[]',
  kinds_json TEXT NOT NULL DEFAULT '{}',
  not_reached INTEGER NOT NULL DEFAULT 0,
  max_items INTEGER,
  budget_s REAL,
  seconds REAL,
  model TEXT
);

CREATE TABLE IF NOT EXISTS then_now_cache (
  key TEXT PRIMARY KEY,
  revision_count INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);
"""

#: Created after the added columns exist (an old notebook gains ``context_json`` in ensure_schema).
TRIGGERS = """
-- ADR 0003: the revision log is append-only. What a revision IS (its identity, ops, source and dependency record)
-- never changes once written; only the bookkeeping of its application (applied_index, applied_kg, result_json,
-- abandoned, error) moves, and its summary/warnings are completed by its own apply, never after it was applied.
-- Backlog 0037 (review T1): where a revision came from (its ledger address and the note's sources) is part of what
-- it is: written on INSERT, never updated. The trigger is dropped and created again in one transaction on every
-- open (``ensure_schema``), so a notebook created with an earlier body of it gets this one.
BEGIN IMMEDIATE;
DROP TRIGGER IF EXISTS revisions_append_only;
CREATE TRIGGER revisions_append_only BEFORE UPDATE ON revisions
WHEN OLD.revision_id IS NOT NEW.revision_id OR OLD.note_id IS NOT NEW.note_id OR OLD.attempt IS NOT NEW.attempt
  OR OLD.action IS NOT NEW.action OR OLD.target IS NOT NEW.target OR OLD.at IS NOT NEW.at
  OR OLD.recorded_at IS NOT NEW.recorded_at OR OLD.run_id IS NOT NEW.run_id OR OLD.ops_json IS NOT NEW.ops_json
  OR OLD.source_json IS NOT NEW.source_json OR OLD.context_json IS NOT NEW.context_json
  OR OLD.ledger_run_id IS NOT NEW.ledger_run_id OR OLD.ledger_seq IS NOT NEW.ledger_seq
  OR OLD.ledger_step_id IS NOT NEW.ledger_step_id OR OLD.sources_json IS NOT NEW.sources_json
  OR (OLD.applied_index = 1 AND (OLD.summary IS NOT NEW.summary OR OLD.warnings_json IS NOT NEW.warnings_json))
BEGIN
  SELECT RAISE(ABORT, 'The revision log is append-only (ADR 0003): a written revision is never changed.');
END;
COMMIT;
"""


FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(note_id UNINDEXED, title, body, summary, attachments);
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(entity_id UNINDEXED, name, aliases, summary);
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(claim_id UNINDEXED, text);
"""

PROJECTION_TABLES = (
    "entities", "entity_aliases", "entity_names", "entity_summaries", "entity_notes", "merges", "claims",
    "claim_reinforcements", "relations", "topics", "note_topics", "tasks", "then_now_cache",
    "entities_fts", "claims_fts", "entity_notes_removed", "note_topics_removed", "note_dependencies",
    "revision_supersessions",
)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


# Columns added after the first notebooks were created (in-place upgrade on open).
_ADDED_COLUMNS = {
    # backlog 0037, no version bump (see SCHEMA_VERSION; precedent ``notes.about_json``): where a note and a
    # revision came from in AbstractRuntime's ledger. On a note: the capture step (``run_id`` is the run;
    # ``ledger_seq`` the ledger row when the runtime gave it, ``ledger_step_id`` the step) and the note body stored
    # as an artifact of that run (``{artifact_id, run_id?}``). On a revision: the tool step that applied it
    # (``ledger_run_id``, ``ledger_seq``, ``ledger_step_id``; ``run_id`` keeps its own meaning, e.g. a
    # consolidation id) and the note's sources at that time (``sources_json``: AbstractMemory ``SourceRef``
    # dicts). NULL on rows written before; set on INSERT only (the revision log stays append-only).
    "notes": [("about_json", "TEXT"), ("ledger_seq", "INTEGER"), ("ledger_step_id", "TEXT"),
              ("body_artifact_json", "TEXT")],
    # schema 3 (ADR 0008): a retired entity (``entity.retire``), a task a re-understanding withdrew, the
    # dependency record of a revision (``context_json``)
    "entities": [("name_key", "TEXT"), ("display_name", "TEXT"), ("key_clash_with", "TEXT"),
                 ("retired_revision_id", "TEXT"), ("retired_at", "TEXT")],
    "tasks": [("removed_revision_id", "TEXT")],
    "entity_aliases": [("alias_key", "TEXT")],
    "revisions": [("context_json", "TEXT"), ("ledger_run_id", "TEXT"), ("ledger_seq", "INTEGER"),
                  ("ledger_step_id", "TEXT"), ("sources_json", "TEXT")],
    # schema 4 (ADR 0008 §4a): a mark raised because an idea the note relies on was understood differently
    # (``reason_code`` direct | propagated, its source entity and re-understanding, the impact carried, the hops)
    "reinterpret_marks": [("reason_code", "TEXT NOT NULL DEFAULT 'direct'"), ("source_entity_id", "TEXT"),
                          ("source_revision_id", "TEXT"), ("impact", "REAL NOT NULL DEFAULT 1.0"),
                          ("hops", "INTEGER NOT NULL DEFAULT 0")],
    # the claims of an understanding outside a drain (store/queue.py, ADR 0008 §8; R4)
    "queue": [("inline_run_id", "TEXT"), ("inline_until", "TEXT"), ("reinterpret_run_id", "TEXT"),
              ("reinterpret_until", "TEXT")],
}

#: Created after the added columns exist. One live entity per canonical name: ``name_key`` is unique among
#: entities that are not merged away, except a legacy duplicate that a replayed revision created while the key was
#: already taken (``key_clash_with`` names the holder) until ``Notebook.normalize_entities`` merges it.
POST_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_key ON entities(name_key)
  WHERE merged_into IS NULL AND key_clash_with IS NULL;
CREATE INDEX IF NOT EXISTS idx_entities_name_key_all ON entities(name_key);
CREATE INDEX IF NOT EXISTS idx_alias_key ON entity_aliases(alias_key);
CREATE INDEX IF NOT EXISTS idx_revisions_ledger_run ON revisions(ledger_run_id);
"""

#: Meta key set when a migration found entities written before canonical names: the next write
#: (``Notebook.recover_pending``) merges the groups that share a canonical name (``normalize_entities``).
NAME_KEY_REPAIR_META = "name_key_repair"

#: Meta entry (ADR 0008 migration flag): ``{schema, since_seq, upgraded_at}``. Revisions with ``seq <= since_seq``
#: were written before the dependency record existed (``context_json`` null: ``context_unknown``); a mark on such a
#: note is always re-understood with the model (no stored digest to compare), and invalidation reaches it through
#: its mentions (``entity_notes``) and the name and alias match only.
DEPENDENCY_RECORD_META = "dependency_record"

#: Meta entry (ADR 0008 §4a migration flag): ``{schema, since_seq, upgraded_at}``. Re-understandings with ``seq <=
#: since_seq`` were applied before change records existed: they have no ``idea_changes`` rows and spread nothing.
PROPAGATION_RECORD_META = "propagation_record"


def _backfill_name_keys(conn: sqlite3.Connection) -> None:
    """Schema 1 -> 2: canonical ``name`` and ``name_key`` for every entity (the surface form kept as
    ``display_name``), ``alias_key`` for every alias. A later live entity whose key an earlier live one holds is
    marked ``key_clash_with`` and the notebook is flagged for the merge (``NAME_KEY_REPAIR_META``)."""
    from .. import ids

    rows = conn.execute("SELECT entity_id, name, merged_into FROM entities WHERE name_key IS NULL"
                        " ORDER BY created_at, recorded_at, entity_id").fetchall()
    if not rows and conn.execute("SELECT 1 FROM entity_aliases WHERE alias_key IS NULL LIMIT 1").fetchone() is None:
        return
    holders = {r[0]: r[1] for r in conn.execute(
        "SELECT name_key, entity_id FROM entities WHERE name_key IS NOT NULL AND merged_into IS NULL"
        " AND key_clash_with IS NULL")}
    for entity_id, name, merged_into in rows:
        key = ids.canonical_name(name)
        clash = None
        if merged_into is None:
            clash = holders.get(key)
            if clash is None:
                holders[key] = entity_id
        conn.execute("UPDATE entities SET name=?, name_key=?, display_name=COALESCE(display_name, ?),"
                     " key_clash_with=? WHERE entity_id=?", (key, key, name, clash, entity_id))
    for entity_id, revision_id, name in conn.execute("SELECT entity_id, revision_id, name FROM entity_names").fetchall():
        conn.execute("UPDATE entity_names SET name=? WHERE entity_id=? AND revision_id=?",
                     (ids.canonical_name(name), entity_id, revision_id))
    for entity_id, fold, alias in conn.execute(
            "SELECT entity_id, alias_fold, alias FROM entity_aliases WHERE alias_key IS NULL").fetchall():
        conn.execute("UPDATE entity_aliases SET alias_key=? WHERE entity_id=? AND alias_fold=?",
                     (ids.name_key(alias), entity_id, fold))
    if rows:
        conn.execute("DELETE FROM then_now_cache")  # cached cards carry the old names
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (NAME_KEY_REPAIR_META, '"pending"'))


def _flag_dependency_record(conn: sqlite3.Connection, *, fresh: bool) -> None:
    """Schema 2 -> 3 (ADR 0008): record from which revision on every processing revision carries its dependency
    record (:data:`DEPENDENCY_RECORD_META`); the revisions before it are ``context_unknown``. Written once."""
    import json
    import time

    if conn.execute("SELECT 1 FROM meta WHERE key=?", (DEPENDENCY_RECORD_META,)).fetchone() is not None:
        return
    since = 0 if fresh else int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM revisions").fetchone()[0])
    value = {"schema": 3, "since_seq": since, "upgraded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (DEPENDENCY_RECORD_META, json.dumps(value, sort_keys=True, separators=(",", ":"))))


def _flag_propagation_record(conn: sqlite3.Connection, *, fresh: bool) -> None:
    """Schema 3 -> 4 (ADR 0008 §4a): record from which revision on re-understandings carry change records
    (:data:`PROPAGATION_RECORD_META`). Written once."""
    import json
    import time

    if conn.execute("SELECT 1 FROM meta WHERE key=?", (PROPAGATION_RECORD_META,)).fetchone() is not None:
        return
    since = 0 if fresh else int(conn.execute("SELECT COALESCE(MAX(seq), 0) FROM revisions").fetchone()[0])
    value = {"schema": 4, "since_seq": since, "upgraded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                 (PROPAGATION_RECORD_META, json.dumps(value, sort_keys=True, separators=(",", ":"))))


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.execute("BEGIN IMMEDIATE")
    try:
        _backfill_name_keys(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.executescript(POST_DDL)
    try:
        conn.executescript(TRIGGERS)  # one transaction: the trigger is never absent for another connection
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    try:
        conn.executescript(FTS_DDL)
    except sqlite3.OperationalError as exc:  # pragma: no cover - every supported Python ships FTS5
        raise RuntimeError("AbstractNote needs SQLite with FTS5; this Python's sqlite3 lacks it.") from exc
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None or int(row[0]) < 3:
        _flag_dependency_record(conn, fresh=row is None)
    if row is None or int(row[0]) < 4:
        _flag_propagation_record(conn, fresh=row is None)
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    elif int(row[0]) > SCHEMA_VERSION:
        raise RuntimeError(
            f"This notebook was written by a newer AbstractNote (schema {row[0]}); upgrade abstractnote to open it."
        )
    elif int(row[0]) < SCHEMA_VERSION:
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
