"""Structured-output schemas (docs/cognition.md, "Structured output schemas").

``SCHEMAS`` maps each name to the JSON Schema dict attached as ``resp_schema``
to the flows' ``llm_call`` nodes; the pydantic models below mirror them for
validation in Python (``note_apply_revision`` validates the Extraction and
Linking it receives). Identifiers never appear as free text in the Linking
schema: entities, existing claims and open tasks are referred to by their
INDEX in the context packet, and decisions are enumerations (ADR 0004).

The flow generator (``abstractnote.flows.build``) checks that its own copy of
these schemas has the same structure and refuses to build otherwise.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

ENTITY_KINDS = ["person", "org", "project", "concept", "place", "event", "artifact", "software", "thing"]
NOTE_KINDS = ["idea", "task", "decision", "fact", "event", "question", "reflection", "reference", "log",
              "conversation", "personal"]
URGENCIES = ["bulk", "normal", "high", "urgent"]
CERTAINTIES = ["stated", "probable", "possible", "reported", "disputed"]
STANCES = ["assert", "revise", "retract"]
MENTION_DECISIONS = ["reuse", "create", "merge", "unsure"]
#: ``earlier_state_of`` (appended, ADR 0008 §2): the statement is an earlier state of a LATER existing statement
#: about the same subject; it is placed before it (``claim.insert_before``), never closing it.
CLAIM_RELATIONS = ["new", "restates", "refines", "supersedes", "contradicts", "earlier_state_of"]
REFLECTION_KINDS = ["warning", "reminder", "connection", "contradiction", "evolution"]


def _s(description: str = "") -> Dict[str, Any]:
    return {"type": "string", "description": description} if description else {"type": "string"}


def _ns(description: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": ["string", "null"]}
    if description:
        out["description"] = description
    return out


def _e(values: List[str]) -> Dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _a(items: Dict[str, Any], description: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "array", "items": items}
    if description:
        out["description"] = description
    return out


def _o(properties: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """An object schema. Every property is required unless ``defaults`` gives it a default: the
    model may then leave it out (backlog 0024: default-valued fields were a quarter of the output
    tokens), and the runtime's model fills the default."""
    defaults = defaults or {}
    props = {k: (dict(v, default=defaults[k]) if k in defaults else v) for k, v in properties.items()}
    return {"type": "object", "properties": props, "required": [k for k in properties if k not in defaults]}


QUICK_FILING: Dict[str, Any] = _o({
    "title": {"type": "string", "maxLength": 80, "description": "short title, at most 80 characters"},
    "kind": _e(NOTE_KINDS),
    "topics": {**_a(_s(), "prefer existing topics, at most 4"), "maxItems": 4},
    "urgency": _e(URGENCIES),
    "language": _s("BCP-47 language tag"),
    "one_line": _s("the note in one line"),
})

EXTRACTION: Dict[str, Any] = _o({
    "summary": _s("1-3 sentences"),
    "mentions": _a(_o({
        "ref": _s("m1, m2, ..."),
        "name": _s("as written in the note"),
        "kind": _e(ENTITY_KINDS),
        "aliases": _a(_s(), "other names as written in the note"),
        "role": _s("what the note says about it, one phrase"),
    }, {"aliases": []})),
    "claims": _a(_o({
        "subject": _s("a mention ref"),
        "predicate": _s("the stated relation from the subject to the object: a vocabulary CURIE that means exactly "
                        "that, else a specific ex:snake_case verb or attribute; the same one for the same kind of "
                        "fact"),
        "object": _s("a mention ref, or a literal value"),
        "object_is_literal": {"type": "boolean"},
        "certainty": _e(CERTAINTIES),
        "stance": _e(STANCES),
        "evidence": _s("verbatim excerpt of the note"),
        "valid_from": _ns("ISO date or null"),
        "valid_until": _ns("ISO date or null"),
    }, {"object_is_literal": False, "certainty": "stated", "stance": "assert", "valid_from": None,
        "valid_until": None})),
    "tasks": _a(_o({
        "text": _s(),
        "status": _e(["open", "done"]),
        "due": _ns("ISO date or null"),
    }, {"due": None})),
    "time_refs": _a(_o({"text": _s(), "iso": _ns("ISO or null")}, {"iso": None})),
    "topics": _a(_s()),
    # Appended (retrospective ingestion): only when the note's date is approximate and its text states its own date.
    "document_date": {"type": ["object", "null"], "description": "the date the text itself says it was written, "
                      "only when the note's date is marked approximate; else null",
                      "properties": {"iso": _s("ISO date"), "basis": _e(["stated in the text"]),
                                     "quote": _s("the exact words of the text that state the date")},
                      "required": ["iso", "basis", "quote"]},
}, {"document_date": None})

