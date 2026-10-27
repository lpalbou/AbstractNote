"""FIX1: every schema a model is handed is strict (every property required, an optional one nullable), the answer
parsing reads null as absent, one statement linked to several existing ones says what it did not apply, and a note
with no text to read fails once with its reason instead of three retries.

Why strict (R4, reproduced live): AbstractCore sends the runtime's model as ``response_format: json_schema`` with
only the required properties in ``required``; OVH's constrained decoder writes the properties of an object in
alphabetical order and SKIPS the optional ones, so a ``reuse`` arrived without its ``candidate`` and ``reason``
(written after ``summary`` in the prompt's example) spilled into the summary string.
"""

from __future__ import annotations

import json

import pytest

from abstractnote.flows.build import load_schemas
from abstractnote.flows.contract import load_flows
from abstractnote.flows.runner import NoteRunner, ScriptedLLM, null_filled
from abstractnote.process import schemas
from abstractnote.tools.context import ToolContext
from abstractnote.tools.queue import NO_TEXT_ERROR, note_queue_ack
from test_process import claim_index, extraction, mention, ml, run, seed, stmt
from test_store_support import nb  # noqa: F401

SENT = ("QuickFiling", "Extraction", "Linking", "Answer", "MiningPlan", "SliceFindings", "Dossier", "Reflections",
        "CommunityLabels", "EntitySummaries")


def _llm_nodes():
    for fid, flow in load_flows().items():
        for node in flow["nodes"]:
            if node.get("type") == "llm_call":
                yield fid, node["id"], node["data"]["pinDefaults"]["resp_schema"]


# ----------------------------------------------------------------------------- 1. strict schemas


def test_every_schema_a_flow_sends_has_every_property_required():
    """The committed flows JSON (what the gateway runs): every llm_call node's resp_schema, nested objects and
    array items included, lists every property in ``required``. Red when strict() is not applied (load_schemas) or
    the JSON is stale."""
    nodes = list(_llm_nodes())
    assert len(nodes) >= 14  # filing, extract(+again), link(+again), answer, plan, findings, dossier, reflect, ...
    seen = set()
    for fid, nid, schema in nodes:
        assert schemas.optional_properties(schema) == [], f"{fid}.{nid}"
        seen.add(json.dumps(sorted(schema["properties"])))
    assert len(seen) == len(SENT)  # every schema reaches at least one node


def test_the_checker_finds_an_optional_property_at_any_depth():
    """The check above is not decoration: the source schemas (before strict) have optional properties, nested in
    array items and in a nullable object, and the checker names them."""
    found = schemas.optional_properties(schemas.LINKING)
    assert "$.mentions.items.candidate" in found and "$.claims.items.existing" in found
    assert "$.tasks.items.closes_open_task" in found
    assert "$.document_date" in schemas.optional_properties(schemas.EXTRACTION)
    assert "$.summaries.items.ties" in schemas.optional_properties(schemas.ENTITY_SUMMARIES)
    assert schemas.optional_properties(load_schemas()["Linking"]) == []


def test_strict_makes_an_optional_property_nullable_and_says_when_null():
    s = schemas.strict(schemas.LINKING)
    m = s["properties"]["mentions"]["items"]["properties"]
    assert m["candidate"]["type"] == ["integer", "null"] and "default" not in m["candidate"]
    assert m["candidate"]["description"].endswith("null when not applicable")
    assert m["kind"]["enum"][-1] is None and m["kind"]["type"] == ["string", "null"]  # the runtime reads enum first
    assert m["aliases"]["type"] == ["array", "null"] and m["aliases"]["description"] == "null when empty"
    assert m["ref"]["type"] == "string"  # a required property stays as it was: never null
    c = schemas.strict(schemas.EXTRACTION)["properties"]["claims"]["items"]["properties"]
    assert c["certainty"]["description"] == 'null means "stated"' and None in c["certainty"]["enum"]
    assert c["object_is_literal"]["type"] == ["boolean", "null"]
    d = schemas.strict(schemas.EXTRACTION)["properties"]["document_date"]
    assert d["type"] == ["object", "null"] and sorted(d["required"]) == ["basis", "iso", "quote"]
    assert schemas.strict(schemas.LINKING) == schemas.strict(schemas.strict(schemas.LINKING))  # idempotent
    assert schemas.LINKING["properties"]["mentions"]["items"]["required"] == ["ref", "decision", "summary"]  # source kept


