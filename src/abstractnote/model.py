"""The records AbstractNote keeps (docs/data-model.md), as pydantic v2 models.

These types are the read and write shapes of the library. The SQLite schema
in ``abstractnote.store.schema`` stores them; ``abstractnote.store.kg`` projects
claims, names, aliases and merges into AbstractMemory.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

NoteKind = Literal[
    "idea", "task", "decision", "fact", "event", "question", "reflection", "reference", "log",
    "conversation", "personal", "synthesis", "insight",
]
NOTE_KINDS: tuple = NoteKind.__args__  # type: ignore[attr-defined]
FILING_KINDS: tuple = tuple(k for k in NOTE_KINDS if k not in ("synthesis", "insight"))

NoteStatus = Literal["received", "queued", "processing", "processed", "failed", "duplicate", "archived"]
Client = Literal["web", "watch", "iphone", "cli", "agent", "importer"]
CLIENTS: tuple = Client.__args__  # type: ignore[attr-defined]
EntityKind = Literal["person", "org", "project", "concept", "place", "event", "artifact", "software", "thing"]
ENTITY_KINDS: tuple = EntityKind.__args__  # type: ignore[attr-defined]
Certainty = Literal["stated", "probable", "possible", "reported", "disputed"]
CERTAINTIES: tuple = Certainty.__args__  # type: ignore[attr-defined]
Stance = Literal["assert", "revise", "retract"]
ClaimStatus = Literal["active", "superseded", "retracted"]
TaskStatus = Literal["open", "done", "dropped"]
InsightKind = Literal["warning", "reminder", "connection", "contradiction", "evolution", "dossier", "cue"]
INSIGHT_KINDS: tuple = InsightKind.__args__  # type: ignore[attr-defined]
ExtractedBy = str  # plain | markdown | pdf | vision:<model> | stt:<model> | media-metadata | none

CERTAINTY_CONFIDENCE: Dict[str, float] = {
    "stated": 0.9, "probable": 0.7, "possible": 0.5, "reported": 0.6, "disputed": 0.3,
}
URGENCY_PRIORITY: Dict[str, int] = {"bulk": 0, "normal": 1, "high": 2, "urgent": 3}

OP_NAMES = (
    "entity.create", "entity.alias", "entity.rename", "entity.summary", "entity.merge", "entity.unmerge",
    "entity.mention", "claim.add", "claim.supersede", "claim.retract", "claim.reinforce", "topic.assign",
    "task.open", "task.close", "task.drop", "note.summary", "claim.insert_before", "note.redate",
    "note.archive", "topic.unassign",
    # ADR 0008: a re-understanding (``note.reinterpret``) and the inverse of every op (data-model.md "Re-understanding")
    "note.reinterpret", "claim.reinterpret", "claim.revise", "claim.reopen", "claim.repoint", "reinforcement.drop",
    "task.reopen", "task.retire", "entity.retire",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(_Model):
    lat: float
    lon: float
    accuracy_m: Optional[float] = None
    label: Optional[str] = None


class Provenance(_Model):
    """The ``source`` of a note or a user action."""

    model_config = ConfigDict(extra="allow")
    client: Client
    importer: Optional[str] = None
    origin: Dict[str, Any] = Field(default_factory=dict)
    author: Optional[str] = None
    account: Optional[str] = None


class Attachment(_Model):
    attachment_id: str
    note_id: str
    sha256: str
    size: int
    mime: str
    filename: Optional[str] = None
    artifact_ref: Optional[Dict[str, Any]] = None
    blob_path: str
    extracted_text: Optional[str] = None
    extracted_by: ExtractedBy = "none"
    extracted_chars: int = 0


class Note(_Model):
    note_id: str
    kind: Optional[NoteKind] = None
    title: Optional[str] = None
    text: str = ""
    text_norm: str = ""
    summary: Optional[str] = None
    occurred_at: str
    received_at: str
    processed_at: Optional[str] = None
    source: Provenance
    location: Optional[Location] = None
    language: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    priority: int = 1
    status: NoteStatus = "received"
    duplicate_of: Optional[str] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    archived_at: Optional[str] = None
    attachments: List[Attachment] = Field(default_factory=list)
    # Insight notes (kind insight|synthesis)
    insight_kind: Optional[InsightKind] = None
    evidence_note_ids: List[str] = Field(default_factory=list)
    entity_ids: List[str] = Field(default_factory=list)
    acked_at: Optional[str] = None


class Entity(_Model):
    entity_id: str
    kind: EntityKind
    name: str
    aliases: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    note_count: int = 0
    merged_into: Optional[str] = None


class Claim(_Model):
    claim_id: str
    subject_id: str
    predicate: str
    object_id: Optional[str] = None
    object_literal: Optional[str] = None
    certainty: Certainty = "stated"
    stance: Stance = "assert"
    observed_at: str
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    status: ClaimStatus = "active"
    superseded_by: Optional[str] = None
    evidence: Optional[str] = None
    note_id: Optional[str] = None
    revision_id: Optional[str] = None
    assertion_id: Optional[str] = None
    disputed_with: List[str] = Field(default_factory=list)


class Relation(_Model):
    subject_id: str
    object_id: str
    predicate: str
    first_seen: str
    last_seen: str
    count: int
    active_claim_ids: List[str] = Field(default_factory=list)


class RevisionOp(_Model):
    op: Literal[OP_NAMES]  # type: ignore[valid-type]
    payload: Dict[str, Any] = Field(default_factory=dict)


class Revision(_Model):
    revision_id: str
    note_id: Optional[str] = None
    attempt: Optional[int] = None
    action: Optional[str] = None
    at: str
    recorded_at: Optional[str] = None
    run_id: Optional[str] = None
    ops: List[RevisionOp] = Field(default_factory=list)
    summary: str = ""
    source: Optional[Dict[str, Any]] = None
    warnings: List[str] = Field(default_factory=list)
    applied_index: bool = False
    applied_kg: bool = False


class QueueItem(_Model):
    note_id: str
    priority: int
    enqueued_at: str
    occurred_at: str
    size: int = 0
    state: Literal["queued", "leased", "done", "failed"] = "queued"
    leased_by: Optional[str] = None
    lease_until: Optional[str] = None
    attempts: int = 0
    failures: int = 0
    next_attempt_at: Optional[str] = None
    last_error: Optional[str] = None


class Task(_Model):
    task_id: str
    note_id: Optional[str] = None
    text: str
    status: TaskStatus = "open"
    due: Optional[str] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    closed_by_note_id: Optional[str] = None


class Topic(_Model):
    topic: str
    note_count: int = 0
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class Insight(_Model):
    note_id: str
    insight_kind: InsightKind
    title: str
    body: str
    evidence_note_ids: List[str] = Field(default_factory=list)
    entity_ids: List[str] = Field(default_factory=list)
    occurred_at: str
    acked_at: Optional[str] = None
    run_id: Optional[str] = None


class CaptureResult(_Model):
    note_id: str
    status: Literal["new", "duplicate"]
    duplicate_of: Optional[str] = None
    first_saved_at: Optional[str] = None
    received_at: str
    revisits: List[Dict[str, Any]] = Field(default_factory=list)
    similar: List[Dict[str, Any]] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