LINKING: Dict[str, Any] = _o({
    "mentions": _a(_o({
        "ref": _s("the mention ref from the extraction"),
        "decision": _e(MENTION_DECISIONS),
        "candidate": {"type": ["integer", "null"], "description": "index in this mention's candidate list"},
        "merge_candidates": _a({"type": "integer"}, "two candidate indexes that are the same entity"),
        "name": _s("preferred name"),
        "kind": _e(ENTITY_KINDS),
        "aliases": _a(_s()),
        "summary": {"type": ["string", "null"], "minLength": 1,
                    "description": "what the entity is on the note's date (its kind, role or purpose, and "
                                   "what it belongs to or works with), one or two sentences, never an event or "
                                   "the note: required for create and unsure, and for a reused or merged entity "
                                   "whose description this note changes; null otherwise"},
        "reason": _s("short"),
    }, {"candidate": None, "merge_candidates": [], "name": "", "kind": None, "aliases": [], "reason": ""})),
    "claims": _a(_o({
        "index": {"type": "integer", "description": "index of the claim in the extraction"},
        "relation_to_existing": _e(CLAIM_RELATIONS),
        "existing": {"type": ["integer", "null"], "description": "the # number of the existing statement in the "
                     "packet (for earlier_state_of: a statement of the LATER block)"},
        "reason": _s("short"),
    }, {"existing": None, "reason": ""})),
    "tasks": _a(_o({
        "index": {"type": "integer", "description": "index of the task in the extraction"},
        "closes_open_task": {"type": ["integer", "null"], "description": "index of the open task it closes"},
        # Appended (D3): a close is applied only with the note's own words saying it is done.
        "evidence": _s("the note's exact words that say the open task is done"),
    }, {"closes_open_task": None, "evidence": ""})),
    "revision_summary": _s("one sentence"),
})

ANSWER: Dict[str, Any] = _o({
    "answer": _s("the answer with inline citations [nt_...]"),
    "evidence": _a(_o({"note_id": _s(), "title": _s(), "occurred_at": _s(), "excerpt": _s()})),
    "evolution": _o({
        "earliest": _s("the earliest stance"),
        "latest": _s("the latest stance"),
        "turning_points": _a(_o({"note_id": _s(), "occurred_at": _s(), "change": _s()})),
    }),
    "open_loops": _a(_s()),
    "follow_ups": _a(_s()),
})

MINING_PLAN: Dict[str, Any] = _o({
    "steps": _a(_o({
        "question": _s("a sub-question to search for"),
        "since": _ns("ISO or null"),
        "until": _ns("ISO or null"),
        "entities": _a(_s(), "names to trace"),
    })),
    "contradictions_to_test": _a(_s()),
    "rounds": {"type": "integer", "minimum": 1, "maximum": 8},
})

SLICE_FINDINGS: Dict[str, Any] = _o({
    "facts": _a(_o({"text": _s(), "citations": _a(_s(), "note ids")})),
    "changes": _a(_s()),
    "gaps": _a(_s()),
})

