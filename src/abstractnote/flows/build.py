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
ENTRYPOINTS = ["note-capture", "note-drain", "note-recall", "note-reflect"]
INTERNAL_FLOWS = ["note-process", "note-mine"]
ALL_FLOWS = ENTRYPOINTS + INTERNAL_FLOWS

INTERFACES = {
    "note-capture": ["abstractnote.capture.v1"],
    "note-drain": ["abstractnote.drain.v1"],
    "note-recall": ["abstractnote.recall.v1", "abstractcode.agent.v1"],
    "note-reflect": ["abstractnote.reflect.v1"],
    "note-process": [],
    "note-mine": [],
}

#: Tools each flow may call (the automations' `_runtime.allowed_tools` is the union).
NOTE_TOOLS = [
    "note_capture", "note_file", "note_context", "note_resolve_candidates", "note_apply_revision",
    "note_queue_lease", "note_queue_ack", "note_queue_stats", "note_search", "note_expand",
    "note_changes", "note_entity", "note_timeline", "note_open_loops", "note_contradictions",
    "note_resurface", "note_anniversaries", "note_insight_write",
]

DRAIN_DEFAULTS = {"max_items": 20, "budget_s": 90}
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


def load_schemas() -> Dict[str, Dict[str, Any]]:
    """The structured-output schemas: the core lane's when importable, checked against ours."""
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
        },
        [("note_id", "string"), ("status", "string"), ("first_saved_at", "string"), ("received_at", "string"),
         ("similar", "array"), ("revisits", "array"), ("topics", "array"), ("warnings", "array")],
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
        "for w in warnings or []:\n"
        "    if isinstance(w, str) and w.strip():\n"
        "        parts.append(w.strip())\n"
        "return {\"message\": \" \".join(parts)}",
        inputs={
            "kind": ("string", ("filing_fields", "kind")), "title": ("string", ("filing_fields", "title")),
            "similar": ("array", (cap, "similar")), "revisits": ("array", (cap, "revisits")),
            "warnings": ("array", (cap, "warnings")),
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
    f.child_check("process_ok", "Understood?", "process")
    ack = f.tool_step(
        "inline_ack", "Mark it processed", "note_queue_ack",
        {
            "note_id": ("string", (cap, "note_id")), "outcome": ("string", "ok"),
            "revision_id": ("string", ("process", "revision_id")),
        },
        [("status", "string")],
        after=("process_ok", "exec-out"),
    )
    f.code(
        "processed_msg", "Processed answer",
        "extra = str(summary or \"\").strip()\n"
        "return {\"message\": (str(message) + \" \" + extra).strip()}",
        inputs={"message": ("string", ("saved_msg", "message")), "summary": ("string", ("process", "response"))},
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
    if warnings:
        merged["warnings"] = warnings
names = [{"ref": m.get("ref"), "name": m.get("name"), "kind": m.get("kind"), "aliases": m.get("aliases") or []}
         for m in merged.get("mentions") or []]
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
         ("vocabulary", "array")],
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
    # empty lists); a second empty answer is kept and flagged in the revision's warnings.
    f.code(
        "nothing_named", "Nothing named?",
        "d = data if isinstance(data, dict) else {}\n"
        "text = str((item or {}).get(\"text\") or \"\") if isinstance(item, dict) else str(item or \"\")\n"
        f"return {{\"again\": not (d.get(\"mentions\") or []) and len(text.strip()) >= {EMPTY_RETRY_MIN_CHARS}}}",
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
        "flag_empty", "Flag a part that names nothing",
        "d = dict(data) if isinstance(data, dict) else {}\n"
        "if not (d.get(\"mentions\") or []):\n"
        "    d[\"warnings\"] = list(d.get(\"warnings\") or []) + [\"A part of this note names nothing although it has \"\n"
        "                                                  \"text, even when read a second time.\"]\n"
        "return {\"entry\": d}",
        inputs={"data": ("object", ("extract_again", "data"))}, outputs=[("entry", "object")],
    )
    f.then("extract_again", "flag_empty")
    f.add_to_list("keep_retry", "Keep the second reading", "extractions", ("flag_empty", "entry"))
    f.then("flag_empty", "keep_retry")
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
        packet_code(["clip", "note_envelope", "note_header", "claim_line", "candidate_card", "link_batches"],
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
    f.add_to_list("keep_linking", "Keep the linking", "linkings", ("link", "data"))
    f.then("link", "keep_linking")
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
        "return {\"message\": msg, \"processed\": n, \"failed\": k, \"errors\": list(errors or [])}",
        inputs={"processed": ("number", ("r_processed", "value")), "failed": ("number", ("r_failed", "value")),
                "errors": ("array", ("r_errors", "value"))},
        outputs=[("message", "string"), ("processed", "number"), ("failed", "number"), ("errors", "array")],
    )
    f.then("drain_loop", "report", "done")
    f.end("end", "Done", {
        "response": ("string", ("report", "message")), "processed": ("number", ("report", "processed")),
        "failed": ("number", ("report", "failed")), "errors": ("array", ("report", "errors")),
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
        "out, dropped = [], 0\n"
        "for it in d.get(\"items\") or []:\n"
        "    c = by_index.get(it.get(\"candidate\")) if isinstance(it, dict) else None\n"
        "    if c is None:\n"
        "        dropped += 1\n"
        "        continue\n"
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
    f.end("end", "Done", {
        "response": ("string", ("report", "message")), "insight_note_ids": ("array", ("report", "insight_note_ids")),
        "considered": ("number", ("report", "considered")),
    })
    f.then("report", "end")
    return f.finish(automation_defaults=AUTOMATION_DEFAULTS["note-reflect"])


BUILDERS = {
    "note-capture": build_capture,
    "note-process": build_process,
    "note-drain": build_drain,
    "note-recall": build_recall,
    "note-mine": build_mine,
    "note-reflect": build_reflect,
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
