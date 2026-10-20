"""Generate the AbstractNote VisualFlow documents (docs/cognition.md) into ``flows/json/``.

    python -m abstractnote.flows.build            # write src/abstractnote/flows/json/*.json
    python -m abstractnote.flows.build --check    # fail when the committed JSON is stale

Judgement happens in ``llm_call`` nodes with structured output (``resp_schema``); deterministic
work happens in note tools called from ``tool_calls`` nodes (ADR 0002). Every model node takes
``provider`` and ``model`` from the start pins (blank = the gateway's default text model).
Tool arguments are built by a code node whose input pins ARE the tool's argument names, so the
contract check (tests/test_flows_contract.py) can compare them with each tool's definition.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from .graph import Flow
from .prompts_fallback import FLOW_PROMPT_VARS
from .schemas_fallback import SCHEMAS as FALLBACK_SCHEMAS

JSON_DIR = Path(__file__).resolve().parent / "json"

BUNDLE_ID = "abstractnote"
ENTRYPOINTS = ["note-capture", "note-drain", "note-recall", "note-reflect", "note-consolidate"]
INTERNAL_FLOWS = ["note-process", "note-mine"]
ALL_FLOWS = ENTRYPOINTS + INTERNAL_FLOWS

INTERFACES = {
    "note-capture": ["abstractnote.capture.v1"],
    "note-drain": ["abstractnote.drain.v1"],
    "note-recall": ["abstractnote.recall.v1", "abstractcode.agent.v1"],
    "note-reflect": ["abstractnote.reflect.v1"],
    "note-consolidate": ["abstractnote.consolidate.v1"],
    "note-process": [],
    "note-mine": [],
}

#: Tools each flow may call (the automations' `_runtime.allowed_tools` is the union).
NOTE_TOOLS = [
    "note_capture", "note_file", "note_context", "note_resolve_candidates", "note_apply_revision",
    "note_queue_lease", "note_queue_ack", "note_queue_stats", "note_search", "note_expand",
    "note_changes", "note_entity", "note_timeline", "note_open_loops", "note_contradictions",
    "note_resurface", "note_anniversaries", "note_insight_write", "note_communities_recompute",
    "note_communities_pending", "note_communities_label", "note_consolidation_begin", "note_consolidation_progress",
    "note_consolidation_end", "note_entities_normalize", "note_entities_stale_summaries", "note_entities_set_summaries",
    "note_then_now_refresh", "note_reinterpret_next", "note_mark_reinterpret",
]

DRAIN_DEFAULTS = {"max_items": 20, "budget_s": 90}
#: The consolidation's run inputs (backlog 0032): its time budget and the reflect stage's insight limit.
CONSOLIDATE_DEFAULTS = {"budget_s": 3600, "limit": 10}
AUTOMATION_DEFAULTS = {
    "note-drain": {
        "schema_version": 1,
        "title": "Note processor",
        "trigger": {"source_id": "schedule", "source_version": 2, "config": {"kind": "every", "every": "2m"}},
        "context": {"mode": "independent"},
        "input_data": {**DRAIN_DEFAULTS, "_runtime": {"allowed_tools": NOTE_TOOLS}},
    },
    "note-reflect": {
        "schema_version": 1,
        "title": "Note reflection",
        "trigger": {"source_id": "schedule", "source_version": 2, "config": {"kind": "every", "every": "7d"}},
        "context": {"mode": "independent"},
        "input_data": {"limit": 10, "_runtime": {"allowed_tools": NOTE_TOOLS}},
    },
    "note-consolidate": {
        "schema_version": 1,
        "title": "Note consolidation",
        "trigger": {"source_id": "schedule", "source_version": 2, "config": {"kind": "every", "every": "1d"}},
        "context": {"mode": "independent"},
        "input_data": {**CONSOLIDATE_DEFAULTS, "_runtime": {"allowed_tools": NOTE_TOOLS}},
    },
}

#: Per-step model settings (backlog 0024, docs/cognition.md "Thinking and bounds"). ``thinking`` is
#: pinned on the node, so it wins over the run's ``_runtime.thinking``; None leaves the
#: runtime/gateway default. ``max_output_tokens`` bounds every call (without it the runtime uses
#: the model card's limit, 81920 for Qwen3.6, and a runaway generation takes minutes).
STEP_SETTINGS: Dict[str, Dict[str, Any]] = {
    "quick_filing": {"thinking": "off", "max_output_tokens": 400},
    "extract": {"thinking": "on", "max_output_tokens": 4000},
    "link": {"thinking": "on", "max_output_tokens": 6000},
    "synthesize": {"thinking": None, "max_output_tokens": 8192},
    "mine_plan": {"thinking": None, "max_output_tokens": 4096},
    "mine_findings": {"thinking": None, "max_output_tokens": 4096},
    "mine_dossier": {"thinking": None, "max_output_tokens": 8192},
    "reflect": {"thinking": None, "max_output_tokens": 8192},
    "label_communities": {"thinking": "off", "max_output_tokens": 2048},
    "entity_summaries": {"thinking": "off", "max_output_tokens": 3000},
}


#: Characters per section the extraction reads in one call (``note_context`` max_chars): it bounds
#: each extraction answer so that it fits the extract output cap; the whole text is still read.
SECTION_CHARS = 6000
#: A section with at least this much text whose extraction names nothing is read once more, then flagged.
EMPTY_RETRY_MIN_CHARS = 1


def packet_code(functions: List[str], tail: str) -> str:
    """A code-node body that defines ``abstractnote.process.packets`` helpers, then ``tail``.

    The flows and the Python tests run the very same source: the helpers are copied from the
    module (its constants first), so a packet view can never drift between the two."""
    import ast
    import inspect

    from ..process import packets

    consts = [f"{k} = {getattr(packets, k)!r}" for k in sorted(vars(packets)) if k.isupper()]
    body = [*consts]
    for name in functions:
        tree = ast.parse(inspect.getsource(getattr(packets, name)))
        fn = tree.body[0]
        # The sandbox refuses `typing`, so the copies carry no annotations.
        fn.returns = None
        for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs, fn.args.vararg, fn.args.kwarg]:
            if a is not None:
                a.annotation = None
        body.append(ast.unparse(fn))
    body.append(tail.strip())
    return "\n".join(body)


def validate_code(tail: str) -> str:
    """A code-node body that defines :mod:`abstractnote.process.validate`'s flow functions (its constants first),
    then ``tail``: the note-process checks of a model answer run the very same source as the apply step."""
    import ast
    import inspect

    from ..process import validate

    body = [f"{k} = {getattr(validate, k)!r}" for k in sorted(vars(validate)) if k.isupper()]
    for name in validate.FLOW_FUNCTIONS:
        tree = ast.parse(inspect.getsource(getattr(validate, name)))
        fn = tree.body[0]
        fn.returns = None
        for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs, fn.args.vararg, fn.args.kwarg]:
            if a is not None:
                a.annotation = None
        body.append(ast.unparse(fn))
    body.append(tail.strip())
    return "\n".join(body)


#: note-process: the candidates each mention was offered (``note_resolve_candidates``), by ref.
_CANDIDATE_COUNTS = (
    "cands = {}\n"
    "for r in resolved or []:\n"
    "    if isinstance(r, dict):\n"
    "        cands[str(r.get('ref'))] = len([c for c in (r.get('candidates') or []) if isinstance(c, dict)])\n"
    "texts = [str(s.get('text') or '') for s in (sections or []) if isinstance(s, dict)]\n"
)


# ------------------------------------------------------------------ inputs


def _skeleton(schema: Any) -> Any:
    """Structure of a JSON Schema that both sides must agree on (no descriptions or limits)."""
    if not isinstance(schema, dict):
        return schema
    out: Dict[str, Any] = {}
    t = schema.get("type")
    if t is not None:
        out["type"] = sorted(t) if isinstance(t, list) else t
    if "enum" in schema:
        out["enum"] = sorted(map(str, schema["enum"]))
    if isinstance(schema.get("properties"), dict):
        out["properties"] = {k: _skeleton(v) for k, v in sorted(schema["properties"].items())}
        out["required"] = sorted(schema.get("required") or [])
    if "items" in schema:
        out["items"] = _skeleton(schema["items"])
    return out


def flow_schemas() -> Dict[str, Dict[str, Any]]:
    """Schemas outside ``abstractnote.process.schemas.SCHEMAS`` (the processing and recall steps):
    ``CommunityLabels`` (:mod:`abstractnote.recall.communities`, backlog 0030) and ``EntitySummaries``
    (``process.schemas.ENTITY_SUMMARIES``, the consolidation, backlog 0032)."""
    from ..process.schemas import ENTITY_SUMMARIES
    from ..recall.communities import COMMUNITY_LABELS_SCHEMA

    return {"CommunityLabels": COMMUNITY_LABELS_SCHEMA, "EntitySummaries": ENTITY_SUMMARIES}


def load_schemas() -> Dict[str, Dict[str, Any]]:
    """The structured-output schemas every ``llm_call`` node is handed: the core lane's when importable, checked
    against ours, plus :func:`flow_schemas` (refused when the core lane defines one of them differently), each made
    :func:`abstractnote.process.schemas.strict` (every property required, an optional one nullable: FIX1). This is
    the one place a schema reaches a model."""
    out = _core_schemas()
    for name, schema in flow_schemas().items():
        if name in out and _skeleton(out[name]) != _skeleton(schema):
            raise RuntimeError(f"{name} differs between abstractnote.process.schemas and its flow-side definition")
        out[name] = schema
    from ..process.schemas import strict

    return {name: strict(schema) for name, schema in out.items()}


def _core_schemas() -> Dict[str, Dict[str, Any]]:
    import importlib
    import importlib.util

    try:
        spec = importlib.util.find_spec("abstractnote.process.schemas")
    except ModuleNotFoundError:
        spec = None
    if spec is None:  # the core lane's schemas module is not there yet
        return dict(FALLBACK_SCHEMAS)
    core = importlib.import_module("abstractnote.process.schemas")
    core_map = getattr(core, "SCHEMAS", None)
    if not isinstance(core_map, dict):
        raise RuntimeError(
            "abstractnote.process.schemas exists but has no SCHEMAS mapping {name: JSON Schema dict}; "
            "the flow generator needs it to attach resp_schema"
        )
    problems = []
    for name, fallback in FALLBACK_SCHEMAS.items():
        if name not in core_map:
            problems.append(f"{name} missing in abstractnote.process.schemas.SCHEMAS")
        elif _skeleton(core_map[name]) != _skeleton(fallback):
            problems.append(f"{name} differs between abstractnote.process.schemas and flows/schemas_fallback.py")
    if problems:
        raise RuntimeError("; ".join(problems))
    return {name: core_map[name] for name in FALLBACK_SCHEMAS}


def load_prompts() -> Dict[str, str]:
    """The user-message templates (``<name>``) and static system prompts (``<name>.system``)."""
    from ..process import prompts

    out: Dict[str, str] = {}
    for name in FLOW_PROMPT_VARS:
        # No trailing whitespace: the runtime keeps a user message's own head envelope only when
        # the text behind it has none (llm_client._head_runtime_grounding_envelope compares the
        # rest with rest.strip()); otherwise it stamps a fresh per-second one and the prefix moves.
        out[name] = prompts.load(name).rstrip()
        out[f"{name}.system"] = prompts.system_prompt(name)
    return out


def _step(name: str, prompts: Dict[str, str]) -> Dict[str, Any]:
    """The llm() keyword arguments of a step: its system prompt and its settings."""
    cfg = STEP_SETTINGS[name]
    return {"system": prompts[f"{name}.system"], "thinking": cfg["thinking"],
            "max_output_tokens": cfg["max_output_tokens"]}


def _version() -> str:
    from abstractnote import __version__

    return str(__version__)


# ------------------------------------------------------------------ flows


def _model_pins() -> List[tuple]:
    return [("provider", "provider_text"), ("model", "model")]


def build_capture(schemas: Dict[str, Any], prompts: Dict[str, str]) -> Dict[str, Any]:
    f = Flow(
        "note-capture", "Capture a note",
        "Store a note, file it quickly, answer, and queue it for understanding (optionally right away).",
        interfaces=INTERFACES["note-capture"],
    )
    f.start(
        [("text", "string"), ("title", "string"), ("occurred_at", "string"), ("location", "object"),
         ("source", "object"), ("idempotency_key", "string"), ("priority", "number"),
         ("process_inline", "boolean"), ("session_id", "string"), ("context", "object"), *_model_pins()],
        {"text": "", "process_inline": False, "provider": "", "model": ""},
    )
    S = lambda p: ("start", p)  # noqa: E731
    f.node("attachments", "get", "Attachments",
           inputs=[{"id": "object", "label": "object", "type": "object"},
                   {"id": "key", "label": "key", "type": "string"},
                   {"id": "default", "label": "default", "type": "any"}],
           outputs=[{"id": "value", "label": "value", "type": "any"}],
           pin_defaults={"key": "attachments", "default": []})
    f.wire(S("context"), "attachments", "object")
    f.node("about", "get", "What it is about",
           inputs=[{"id": "object", "label": "object", "type": "object"},
                   {"id": "key", "label": "key", "type": "string"},
                   {"id": "default", "label": "default", "type": "any"}],
           outputs=[{"id": "value", "label": "value", "type": "any"}],
           pin_defaults={"key": "about", "default": None})
    f.wire(S("context"), "about", "object")
    f.node("answer", "get", "A saved answer?",
           inputs=[{"id": "object", "label": "object", "type": "object"},
                   {"id": "key", "label": "key", "type": "string"},
                   {"id": "default", "label": "default", "type": "any"}],
           outputs=[{"id": "value", "label": "value", "type": "any"}],
           pin_defaults={"key": "answer", "default": None})
    f.wire(S("context"), "answer", "object")
    cap = f.tool_step(
        "capture", "Store the note", "note_capture",
        {
            "text": ("string", S("text")), "title": ("string", S("title")),
            "occurred_at": ("string", S("occurred_at")), "location": ("object", S("location")),
            "source": ("object", S("source")), "idempotency_key": ("string", S("idempotency_key")),
            "priority": ("number", S("priority")), "session_id": ("string", S("session_id")),
            "attachments": ("array", ("attachments", "value")),
            "about": ("object", ("about", "value")),
            "answer": ("object", ("answer", "value")),
            # claims the queue row for the inline understanding in the enqueue's transaction (ADR 0008 §8)
            "process_inline": ("boolean", S("process_inline")),
        },
        [("note_id", "string"), ("status", "string"), ("first_saved_at", "string"), ("received_at", "string"),
         ("similar", "array"), ("revisits", "array"), ("topics", "array"), ("warnings", "array"),
         ("occurred_at", "string"), ("date_basis", "string"), ("date_sentence", "string")],
        after=("start", "exec-out"),
    )
    f.code(
        "is_dup", "Saved before?",
        "return {\"duplicate\": status == \"duplicate\"}",
        inputs={"status": ("string", (cap, "status"))}, outputs=[("duplicate", "boolean")], exec_lane=False,
    )
    f.if_("dup_branch", "Already saved?", ("is_dup", "duplicate"))
    f.then(cap, "dup_branch")
    f.code(
        "dup_msg", "Already-saved answer",
        "when = str(first_saved_at or \"\")[:10]\n"
        "return {\"message\": (\"Already saved on \" + when + \".\") if when else \"Already saved.\"}",
        inputs={"first_saved_at": ("string", (cap, "first_saved_at"))}, outputs=[("message", "string")],
    )
    f.then("dup_branch", "dup_msg", "true")
    f.answer_user("dup_answer", "Say it was saved before", ("dup_msg", "message"))
    f.then("dup_msg", "dup_answer")
    f.end("end_duplicate", "Done (already saved)", {
        "response": ("string", ("dup_msg", "message")), "note_id": ("string", (cap, "note_id")),
        "status": ("string", "duplicate"),
    })
    f.then("dup_answer", "end_duplicate")

    f.code(
        "filing_packet", "What filing sees",
        packet_code(["clip", "note_envelope", "filing_packet"],
                    "return {\"packet\": filing_packet(note_id, occurred_at or received_at, title, text, similar,"
                    " revisits, topics)}"),
        inputs={"note_id": ("string", (cap, "note_id")), "occurred_at": ("string", S("occurred_at")),
                "received_at": ("string", (cap, "received_at")), "title": ("string", S("title")),
                "text": ("string", S("text")), "similar": ("array", (cap, "similar")),
                "revisits": ("array", (cap, "revisits")), "topics": ("array", (cap, "topics"))},
        outputs=[("packet", "object")],
    )
    f.then("dup_branch", "filing_packet", "false")
    f.template("filing_prompt", "Filing prompt", prompts["quick_filing"], {"packet": ("filing_packet", "packet")})
    f.llm("filing", "File it quickly", schema=schemas["QuickFiling"], prompt=("filing_prompt", "result"),
          provider=S("provider"), model=S("model"), **_step("quick_filing", prompts))
    f.then("filing_packet", "filing")
    f.break_object("filing_fields", "Filing", ("filing", "data"), [
        ("title", "string"), ("kind", "string"), ("topics", "array"), ("urgency", "string"),
        ("language", "string"), ("one_line", "string"),
    ])
    filed = f.tool_step(
        "file", "File the note", "note_file",
        {
            "note_id": ("string", (cap, "note_id")), "title": ("string", ("filing_fields", "title")),
            "kind": ("string", ("filing_fields", "kind")), "topics": ("array", ("filing_fields", "topics")),
            "urgency": ("string", ("filing_fields", "urgency")),
            "language": ("string", ("filing_fields", "language")),
            "one_line": ("string", ("filing_fields", "one_line")),
        },
        [("priority", "number"), ("status", "string")],
        after=("filing", "exec-out"),
    )
    f.code(
        "saved_msg", "Saved answer",
        "titles = []\n"
        "for s in similar or []:\n"
        "    t = s.get(\"title\") if isinstance(s, dict) else None\n"
        "    if t and t not in titles:\n"
        "        titles.append(t)\n"
        "parts = [\"Saved as \" + str(kind or \"note\") + \" \\u201c\" + str(title or \"\") + \"\\u201d.\"]\n"
        "if titles:\n"
        "    parts.append(\"Related: \" + \", \".join(titles[:3]) + \".\")\n"
        "dates = [str(r.get(\"occurred_at\") or \"\")[:10] for r in (revisits or []) if isinstance(r, dict)]\n"
        "dates = [d for d in dates if d]\n"
        "if dates:\n"
        "    parts.append(\"You noted this on \" + dates[0] + \".\")\n"
        "if str(dated or \"\").strip():\n"
        "    parts.append(str(dated).strip())\n"
        "for w in warnings or []:\n"
        "    if isinstance(w, str) and w.strip():\n"
        "        parts.append(w.strip())\n"
        "return {\"message\": \" \".join(parts)}",
        inputs={
            "kind": ("string", ("filing_fields", "kind")), "title": ("string", ("filing_fields", "title")),
            "similar": ("array", (cap, "similar")), "revisits": ("array", (cap, "revisits")),
            "warnings": ("array", (cap, "warnings")), "dated": ("string", (cap, "date_sentence")),
        },
        outputs=[("message", "string")],
    )
    f.then(filed, "saved_msg")
    f.answer_user("saved_answer", "Say where it was filed", ("saved_msg", "message"))
    f.then("saved_msg", "saved_answer")
    f.if_("inline_branch", "Process now?", S("process_inline"))
    f.then("saved_answer", "inline_branch")
    f.end("end_queued", "Done (queued)", {
        "response": ("string", ("saved_msg", "message")), "note_id": ("string", (cap, "note_id")),
        "status": ("string", "queued"),
    })
    f.then("inline_branch", "end_queued", "false")
    f.subflow("process", "Understand the note", "note-process", {
        "note_id": ("string", (cap, "note_id")), "provider": ("provider_text", S("provider")),
        "model": ("model", S("model")),
    }, [("response", "string"), ("revision_id", "string"), ("revision", "object")])
    f.then("inline_branch", "process", "true")
    # The outcome is recorded either way (the claim ends with it): a failure is a failed attempt in the queue log
    # (queue_errors) with the child's own sentence, then the run ends with that sentence.
    f.code(
        "inline_outcome", "Understood?",
        "o = output if isinstance(output, dict) else {}\n"
        "c = child_output if isinstance(child_output, dict) else {}\n"
        "err = str(c.get(\"error\") or o.get(\"error\") or \"\")\n"
        "for prefix in (\"Runtime error: \", \"Execution error: \"):\n"
        "    if err.startswith(prefix):\n"
        "        err = err[len(prefix):]\n"
        "failed = c.get(\"success\") is False or o.get(\"success\") is False or bool(err) or not o.get(\"revision_id\")\n"
        "return {\"ok\": not failed, \"failure\": (err or \"The note could not be processed.\") if failed else \"\"}",
        inputs={"output": ("object", ("process", "output")), "child_output": ("object", ("process", "child_output"))},
        outputs=[("ok", "boolean"), ("failure", "string")],
    )
    f.then("process", "inline_outcome")
    f.if_("inline_ok", "Understood?", ("inline_outcome", "ok"))
    f.then("inline_outcome", "inline_ok")
    failed_ack = f.tool_step(
        "inline_fail", "Record the failure", "note_queue_ack",
        {"note_id": ("string", (cap, "note_id")), "outcome": ("string", "error"),
         "error": ("string", ("inline_outcome", "failure"))},
        [("status", "string")],
        after=("inline_ok", "false"),
    )
    f.code(
        "inline_failed", "Say why",
        "raise ValueError(str(failure or \"The note could not be processed.\"))",
        inputs={"failure": ("string", ("inline_outcome", "failure"))}, outputs=[("checked", "boolean")],
    )
    f.then(failed_ack, "inline_failed")
    ack = f.tool_step(
        "inline_ack", "Mark it processed", "note_queue_ack",
        {
            "note_id": ("string", (cap, "note_id")), "outcome": ("string", "ok"),
            "revision_id": ("string", ("process", "revision_id")),
        },
        [("status", "string")],
        after=("inline_ok", "true"),
    )
    f.code(
        "processed_msg", "Processed answer",
        "extra = str(summary or \"\").strip()\n"
        "rev = revision if isinstance(revision, dict) else {}\n"
        "dated = str(rev.get(\"date_sentence\") or \"\").strip()\n"
        "return {\"message\": \" \".join(x for x in (str(message or \"\").strip(), dated, extra) if x)}",
        inputs={"message": ("string", ("saved_msg", "message")), "summary": ("string", ("process", "response")),
                "revision": ("object", ("process", "revision"))},
        outputs=[("message", "string")],
    )
    f.then(ack, "processed_msg")
    f.end("end_processed", "Done (processed)", {
        "response": ("string", ("processed_msg", "message")), "note_id": ("string", (cap, "note_id")),
        "status": ("string", "processed"), "revision_summary": ("string", ("process", "response")),
        "revision": ("object", ("process", "revision")),
    })
    f.then("processed_msg", "end_processed")
    return f.finish()


MERGE_EXTRACTIONS = r'''
parts = [e for e in (extractions or []) if isinstance(e, dict)]
if not parts:
    raise ValueError("The note could not be read.")
if len(parts) == 1:
    merged = parts[0]
else:
    mentions, refmap, by_key = [], {}, {}
    claims, tasks, time_refs, topics, summaries, warnings = [], [], [], [], [], []
    for i, part in enumerate(parts):
        for m in part.get("mentions") or []:
            key = (str(m.get("name") or "").casefold(), str(m.get("kind") or ""))
            if key in by_key:
                kept = by_key[key]
                for a in m.get("aliases") or []:
                    if a not in kept["aliases"]:
                        kept["aliases"].append(a)
            else:
                kept = dict(m, ref="m" + str(len(mentions) + 1), aliases=list(m.get("aliases") or []))
                by_key[key] = kept
                mentions.append(kept)
            refmap[(i, m.get("ref"))] = kept["ref"]
        for c in part.get("claims") or []:
            c2 = dict(c)
            c2["subject"] = refmap.get((i, c.get("subject")), c.get("subject"))
            if not c.get("object_is_literal"):
                c2["object"] = refmap.get((i, c.get("object")), c.get("object"))
            claims.append(c2)
        tasks.extend(part.get("tasks") or [])
        time_refs.extend(part.get("time_refs") or [])
        for t in part.get("topics") or []:
            if t not in topics:
                topics.append(t)
        if part.get("summary"):
            summaries.append(str(part.get("summary")))
        for w in part.get("warnings") or []:
            if w not in warnings:
                warnings.append(w)
    merged = {"summary": " ".join(summaries), "mentions": mentions, "claims": claims,
              "tasks": tasks, "time_refs": time_refs, "topics": topics}
    dated = [part.get("document_date") for part in parts if isinstance(part.get("document_date"), dict)]
    if dated:
        merged["document_date"] = dated[0]
    if warnings:
        merged["warnings"] = warnings
slots = {}
for c in merged.get("claims") or []:
    for end in (c.get("subject"), None if c.get("object_is_literal") else c.get("object")):
        if end is not None and c.get("predicate") and c.get("predicate") not in slots.setdefault(str(end), []):
            slots[str(end)].append(c.get("predicate"))
names = [{"ref": m.get("ref"), "name": m.get("name"), "kind": m.get("kind"), "aliases": m.get("aliases") or [],
          "predicates": slots.get(str(m.get("ref")), [])} for m in merged.get("mentions") or []]
return {"extraction": merged, "mentions": names}
'''


def build_process(schemas: Dict[str, Any], prompts: Dict[str, str]) -> Dict[str, Any]:
    f = Flow(
        "note-process", "Understand a note",
        "Read one note against what the notebook knows, extract, link and revise the graph.",
        interfaces=INTERFACES["note-process"],
    )
    f.start([("note_id", "string"), ("thinking", "string"), *_model_pins()],
            {"note_id": "", "thinking": "on", "provider": "", "model": ""})
    S = lambda p: ("start", p)  # noqa: E731
    # Thinking for extraction and linking is a per-run choice (on by default; note-drain passes its own,
    # off by default for bulk imports): a thinking call can run to its output budget and retry.
    f.code(
        "thinking", "Thinking for this note",
        "t = str(thinking or \"on\").strip().lower()\n"
        "if t not in (\"on\", \"off\"):\n"
        "    raise ValueError(\"Thinking must be on or off.\")\n"
        "return {\"value\": t}",
        inputs={"thinking": ("string", S("thinking"))}, outputs=[("value", "string")],
    )
    f.then("start", "thinking")
    TH = ("thinking", "value")
    ctx = f.tool_step(
        "context", "Look at what is known", "note_context",
        {"note_id": ("string", S("note_id")), "max_chars": ("number", SECTION_CHARS)},
        [("note", "object"), ("sections", "array"), ("similar_notes", "array"),
         ("candidate_entities", "array"), ("open_tasks", "array"), ("topics", "array"),
         ("vocabulary", "array"), ("context", "object")],
        after=("thinking", "exec-out"),
    )
    f.code(
        "sections", "Sections to read",
        "if not isinstance(sections, list) or not sections:\n"
        "    raise ValueError(\"The note has no text to read.\")\n"
        "return {\"items\": sections, \"count\": len(sections)}",
        inputs={"sections": ("array", (ctx, "sections"))}, outputs=[("items", "array"), ("count", "number")],
    )
    f.then(ctx, "sections")
    f.for_each("each_section", "For each section", ("sections", "items"))
    f.then("sections", "each_section")
    f.code(
        "extract_packet", "What extraction sees",
        packet_code(["clip", "note_envelope", "note_header", "extraction_packet"],
                    "return {\"packet\": extraction_packet(note, item, parts, similar_notes, candidate_entities,"
                    " open_tasks, topics)}"),
        inputs={"note": ("object", (ctx, "note")), "item": ("object", ("each_section", "item")),
                "parts": ("number", ("sections", "count")), "similar_notes": ("array", (ctx, "similar_notes")),
                "candidate_entities": ("array", (ctx, "candidate_entities")),
                "open_tasks": ("array", (ctx, "open_tasks")), "topics": ("array", (ctx, "topics"))},
        outputs=[("packet", "object")],
    )
    f.then("each_section", "extract_packet", "loop")
    f.template("extract_prompt", "Extraction prompt", prompts["extract"], {"packet": ("extract_packet", "packet")})
    f.llm("extract", "Extract", schema=schemas["Extraction"], prompt=("extract_prompt", "result"),
          provider=S("provider"), model=S("model"), thinking_from=TH, **_step("extract", prompts))
    f.then("extract_packet", "extract")
    # A section with text that names nothing is read once more (the model sometimes answers valid
    # empty lists); a second empty answer is kept and flagged in the revision's warnings. A structurally
    # corrupt answer (process/validate.py, ADR 0008 §8) is read once more too; a second one fails the note.
    f.code(
        "nothing_named", "Nothing named, or a corrupt answer?",
        validate_code(
            "d = data if isinstance(data, dict) else {}\n"
            "text = str((item or {}).get(\"text\") or \"\") if isinstance(item, dict) else str(item or \"\")\n"
            "corrupt = extraction_problems(d, text)\n"
            f"empty = not (d.get(\"mentions\") or []) and len(text.strip()) >= {EMPTY_RETRY_MIN_CHARS}\n"
            "return {\"again\": bool(corrupt) or empty}"),
        inputs={"data": ("object", ("extract", "data")), "item": ("object", ("each_section", "item"))},
        outputs=[("again", "boolean")],
    )
    f.then("extract", "nothing_named")
    f.if_("read_again", "Read it again?", ("nothing_named", "again"))
    f.then("nothing_named", "read_again")
    f.add_to_list("keep_extraction", "Keep the extraction", "extractions", ("extract", "data"))
    f.then("read_again", "keep_extraction", "false")
    f.llm("extract_again", "Extract again", schema=schemas["Extraction"], prompt=("extract_prompt", "result"),
          provider=S("provider"), model=S("model"), thinking_from=TH, **_step("extract", prompts))
    f.then("read_again", "extract_again", "true")
    f.code(
        "flag_empty", "Flag a part that names nothing (a corrupt answer fails)",
        validate_code(
            "d = dict(data) if isinstance(data, dict) else {}\n"
            "text = str((item or {}).get(\"text\") or \"\") if isinstance(item, dict) else str(item or \"\")\n"
            "corrupt = extraction_problems(d, text)\n"
            "if corrupt:\n"
            "    return {\"entry\": d, \"failed\": True, \"failure\": problems_sentence(\"reading\", corrupt, True)}\n"
            "if not (d.get(\"mentions\") or []):\n"
            "    d[\"warnings\"] = list(d.get(\"warnings\") or []) + [\"A part of this note names nothing although it has \"\n"
            "                                                  \"text, even when read a second time.\"]\n"
            "return {\"entry\": d, \"failed\": False, \"failure\": \"\"}"),
        inputs={"data": ("object", ("extract_again", "data")), "item": ("object", ("each_section", "item"))},
        outputs=[("entry", "object"), ("failed", "boolean"), ("failure", "string")],
    )
    f.then("extract_again", "flag_empty")
    # Inside the loop a raising node would only end the iteration: an end node stops the run with the problem.
    f.if_("reread_corrupt", "Corrupt twice?", ("flag_empty", "failed"))
    f.then("flag_empty", "reread_corrupt")
    f.end("stopped_reading", "Stopped: the reading was corrupt twice", {
        "response": ("string", ("flag_empty", "failure")), "error": ("string", ("flag_empty", "failure")),
        "success": ("boolean", False)})
    f.then("reread_corrupt", "stopped_reading", "true")
    f.add_to_list("keep_retry", "Keep the second reading", "extractions", ("flag_empty", "entry"))
    f.then("reread_corrupt", "keep_retry", "false")
    f.get_var("extractions", "extractions", [])
    f.code("merge", "Merge the sections", MERGE_EXTRACTIONS,
           inputs={"extractions": ("array", ("extractions", "value"))},
           outputs=[("extraction", "object"), ("mentions", "array")])
    f.then("each_section", "merge", "done")
    res = f.tool_step(
        "resolve", "Find candidate entities", "note_resolve_candidates",
        {"note_id": ("string", S("note_id")), "mentions": ("array", ("merge", "mentions"))},
        [("mentions", "array")],
        after=("merge", "exec-out"),
    )
    f.code(
        "link_parts", "What linking sees",
        packet_code(["clip", "later_than", "predicate_terms", "slot_first", "note_envelope", "note_header",
                     "claim_line", "candidate_card", "link_batches"],
                    "return {\"items\": link_batches(note, extraction, resolved, open_tasks)}"),
        inputs={"note": ("object", (ctx, "note")), "extraction": ("object", ("merge", "extraction")),
                "resolved": ("array", (res, "mentions")), "open_tasks": ("array", (ctx, "open_tasks"))},
        outputs=[("items", "array")],
    )
    f.then(res, "link_parts")
    f.for_each("each_link", "For each linking part", ("link_parts", "items"))
    f.then("link_parts", "each_link")
    f.template("link_prompt", "Linking prompt", prompts["link"], {"batch": ("each_link", "item")})
    f.llm("link", "Link", schema=schemas["Linking"], prompt=("link_prompt", "result"),
          provider=S("provider"), model=S("model"), thinking_from=TH, **_step("link", prompts))
    f.then("each_link", "link", "loop")
    # A structurally corrupt linking answer (process/validate.py, ADR 0008 §8) is asked once more with a fresh
    # call; a second corrupt answer fails the note (its error is the problem), so nothing is applied in part.
    link_inputs = {"resolved": ("array", (res, "mentions")), "extraction": ("object", ("merge", "extraction")),
                   "sections": ("array", ("sections", "items"))}
    # The second call is told what was wrong (live 2026-10-20: the same prompt again gave the same candidate-less
    # reuse in 8 of 8 retries on gpt-oss-120b), after the very same message, so the cached prefix is unchanged.
    f.code(
        "link_valid", "A sound answer?",
        validate_code(_CANDIDATE_COUNTS
                      + "problems = link_problems(data, cands, context_text(texts, extraction))\n"
                        "again = str(prompt or \"\")\n"
                        "if problems:\n"
                        "    again += (\"\\n\\nYour previous answer to this message could not be used: \" + \"; \".join(problems[:5])\n"
                        "              + \". Answer again in full: a reuse or unsure decision gives its candidate's index from that \"\n"
                        "              \"mention's list (or the decision is create), and every text field holds only its own text.\")\n"
                        "return {\"ok\": not problems, \"retry_prompt\": again}"),
        inputs={"data": ("object", ("link", "data")), "prompt": ("string", ("link_prompt", "result")), **link_inputs},
        outputs=[("ok", "boolean"), ("retry_prompt", "string")],
    )
    f.then("link", "link_valid")
    f.if_("link_ok", "Sound?", ("link_valid", "ok"))
    f.then("link_valid", "link_ok")
    f.add_to_list("keep_linking", "Keep the linking", "linkings", ("link", "data"))
    f.then("link_ok", "keep_linking", "true")
    f.llm("link_again", "Link again", schema=schemas["Linking"], prompt=("link_valid", "retry_prompt"),
          provider=S("provider"), model=S("model"), thinking_from=TH, **_step("link", prompts))
    f.then("link_ok", "link_again", "false")
    f.code(
        "link_checked", "A sound second answer (else the note fails)",
        validate_code(_CANDIDATE_COUNTS
                      + "problems = link_problems(data, cands, context_text(texts, extraction))\n"
                        "return {\"entry\": data, \"failed\": bool(problems),\n"
                        "        \"failure\": problems_sentence(\"linking\", problems, True) if problems else \"\"}"),
        inputs={"data": ("object", ("link_again", "data")), **link_inputs},
        outputs=[("entry", "object"), ("failed", "boolean"), ("failure", "string")],
    )
    f.then("link_again", "link_checked")
    f.if_("relink_corrupt", "Corrupt twice?", ("link_checked", "failed"))
    f.then("link_checked", "relink_corrupt")
    f.end("stopped_linking", "Stopped: the linking was corrupt twice", {
        "response": ("string", ("link_checked", "failure")), "error": ("string", ("link_checked", "failure")),
        "success": ("boolean", False)})
    f.then("relink_corrupt", "stopped_linking", "true")
    f.add_to_list("keep_relinking", "Keep the second linking", "linkings", ("link_checked", "entry"))
    f.then("relink_corrupt", "keep_relinking", "false")
    f.get_var("linkings", "linkings", [])
    f.code("merge_links", "Merge the linking parts",
           packet_code(["merge_linkings"], "return {\"linking\": merge_linkings(linkings)}"),
           inputs={"linkings": ("array", ("linkings", "value"))}, outputs=[("linking", "object")])
    f.then("each_link", "merge_links", "done")
    rev = f.tool_step(
        "apply", "Revise the graph", "note_apply_revision",
        {"note_id": ("string", S("note_id")), "extraction": ("object", ("merge", "extraction")),
         "linking": ("object", ("merge_links", "linking"))},
        [("revision_id", "string"), ("summary", "string")],
        after=("merge_links", "exec-out"),
    )
    f.end("end", "Done", {
        "response": ("string", (rev, "summary")), "note_id": ("string", S("note_id")),
        "revision_id": ("string", (rev, "revision_id")), "revision": ("object", (rev, "tool_output")),
        "status": ("string", "processed"),
    })
    f.then(rev, "end")
    return f.finish()


def build_drain(schemas: Dict[str, Any], prompts: Dict[str, str]) -> Dict[str, Any]:
    f = Flow(
        "note-drain", "Process queued notes",
        "Take queued notes by priority and understand them one at a time, within a count and time budget.",
        interfaces=INTERFACES["note-drain"],
    )
    f.start([("max_items", "number"), ("budget_s", "number"), ("thinking", "string"), *_model_pins()],
            {**DRAIN_DEFAULTS, "thinking": "off", "provider": "", "model": ""})
    S = lambda p: ("start", p)  # noqa: E731
    f.code(
        "init", "Budget",
        "import time\n"
        "left = int(max_items if max_items is not None else 20)\n"
        "if left < 1:\n"
        "    raise ValueError(\"max_items must be at least 1.\")\n"
        "budget = float(budget_s if budget_s is not None else 90)\n"
        "now = time.time()\n"
        "return {\"updates\": {\"drain_deadline\": now + budget, \"drain_left\": left, \"drain_continue\": True,\n"
        "        \"drain_processed\": 0, \"drain_failed\": 0, \"drain_errors\": [],\n"
        "        \"drain_worker\": \"drain-\" + str(int(now * 1000))}}",
        inputs={"max_items": ("number", S("max_items")), "budget_s": ("number", S("budget_s"))},
        outputs=[("updates", "object")],
    )
    f.then("start", "init")
    f.set_vars("init_vars", "Remember the budget", ("init", "updates"))
    f.then("init", "init_vars")
    f.get_var("continue", "drain_continue", False)
    f.while_("drain_loop", "While there is budget", ("continue", "value"))
    f.then("init_vars", "drain_loop")
    f.get_var("worker", "drain_worker", "")
    lease, lease_ok = f.loop_tool_step(
        "lease", "Take the next note", "note_queue_lease",
        {"worker_id": ("string", ("worker", "value")), "max_items": ("number", 1)},
        [("items", "array")],
        after=("drain_loop", "loop"),
    )
    f.code(
        "pick", "Next note",
        "first = items[0] if isinstance(items, list) and items else None\n"
        "return {\"has_item\": isinstance(first, dict), \"note_id\": first.get(\"note_id\") if isinstance(first, dict) else None}",
        inputs={"items": ("array", (lease, "items"))}, outputs=[("has_item", "boolean"), ("note_id", "string")],
    )
    f.then(lease_ok[0], "pick", lease_ok[1])
    f.if_("has_item", "Anything queued?", ("pick", "has_item"))
    f.then("pick", "has_item")
    f.set_var("stop", "Stop: queue empty", "drain_continue", False)
    f.then("has_item", "stop", "false")
    f.subflow("process", "Understand the note", "note-process", {
        "note_id": ("string", ("pick", "note_id")), "provider": ("provider_text", S("provider")),
        "model": ("model", S("model")), "thinking": ("string", S("thinking")),
    }, [("revision_id", "string")])
    f.then("has_item", "process", "true")
    f.code(
        "outcome", "Outcome",
        "o = output if isinstance(output, dict) else {}\n"
        "c = child_output if isinstance(child_output, dict) else {}\n"
        "err = str(c.get(\"error\") or o.get(\"error\") or \"\")\n"
        "for prefix in (\"Runtime error: \", \"Execution error: \"):\n"
        "    if err.startswith(prefix):\n"
        "        err = err[len(prefix):]\n"
        "failed = c.get(\"success\") is False or o.get(\"success\") is False or bool(err) or not o.get(\"revision_id\")\n"
        "return {\"outcome\": \"error\" if failed else \"ok\",\n"
        "        \"failure\": (str(err) if err else \"The note could not be processed.\") if failed else None,\n"
        "        \"revision_id\": None if failed else o.get(\"revision_id\")}",
        inputs={"output": ("object", ("process", "output")), "child_output": ("object", ("process", "child_output"))},
        outputs=[("outcome", "string"), ("failure", "string"), ("revision_id", "string")],
    )
    f.then("process", "outcome")
    ack, ack_ok = f.loop_tool_step(
        "ack", "Record the outcome", "note_queue_ack",
        {"note_id": ("string", ("pick", "note_id")), "outcome": ("string", ("outcome", "outcome")),
         "error": ("string", ("outcome", "failure")), "revision_id": ("string", ("outcome", "revision_id"))},
        [("status", "string")],
        after=("outcome", "exec-out"),
    )
    for vid, name, default in (("left", "drain_left", 0), ("deadline", "drain_deadline", 0),
                               ("processed", "drain_processed", 0), ("failed", "drain_failed", 0),
                               ("errors", "drain_errors", [])):
        f.get_var(vid, name, default)
    f.code(
        "budget", "Budget left?",
        "import time\n"
        "ok = outcome == \"ok\"\n"
        "errs = list(errors or [])\n"
        "if not ok:\n"
        "    errs.append({\"note_id\": note_id, \"error\": failure})\n"
        "remaining = int(left or 0) - 1\n"
        "return {\"updates\": {\"drain_left\": remaining,\n"
        "        \"drain_processed\": int(processed or 0) + (1 if ok else 0),\n"
        "        \"drain_failed\": int(failed or 0) + (0 if ok else 1), \"drain_errors\": errs,\n"
        "        \"drain_continue\": remaining > 0 and time.time() < float(deadline or 0)}}",
        inputs={
            "outcome": ("string", ("outcome", "outcome")), "failure": ("string", ("outcome", "failure")),
            "note_id": ("string", ("pick", "note_id")), "left": ("number", ("left", "value")),
            "deadline": ("number", ("deadline", "value")), "processed": ("number", ("processed", "value")),
            "failed": ("number", ("failed", "value")), "errors": ("array", ("errors", "value")),
        },
        outputs=[("updates", "object")],
    )
    f.then(ack_ok[0], "budget", ack_ok[1])
    f.set_vars("budget_vars", "Remember the budget", ("budget", "updates"))
    f.then("budget", "budget_vars")
    for vid, name, default in (("r_processed", "drain_processed", 0), ("r_failed", "drain_failed", 0),
                               ("r_errors", "drain_errors", [])):
        f.get_var(vid, name, default)
    # The end of the batch (ADR 0008 §5): the themes are found again when what was applied made them due (notes
    # dated earlier than what was known, notes understood again, many revisions; no model). A failure is reported
    # in the answer, never silent, and does not undo the notes processed.
    themes = f._tool_nodes("communities", "Themes due?", "note_communities_recompute", {"only_if_due": ("boolean", True)},
                           [("recomputed", "boolean")], ("drain_loop", "done"), raising=False)
    f.code(
        "report", "Report",
        "n = int(processed or 0)\n"
        "k = int(failed or 0)\n"
        "if n == 0 and k == 0:\n"
        "    msg = \"Nothing to process.\"\n"
        "else:\n"
        "    msg = \"Processed \" + str(n) + (\" note.\" if n == 1 else \" notes.\")\n"
        "    if k:\n"
        "        msg = msg[:-1] + \"; \" + str(k) + (\" could not be processed.\" if k > 1 else \" could not be processed.\")\n"
        "c = dict(themes) if isinstance(themes, dict) else {}\n"
        "if themes_failed:\n"
        "    c = {\"recomputed\": False, \"error\": str(themes_failure or \"\")}\n"
        "    msg += \" The themes could not be updated: \" + str(themes_failure or \"no reason was given.\")\n"
        "elif c.get(\"recomputed\"):\n"
        "    g = int(c.get(\"communities\") or 0)\n"
        "    w = int(c.get(\"labels_waiting\") or 0)\n"
        "    msg += \" The themes were updated (\" + str(g) + (\" group\" if g == 1 else \" groups\")\n"
        "    msg += ((\", \" + str(w) + \" to name next time).\") if w else \").\")\n"
        "return {\"message\": msg, \"processed\": n, \"failed\": k, \"errors\": list(errors or []), \"communities\": c}",
        inputs={"processed": ("number", ("r_processed", "value")), "failed": ("number", ("r_failed", "value")),
                "errors": ("array", ("r_errors", "value")), "themes": ("object", (themes, "tool_output")),
                "themes_failed": ("boolean", (themes, "failed")), "themes_failure": ("string", (themes, "failure"))},
        outputs=[("message", "string"), ("processed", "number"), ("failed", "number"), ("errors", "array"),
                 ("communities", "object")],
    )
    f.then(themes, "report")
    f.end("end", "Done", {
        "response": ("string", ("report", "message")), "processed": ("number", ("report", "processed")),
        "failed": ("number", ("report", "failed")), "errors": ("array", ("report", "errors")),
        "communities": ("object", ("report", "communities")),
    })
    f.then("report", "end")
    return f.finish(automation_defaults=AUTOMATION_DEFAULTS["note-drain"])


RECALL_OUT_PINS = [
    ("response", "string"), ("answer", "string"), ("evidence", "array"), ("evolution", "object"),
    ("open_loops", "array"), ("follow_ups", "array"), ("hits", "object"), ("dossier_note_id", "string"),
]


def _recall_end(f: Flow, node_id: str, label: str, src: str, effort: str, hits: tuple) -> str:
    values: Dict[str, Any] = {p: (t, (src, p)) for p, t in RECALL_OUT_PINS if p != "hits"}
    values["hits"] = ("object", hits)
    values["effort"] = ("string", effort)
    return f.end(node_id, label, values)


def build_recall(schemas: Dict[str, Any], prompts: Dict[str, str]) -> Dict[str, Any]:
    f = Flow(
        "note-recall", "Ask your notes",
        "Answer a question from the notes at the requested effort (quick, standard or deep). "
        "Also serves the chat agent contract: prompt in, response out.",
        interfaces=INTERFACES["note-recall"],
    )
    f.start(
        [("question", "string"), ("prompt", "string"), ("effort", "string"), ("as_of", "string"),
         ("since", "string"), ("until", "string"), ("entities", "array"), ("topics", "array"), *_model_pins()],
        {"effort": "standard", "provider": "", "model": ""},
    )
    S = lambda p: ("start", p)  # noqa: E731
    f.code(
        "ask", "The question",
        "q = question if isinstance(question, str) and question.strip() else prompt\n"
        "if not isinstance(q, str) or not q.strip():\n"
        "    raise ValueError(\"Ask a question.\")\n"
        "e = str(effort or \"standard\").strip().lower()\n"
        "if e not in (\"quick\", \"standard\", \"deep\"):\n"
        "    raise ValueError(\"Effort must be quick, standard or deep.\")\n"
        "return {\"question_text\": q.strip(), \"effort_level\": e}",
        inputs={"question": ("string", S("question")), "prompt": ("string", S("prompt")),
                "effort": ("string", S("effort"))},
        outputs=[("question_text", "string"), ("effort_level", "string")],
    )
    f.then("start", "ask")
    hits = f.tool_step(
        "search", "Search the notes", "note_search",
        {"query": ("string", ("ask", "question_text")), "since": ("string", S("since")),
         "until": ("string", S("until")), "entities": ("array", S("entities")),
         "topics": ("array", S("topics")), "limit": ("number", 20)},
        [("notes", "array"), ("entities", "array")],
        after=("ask", "exec-out"),
    )
    f.switch("by_effort", "How hard to think", ("ask", "effort_level"), ["quick", "standard", "deep"])
    f.then(hits, "by_effort")
    # quick: no model, the hits are the answer.
    f.code(
        "quick", "List what was found",
        "found = [n for n in (notes or []) if isinstance(n, dict)]\n"
        "evidence = [{\"note_id\": n.get(\"note_id\"), \"title\": n.get(\"title\"), \"kind\": n.get(\"kind\"),\n"
        "             \"occurred_at\": n.get(\"occurred_at\"), \"excerpt\": n.get(\"excerpt\")} for n in found]\n"
        "if not found:\n"
        "    text = \"I found nothing in your notes about that.\"\n"
        "else:\n"
        "    items = [\"\\u201c\" + str(n.get(\"title\") or \"Untitled\") + \"\\u201d (\" + str(n.get(\"occurred_at\") or \"\")[:10] + \") [\" + str(n.get(\"note_id\")) + \"]\" for n in found[:5]]\n"
        "    text = \"I found \" + str(len(found)) + (\" note: \" if len(found) == 1 else \" notes: \") + \"; \".join(items) + \".\"\n"
        "return {\"response\": text, \"answer\": text, \"evidence\": evidence, \"evolution\": {}, \"open_loops\": [],\n"
        "        \"follow_ups\": [], \"dossier_note_id\": None}",
        inputs={"notes": ("array", (hits, "notes"))},
        outputs=[(p, t) for p, t in RECALL_OUT_PINS if p != "hits"],
    )
    f.then("by_effort", "quick", "case:quick")
    _recall_end(f, "end_quick", "Done (quick)", "quick", "quick", (hits, "tool_output"))
    f.then("quick", "end_quick")
    # standard: expand, then one synthesis call.
    f.code(
        "ids", "Notes and entities found",
        "return {\"note_ids\": [n.get(\"note_id\") for n in (notes or []) if isinstance(n, dict) and n.get(\"note_id\")],\n"
        "        \"entity_ids\": [x.get(\"entity_id\") for x in (entities or []) if isinstance(x, dict) and x.get(\"entity_id\")]}",
        inputs={"notes": ("array", (hits, "notes")), "entities": ("array", (hits, "entities"))},
        outputs=[("note_ids", "array"), ("entity_ids", "array")], exec_lane=False,
    )
    exp = f.tool_step(
        "expand", "Look closer", "note_expand",
        {"note_ids": ("array", ("ids", "note_ids")), "entity_ids": ("array", ("ids", "entity_ids")),
         "as_of": ("string", S("as_of")), "query": ("string", ("ask", "question_text"))},
        [],
        after=("by_effort", "case:standard"),
    )
    f.template("answer_prompt", "Answer prompt", prompts["synthesize"], {
        "question": ("ask", "question_text"), "as_of": S("as_of"), "hits": (hits, "tool_output"),
        "expansion": (exp, "tool_output"),
    })
    f.llm("synthesize", "Answer", schema=schemas["Answer"], prompt=("answer_prompt", "result"),
          provider=S("provider"), model=S("model"), **_step("synthesize", prompts))
    f.then(exp, "synthesize")
    f.code(
        "standard", "Shape the answer",
        "d = data if isinstance(data, dict) else {}\n"
        "text = str(d.get(\"answer\") or \"\").strip()\n"
        "if not text:\n"
        "    raise ValueError(\"The answer came back empty.\")\n"
        "found = {n.get(\"note_id\"): n for n in (notes or []) if isinstance(n, dict) and n.get(\"note_id\")}\n"
        "evidence, seen = [], set()\n"
        "for e in d.get(\"evidence\") or []:\n"
        "    n = found.get(e.get(\"note_id\")) if isinstance(e, dict) else None\n"
        "    if n is None or n[\"note_id\"] in seen:\n"
        "        continue\n"
        "    seen.add(n[\"note_id\"])\n"
        "    evidence.append({\"note_id\": n[\"note_id\"], \"title\": n.get(\"title\"), \"kind\": n.get(\"kind\"),\n"
        "                     \"occurred_at\": n.get(\"occurred_at\"), \"excerpt\": e.get(\"excerpt\") or n.get(\"excerpt\")})\n"
        "return {\"response\": text, \"answer\": text, \"evidence\": evidence,\n"
        "        \"evolution\": d.get(\"evolution\") or {}, \"open_loops\": d.get(\"open_loops\") or [],\n"
        "        \"follow_ups\": d.get(\"follow_ups\") or [], \"dossier_note_id\": None}",
        inputs={"data": ("object", ("synthesize", "data")), "notes": ("array", (hits, "notes"))},
        outputs=[(p, t) for p, t in RECALL_OUT_PINS if p != "hits"],
    )
    f.then("synthesize", "standard")
    _recall_end(f, "end_standard", "Done (standard)", "standard", "standard", (hits, "tool_output"))
    f.then("standard", "end_standard")
    # deep: mining subflow writes a dossier note.
    f.subflow("mine", "Dig through time", "note-mine", {
        "question": ("string", ("ask", "question_text")), "since": ("string", S("since")),
        "until": ("string", S("until")), "provider": ("provider_text", S("provider")), "model": ("model", S("model")),
    }, [(p, t) for p, t in RECALL_OUT_PINS if p != "hits"])
    f.then("by_effort", "mine", "case:deep")
    f.child_check("mine_ok", "Dug through?", "mine")
    _recall_end(f, "end_deep", "Done (deep)", "mine", "deep", (hits, "tool_output"))
    f.then("mine_ok", "end_deep")
    return f.finish()


def build_mine(schemas: Dict[str, Any], prompts: Dict[str, str]) -> Dict[str, Any]:
    f = Flow(
        "note-mine", "Dig through your notes",
        "Plan a multi-step search across time, read each slice, and write a dossier note.",
        interfaces=INTERFACES["note-mine"],
    )
    f.start([("question", "string"), ("since", "string"), ("until", "string"), *_model_pins()],
            {"question": "", "provider": "", "model": ""})
    S = lambda p: ("start", p)  # noqa: E731
    first = f.tool_step(
        "search", "First search", "note_search",
        {"query": ("string", S("question")), "since": ("string", S("since")), "until": ("string", S("until")),
         "limit": ("number", 20)},
        [("notes", "array"), ("entities", "array")],
        after=("start", "exec-out"),
    )
    f.template("plan_prompt", "Planning prompt", prompts["mine_plan"], {
        "question": S("question"), "since": S("since"), "until": S("until"), "hits": (first, "tool_output"),
    })
    f.llm("plan", "Plan the search", schema=schemas["MiningPlan"], prompt=("plan_prompt", "result"),
          provider=S("provider"), model=S("model"), **_step("mine_plan", prompts))
    f.then(first, "plan")
    f.code(
        "steps", "Steps",
        "p = plan if isinstance(plan, dict) else {}\n"
        "rounds = int(p.get(\"rounds\") or 6)\n"
        "rounds = max(1, min(rounds, 6))\n"
        "steps = [s for s in (p.get(\"steps\") or []) if isinstance(s, dict) and str(s.get(\"question\") or \"\").strip()]\n"
        "if not steps:\n"
        "    steps = [{\"question\": question, \"since\": None, \"until\": None, \"entities\": []}]\n"
        "return {\"items\": steps[:rounds]}",
        inputs={"plan": ("object", ("plan", "data")), "question": ("string", S("question"))},
        outputs=[("items", "array")],
    )
    f.then("plan", "steps")
    f.for_each("each_step", "For each step", ("steps", "items"))
    f.then("steps", "each_step")
    f.code(
        "step_query", "Step query",
        "s = item if isinstance(item, dict) else {}\n"
        "return {\"query\": str(s.get(\"question\") or \"\"), \"step_since\": s.get(\"since\") or since,\n"
        "        \"step_until\": s.get(\"until\") or until}",
        inputs={"item": ("object", ("each_step", "item")), "since": ("string", S("since")),
                "until": ("string", S("until"))},
        outputs=[("query", "string"), ("step_since", "string"), ("step_until", "string")],
    )
    f.then("each_step", "step_query", "loop")
    step_hits, step_ok = f.loop_tool_step(
        "step_search", "Search this slice", "note_search",
        {"query": ("string", ("step_query", "query")), "since": ("string", ("step_query", "step_since")),
         "until": ("string", ("step_query", "step_until")), "limit": ("number", 20)},
        [("notes", "array")],
        after=("step_query", "exec-out"),
    )
    f.template("findings_prompt", "Findings prompt", prompts["mine_findings"], {
        "question": S("question"), "step": ("each_step", "item"), "hits": (step_hits, "tool_output"),
    })
    f.llm("findings", "Read this slice", schema=schemas["SliceFindings"], prompt=("findings_prompt", "result"),
          provider=S("provider"), model=S("model"), **_step("mine_findings", prompts))
    f.then(step_ok[0], "findings", step_ok[1])
    f.code(
        "finding", "Keep the findings",
        "return {\"entry\": {\"step\": item, \"findings\": data if isinstance(data, dict) else {},\n"
        "        \"notes\": [n for n in (notes or []) if isinstance(n, dict)]}}",
        inputs={"item": ("object", ("each_step", "item")), "data": ("object", ("findings", "data")),
                "notes": ("array", (step_hits, "notes"))},
        outputs=[("entry", "object")],
    )
    f.then("findings", "finding")
    f.add_to_list("keep_finding", "Keep the findings", "mine_findings", ("finding", "entry"))
    f.then("finding", "keep_finding")
    f.get_var("all_findings", "mine_findings", [])
    f.template("dossier_prompt", "Dossier prompt", prompts["mine_dossier"], {
        "question": S("question"), "plan": ("plan", "data"), "findings": ("all_findings", "value"),
    })
    f.llm("dossier", "Write the dossier", schema=schemas["Dossier"], prompt=("dossier_prompt", "result"),
          provider=S("provider"), model=S("model"), **_step("mine_dossier", prompts))
    f.then("each_step", "dossier", "done")
    f.code(
        "dossier_note", "Dossier note",
        "d = data if isinstance(data, dict) else {}\n"
        "seen, evidence = set(), []\n"
        "pools = [first_notes or []] + [e.get(\"notes\") or [] for e in (findings or []) if isinstance(e, dict)]\n"
        "for pool in pools:\n"
        "    for n in pool:\n"
        "        nid = n.get(\"note_id\") if isinstance(n, dict) else None\n"
        "        if nid and nid not in seen:\n"
        "            seen.add(nid)\n"
        "            evidence.append({\"note_id\": nid, \"title\": n.get(\"title\"), \"kind\": n.get(\"kind\"), \"occurred_at\": n.get(\"occurred_at\"),\n"
        "                             \"excerpt\": n.get(\"excerpt\")})\n"
        "entity_ids = [x.get(\"entity_id\") for x in (first_entities or []) if isinstance(x, dict) and x.get(\"entity_id\")]\n"
        "narrative = str(d.get(\"narrative\") or \"\").strip()\n"
        "if not narrative:\n"
        "    raise ValueError(\"The dossier came back empty.\")\n"
        "lines = [narrative]\n"
        "def section(title, items):\n"
        "    rows = [str(i).strip() for i in (items or []) if str(i).strip()]\n"
        "    if rows:\n"
        "        lines.append(\"\")\n"
        "        lines.append(\"## \" + title)\n"
        "        lines.extend([\"- \" + r for r in rows])\n"
        "section(\"Timeline\", [str(t.get(\"at\") or \"\") + \": \" + str(t.get(\"event\") or \"\") for t in (d.get(\"timeline\") or []) if isinstance(t, dict)])\n"
        "if str(d.get(\"evolution\") or \"\").strip():\n"
        "    lines.extend([\"\", \"## How the view evolved\", str(d.get(\"evolution\")).strip()])\n"
        "section(\"Contradictions\", d.get(\"contradictions\"))\n"
        "section(\"Worth revisiting\", d.get(\"forgotten\"))\n"
        "section(\"Suggestions\", d.get(\"suggestions\"))\n"
        "if str(d.get(\"confidence\") or \"\").strip():\n"
        "    lines.extend([\"\", \"## Confidence\", str(d.get(\"confidence\")).strip()])\n"
        "return {\"title\": str(d.get(\"title\") or question)[:120], \"body\": \"\\n\".join(lines), \"narrative\": narrative,\n"
        "        \"evidence\": evidence, \"evidence_note_ids\": [e[\"note_id\"] for e in evidence], \"entity_ids\": entity_ids}",
        inputs={"data": ("object", ("dossier", "data")), "findings": ("array", ("all_findings", "value")),
                "first_notes": ("array", (first, "notes")), "first_entities": ("array", (first, "entities")),
                "question": ("string", S("question"))},
        outputs=[("title", "string"), ("body", "string"), ("narrative", "string"), ("evidence", "array"),
                 ("evidence_note_ids", "array"), ("entity_ids", "array")],
    )
    f.then("dossier", "dossier_note")
    wrote = f.tool_step(
        "write", "Keep the dossier", "note_insight_write",
        {"kind": ("string", "dossier"), "title": ("string", ("dossier_note", "title")),
         "body": ("string", ("dossier_note", "body")),
         "evidence_note_ids": ("array", ("dossier_note", "evidence_note_ids")),
         "entity_ids": ("array", ("dossier_note", "entity_ids"))},
        [("note_id", "string")],
        after=("dossier_note", "exec-out"),
    )
    f.code(
        "shape", "Shape the answer",
        "d = data if isinstance(data, dict) else {}\n"
        "ev = d.get(\"evolution\")\n"
        "return {\"response\": narrative, \"answer\": narrative, \"evidence\": evidence,\n"
        "        \"evolution\": {\"summary\": ev} if ev else {}, \"open_loops\": d.get(\"forgotten\") or [],\n"
        "        \"follow_ups\": d.get(\"suggestions\") or [], \"dossier_note_id\": note_id}",
        inputs={"data": ("object", ("dossier", "data")), "narrative": ("string", ("dossier_note", "narrative")),
                "evidence": ("array", ("dossier_note", "evidence")), "note_id": ("string", (wrote, "note_id"))},
        outputs=[(p, t) for p, t in RECALL_OUT_PINS if p != "hits"],
    )
    f.then(wrote, "shape")
    f.end("end", "Done", {p: (t, ("shape", p)) for p, t in RECALL_OUT_PINS if p != "hits"})
    f.then("shape", "end")
    return f.finish()


#: Insights of one kind a reflect run writes at most (eval2 D5); the model's further picks are dropped, as is a
#: second pick of the same candidate.
REFLECT_PER_KIND = 3


def build_reflect(schemas: Dict[str, Any], prompts: Dict[str, str]) -> Dict[str, Any]:
    f = Flow(
        "note-reflect", "Reflect on your notes",
        "Gather open loops, contradictions, resurfacing material and anniversaries; choose what deserves "
        "attention and write it down as insights.",
        interfaces=INTERFACES["note-reflect"],
    )
    f.start([("limit", "number"), ("since", "string"), *_model_pins()], {"limit": 10, "provider": "", "model": ""})
    S = lambda p: ("start", p)  # noqa: E731
    prev = ("start", "exec-out")
    gathered = {}
    for tool, label in (("note_open_loops", "Open loops"), ("note_contradictions", "Contradictions"),
                        ("note_resurface", "Gone quiet"), ("note_anniversaries", "Anniversaries")):
        step = tool.replace("note_", "")
        out = f.tool_step(step, label, tool, {"limit": ("number", S("limit")), "since": ("string", S("since"))},
                          [("items", "array")], after=prev)
        gathered[step] = out
        prev = (out, "exec-out")
    f.code(
        "candidates", "Candidates",
        "cands = []\n"
        "for source, items in ((\"open_loops\", open_loops), (\"contradictions\", contradictions),\n"
        "                      (\"resurface\", resurface), (\"anniversaries\", anniversaries)):\n"
        "    for it in items or []:\n"
        "        if isinstance(it, dict):\n"
        "            cands.append(dict(it, index=len(cands), source=source))\n"
        "return {\"candidates\": cands, \"count\": len(cands)}",
        inputs={k: ("array", (v, "items")) for k, v in (
            ("open_loops", gathered["open_loops"]), ("contradictions", gathered["contradictions"]),
            ("resurface", gathered["resurface"]), ("anniversaries", gathered["anniversaries"]))},
        outputs=[("candidates", "array"), ("count", "number")],
    )
    f.then(prev[0], "candidates", prev[1])
    f.template("reflect_prompt", "Reflection prompt", prompts["reflect"], {
        "candidates": ("candidates", "candidates"), "limit": S("limit"),
    })
    f.llm("reflect", "Choose what matters", schema=schemas["Reflections"], prompt=("reflect_prompt", "result"),
          provider=S("provider"), model=S("model"), **_step("reflect", prompts))
    f.then("candidates", "reflect")
    f.code(
        "chosen", "Chosen insights",
        "d = data if isinstance(data, dict) else {}\n"
        "by_index = {c.get(\"index\"): c for c in (candidates or []) if isinstance(c, dict)}\n"
        "out, dropped, used, per_kind = [], 0, set(), {}\n"
        "for it in d.get(\"items\") or []:\n"
        "    c = by_index.get(it.get(\"candidate\")) if isinstance(it, dict) else None\n"
        "    if c is None or c.get(\"index\") in used or per_kind.get(it.get(\"kind\"), 0) >= "
        + str(REFLECT_PER_KIND) + ":\n"
        "        dropped += 1\n"
        "        continue\n"
        "    used.add(c.get(\"index\"))\n"
        "    per_kind[it.get(\"kind\")] = per_kind.get(it.get(\"kind\"), 0) + 1\n"
        "    out.append({\"kind\": it.get(\"kind\"), \"title\": str(it.get(\"title\") or \"\"), \"body\": str(it.get(\"body\") or \"\"),\n"
        "                \"evidence_note_ids\": list(c.get(\"note_ids\") or []), \"entity_ids\": list(c.get(\"entity_ids\") or [])})\n"
        "cap = int(limit or 10)\n"
        "return {\"items\": out[:cap], \"dropped\": dropped}",
        inputs={"data": ("object", ("reflect", "data")), "candidates": ("array", ("candidates", "candidates")),
                "limit": ("number", S("limit"))},
        outputs=[("items", "array"), ("dropped", "number")],
    )
    f.then("reflect", "chosen")
    f.for_each("each_insight", "For each insight", ("chosen", "items"))
    f.then("chosen", "each_insight")
    f.break_object("insight", "Insight", ("each_insight", "item"), [
        ("kind", "string"), ("title", "string"), ("body", "string"), ("evidence_note_ids", "array"),
        ("entity_ids", "array"),
    ])
    wrote, wrote_ok = f.loop_tool_step(
        "write", "Write the insight", "note_insight_write",
        {"kind": ("string", ("insight", "kind")), "title": ("string", ("insight", "title")),
         "body": ("string", ("insight", "body")), "evidence_note_ids": ("array", ("insight", "evidence_note_ids")),
         "entity_ids": ("array", ("insight", "entity_ids"))},
        [("note_id", "string")],
        after=("each_insight", "loop"),
    )
    f.add_to_list("keep_written", "Remember it", "reflect_written", (wrote, "note_id"))
    f.then(wrote_ok[0], "keep_written", wrote_ok[1])
    f.get_var("written", "reflect_written", [])
    f.code(
        "report", "Report",
        "n = len(written or [])\n"
        "msg = \"Nothing needs your attention.\" if n == 0 else (\"Wrote \" + str(n) + (\" insight.\" if n == 1 else \" insights.\"))\n"
        "return {\"message\": msg, \"insight_note_ids\": list(written or []), \"considered\": int(count or 0)}",
        inputs={"written": ("array", ("written", "value")), "count": ("number", ("candidates", "count"))},
        outputs=[("message", "string"), ("insight_note_ids", "array"), ("considered", "number")],
    )
    f.then("each_insight", "report", "done")
    _label_communities(f, schemas, prompts, after=("report", "exec-out"))
    return f.finish(automation_defaults=AUTOMATION_DEFAULTS["note-reflect"])


#: The labelling step's view of the pending communities and the mapping of the model's answer back by index; the
#: reflect flow and the consolidation run the same code (backlog 0030, 0032).
LABEL_VIEW_CODE = (
    "view = []\n"
    "for p in items or []:\n"
    "    if not isinstance(p, dict):\n"
    "        continue\n"
    "    view.append({\"index\": p.get(\"index\"), \"size\": p.get(\"size\"),\n"
    "                 \"previous_label\": p.get(\"previous_label\"), \"previous_summary\": p.get(\"previous_summary\"),\n"
    "                 \"entities\": [{\"name\": e.get(\"name\"), \"kind\": e.get(\"kind\"), \"notes\": e.get(\"note_count\"),\n"
    "                               \"summary\": e.get(\"summary\")} for e in (p.get(\"top_entities\") or []) if isinstance(e, dict)],\n"
    "                 \"statements\": [c.get(\"text\") for c in (p.get(\"claims\") or []) if isinstance(c, dict)],\n"
    "                 \"notes\": [{\"title\": n.get(\"title\"), \"date\": str(n.get(\"occurred_at\") or \"\")[:10],\n"
    "                            \"excerpt\": n.get(\"excerpt\")} for n in (p.get(\"excerpts\") or []) if isinstance(n, dict)]})\n"
    "return {\"view\": view, \"any\": len(view) > 0}"
)
LABEL_MAP_CODE = (
    "d = data if isinstance(data, dict) else {}\n"
    "by_index = {p.get(\"index\"): p for p in (items or []) if isinstance(p, dict)}\n"
    "out, seen = [], set()\n"
    "for it in d.get(\"communities\") or []:\n"
    "    p = by_index.get(it.get(\"index\")) if isinstance(it, dict) else None\n"
    "    if p is None or p.get(\"community_id\") in seen:\n"
    "        continue\n"
    "    seen.add(p.get(\"community_id\"))\n"
    "    out.append({\"community_id\": p.get(\"community_id\"), \"label\": str(it.get(\"label\") or \"\"),\n"
    "                \"summary\": str(it.get(\"summary\") or \"\"), \"kind\": it.get(\"kind\")})\n"
    "return {\"items\": out}"
)


def _label_communities(f: Flow, schemas: Dict[str, Any], prompts: Dict[str, str], after: tuple) -> None:
    """The reflect flow's last part (backlog 0030): find the communities again (no model), and when some have
    no name or a stale one, name them in ONE structured call and store the names. Communities are referred to by
    index; an index the list does not have is dropped. Nothing waiting: no model call."""
    from ..recall import communities as cm

    S = lambda p: ("start", p)  # noqa: E731
    recomputed = f.tool_step("communities", "Find groups", "note_communities_recompute", {},
                             [("communities", "number")], after=after)
    waiting = f.tool_step("communities_pending", "Groups to name", "note_communities_pending",
                          {"limit": ("number", cm.PENDING_LIMIT)}, [("items", "array"), ("count", "number")],
                          after=(recomputed, "exec-out"))
    f.code(
        "communities_view", "What the model sees", LABEL_VIEW_CODE,
        inputs={"items": ("array", (waiting, "items"))},
        outputs=[("view", "array"), ("any", "boolean")],
    )
    f.then(waiting, "communities_view")
    f.if_("communities_any", "Any to name?", ("communities_view", "any"))
    f.then("communities_view", "communities_any")
    report = {"response": ("string", ("report", "message")),
              "insight_note_ids": ("array", ("report", "insight_note_ids")),
              "considered": ("number", ("report", "considered"))}
    f.end("end", "Done", {**report, "communities_labelled": ("number", 0)})
    f.then("communities_any", "end", "false")
    f.template("communities_prompt", "Naming prompt", cm.LABEL_TEMPLATE,
               {"communities": ("communities_view", "view")})
    cfg = STEP_SETTINGS["label_communities"]
    f.llm("communities_names", "Name the groups", schema=schemas["CommunityLabels"],
          prompt=("communities_prompt", "result"), provider=S("provider"), model=S("model"),
          system=cm.LABEL_SYSTEM_PROMPT, thinking=cfg["thinking"], max_output_tokens=cfg["max_output_tokens"])
    f.then("communities_any", "communities_names", "true")
    f.code(
        "communities_labels", "Names by group", LABEL_MAP_CODE,
        inputs={"data": ("object", ("communities_names", "data")), "items": ("array", (waiting, "items"))},
        outputs=[("items", "array")],
    )
    f.then("communities_names", "communities_labels")
    stored = f.tool_step("communities_label", "Keep the names", "note_communities_label",
                         {"items": ("array", ("communities_labels", "items"))}, [("labelled", "array")],
                         after=("communities_labels", "exec-out"))
    f.code("communities_named", "Named", "return {\"count\": len(labelled or [])}",
           inputs={"labelled": ("array", (stored, "labelled"))}, outputs=[("count", "number")])
    f.then(stored, "communities_named")
    f.end("end_named", "Done (groups named)", {**report, "communities_labelled": ("number", ("communities_named", "count"))})
    f.then("communities_named", "end_named")


SUMMARY_VIEW_CODE = (
    "view = []\n"
    "for i, p in enumerate(items or []):\n"
    "    if not isinstance(p, dict):\n"
    "        continue\n"
    "    e = {\"index\": i, \"name\": p.get(\"name\"), \"kind\": p.get(\"kind\"), \"aliases\": p.get(\"aliases\") or [],\n"
    "         \"summary\": p.get(\"summary\"), \"as_of\": p.get(\"as_of\"), \"statements\": p.get(\"claims\") or [],\n"
    "         \"history\": p.get(\"history\") or [], \"earlier\": p.get(\"earlier\") or [], \"notes\": p.get(\"notes\") or []}\n"
    "    left = {k: v for k, v in (p.get(\"omitted\") or {}).items() if v}\n"
    "    if left:\n"
    "        e[\"omitted\"] = left\n"
    "    view.append(e)\n"
    "return {\"view\": view, \"any\": len(view) > 0}"
)

#: A labelling or summary round asks again once when the answer leaves out an offered item (ADR 0007: an empty or
#: partial answer never ends a stage). These codes keep the first answer's entries and take the second answer only
#: for what is still missing; ``again`` is true when the first answer misses an offered item (an entry with an
#: empty label or summary counts as missing).
LABEL_FIRST_CODE = (
    "d = data if isinstance(data, dict) else {}\n"
    "by_index = {p.get(\"index\"): p for p in (items or []) if isinstance(p, dict)}\n"
    "out, seen = [], set()\n"
    "for it in d.get(\"communities\") or []:\n"
    "    p = by_index.get(it.get(\"index\")) if isinstance(it, dict) else None\n"
    "    if p is None or p.get(\"community_id\") in seen or not str(it.get(\"label\") or \"\").strip():\n"
    "        continue\n"
    "    seen.add(p.get(\"community_id\"))\n"
    "    out.append({\"community_id\": p.get(\"community_id\"), \"label\": str(it.get(\"label\") or \"\"),\n"
    "                \"summary\": str(it.get(\"summary\") or \"\"), \"kind\": it.get(\"kind\")})\n"
    "missing = [p.get(\"community_id\") for p in by_index.values() if p.get(\"community_id\") not in seen]\n"
    "return {\"items\": out, \"again\": len(missing) > 0}"
)
LABEL_RETRY_CODE = (
    "d = data if isinstance(data, dict) else {}\n"
    "by_index = {p.get(\"index\"): p for p in (items or []) if isinstance(p, dict)}\n"
    "out = [x for x in (first or []) if isinstance(x, dict)]\n"
    "seen = {x.get(\"community_id\") for x in out}\n"
    "for it in d.get(\"communities\") or []:\n"
    "    p = by_index.get(it.get(\"index\")) if isinstance(it, dict) else None\n"
    "    if p is None or p.get(\"community_id\") in seen or not str(it.get(\"label\") or \"\").strip():\n"
    "        continue\n"
    "    seen.add(p.get(\"community_id\"))\n"
    "    out.append({\"community_id\": p.get(\"community_id\"), \"label\": str(it.get(\"label\") or \"\"),\n"
    "                \"summary\": str(it.get(\"summary\") or \"\"), \"kind\": it.get(\"kind\")})\n"
    "return {\"updates\": {\"labels_answer\": out}}"
)
SUMMARY_FIRST_CODE = (
    "d = data if isinstance(data, dict) else {}\n"
    "by_index = {i: p for i, p in enumerate(items or []) if isinstance(p, dict)}\n"
    "out, seen = [], set()\n"
    "for it in d.get(\"summaries\") or []:\n"
    "    p = by_index.get(it.get(\"index\")) if isinstance(it, dict) else None\n"
    "    if p is None or p.get(\"entity_id\") in seen or not str(it.get(\"summary\") or \"\").strip():\n"
    "        continue\n"
    "    seen.add(p.get(\"entity_id\"))\n"
    "    out.append({\"entity_id\": p.get(\"entity_id\"), \"summary\": str(it.get(\"summary\") or \"\")})\n"
    "missing = [p.get(\"entity_id\") for p in by_index.values() if p.get(\"entity_id\") not in seen]\n"
    "return {\"items\": out, \"again\": len(missing) > 0}"
)
SUMMARY_RETRY_CODE = (
    "d = data if isinstance(data, dict) else {}\n"
    "by_index = {i: p for i, p in enumerate(items or []) if isinstance(p, dict)}\n"
    "out = [x for x in (first or []) if isinstance(x, dict)]\n"
    "seen = {x.get(\"entity_id\") for x in out}\n"
    "for it in d.get(\"summaries\") or []:\n"
    "    p = by_index.get(it.get(\"index\")) if isinstance(it, dict) else None\n"
    "    if p is None or p.get(\"entity_id\") in seen or not str(it.get(\"summary\") or \"\").strip():\n"
    "        continue\n"
    "    seen.add(p.get(\"entity_id\"))\n"
    "    out.append({\"entity_id\": p.get(\"entity_id\"), \"summary\": str(it.get(\"summary\") or \"\")})\n"
    "return {\"updates\": {\"sum_answer\": out}}"
)
#: Why an offered item is skipped for this run (the stage's ``reasons``, the warning log, ``left_out.reasons``).
NO_LABEL_REASON = "The model gave no name for these groups, even when asked a second time."
NO_REINTERPRET_REASON = "It could not be understood again this time."
NO_SUMMARY_REASON = "The model gave no summary for these entries, even when asked a second time."


def build_consolidate(schemas: Dict[str, Any], prompts: Dict[str, str]) -> Dict[str, Any]:
    """note-consolidate (backlog 0032, docs/cognition.md): normalize -> reinterpret (ADR 0008: loop, one marked note at a
    time through note-process, in history order, each once per pass, budget-bounded; a note whose context did not
    change is settled unchanged by the next call without a model; a note that cannot be understood again is skipped
    with its reason and its mark waits) -> communities -> labels (loop, 5 per call) ->
    summaries (loop, 8 per call, budget-bounded) -> then_now -> reflect (subflow), each stage reporting its
    progress. A labelling or summary round whose answer leaves out an offered item asks once more; what is still
    unanswered is skipped for this run (reported with the round's progress, so it is logged and counted; the labels
    pending call excludes the skipped groups, the stale-summary call never offers an entry twice in one run) and
    the loop goes on; a stage ends only when nothing it did not skip is left or the time budget is spent, and the
    close reports what was left out (``completed_with_gaps``, ADR 0007). After ``begin`` no tool step raises: a
    failure is remembered (``cons_error``) and routed to one
    failure path that closes the consolidation as failed and ends the run with the tool's sentence (inside a loop
    a raising node would only end the iteration). A node that raises inside a loop round leaves the loop's
    ``*_inflight`` flag set; the check after the loop ends the run the same way. A model call that fails ends the
    run at once (an effect failure); its row stays open and reads as ``interrupted`` (gateway view, next begin)."""
    from ..recall import communities as cm
    from ..recall import consolidation as cons

    f = Flow(
        "note-consolidate", "Consolidate your notes",
        "Merge duplicate entries, refresh the groups and their names, refresh stale summaries and the then-and-now "
        "of what changed, then reflect; every stage reports its progress.",
        interfaces=INTERFACES["note-consolidate"],
    )
    f.start([("budget_s", "number"), ("limit", "number"), ("thinking", "string"), *_model_pins()],
            {**CONSOLIDATE_DEFAULTS, "thinking": "off", "provider": "", "model": ""})
    S = lambda p: ("start", p)  # noqa: E731

    f.code(
        "thinking", "Thinking for this run",
        "t = str(thinking or \"off\").strip().lower()\n"
        "if t not in (\"on\", \"off\"):\n"
        "    raise ValueError(\"Thinking must be on or off.\")\n"
        "return {\"value\": t}",
        inputs={"thinking": ("string", S("thinking"))}, outputs=[("value", "string")],
    )
    f.then("start", "thinking")
    TH = ("thinking", "value")
    begun = f.tool_step("begin", "Begin", "note_consolidation_begin", {},
                        [("consolidation_id", "string"), ("planned_merges", "number"), ("pending", "object")],
                        after=("thinking", "exec-out"))

    # ---- the one failure path (entered from every step after begin)
    f.get_var("fail_error", "cons_error", "")
    f.code(
        "fail_results", "What failed",
        "e = str(error or \"\").strip() or \"The consolidation could not finish.\"\n"
        "for prefix in (\"Runtime error: \", \"Execution error: \"):\n"
        "    if e.startswith(prefix):\n"
        "        e = e[len(prefix):]\n"
        "return {\"results\": {\"error\": e}, \"message\": e}",
        inputs={"error": ("string", ("fail_error", "value"))}, outputs=[("results", "object"), ("message", "string")],
    )
    failed_out = f._tool_nodes("fail_end", "Close as failed", "note_consolidation_end",
                               {"status": ("string", "failed"), "results": ("object", ("fail_results", "results"))},
                               [], after=("fail_results", "exec-out"), raising=False)
    f.end("end_failed", "Stopped: the consolidation failed", {
        "response": ("string", ("fail_results", "message")), "error": ("string", ("fail_results", "message")),
        "success": ("boolean", False)})
    f.then(failed_out, "end_failed")

    def fail_from(node_id: str, handle: str, failure: tuple) -> None:
        keep = f.set_var(f"{node_id}_keep_error", "Remember the failure", "cons_error", "")
        f.wire(failure, keep, "value")
        f.then(node_id, keep, handle)
        f.then(keep, "fail_results")

    def step(step_id: str, label: str, tool: str, args: Dict[str, Any], outputs: List[tuple],
             after: tuple) -> tuple:
        out = f._tool_nodes(step_id, label, tool, args, outputs, after, raising=False)
        check = f.if_(f"{step_id}_failed", f"{label}: failed?", (out, "failed"))
        f.then(out, check)
        fail_from(check, "true", (out, "failure"))
        return out, (check, "false")

    def report(step_id: str, stage: str, done: Any, total: Any, after: tuple, skipped: Any = None,
               reason: Any = None) -> tuple:
        args: Dict[str, Any] = {"stage": ("string", stage), "done": ("number", done), "total": ("number", total)}
        if skipped is not None:
            args.update({"skipped": ("array", skipped), "reason": ("string", reason or "")})
        _, cont = step(step_id, f"Progress: {stage}", "note_consolidation_progress", args, [("eta_s", "number")],
                       after)
        return cont

    def flags(node_id: str, label: str, updates: Dict[str, Any], after: tuple) -> str:
        f.code(f"{node_id}_values", label, "return {\"updates\": " + repr(updates) + "}", inputs={},
               outputs=[("updates", "object")])
        f.then(after[0], f"{node_id}_values", after[1])
        f.set_vars(node_id, label, (f"{node_id}_values", "updates"))
        f.then(f"{node_id}_values", node_id)
        return node_id

    f.code(
        "init", "Budget",
        "import time\n"
        "budget = float(budget_s if budget_s is not None else 3600)\n"
        "if budget <= 0:\n"
        "    raise ValueError(\"budget_s must be above 0.\")\n"
        "return {\"updates\": {\"cons_deadline\": time.time() + budget, \"cons_error\": \"\",\n"
        "        \"labels_continue\": True, \"labels_done\": 0, \"labels_inflight\": False, \"labels_skipped\": [],\n"
        "        \"labels_left\": 0, \"labels_budget\": False, \"labels_answer\": [],\n"
        "        \"sum_continue\": True, \"sum_done\": 0, \"sum_inflight\": False, \"sum_skipped\": 0,\n"
        "        \"sum_left\": 0, \"sum_budget\": False, \"sum_answer\": [],\n"
        "        \"re_continue\": True, \"re_inflight\": False, \"re_failed\": [], \"re_budget\": False}}",
        inputs={"budget_s": ("number", S("budget_s"))}, outputs=[("updates", "object")],
    )
    f.then(begun, "init")
    f.set_vars("init_vars", "Remember the budget", ("init", "updates"))
    f.then("init", "init_vars")

    # ---- normalize
    cont = report("p_norm_start", "normalize", 0, (begun, "planned_merges"), ("init_vars", "exec-out"))
    norm, cont = step("normalize", "Merge duplicates", "note_entities_normalize", {}, [("merges", "number")], cont)
    f.code("norm_total", "Merges", "return {\"done\": int(merges or 0), \"total\": max(int(merges or 0), int(planned or 0))}",
           inputs={"merges": ("number", (norm, "merges")), "planned": ("number", (begun, "planned_merges"))},
           outputs=[("done", "number"), ("total", "number")], exec_lane=False)
    cont = report("p_norm_done", "normalize", ("norm_total", "done"), ("norm_total", "total"), cont)

    # ---- reinterpret (loop, ADR 0008): the notes an earlier note made out of date, one at a time, in history order
    re_first, cont = step("re_count", "Notes to understand again", "note_reinterpret_next", {"limit": ("number", 0)},
                          [("done", "number"), ("total", "number"), ("pending", "number")], cont)
    cont = report("p_re_start", "reinterpret", (re_first, "done"), (re_first, "total"), cont)
    f.get_var("re_go", "re_continue", False)
    f.while_("re_loop", "While notes wait to be understood again", ("re_go", "value"))
    f.then(cont[0], "re_loop", cont[1])
    flags("re_begin", "A re-understanding round", {"re_inflight": True, "re_continue": False}, ("re_loop", "loop"))
    f.get_var("re_deadline", "cons_deadline", 0)
    f.code("re_time", "Time left?", "import time\nreturn {\"in_time\": time.time() < float(deadline or 0)}",
           inputs={"deadline": ("number", ("re_deadline", "value"))}, outputs=[("in_time", "boolean")])
    f.then("re_begin", "re_time")
    f.if_("re_in_time", "Time left to understand a note again?", ("re_time", "in_time"))
    f.then("re_time", "re_in_time")
    flags("re_spent", "The time is spent", {"re_inflight": False, "re_continue": False, "re_budget": True},
          ("re_in_time", "false"))
    f.get_var("re_failed_last", "re_failed", [])
    re_next, cont = step("re_next", "Next note to understand again", "note_reinterpret_next",
                         {"limit": ("number", cons.REINTERPRET_BATCH), "failed": ("array", ("re_failed_last", "value"))},
                         [("items", "array"), ("done", "number"), ("total", "number"), ("pending", "number")],
                         ("re_in_time", "true"))
    f.code("re_pick", "The note",
           "first = items[0] if isinstance(items, list) and items else None\n"
           "return {\"has_item\": isinstance(first, dict),"
           " \"note_id\": first.get(\"note_id\") if isinstance(first, dict) else None}",
           inputs={"items": ("array", (re_next, "items"))}, outputs=[("has_item", "boolean"), ("note_id", "string")])
    f.then(cont[0], "re_pick", cont[1])
    f.if_("re_has", "A note to read again?", ("re_pick", "has_item"))
    f.then("re_pick", "re_has")
    flags("re_none", "Nothing left to read again", {"re_inflight": False, "re_continue": False, "re_failed": []},
          ("re_has", "false"))
    f.subflow("re_process", "Understand the note again", "note-process", {
        "note_id": ("string", ("re_pick", "note_id")), "provider": ("provider_text", S("provider")),
        "model": ("model", S("model")), "thinking": ("string", S("thinking")),
    }, [("revision_id", "string")])
    f.then("re_has", "re_process", "true")
    f.code(
        "re_outcome", "Understood again?",
        "o = output if isinstance(output, dict) else {}\n"
        "c = child_output if isinstance(child_output, dict) else {}\n"
        "err = str(c.get(\"error\") or o.get(\"error\") or \"\")\n"
        "for prefix in (\"Runtime error: \", \"Execution error: \"):\n"
        "    if err.startswith(prefix):\n"
        "        err = err[len(prefix):]\n"
        "failed = c.get(\"success\") is False or o.get(\"success\") is False or bool(err)\n"
        f"skipped = [{{\"id\": note_id, \"reason\": err or {NO_REINTERPRET_REASON!r}}}] if failed else []\n"
        "return {\"skipped\": skipped, \"done\": int(done or 0) + 1, \"total\": max(int(total or 0), int(done or 0) + 1),\n"
        "        \"updates\": {\"re_failed\": skipped, \"re_inflight\": False, \"re_continue\": True}}",
        inputs={"output": ("object", ("re_process", "output")), "child_output": ("object", ("re_process", "child_output")),
                "note_id": ("string", ("re_pick", "note_id")), "done": ("number", (re_next, "done")),
                "total": ("number", (re_next, "total"))},
        outputs=[("skipped", "array"), ("done", "number"), ("total", "number"), ("updates", "object")],
    )
    f.then("re_process", "re_outcome")
    cont = report("p_re", "reinterpret", ("re_outcome", "done"), ("re_outcome", "total"), ("re_outcome", "exec-out"),
                  skipped=("re_outcome", "skipped"), reason=NO_REINTERPRET_REASON)
    f.set_vars("re_vars", "Remember the round", ("re_outcome", "updates"))
    f.then(cont[0], "re_vars", cont[1])
    # after the loop: a round that did not reach its end ends the run; the last failure is recorded on its mark
    f.get_var("re_unfinished", "re_inflight", False)
    f.if_("re_broke", "Did understanding again stop half-way?", ("re_unfinished", "value"))
    f.then("re_loop", "re_broke", "done")
    f.code("re_broke_msg", "Understanding again failed",
           "return {\"message\": \"Understanding the notes again did not finish.\"}",
           inputs={}, outputs=[("message", "string")], exec_lane=False)
    fail_from("re_broke", "true", ("re_broke_msg", "message"))
    f.get_var("re_failed_end", "re_failed", [])
    re_last, cont = step("re_final", "Notes understood again", "note_reinterpret_next",
                         {"limit": ("number", 0), "failed": ("array", ("re_failed_end", "value"))},
                         [("done", "number"), ("total", "number"), ("pending", "number")], ("re_broke", "false"))
    cont = report("p_re_done", "reinterpret", (re_last, "done"), (re_last, "total"), cont)

    # ---- communities
    cont = report("p_comm_start", "communities", 0, 1, cont)
    comm, cont = step("communities", "Find groups", "note_communities_recompute", {},
                      [("communities", "number"), ("unlabelled", "number"), ("stale_labels", "number")], cont)
    cont = report("p_comm_done", "communities", 1, 1, cont)
    f.code("labels_total", "Groups to name", "return {\"total\": int(unlabelled or 0) + int(stale or 0)}",
           inputs={"unlabelled": ("number", (comm, "unlabelled")), "stale": ("number", (comm, "stale_labels"))},
           outputs=[("total", "number")], exec_lane=False)
    cont = report("p_labels_start", "labels", 0, ("labels_total", "total"), cont)

    # ---- labels (loop)
    f.get_var("labels_go", "labels_continue", False)
    f.while_("labels_loop", "While groups wait for a name", ("labels_go", "value"))
    f.then(cont[0], "labels_loop", cont[1])
    flags("labels_begin", "A naming round", {"labels_inflight": True, "labels_continue": False},
          ("labels_loop", "loop"))
    f.get_var("labels_skip", "labels_skipped", [])
    waiting, cont = step("communities_pending", "Groups to name", "note_communities_pending",
                         {"limit": ("number", cons.LABEL_BATCH), "exclude": ("array", ("labels_skip", "value"))},
                         [("items", "array"), ("count", "number")], ("labels_begin", "exec-out"))
    f.code("communities_view", "What the model sees", LABEL_VIEW_CODE,
           inputs={"items": ("array", (waiting, "items"))}, outputs=[("view", "array"), ("any", "boolean")])
    f.then(cont[0], "communities_view", cont[1])
    f.if_("communities_any", "Any to name?", ("communities_view", "any"))
    f.then("communities_view", "communities_any")
    flags("labels_none", "Nothing to name", {"labels_inflight": False, "labels_continue": False},
          ("communities_any", "false"))
    f.template("communities_prompt", "Naming prompt", cm.LABEL_TEMPLATE, {"communities": ("communities_view", "view")})
    cfg = STEP_SETTINGS["label_communities"]
    f.llm("communities_names", "Name the groups", schema=schemas["CommunityLabels"],
          prompt=("communities_prompt", "result"), provider=S("provider"), model=S("model"),
          system=cm.LABEL_SYSTEM_PROMPT, thinking=cfg["thinking"], max_output_tokens=cfg["max_output_tokens"],
          thinking_from=TH)
    f.then("communities_any", "communities_names", "true")
    f.code("communities_labels", "Names by group", LABEL_FIRST_CODE,
           inputs={"data": ("object", ("communities_names", "data")), "items": ("array", (waiting, "items"))},
           outputs=[("items", "array"), ("again", "boolean")])
    f.then("communities_names", "communities_labels")
    # an answer that leaves out an offered group is asked once more (the same prompt, a fresh call)
    f.if_("labels_again", "Some group without a name?", ("communities_labels", "again"))
    f.then("communities_labels", "labels_again")
    f.code("labels_first_values", "Keep the first answer",
           "return {\"updates\": {\"labels_answer\": items or []}}",
           inputs={"items": ("array", ("communities_labels", "items"))}, outputs=[("updates", "object")])
    f.then("labels_again", "labels_first_values", "false")
    f.set_vars("labels_first_vars", "Keep the first answer", ("labels_first_values", "updates"))
    f.then("labels_first_values", "labels_first_vars")
    f.llm("communities_names_again", "Name the groups again", schema=schemas["CommunityLabels"],
          prompt=("communities_prompt", "result"), provider=S("provider"), model=S("model"),
          system=cm.LABEL_SYSTEM_PROMPT, thinking=cfg["thinking"], max_output_tokens=cfg["max_output_tokens"],
          thinking_from=TH)
    f.then("labels_again", "communities_names_again", "true")
    f.code("labels_retry", "Both answers", LABEL_RETRY_CODE,
           inputs={"data": ("object", ("communities_names_again", "data")), "items": ("array", (waiting, "items")),
                   "first": ("array", ("communities_labels", "items"))}, outputs=[("updates", "object")])
    f.then("communities_names_again", "labels_retry")
    f.set_vars("labels_retry_vars", "Keep both answers", ("labels_retry", "updates"))
    f.then("labels_retry", "labels_retry_vars")
    f.get_var("labels_answer", "labels_answer", [])
    stored, cont = step("communities_label", "Keep the names", "note_communities_label",
                        {"items": ("array", ("labels_answer", "value"))}, [("labelled", "array"), ("warnings", "array")],
                        ("labels_first_vars", "exec-out"))
    f.then("labels_retry_vars", "communities_label_args")
    f.get_var("labels_so_far", "labels_done", 0)
    f.get_var("labels_deadline", "cons_deadline", 0)
    f.get_var("labels_skipped_so_far", "labels_skipped", [])
    f.code(
        "labels_count", "Named so far",
        "import time\n"
        "got = set(labelled or [])\n"
        "answered = {a.get(\"community_id\") for a in (answer or []) if isinstance(a, dict)}\n"
        "prev = [x for x in (skipped or []) if x]\n"
        "new = []\n"
        "for p in items or []:\n"
        "    c = p.get(\"community_id\") if isinstance(p, dict) else None\n"
        "    if not c or c in got or c in prev or c in [s[\"id\"] for s in new]:\n"
        "        continue\n"
        "    said = [str(w) for w in (warnings or []) if c in str(w)] if c in answered else []\n"
        "    why = said[0] if said else None\n"
        f"    new.append({{\"id\": c, \"reason\": why or {NO_LABEL_REASON!r}}})\n"
        "n = len(got)\n"
        "done = int(so_far or 0) + n\n"
        "all_skipped = prev + [s[\"id\"] for s in new]\n"
        "left = max(int(total or 0) - done - len(all_skipped), 0)\n"
        "in_time = time.time() < float(deadline or 0)\n"
        "return {\"done\": done, \"skipped\": new, \"updates\": {\"labels_done\": done, \"labels_inflight\": False,\n"
        "        \"labels_skipped\": all_skipped, \"labels_left\": left, \"labels_budget\": left > 0 and not in_time,\n"
        "        \"labels_continue\": (n + len(new)) > 0 and left > 0 and in_time}}",
        inputs={"labelled": ("array", (stored, "labelled")), "warnings": ("array", (stored, "warnings")),
                "items": ("array", (waiting, "items")), "answer": ("array", ("labels_answer", "value")),
                "skipped": ("array", ("labels_skipped_so_far", "value")),
                "so_far": ("number", ("labels_so_far", "value")),
                "total": ("number", ("labels_total", "total")), "deadline": ("number", ("labels_deadline", "value"))},
        outputs=[("done", "number"), ("skipped", "array"), ("updates", "object")],
    )
    f.then(cont[0], "labels_count", cont[1])
    cont = report("p_labels", "labels", ("labels_count", "done"), ("labels_total", "total"), ("labels_count", "exec-out"),
                  skipped=("labels_count", "skipped"), reason=NO_LABEL_REASON)
    f.set_vars("labels_vars", "Remember the round", ("labels_count", "updates"))
    f.then(cont[0], "labels_vars", cont[1])
    # after the loop: a round that did not reach its end (a model call failed) ends the run
    f.get_var("labels_unfinished", "labels_inflight", False)
    f.if_("labels_broke", "Did naming stop half-way?", ("labels_unfinished", "value"))
    f.then("labels_loop", "labels_broke", "done")
    fail_from("labels_broke", "true", ("labels_broke_msg", "message"))
    f.code("labels_broke_msg", "Naming failed", "return {\"message\": \"Naming the groups did not finish.\"}",
           inputs={}, outputs=[("message", "string")], exec_lane=False)

    # ---- summaries (loop)
    first, cont = step("stale_count", "Summaries to refresh", "note_entities_stale_summaries",
                       {"limit": ("number", 0)}, [("count", "number")], ("labels_broke", "false"))
    cont = report("p_sum_start", "summaries", 0, (first, "count"), cont)
    f.get_var("sum_go", "sum_continue", False)
    f.while_("sum_loop", "While summaries are stale", ("sum_go", "value"))
    f.then(cont[0], "sum_loop", cont[1])
    flags("sum_begin", "A summary round", {"sum_inflight": True, "sum_continue": False}, ("sum_loop", "loop"))
    batch, cont = step("stale", "Next stale summaries", "note_entities_stale_summaries",
                       {"limit": ("number", cons.SUMMARY_BATCH)}, [("items", "array"), ("count", "number")],
                       ("sum_begin", "exec-out"))
    f.code("summary_view", "What the model sees", SUMMARY_VIEW_CODE,
           inputs={"items": ("array", (batch, "items"))}, outputs=[("view", "array"), ("any", "boolean")])
    f.then(cont[0], "summary_view", cont[1])
    f.if_("summary_any", "Any stale?", ("summary_view", "any"))
    f.then("summary_view", "summary_any")
    flags("sum_none", "Nothing stale", {"sum_inflight": False, "sum_continue": False}, ("summary_any", "false"))
    f.template("summary_prompt", "Summary prompt", cons.summary_template(), {"entries": ("summary_view", "view")})
    cfg = STEP_SETTINGS["entity_summaries"]
    f.llm("summaries", "Summarise the entries", schema=schemas["EntitySummaries"], prompt=("summary_prompt", "result"),
          provider=S("provider"), model=S("model"), system=cons.summary_system_prompt(), thinking=cfg["thinking"],
          max_output_tokens=cfg["max_output_tokens"], thinking_from=TH)
    f.then("summary_any", "summaries", "true")
    f.code("summary_items", "Summaries by entry", SUMMARY_FIRST_CODE,
           inputs={"data": ("object", ("summaries", "data")), "items": ("array", (batch, "items"))},
           outputs=[("items", "array"), ("again", "boolean")])
    f.then("summaries", "summary_items")
    # an empty or partial answer is asked once more (the same prompt, a fresh call); it never ends the stage
    f.if_("sum_again", "Some entry without a summary?", ("summary_items", "again"))
    f.then("summary_items", "sum_again")
    f.code("sum_first_values", "Keep the first answer", "return {\"updates\": {\"sum_answer\": items or []}}",
           inputs={"items": ("array", ("summary_items", "items"))}, outputs=[("updates", "object")])
    f.then("sum_again", "sum_first_values", "false")
    f.set_vars("sum_first_vars", "Keep the first answer", ("sum_first_values", "updates"))
    f.then("sum_first_values", "sum_first_vars")
    f.llm("summaries_again", "Summarise the entries again", schema=schemas["EntitySummaries"],
          prompt=("summary_prompt", "result"), provider=S("provider"), model=S("model"),
          system=cons.summary_system_prompt(), thinking=cfg["thinking"], max_output_tokens=cfg["max_output_tokens"],
          thinking_from=TH)
    f.then("sum_again", "summaries_again", "true")
    f.code("sum_retry", "Both answers", SUMMARY_RETRY_CODE,
           inputs={"data": ("object", ("summaries_again", "data")), "items": ("array", (batch, "items")),
                   "first": ("array", ("summary_items", "items"))}, outputs=[("updates", "object")])
    f.then("summaries_again", "sum_retry")
    f.set_vars("sum_retry_vars", "Keep both answers", ("sum_retry", "updates"))
    f.then("sum_retry", "sum_retry_vars")
    f.get_var("sum_answer", "sum_answer", [])
    kept, cont = step("set_summaries", "Keep the summaries", "note_entities_set_summaries",
                      {"items": ("array", ("sum_answer", "value"))}, [("stored", "array"), ("left_out", "array")],
                      ("sum_first_vars", "exec-out"))
    f.then("sum_retry_vars", "set_summaries_args")
    f.get_var("sum_so_far", "sum_done", 0)
    f.get_var("sum_deadline", "cons_deadline", 0)
    f.get_var("sum_skipped_so_far", "sum_skipped", 0)
    f.code(
        "sum_count", "Refreshed so far",
        "import time\n"
        "got = set(stored or [])\n"
        "why = {x.get(\"entity_id\"): x.get(\"reason\") for x in (left_out or []) if isinstance(x, dict)}\n"
        "new = []\n"
        "for p in items or []:\n"
        "    e = p.get(\"entity_id\") if isinstance(p, dict) else None\n"
        "    if e and e not in got and e not in [s[\"id\"] for s in new]:\n"
        f"        new.append({{\"id\": e, \"reason\": why.get(e) or {NO_SUMMARY_REASON!r}}})\n"
        "n = len(got)\n"
        "done = int(so_far or 0) + n\n"
        "left = max(int(count or 0) - len(items or []), 0)\n"
        "in_time = time.time() < float(deadline or 0)\n"
        "return {\"done\": done, \"skipped\": new, \"total\": max(int(total or 0), done + left),\n"
        "        \"updates\": {\"sum_done\": done, \"sum_inflight\": False, \"sum_skipped\": int(skipped or 0) + len(new),\n"
        "        \"sum_left\": left, \"sum_budget\": left > 0 and not in_time,\n"
        "        \"sum_continue\": (n + len(new)) > 0 and left > 0 and in_time}}",
        inputs={"stored": ("array", (kept, "stored")), "left_out": ("array", (kept, "left_out")),
                "so_far": ("number", ("sum_so_far", "value")), "skipped": ("number", ("sum_skipped_so_far", "value")),
                "count": ("number", (batch, "count")), "items": ("array", (batch, "items")),
                "total": ("number", (first, "count")), "deadline": ("number", ("sum_deadline", "value"))},
        outputs=[("done", "number"), ("skipped", "array"), ("total", "number"), ("updates", "object")],
    )
    f.then(cont[0], "sum_count", cont[1])
    cont = report("p_sum", "summaries", ("sum_count", "done"), ("sum_count", "total"), ("sum_count", "exec-out"),
                  skipped=("sum_count", "skipped"), reason=NO_SUMMARY_REASON)
    f.set_vars("sum_vars", "Remember the round", ("sum_count", "updates"))
    f.then(cont[0], "sum_vars", cont[1])
    f.get_var("sum_unfinished", "sum_inflight", False)
    f.if_("sum_broke", "Did summarising stop half-way?", ("sum_unfinished", "value"))
    f.then("sum_loop", "sum_broke", "done")
    f.code("sum_broke_msg", "Summarising failed", "return {\"message\": \"Refreshing the summaries did not finish.\"}",
           inputs={}, outputs=[("message", "string")], exec_lane=False)
    fail_from("sum_broke", "true", ("sum_broke_msg", "message"))

    # ---- then and now
    cont = report("p_tn_start", "then_now", 0, 0, ("sum_broke", "false"))
    tn, cont = step("then_now", "Refresh then and now", "note_then_now_refresh", {}, [("refreshed", "number")], cont)
    cont = report("p_tn_done", "then_now", (tn, "refreshed"), (tn, "refreshed"), cont)

    # ---- reflect
    cont = report("p_reflect_start", "reflect", 0, 1, cont)
    f.subflow("reflect", "Reflect", "note-reflect", {
        "limit": ("number", S("limit")), "provider": ("provider_text", S("provider")), "model": ("model", S("model")),
    }, [("insight_note_ids", "array")])
    f.then(cont[0], "reflect", cont[1])
    f.code(
        "reflect_outcome", "Reflected?",
        "o = output if isinstance(output, dict) else {}\n"
        "c = child_output if isinstance(child_output, dict) else {}\n"
        "err = str(c.get(\"error\") or o.get(\"error\") or \"\")\n"
        "for prefix in (\"Runtime error: \", \"Execution error: \"):\n"
        "    if err.startswith(prefix):\n"
        "        err = err[len(prefix):]\n"
        "failed = c.get(\"success\") is False or o.get(\"success\") is False or bool(err)\n"
        "written = o.get(\"insight_note_ids\") if isinstance(o.get(\"insight_note_ids\"), list) else []\n"
        "return {\"failed\": failed, \"failure\": (err or \"The reflection could not finish.\") if failed else None,\n"
        "        \"insights\": len(written)}",
        inputs={"output": ("object", ("reflect", "output")), "child_output": ("object", ("reflect", "child_output"))},
        outputs=[("failed", "boolean"), ("failure", "string"), ("insights", "number")],
    )
    f.then("reflect", "reflect_outcome")
    f.if_("reflect_failed", "Reflection failed?", ("reflect_outcome", "failed"))
    f.then("reflect_outcome", "reflect_failed")
    fail_from("reflect_failed", "true", ("reflect_outcome", "failure"))
    cont = report("p_reflect_done", "reflect", 1, 1, ("reflect_failed", "false"))

    # ---- end
    # what the time budget did not reach (the close adds the skipped items still pending: left_out)
    for name, var, default in (("end_sum_budget", "sum_budget", False), ("end_sum_left", "sum_left", 0),
                               ("end_labels_budget", "labels_budget", False), ("end_labels_left", "labels_left", 0),
                               ("end_re_budget", "re_budget", False)):
        f.get_var(name, var, default)
    f.code("results", "Results",
           "sb, lb, rb = bool(sum_budget), bool(labels_budget), bool(re_budget)\n"
           "return {\"results\": {\"communities\": int(communities or 0), \"insights\": int(insights or 0),\n"
           "        \"budget_spent\": sb or lb or rb, \"not_reached\": {\"summaries\": int(sum_left or 0) if sb else 0,\n"
           "                                                \"labels\": int(labels_left or 0) if lb else 0,\n"
           "                                                \"reinterpret\": int(re_left or 0) if rb else 0}}}",
           inputs={"communities": ("number", (comm, "communities")),
                   "insights": ("number", ("reflect_outcome", "insights")),
                   "sum_budget": ("boolean", ("end_sum_budget", "value")), "sum_left": ("number", ("end_sum_left", "value")),
                   "labels_budget": ("boolean", ("end_labels_budget", "value")),
                   "labels_left": ("number", ("end_labels_left", "value")),
                   "re_budget": ("boolean", ("end_re_budget", "value")), "re_left": ("number", (re_last, "pending"))},
           outputs=[("results", "object")], exec_lane=False)
    ended, cont = step("finish", "Close the consolidation", "note_consolidation_end",
                       {"status": ("string", "completed"), "results": ("object", ("results", "results"))},
                       [("merges", "number"), ("labelled", "number"), ("summaries_refreshed", "number"),
                        ("insights", "number"), ("communities", "number"), ("status", "string"),
                        ("notice", "string"), ("reinterpreted", "number"), ("unchanged", "number")], cont)
    f.code(
        "report", "Report",
        "def n(x, one, many):\n"
        "    x = int(x or 0)\n"
        "    return str(x) + \" \" + (one if x == 1 else many)\n"
        "head = \"Consolidated with gaps: \" if status == \"completed_with_gaps\" else \"Consolidated: \"\n"
        "again = (n(reinterpreted, \"note understood again\", \"notes understood again\") + \", \") if reinterpreted else \"\"\n"
        "msg = (head + n(merges, \"merge\", \"merges\") + \", \" + again + n(labelled, \"group named\", \"groups named\")\n"
        "       + \", \" + n(summaries, \"summary refreshed\", \"summaries refreshed\") + \", \"\n"
        "       + n(insights, \"insight\", \"insights\") + \".\")\n"
        "if notice:\n"
        "    msg += \" \" + str(notice)[0].upper() + str(notice)[1:]\n"
        "return {\"message\": msg}",
        inputs={"merges": ("number", (ended, "merges")), "labelled": ("number", (ended, "labelled")),
                "summaries": ("number", (ended, "summaries_refreshed")), "insights": ("number", (ended, "insights")),
                "status": ("string", (ended, "status")), "notice": ("string", (ended, "notice")),
                "reinterpreted": ("number", (ended, "reinterpreted"))},
        outputs=[("message", "string")],
    )
    f.then(cont[0], "report", cont[1])
    f.end("end", "Done", {
        "response": ("string", ("report", "message")), "consolidation": ("object", (ended, "tool_output")),
        "consolidation_id": ("string", (begun, "consolidation_id")),
        "insight_note_ids": ("array", ("reflect", "insight_note_ids")),
    })
    f.then("report", "end")
    return f.finish(automation_defaults=AUTOMATION_DEFAULTS["note-consolidate"])


BUILDERS = {
    "note-capture": build_capture,
    "note-process": build_process,
    "note-drain": build_drain,
    "note-recall": build_recall,
    "note-mine": build_mine,
    "note-reflect": build_reflect,
    "note-consolidate": build_consolidate,
}


def build_all() -> Dict[str, Dict[str, Any]]:
    schemas = load_schemas()
    prompts = load_prompts()
    for name, allowed in FLOW_PROMPT_VARS.items():
        if name not in prompts:
            raise RuntimeError(f"prompt {name!r} is missing")
    return {fid: BUILDERS[fid](schemas, prompts) for fid in ALL_FLOWS}


def render(flow: Dict[str, Any]) -> str:
    return json.dumps(flow, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_all(out_dir: Path = JSON_DIR) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for fid, flow in build_all().items():
        path = out_dir / f"{fid}.json"
        path.write_text(render(flow), encoding="utf-8")
        paths.append(path)
    return paths


def stale(out_dir: Path = JSON_DIR) -> List[str]:
    out = []
    for fid, flow in build_all().items():
        path = out_dir / f"{fid}.json"
        if not path.is_file() or path.read_text(encoding="utf-8") != render(flow):
            out.append(fid)
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m abstractnote.flows.build", description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=JSON_DIR, help="output directory (default: the package's flows/json)")
    ap.add_argument("--check", action="store_true", help="exit 1 when the JSON on disk differs from the generator")
    args = ap.parse_args(argv)
    if args.check:
        bad = stale(args.out)
        if bad:
            print("stale flows: " + ", ".join(bad), file=sys.stderr)
            return 1
        print("flows up to date")
        return 0
    for p in write_all(args.out):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