DOSSIER: Dict[str, Any] = _o({
    "title": _s(),
    "narrative": _s("with inline citations [nt_...]"),
    "timeline": _a(_o({"at": _s(), "event": _s(), "note_ids": _a(_s())})),
    "evolution": _s("how the view evolved"),
    "contradictions": _a(_s()),
    "forgotten": _a(_s(), "items worth revisiting"),
    "suggestions": _a(_s()),
    "confidence": _s("a confidence statement"),
})

REFLECTIONS: Dict[str, Any] = _o({
    "items": _a(_o({
        "candidate": {"type": "integer", "description": "index in the candidate list"},
        "kind": _e(REFLECTION_KINDS),
        "title": _s(),
        "body": _s(),
    })),
})

#: The consolidation's summary step (backlog 0032; abstractnote.recall.consolidation). Entries are referred to by
#: their INDEX in the message, never by id (ADR 0004). Not in :data:`SCHEMAS` (the processing and recall steps):
#: the flow generator adds it next to ``CommunityLabels`` (``flows.build.flow_schemas``).
ENTITY_SUMMARIES: Dict[str, Any] = _o({
    "summaries": _a(_o({
        "index": {"type": "integer", "description": "index of the entry in the list"},
        # Before the summary (written first): the ties the notes marked ``new`` add, which the summary must account
        # for (R1, live S4: the model kept the old sentence and left the new project out). Not stored.
        "ties": _a(_s(), "each project, organisation or person a note marked new ties the entry to, with the "
                         "entry's role in it and its date; empty when no note is new"),
        "summary": _s("at most two sentences, third person, present tense, naming every tie listed in ties"),
    }, {"ties": []})),
})

SCHEMAS: Dict[str, Dict[str, Any]] = {
    "QuickFiling": QUICK_FILING,
    "Extraction": EXTRACTION,
    "Linking": LINKING,
    "Answer": ANSWER,
    "MiningPlan": MINING_PLAN,
    "SliceFindings": SLICE_FINDINGS,
    "Dossier": DOSSIER,
    "Reflections": REFLECTIONS,
}


# ----------------------------------------------------------------------------- pydantic mirrors

class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _null_means_default(cls, data: Any) -> Any:
        """An explicit null for a field whose default is not null means "the default"."""
        if isinstance(data, dict):
            out = dict(data)
            for name, field in cls.model_fields.items():
                if out.get(name, 0) is None and field.default is not None and not field.is_required():
                    out.pop(name)
            return out
        return data


class QuickFiling(_M):
    title: str
    kind: Literal[tuple(NOTE_KINDS)]  # type: ignore[valid-type]
    topics: List[str] = Field(default_factory=list, max_length=4)
    urgency: Literal[tuple(URGENCIES)]  # type: ignore[valid-type]
    language: str = ""
    one_line: str = ""


class Mention(_M):
    ref: str
    name: str
    kind: Literal[tuple(ENTITY_KINDS)] = "thing"  # type: ignore[valid-type]
    aliases: List[str] = Field(default_factory=list)
    role: str = ""


class ExtractedClaim(_M):
    subject: str
    predicate: str
    object: str
    object_is_literal: bool = False
    certainty: Literal[tuple(CERTAINTIES)] = "stated"  # type: ignore[valid-type]
    stance: Literal[tuple(STANCES)] = "assert"  # type: ignore[valid-type]
    evidence: str = ""
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


class ExtractedTask(_M):
    text: str
    status: Literal["open", "done"] = "open"
    due: Optional[str] = None


class TimeRef(_M):
    text: str
    iso: Optional[str] = None


class DocumentDate(_M):
    iso: str
    basis: Literal["stated in the text"] = "stated in the text"
    quote: str = ""


class Extraction(_M):
    summary: str = ""
    mentions: List[Mention] = Field(default_factory=list)
    claims: List[ExtractedClaim] = Field(default_factory=list)
    tasks: List[ExtractedTask] = Field(default_factory=list)
    time_refs: List[TimeRef] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    document_date: Optional[DocumentDate] = None


