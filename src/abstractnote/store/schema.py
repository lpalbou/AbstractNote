"""SQLite schema of the note index (``note.sqlite3``).

Truth tables (kept by a rebuild): ``meta``, ``notes``, ``attachments``,
``note_links``, ``queue``, ``revisions``, ``vectors`` (the embedding cache) and
``context_packets``. Everything else is a projection of notes plus revisions
and is recreated by ``Notebook.rebuild``.

Vectors are float32 blobs; cosine search runs in Python over the filtered rows.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2  # 2: entities.name_key/display_name/key_clash_with, entity_aliases.alias_key

TRUTH_TABLES = ("meta", "notes", "attachments", "note_links", "queue", "revisions", "vectors", "context_packets")

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
  seq INTEGER
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
  done_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_queue_order ON queue(state, priority DESC, occurred_at ASC, size ASC);

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
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_revisions_note ON revisions(note_id);
CREATE INDEX IF NOT EXISTS idx_revisions_at ON revisions(at);
CREATE INDEX IF NOT EXISTS idx_revisions_recorded ON revisions(recorded_at);

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

CREATE TABLE IF NOT EXISTS then_now_cache (
  key TEXT PRIMARY KEY,
  revision_count INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);
"""

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(note_id UNINDEXED, title, body, summary, attachments);
CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(entity_id UNINDEXED, name, aliases, summary);
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(claim_id UNINDEXED, text);
"""

PROJECTION_TABLES = (
    "entities", "entity_aliases", "entity_names", "entity_summaries", "entity_notes", "merges", "claims",
    "claim_reinforcements", "relations", "topics", "note_topics", "tasks", "then_now_cache",
    "entities_fts", "claims_fts",
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
    "notes": [("about_json", "TEXT")],
    "entities": [("name_key", "TEXT"), ("display_name", "TEXT"), ("key_clash_with", "TEXT")],
    "entity_aliases": [("alias_key", "TEXT")],
}

#: Created after the added columns exist. One live entity per canonical name: ``name_key`` is unique among
#: entities that are not merged away, except a legacy duplicate that a replayed revision created while the key was
#: already taken (``key_clash_with`` names the holder) until ``Notebook.normalize_entities`` merges it.
POST_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name_key ON entities(name_key)
  WHERE merged_into IS NULL AND key_clash_with IS NULL;
CREATE INDEX IF NOT EXISTS idx_entities_name_key_all ON entities(name_key);
CREATE INDEX IF NOT EXISTS idx_alias_key ON entity_aliases(alias_key);
"""

#: Meta key set when a migration found entities written before canonical names: the next write
#: (``Notebook.recover_pending``) merges the groups that share a canonical name (``normalize_entities``).
NAME_KEY_REPAIR_META = "name_key_repair"


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
        conn.executescript(FTS_DDL)
    except sqlite3.OperationalError as exc:  # pragma: no cover - every supported Python ships FTS5
        raise RuntimeError("AbstractNote needs SQLite with FTS5; this Python's sqlite3 lacks it.") from exc
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    elif int(row[0]) > SCHEMA_VERSION:
        raise RuntimeError(
            f"This notebook was written by a newer AbstractNote (schema {row[0]}); upgrade abstractnote to open it."
        )
    elif int(row[0]) < SCHEMA_VERSION:
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