def test_the_schema_sent_to_the_provider_requires_every_property():
    """What OVH receives: AbstractRuntime builds a pydantic model from resp_schema and AbstractCore's
    openai-compatible provider sends its ``model_json_schema()`` (refs inlined) as response_format. Every property
    of that schema is required (a nullable one as ``anyOf [..., null]`` or an enum holding null)."""
    from abstractruntime.integrations.abstractcore.effect_handlers import _pydantic_model_from_json_schema

    try:
        from abstractcore.providers.openai_compatible_provider import _inline_json_schema_refs
    except ImportError:  # pragma: no cover - AbstractCore is a dependency of the runtime integration
        pytest.fail("AbstractCore's openai-compatible provider is not importable")
    for name, schema in load_schemas().items():
        model = _pydantic_model_from_json_schema(schema, name="LLM_StructuredOutput")
        sent = _inline_json_schema_refs(model.model_json_schema())
        assert schemas.optional_properties(sent) == [], name
    before = _pydantic_model_from_json_schema(schemas.LINKING, name="LLM_StructuredOutput")
    assert "$.mentions.items.candidate" in schemas.optional_properties(
        _inline_json_schema_refs(before.model_json_schema()))  # the R4 cause, red without strict()


def test_the_local_mlx_path_compiles_the_strict_schemas():
    """AbstractCore's MLX provider constrains with Outlines (``outlines.json_schema(response_model)``): each strict
    schema compiles to its regex, which accepts an answer with explicit nulls and refuses one that leaves out a
    property (so the local path writes every field too)."""
    outlines = pytest.importorskip("outlines")
    import re

    from outlines.types.dsl import to_regex
    from abstractruntime.integrations.abstractcore.effect_handlers import _pydantic_model_from_json_schema

    regexes = {}
    for name, schema in load_schemas().items():
        model = _pydantic_model_from_json_schema(schema, name="LLM_StructuredOutput")
        regexes[name] = to_regex(outlines.json_schema(model))
    answer = {"mentions": [{"ref": "m1", "decision": "reuse", "candidate": 0, "merge_candidates": None, "name": None,
                            "kind": None, "aliases": None, "summary": None, "reason": None}],
              "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": 2, "reason": None}],
              "tasks": [], "revision_summary": "Dana moved."}
    assert re.fullmatch(regexes["Linking"], json.dumps(answer, separators=(",", ":")))
    del answer["mentions"][0]["candidate"]
    assert not re.fullmatch(regexes["Linking"], json.dumps(answer, separators=(",", ":")))


def test_null_reads_as_absent_in_the_answer_parsing():
    """A strict answer carries null for what it has nothing to say: the mirrors read the default."""
    link = schemas.validate("Linking", {
        "mentions": [{"ref": "m1", "decision": "create", "candidate": None, "merge_candidates": None, "name": None,
                      "kind": None, "aliases": None, "summary": "Dana.", "reason": None}],
        "claims": [{"index": 0, "relation_to_existing": None, "existing": None, "reason": None}],
        "tasks": [{"index": 0, "closes_open_task": None, "evidence": None}], "revision_summary": None})
    m = link.mentions[0]
    assert (m.merge_candidates, m.name, m.aliases, m.reason, m.kind) == ([], "", [], "", None)
    assert link.claims[0].relation_to_existing == "new" and link.tasks[0].evidence == ""
    ext = schemas.validate("Extraction", {
        "summary": None, "mentions": [{"ref": "m1", "name": "Dana", "kind": None, "aliases": None, "role": None}],
        "claims": [{"subject": "m1", "predicate": "ex:x", "object": "y", "object_is_literal": None, "certainty": None,
                    "stance": None, "evidence": None, "valid_from": None, "valid_until": None}],
        "tasks": None, "time_refs": None, "topics": None, "document_date": None})
    c = ext.claims[0]
    assert (c.object_is_literal, c.certainty, c.stance, ext.mentions[0].kind, ext.tasks) == (
        False, "stated", "assert", "thing", [])


def test_a_duplicate_that_differs_only_by_null_is_not_a_contradiction():
    from abstractnote.process.validate import link_problems

    a = {"ref": "m1", "decision": "create", "name": "Dana", "aliases": [], "reason": ""}
    b = {"ref": "m1", "decision": "create", "name": "Dana", "aliases": None, "reason": None}
    assert link_problems({"mentions": [a, b]}, {"m1": 0}) == []
    assert link_problems({"mentions": [a, dict(b, name="Dan")]}, {"m1": 0}) == ["mention m1 is answered twice, "
                                                                                 "differently"]


def test_the_scripted_model_answers_null_for_what_a_script_leaves_out():
    """ScriptedLLM answers like a model under the strict schema: a nullable property the script leaves out is
    null; a required non-nullable one left out still fails the call."""
    strict_link = load_schemas()["Linking"]
    filled = null_filled({"mentions": [{"ref": "m1", "decision": "create"}], "claims": [], "tasks": []},
                         strict_link)
    assert filled["mentions"][0]["candidate"] is None and filled["mentions"][0]["summary"] is None
    assert "revision_summary" not in filled  # required and not nullable: never invented
    assert "ref" not in null_filled({"mentions": [{"decision": "create"}]}, strict_link)["mentions"][0]
    from pydantic import ValidationError
    from abstractruntime.integrations.abstractcore.effect_handlers import _pydantic_model_from_json_schema

    model = _pydantic_model_from_json_schema(strict_link, name="LLM_StructuredOutput")
    model.model_validate(dict(filled, revision_summary="x"))
    with pytest.raises(ValidationError):
        model.model_validate(filled)


