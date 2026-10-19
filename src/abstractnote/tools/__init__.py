"""Note tools: plane-bound callables for flows (``tool_calls`` nodes) and agents (``allowed_tools``).

``bind(notebook_dir, ...)`` returns ``{tool_name: callable}``; each callable is
decorated with AbstractCore's ``@tool``, so ``MappingToolExecutor.from_tools``
and the runtime's ``tool_calls`` nodes use them directly and their
``ToolDefinition.parameters`` describe the accepted arguments. Every tool takes
JSON-friendly keyword arguments, returns a dict, and raises ``ValueError`` with
a plain sentence on bad input. ``TOOL_NAMES`` is the auto-approve list.

The notebook is opened on the first call, so binding is cheap and creates no
files.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .context import ArtifactReader, ToolContext

TOOL_NAMES: List[str] = [
    "note_capture", "note_file", "note_context", "note_resolve_candidates", "note_apply_revision",
    "note_queue_lease", "note_queue_ack", "note_queue_stats", "note_search", "note_expand",
    "note_changes", "note_entity", "note_timeline", "note_open_loops", "note_contradictions",
    "note_resurface", "note_anniversaries", "note_insight_write",
]


class _LazyContext(ToolContext):
    """A ToolContext whose notebook opens on first use."""

    def __init__(self, notebook_dir: Path, embedder: Any, **kwargs: Any) -> None:
        super().__init__(nb=None, **kwargs)
        self._dir = Path(notebook_dir)
        self._embedder = embedder
        self._open_lock = threading.Lock()

    def __getattribute__(self, name: str) -> Any:
        if name == "nb":
            nb = object.__getattribute__(self, "__dict__").get("nb")
            if nb is None:
                with object.__getattribute__(self, "_open_lock"):
                    nb = object.__getattribute__(self, "__dict__").get("nb")
                    if nb is None:
                        from ..store.notebook import Notebook

                        nb = Notebook.open(object.__getattribute__(self, "_dir"),
                                           embedder=object.__getattribute__(self, "_embedder"))
                        nb.extractors.update(object.__getattribute__(self, "extractors"))
                        object.__getattribute__(self, "__dict__")["nb"] = nb
            return nb
        return object.__getattribute__(self, name)


def bind(notebook_dir: "str | Path", *, embedder: Any = None, artifact_reader: Optional[ArtifactReader] = None,
         extractors: Optional[Dict[str, Any]] = None, allow_paths_under: "str | Path | None" = None,
         account: Optional[str] = None, default_client: str = "agent") -> Dict[str, Callable[..., Any]]:
    """The note tools bound to one notebook directory.

    - ``embedder``: the notebook's embedding model (None keeps the notebook's own pin).
    - ``artifact_reader(ref) -> (bytes, mime, filename)``: resolves ``{"$artifact": id}`` attachments.
    - ``extractors``: ``{"vision": fn, "stt": fn}`` for image descriptions and transcripts.
    - ``allow_paths_under``: accept ``{"path": ...}`` attachments inside this folder (headless only).
    - ``account``: stamped as ``source.account`` on every captured note.
    """
    from abstractcore.tools.core import tool

    from . import capture as cap
    from . import process as proc
    from . import queue as que
    from . import recall as rec

    ctx = _LazyContext(Path(notebook_dir).expanduser(), embedder, artifact_reader=artifact_reader,
                       allow_paths_under=Path(allow_paths_under).expanduser() if allow_paths_under else None,
                       account=account, default_client=default_client, extractors=dict(extractors or {}))

    @tool(name="note_capture", description="Save a note (text, time, place, attachments) to the notebook now.",
          when_to_use="Whenever something should be remembered; returns the note id, revisits and similar notes.")
    def note_capture(text: str = "", idempotency_key: Optional[str] = None, title: Optional[str] = None,
                     occurred_at: Optional[str] = None, location: Optional[Dict[str, Any]] = None,
                     source: Optional[Dict[str, Any]] = None, priority: Optional[int] = None,
                     attachments: Optional[List[Dict[str, Any]]] = None, session_id: Optional[str] = None,
                     about: Optional[Dict[str, Any]] = None, answer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Save a note to the notebook now (``answer`` = {question, evidence_note_ids}: a saved answer)."""
        return cap.note_capture(ctx, text, idempotency_key, title, occurred_at, location, source, priority,
                                attachments, session_id, about, answer)

    @tool(name="note_file", description="Store the quick filing of a note: title, kind, topics, urgency, language.")
    def note_file(note_id: str, title: Optional[str] = None, kind: Optional[str] = None,
                  topics: Optional[List[str]] = None, urgency: Optional[str] = None, language: Optional[str] = None,
                  one_line: Optional[str] = None) -> Dict[str, Any]:
        """Store the quick filing of a note."""
        return cap.note_file(ctx, note_id, title, kind, topics, urgency, language, one_line)

    @tool(name="note_context", description="What the notebook already knows that is relevant to one note.",
          when_to_use="First step of processing a note: sections, similar notes, candidate entities, open tasks.")
    def note_context(note_id: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
        """What the notebook already knows that is relevant to one note."""
        return proc.note_context(ctx, note_id, max_chars)

    @tool(name="note_resolve_candidates", description="Existing entities that each mention of a note may refer to.")
    def note_resolve_candidates(note_id: str, mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Existing entities that each mention may refer to."""
        return proc.note_resolve_candidates(ctx, note_id, mentions)

    @tool(name="note_apply_revision", description="Apply the extraction and linking decisions of a note to the graph.")
    def note_apply_revision(note_id: str, extraction: Dict[str, Any], linking: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the extraction and linking decisions of a note to the graph."""
        return proc.note_apply_revision(ctx, note_id, extraction, linking)

    @tool(name="note_queue_lease", description="Take the next notes to process from the queue, by priority.")
    def note_queue_lease(worker_id: str, max_items: int = 1) -> Dict[str, Any]:
        """Take the next notes to process from the queue."""
        return que.note_queue_lease(ctx, worker_id, max_items)

    @tool(name="note_queue_ack", description="Report how processing a leased note went (ok or error).")
    def note_queue_ack(note_id: str, outcome: str, error: Optional[str] = None,
                       revision_id: Optional[str] = None) -> Dict[str, Any]:
        """Report how processing a leased note went."""
        return que.note_queue_ack(ctx, note_id, outcome, error or None, revision_id or None)

    @tool(name="note_queue_stats", description="How many notes wait, are being processed or failed.")
    def note_queue_stats() -> Dict[str, Any]:
        """How many notes wait, are being processed or failed."""
        return que.note_queue_stats(ctx)

    @tool(name="note_search", description="Quick search of the notes, entities and statements (no model).",
          when_to_use="Before a task or to answer a question cheaply; filters by time, kind, entity and topic.")
    def note_search(query: str = "", since: Optional[str] = None, until: Optional[str] = None,
                    kinds: Optional[List[str]] = None, entities: Optional[List[str]] = None,
                    topics: Optional[List[str]] = None, limit: int = 20, as_of: Optional[str] = None
                    ) -> Dict[str, Any]:
        """Quick search of the notes, entities and statements."""
        return rec.note_search(ctx, query, since, until, kinds, entities, topics, limit, as_of)

    @tool(name="note_expand", description="Entity cards, timelines, relations and then-versus-now around notes or entities.")
    def note_expand(note_ids: Optional[List[str]] = None, entity_ids: Optional[List[str]] = None,
                    as_of: Optional[str] = None) -> Dict[str, Any]:
        """Entity cards, timelines, relations and then-versus-now around notes or entities."""
        return rec.note_expand(ctx, note_ids, entity_ids, as_of)

    @tool(name="note_changes", description="What the notebook learned since a cursor or a time.",
          when_to_use="First call of a session: new notes and entities, superseded beliefs, closed loops, failures.")
    def note_changes(since: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """What the notebook learned since a cursor or a time."""
        return rec.note_changes(ctx, since, limit)

    @tool(name="note_entity", description="The card of one entity as known at a moment.")
    def note_entity(entity_id: str, at: Optional[str] = None) -> Dict[str, Any]:
        """The card of one entity as known at a moment."""
        return rec.note_entity(ctx, entity_id, at)

    @tool(name="note_timeline", description="Notebook activity per day, week or month.")
    def note_timeline(since: Optional[str] = None, until: Optional[str] = None,
                      granularity: str = "day") -> Dict[str, Any]:
        """Notebook activity per day, week or month."""
        return rec.note_timeline(ctx, since, until, granularity)

    @tool(name="note_open_loops", description="Open tasks and unanswered questions that may need attention.")
    def note_open_loops(limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
        """Open tasks and unanswered questions."""
        return rec.note_open_loops(ctx, limit, since)

    @tool(name="note_contradictions", description="Disputed statements and subjects with several current values.")
    def note_contradictions(limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
        """Disputed statements and subjects with several current values."""
        return rec.note_contradictions(ctx, limit, since)

    @tool(name="note_resurface", description="Entities and topics gone quiet that relate to recent notes.")
    def note_resurface(limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
        """Entities and topics gone quiet that relate to recent notes."""
        return rec.note_resurface(ctx, limit, since)

    @tool(name="note_anniversaries", description="Notes from this day in earlier months and years.")
    def note_anniversaries(limit: int = 20, since: Optional[str] = None) -> Dict[str, Any]:
        """Notes from this day in earlier months and years."""
        return rec.note_anniversaries(ctx, limit, since)

    @tool(name="note_insight_write", description="Store an insight or a dossier with the notes and entities behind it.")
    def note_insight_write(kind: str, title: str, body: str, evidence_note_ids: Optional[List[str]] = None,
                           entity_ids: Optional[List[str]] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Store an insight or a dossier with its evidence."""
        return rec.note_insight_write(ctx, kind, title, body, evidence_note_ids, entity_ids, run_id)

    tools = {
        "note_capture": note_capture, "note_file": note_file, "note_context": note_context,
        "note_resolve_candidates": note_resolve_candidates, "note_apply_revision": note_apply_revision,
        "note_queue_lease": note_queue_lease, "note_queue_ack": note_queue_ack, "note_queue_stats": note_queue_stats,
        "note_search": note_search, "note_expand": note_expand, "note_changes": note_changes,
        "note_entity": note_entity, "note_timeline": note_timeline, "note_open_loops": note_open_loops,
        "note_contradictions": note_contradictions, "note_resurface": note_resurface,
        "note_anniversaries": note_anniversaries, "note_insight_write": note_insight_write,
    }
    assert list(tools) == TOOL_NAMES
    return tools


def bound_notebook(tools: Dict[str, Callable[..., Any]]) -> Any:
    """The Notebook behind a bound tool set (opens it when needed)."""
    fn = tools["note_search"]
    for cell in getattr(fn, "__closure__", None) or ():
        value = cell.cell_contents
        if isinstance(value, ToolContext):
            return value.nb
    raise ValueError("These tools were not made by abstractnote.tools.bind.")


def tool_specs() -> List[Dict[str, Any]]:
    """The LLM-facing specs of the note tools (for ``llm_call.tools`` or an agent's catalog)."""
    tools = bind(Path("/nonexistent-abstractnote-spec-only"))  # never opened: specs only
    return [tools[name]._tool_definition.to_dict() for name in TOOL_NAMES]


__all__ = ["TOOL_NAMES", "ToolContext", "bind", "bound_notebook", "tool_specs"]
