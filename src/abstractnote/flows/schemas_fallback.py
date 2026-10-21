"""JSON Schema dicts for the structured model outputs of the AbstractNote flows.

These mirror docs/cognition.md "Structured output schemas". The canonical home is
``abstractnote.process.schemas`` (core lane); the flow generator uses that module
when it is importable and refuses to build when the two disagree on structure
(property names, types, enumerations, required fields). Entity, decision and
relation fields are enumerations or candidate INDEXES, never free-form ids, so a
small model cannot invent an identifier (ADR 0004).
"""

from __future__ import annotations

from typing import Any, Dict

ENTITY_KINDS = ["person", "org", "project", "concept", "place", "event", "artifact", "software", "thing"]
NOTE_KINDS = [
    "idea", "task", "decision", "fact", "event", "question", "reflection", "reference", "log",
    "conversation", "personal",
]
URGENCIES = ["bulk", "normal", "high", "urgent"]
CERTAINTIES = ["stated", "probable", "possible", "reported", "disputed"]
STANCES = ["assert", "revise", "retract"]
INSIGHT_KINDS = ["warning", "reminder", "connection", "contradiction", "evolution"]


def _str(description: str = "") -> Dict[str, Any]:
    return {"type": "string", "description": description} if description else {"type": "string"}


def _nullable_str(description: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": ["string", "null"]}
    if description:
        out["description"] = description
    return out


def _arr(items: Dict[str, Any], description: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "array", "items": items}
    if description:
        out["description"] = description
    return out


def _obj(properties: Dict[str, Any], required: list[str] | None = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required if required is not None else properties.keys()),
    }


QUICK_FILING = _obj({
    "title": {"type": "string", "maxLength": 80, "description": "short title, at most 80 characters"},
    "kind": {"type": "string", "enum": NOTE_KINDS},
    "topics": _arr(_str(), "prefer existing topics, at most 4"),
    "urgency": {"type": "string", "enum": URGENCIES},
    "language": _str("BCP-47 language tag"),
    "one_line": _str("the note in one line"),
})

EXTRACTION = _obj({
    "summary": _str("1-3 sentences"),
    "mentions": _arr(_obj({
        "ref": _str("m1, m2, ..."),
        "name": _str(),
        "kind": {"type": "string", "enum": ENTITY_KINDS},
        "aliases": _arr(_str(), "as written in the note"),
        "role": _str("what the note says about it, one phrase"),
    }, ["ref", "name", "kind", "role"])),
    "claims": _arr(_obj({
        "subject": _str("a mention ref"),
        "predicate": _str("CURIE from the vocabulary or ex:snake_case"),
        "object": _str("a mention ref or a literal"),
        "object_is_literal": {"type": "boolean"},
        "certainty": {"type": "string", "enum": CERTAINTIES},
        "stance": {"type": "string", "enum": STANCES},
        "evidence": _str("verbatim excerpt"),
        "valid_from": _nullable_str("ISO date or null"),
        "valid_until": _nullable_str("ISO date or null"),
    }, ["subject", "predicate", "object", "evidence"])),
    "tasks": _arr(_obj({
        "text": _str(),
        "status": {"type": "string", "enum": ["open", "done"]},
        "due": _nullable_str("ISO date or null"),
    }, ["text", "status"])),
    "time_refs": _arr(_obj({"text": _str(), "iso": _nullable_str("ISO or null")}, ["text"])),
    "topics": _arr(_str()),
    "document_date": {**_obj({"iso": _str("ISO date"), "basis": {"type": "string", "enum": ["stated in the text"]},
                              "quote": _str("the exact words that state the date")}),
                      "type": ["object", "null"]},
    "moments": {**_arr(_obj({"heading": _str("the entry's title or heading as written"),
                             "date": _str("the date the entry states for itself"),
                             "basis": {"type": "string", "enum": ["stated in the text"]},
                             "text_span": _str("the exact words the entry begins with")})),
                "type": ["array", "null"]},
}, ["summary", "mentions", "claims", "tasks", "time_refs", "topics"])

LINKING = _obj({
    "mentions": _arr(_obj({
        "ref": _str(),
        "decision": {"type": "string", "enum": ["reuse", "create", "merge", "unsure"]},
        "candidate": {"type": ["integer", "null"], "description": "index in this mention's candidate list"},
        "merge_candidates": _arr({"type": "integer"}, "two candidate indexes to merge"),
        "name": _str("preferred name"),
        "kind": {"type": "string", "enum": ENTITY_KINDS},
        "aliases": _arr(_str()),
        "summary": _nullable_str("updated one-paragraph summary or null"),
        "reason": _str("short"),
    }, ["ref", "decision", "summary"])),
    "claims": _arr(_obj({
        "index": {"type": "integer", "description": "index of the claim in the extraction"},
        "relation_to_existing": {"type": "string", "enum": ["new", "restates", "refines", "supersedes", "contradicts",
                                                          "earlier_state_of", "same_statement_as_previous"]},
        "existing": {"type": ["integer", "null"], "description": "index of the existing claim in the context packet"},
        "reason": _str("short"),
    }, ["index", "relation_to_existing"])),
    "tasks": _arr(_obj({
        "index": {"type": "integer"},
        "closes_open_task": {"type": ["integer", "null"], "description": "index of the open task it closes"},
        "evidence": _str("the note's exact words that say the open task is done"),
    }, ["index"])),
    "revision_summary": _str("one sentence"),
})

ANSWER = _obj({
    "answer": _str("the answer with inline citations [nt_...]"),
    "evidence": _arr(_obj({
        "note_id": _str(), "title": _str(), "occurred_at": _str(), "excerpt": _str(),
    })),
    "evolution": _obj({
        "earliest": _str("the earliest stance"),
        "latest": _str("the latest stance"),
        "turning_points": _arr(_obj({"note_id": _str(), "occurred_at": _str(), "change": _str()})),
    }),
    "open_loops": _arr(_str()),
    "follow_ups": _arr(_str()),
})

MINING_PLAN = _obj({
    "steps": _arr(_obj({
        "question": _str("a sub-question to search for"),
        "since": _nullable_str("ISO or null"),
        "until": _nullable_str("ISO or null"),
        "entities": _arr(_str(), "names to trace"),
    })),
    "contradictions_to_test": _arr(_str()),
    "rounds": {"type": "integer", "minimum": 1, "maximum": 8},
})

SLICE_FINDINGS = _obj({
    "facts": _arr(_obj({"text": _str(), "citations": _arr(_str(), "note ids")})),
    "changes": _arr(_str()),
    "gaps": _arr(_str()),
})

DOSSIER = _obj({
    "title": _str(),
    "narrative": _str("with inline citations [nt_...]"),
    "timeline": _arr(_obj({"at": _str(), "event": _str(), "note_ids": _arr(_str())})),
    "evolution": _str("how the view evolved"),
    "contradictions": _arr(_str()),
    "forgotten": _arr(_str(), "items worth revisiting"),
    "suggestions": _arr(_str()),
    "confidence": _str("a confidence statement"),
})

REFLECTIONS = _obj({
    "items": _arr(_obj({
        "candidate": {"type": "integer", "description": "index in the candidate list"},
        "kind": {"type": "string", "enum": INSIGHT_KINDS},
        "title": _str(),
        "body": _str(),
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