class MentionLink(_M):
    """A mention decision. The schema asks for ``summary`` when the entity is created (``create``,
    ``unsure``) and when the note changes what is known about a reused entity; a missing one is
    never a failed note: the revision builder warns and writes a placeholder from the mention's
    role (created entities) or keeps the current summary (reused ones)."""

    ref: str
    decision: Literal[tuple(MENTION_DECISIONS)]  # type: ignore[valid-type]
    candidate: Optional[int] = None
    merge_candidates: List[int] = Field(default_factory=list)
    name: str = ""
    kind: Optional[Literal[tuple(ENTITY_KINDS)]] = None  # type: ignore[valid-type]
    aliases: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    reason: str = ""


class ClaimLink(_M):
    index: int
    relation_to_existing: Literal[tuple(CLAIM_RELATIONS)] = "new"  # type: ignore[valid-type]
    existing: Optional[int] = None
    reason: str = ""


class TaskLink(_M):
    index: int
    closes_open_task: Optional[int] = None
    evidence: str = ""


class Linking(_M):
    mentions: List[MentionLink] = Field(default_factory=list)
    claims: List[ClaimLink] = Field(default_factory=list)
    tasks: List[TaskLink] = Field(default_factory=list)
    revision_summary: str = ""


class Evidence(_M):
    note_id: str
    title: str = ""
    occurred_at: str = ""
    excerpt: str = ""


class TurningPoint(_M):
    note_id: str
    occurred_at: str = ""
    change: str = ""


class Evolution(_M):
    earliest: str = ""
    latest: str = ""
    turning_points: List[TurningPoint] = Field(default_factory=list)


class Answer(_M):
    answer: str
    evidence: List[Evidence] = Field(default_factory=list)
    evolution: Evolution = Field(default_factory=Evolution)
    open_loops: List[str] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)


class MiningStep(_M):
    question: str
    since: Optional[str] = None
    until: Optional[str] = None
    entities: List[str] = Field(default_factory=list)


class MiningPlan(_M):
    steps: List[MiningStep] = Field(default_factory=list)
    contradictions_to_test: List[str] = Field(default_factory=list)
    rounds: int = Field(1, ge=1, le=8)


class Fact(_M):
    text: str
    citations: List[str] = Field(default_factory=list)


class SliceFindings(_M):
    facts: List[Fact] = Field(default_factory=list)
    changes: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)


class TimelineEntry(_M):
    at: str
    event: str
    note_ids: List[str] = Field(default_factory=list)


class Dossier(_M):
    title: str
    narrative: str
    timeline: List[TimelineEntry] = Field(default_factory=list)
    evolution: str = ""
    contradictions: List[str] = Field(default_factory=list)
    forgotten: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    confidence: str = ""


class Reflection(_M):
    candidate: int
    kind: Literal[tuple(REFLECTION_KINDS)]  # type: ignore[valid-type]
    title: str
    body: str


class Reflections(_M):
    items: List[Reflection] = Field(default_factory=list)


class EntitySummary(_M):
    index: int
    ties: List[str] = Field(default_factory=list)
    summary: str


class EntitySummaries(_M):
    summaries: List[EntitySummary] = Field(default_factory=list)


MODELS = {
    "QuickFiling": QuickFiling, "Extraction": Extraction, "Linking": Linking, "Answer": Answer,
    "MiningPlan": MiningPlan, "SliceFindings": SliceFindings, "Dossier": Dossier, "Reflections": Reflections,
    "EntitySummaries": EntitySummaries,
}


def validate(name: str, data: Any) -> BaseModel:
    """Validate model output against a schema mirror; raises ValueError with a plain sentence."""
    from pydantic import ValidationError

    if not isinstance(data, dict):
        raise ValueError(f"The {name} must be a JSON object.")
    try:
        return MODELS[name].model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ())) or name
        raise ValueError(f"The {name} does not match its schema ({loc}: {first.get('msg')}).") from None
