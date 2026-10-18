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

from pydantic import BaseModel, ConfigDict, Field

ENTITY_KINDS = ["person", "org", "project", "concept", "place", "event", "artifact", "software", "thing"]
NOTE_KINDS = ["idea", "task", "decision", "fact", "event", "question", "reflection", "reference", "log",
              "conversation", "personal"]
URGENCIES = ["bulk", "normal", "high", "urgent"]
CERTAINTIES = ["stated", "probable", "possible", "reported", "disputed"]
STANCES = ["assert", "revise", "retract"]
MENTION_DECISIONS = ["reuse", "create", "merge", "unsure"]
CLAIM_RELATIONS = ["new", "restates", "refines", "supersedes", "contradicts"]
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


def _o(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties)}


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
    })),
    "claims": _a(_o({
        "subject": _s("a mention ref"),
        "predicate": _s("a CURIE from the vocabulary, or ex:snake_case"),
        "object": _s("a mention ref, or a literal value"),
        "object_is_literal": {"type": "boolean"},
        "certainty": _e(CERTAINTIES),
        "stance": _e(STANCES),
        "evidence": _s("verbatim excerpt of the note"),
        "valid_from": _ns("ISO date or null"),
        "valid_until": _ns("ISO date or null"),
    })),
    "tasks": _a(_o({
        "text": _s(),
        "status": _e(["open", "done"]),
        "due": _ns("ISO date or null"),
    })),
    "time_refs": _a(_o({"text": _s(), "iso": _ns("ISO or null")})),
    "topics": _a(_s()),
})

LINKING: Dict[str, Any] = _o({
    "mentions": _a(_o({
        "ref": _s("the mention ref from the extraction"),
        "decision": _e(MENTION_DECISIONS),
        "candidate": {"type": ["integer", "null"], "description": "index in this mention's candidate list"},
        "merge_candidates": _a({"type": "integer"}, "two candidate indexes that are the same entity"),
        "name": _s("preferred name"),
        "kind": _e(ENTITY_KINDS),
        "aliases": _a(_s()),
        "summary": _ns("updated one-paragraph summary, or null when nothing changed"),
        "reason": _s("short"),
    })),
    "claims": _a(_o({
        "index": {"type": "integer", "description": "index of the claim in the extraction"},
        "relation_to_existing": _e(CLAIM_RELATIONS),
        "existing": {"type": ["integer", "null"], "description": "index of the existing claim in the packet"},
        "reason": _s("short"),
    })),
    "tasks": _a(_o({
        "index": {"type": "integer", "description": "index of the task in the extraction"},
        "closes_open_task": {"type": ["integer", "null"], "description": "index of the open task it closes"},
    })),
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


class Extraction(_M):
    summary: str = ""
    mentions: List[Mention] = Field(default_factory=list)
    claims: List[ExtractedClaim] = Field(default_factory=list)
    tasks: List[ExtractedTask] = Field(default_factory=list)
    time_refs: List[TimeRef] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)


class MentionLink(_M):
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


MODELS = {
    "QuickFiling": QuickFiling, "Extraction": Extraction, "Linking": Linking, "Answer": Answer,
    "MiningPlan": MiningPlan, "SliceFindings": SliceFindings, "Dossier": Dossier, "Reflections": Reflections,
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