# ----------------------------------------------------------------------------- 2. one statement, several links


def test_a_statement_linked_to_several_existing_ones_says_which_links_were_not_applied(nb):
    """R1 leftover (ADR 0007): the builder applies one link per statement, the first; the others are named in a
    ``multi_link`` warning instead of being dropped silently."""
    _, _, _, out0 = seed(nb)
    by_pred = {nb.store.claim_row(c)["predicate"]: c for c in out0["claims_added"]}
    works, part = by_pred["ex:works_at"], by_pred["schema:participant"]
    ext = extraction([mention("m1", "Dana", "person"), mention("m2", "Beta", "org")],
                     [stmt("m1", "ex:works_at", "m2", stance="revise")])

    def link(packet, cands):
        a, b = claim_index(packet, cands, works), claim_index(packet, cands, part)
        return {"mentions": [ml("m1", "reuse", 0), ml("m2", "create", name="Beta", kind="org")],
                "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": a, "reason": "moved"},
                           {"index": 0, "relation_to_existing": "supersedes", "existing": b, "reason": "moved"},
                           {"index": 0, "relation_to_existing": "supersedes", "existing": a, "reason": "moved"}],
                "tasks": [], "revision_summary": "Dana moved to Beta."}

    _, _, _, out = run(nb, "Dana left Acme and joined Beta.", "2026-03-01T10:00:00Z", ext, link)
    assert out["claims_superseded"] == [works]
    assert nb.store.claim_row(part)["status"] == "active"
    [w] = [w for w in out["warnings"] if w.startswith("multi_link:")]
    assert w.startswith("multi_link: statement 0 was linked to 2 existing statements; only the link to #")
    assert "(supersedes) was applied" in w and w.endswith("was not.")


def test_a_task_closing_several_open_tasks_says_so(nb):
    seed(nb)
    ext = extraction([mention("m1", "Dana", "person")], [], [{"text": "Sent Dana the Pareto", "status": "done"}])

    def link(packet, cands):
        return {"mentions": [ml("m1", "reuse", 0)], "claims": [],
                "tasks": [{"index": 0, "closes_open_task": 0, "evidence": "Sent Dana the Pareto"},
                          {"index": 0, "closes_open_task": 3, "evidence": "Sent Dana the Pareto"}],
                "revision_summary": "Pareto sent."}

    _, _, _, out = run(nb, "Sent Dana the Pareto.", "2026-01-08T10:00:00Z", ext, link)
    assert any(w.startswith("multi_link: task 0 was linked to 2 open tasks; only open task #0 was considered")
               for w in out["warnings"])


# ----------------------------------------------------------------------------- 3. no text to read


def _blank(nb, note_id):
    with nb.store.tx() as c:
        c.execute("UPDATE notes SET text='', title=NULL WHERE note_id=?", (note_id,))


def test_a_note_with_no_text_to_read_fails_once_with_its_reason(nb):
    """B item 11: a note whose stored text is gone (no words, no title, no extracted file) is never retried: the
    first failure is final, recorded in queue_errors and last_error."""
    ctx = ToolContext(nb=nb)
    note_id = nb.capture("Something to read.", occurred_at="2026-10-19T12:00:00Z").note_id
    _blank(nb, note_id)
    nb.queue.lease("w", 1)
    out = note_queue_ack(ctx, note_id, "error", "Runtime error: something else went wrong")
    assert out["status"] == "failed"
    row = nb.queue.item(note_id)
    assert row["state"] == "failed" and row["failures"] == 1 and row["attempts"] == 1
    assert [e["error"] for e in nb.queue.errors(note_id)] == ["Runtime error: something else went wrong"]
    other = nb.capture("Another note.", occurred_at="2026-10-19T12:00:00Z").note_id
    nb.queue.lease("w", 1)
    assert note_queue_ack(ctx, other, "error", "The model timed out.")["status"] == "queued"  # a note with text retries


def test_the_drain_takes_a_note_with_no_text_once(tmp_path):
    """Through the real flows: note-process stops at its sections step, the drain acks it, the note is failed at
    once with "The note has no text to read." and the next drain finds nothing to process."""
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    root = tmp_path / "notebook"
    runner = NoteRunner.with_llm(ScriptedLLM({}), bind(root))
    nb = Notebook.open(root)
    try:
        note_id = nb.capture("Something to read.", occurred_at="2026-10-19T12:00:00Z").note_id
        _blank(nb, note_id)
    finally:
        nb.close()
    drained = runner.run_drain(max_items=5, budget_s=60)
    assert drained["failed"] == 1 and drained["processed"] == 0
    assert runner.run_drain()["response"] == "Nothing to process."
    nb = Notebook.open(root)
    try:
        row = nb.queue.item(note_id)
        assert row["state"] == "failed" and row["failures"] == 1
        assert [e["error"] for e in nb.queue.errors(note_id)] == [NO_TEXT_ERROR]
    finally:
        nb.close()
