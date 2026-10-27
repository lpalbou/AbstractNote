"""Backlog 0037: every revision, projected assertion and closure names where it came from in AbstractRuntime's ledger.

The shapes are AbstractMemory's (``abstractmemory.graph.SourceRef`` / ``Provenance``, contract §1-§2): the parity
tests read what ``store/kg.py`` wrote back through ``Provenance.from_dict``, so a drift between the two packages
fails here. The step comes from AbstractRuntime's own step context (``abstractruntime.core.step_context``); the
end-to-end runs the real flows on a real Runtime and checks the ledger row the provenance points at.
"""

from __future__ import annotations

import sqlite3

import pytest

from abstractmemory.graph import Provenance, SourceRef
from abstractruntime.core.step_context import StepContext, effect_step_context
from abstractruntime.storage.artifacts import FileArtifactStore, compute_artifact_id

from abstractnote.ledger import note_columns, step_address
from abstractnote.store.notebook import Notebook
from abstractnote.store.schema import SCHEMA_VERSION
from test_store_support import claim, create, nb, rev, t  # noqa: F401 - nb is a fixture

STEP = {"run_id": "run-apply", "seq": 7, "step_id": "st-apply"}


def _history(nb, *, ledger=STEP, note_id=None):
    first = rev("rv_1", t(1), create("ex:person-dana", "Dana", aliases=["D. Ng"]), create("ex:org-acme", "Acme"),
                claim("cl_1", "ex:person-dana", "schema:worksFor", "ex:org-acme", at=t(1), note_id=note_id),
                note_id=note_id)
    if ledger is not None:
        first["ledger"] = dict(ledger)
    nb.apply_revision(first)
    second = rev("rv_2", t(10), create("ex:org-globex", "Globex"),
                 claim("cl_2", "ex:person-dana", "schema:worksFor", "ex:org-globex", at=t(10)),
                 {"op": "claim.supersede", "payload": {"claim_id": "cl_1", "by_claim_id": "cl_2",
                                                       "valid_until": t(10), "reason": "moved"}})
    if ledger is not None:
        second["ledger"] = {**ledger, "seq": ledger.get("seq") and ledger["seq"] + 2}
    nb.apply_revision(second)


def _step(run_id="run-apply", seq=7, step_id="st-apply"):
    return StepContext(run_id=run_id, step_id=step_id, seq=seq, node_id="n1", attempt=1, effect_type="tool_calls")


# ---------------------------------------------------------------------------- 1. the contract shape


def test_a_claim_names_the_step_that_applied_it_in_abstractmemorys_shape(nb):
    _history(nb)
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    assert a.provenance == {
        "sources": [{"kind": "step", "run_id": "run-apply", "seq": 7, "step_id": "st-apply"}],
        "derived_from": [], "actor": "abstractnote", "reader": None, "reason": None,
        "extra": {"revision_id": "rv_1", "recorded_at": a.provenance["extra"]["recorded_at"],
                  "evidence_quote": "ex:person-dana schema:worksFor ex:org-acme"},
    }
    # parity: AbstractMemory reads it as is (strict: no key outside the six)
    prov = Provenance.from_dict(a.provenance)
    assert prov.actor == "abstractnote" and prov.is_grounded()
    assert [s.key() for s in prov.sources] == ["step:run-apply#7"]
    [name] = nb.kg.query(subject="ex:person-dana", predicate="schema:name")
    assert Provenance.from_dict(name.provenance).sources == (SourceRef.from_dict(STEP | {"kind": "step"}),)


def test_a_closure_names_the_step_that_closed_it_in_the_same_shape(nb):
    _history(nb)
    [closure] = nb.kg.journal.closures(assertion_id="cl_1")
    prov = Provenance.from_dict(closure.provenance)
    assert [s.key() for s in prov.sources] == ["step:run-apply#9"]
    assert prov.actor == "abstractnote"
    assert prov.extra["revision_id"] == "rv_2" and prov.extra["valid_until"] == t(10)
    # observation time is the closing revision's date (as MemoryGraph stamps it), never its record time (N8)
    rv2 = nb.revision("rv_2")
    assert rv2["at"] != rv2["recorded_at"]
    from datetime import datetime

    def when(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    assert when(closure.observed_at) == when(rv2["at"])


def test_without_its_row_a_step_is_a_run_ref_and_its_step_id_waits_in_extra(nb):
    _history(nb, ledger={"run_id": "run-apply", "step_id": "st-apply"})
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    prov = Provenance.from_dict(a.provenance)
    assert [s.to_dict() for s in prov.sources] == [{"kind": "run", "run_id": "run-apply"}]
    assert prov.extra["step_id"] == "st-apply"


def test_a_revision_written_outside_any_run_names_no_source(nb):
    """Nothing is invented: no run, no step, no artifact (the CLI, a test). Ungrounded for AbstractMemory's graph
    write path; the one-time projection decides what grounds these (operator decision 1/4)."""
    _history(nb, ledger=None)
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    assert a.provenance["sources"] == [] and not Provenance.from_dict(a.provenance).is_grounded()


def test_a_bare_run_id_on_a_revision_is_a_label_not_a_source(nb):
    """Review N2/N4: only an address that names its step (seq or step id) is a source."""
    _history(nb, ledger={"run_id": "run-told"})
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    assert a.provenance["sources"] == [] and a.provenance["extra"]["run_id"] == "run-told"
    assert not Provenance.from_dict(a.provenance).is_grounded()


def test_a_note_run_id_without_its_step_is_a_label_not_a_source(nb):
    """Review N4: ``capture(run_id=...)`` (the CLI, importers, notes written before 0037) stores a run id nothing
    checked against a ledger; it never becomes a ``run`` source of the note's statements."""
    res = nb.capture("Met Dana at Acme.", run_id="run-old")
    assert nb.store.note_row(res.note_id)["run_id"] == "run-old"
    assert nb.store.note_sources(res.note_id) == [] and nb.store.note_run_label(res.note_id) == "run-old"
    nb.apply_revision(dict(rev("rv_1", t(1), create("ex:person-dana", "Dana"),
                               claim("cl_1", "ex:person-dana", "schema:name", literal="Dana", at=t(1),
                                     note_id=res.note_id), note_id=res.note_id), ledger=STEP))
    prov = nb.kg.query(assertion_ids=("cl_1",))[0].provenance
    assert prov["sources"] == [{"kind": "step", "run_id": "run-apply", "seq": 7, "step_id": "st-apply"}]
    assert prov["extra"]["note_run_id"] == "run-old"
    before = {a.assertion_id: a.provenance for a in nb.kg.query()}
    nb.rebuild()
    assert {a.assertion_id: a.provenance for a in nb.kg.query()} == before


def test_a_ledger_address_is_checked_never_guessed():
    assert step_address("r", 0, "s") == {"run_id": "r", "seq": 0, "step_id": "s"}
    assert step_address("r", "", "") == {"run_id": "r"}
    assert step_address(None) is None
    for bad in (-1, "7a", 1.5, True):
        with pytest.raises(ValueError):
            step_address("r", bad)
    with pytest.raises(ValueError, match="needs the run"):
        step_address(None, 3)
    with pytest.raises(ValueError, match="not the run this step belongs to"):
        note_columns("run-other", {"run_id": "run-1", "seq": 2})


# ---------------------------------------------------------------------------- 2. stamping, revision row, rebuild


def test_the_runtime_step_is_stamped_on_a_revision_that_names_none_and_kept_on_its_row(nb):
    nb.step_reader = lambda: {"run_id": "run-x", "seq": 4, "step_id": "st-x"}
    _history(nb, ledger=None)
    assert nb.revision("rv_1")["ledger"] == {"run_id": "run-x", "seq": 4, "step_id": "st-x"}
    row = nb.store.revision_row("rv_1")
    assert (row["ledger_run_id"], row["ledger_seq"], row["ledger_step_id"]) == ("run-x", 4, "st-x")
    [a] = nb.kg.query(assertion_ids=("cl_1",))
    assert a.provenance["sources"] == [{"kind": "step", "run_id": "run-x", "seq": 4, "step_id": "st-x"}]


def test_a_given_address_wins_over_the_runtime_step(nb):
    nb.step_reader = lambda: {"run_id": "run-x", "seq": 4, "step_id": "st-x"}
    _history(nb)
    assert nb.revision("rv_1")["ledger"] == STEP


def test_a_rebuild_projects_the_same_provenance(nb):
    _history(nb)
    before = {a.assertion_id: a.provenance for a in nb.kg.query()}
    closures = [c.provenance for c in nb.kg.journal.closures(assertion_id="cl_1")]
    nb.rebuild()
    assert {a.assertion_id: a.provenance for a in nb.kg.query()} == before
    assert [c.provenance for c in nb.kg.journal.closures(assertion_id="cl_1")] == closures


def test_a_notebook_without_the_ledger_columns_opens_unchanged_at_the_same_schema(tmp_path):
    """Lead decision (a): no SCHEMA_VERSION bump; the columns arrive through ``_ADDED_COLUMNS``, NULL on old rows."""
    root = tmp_path / "nb"
    with Notebook.open(root) as nb:
        nb.apply_revision(rev("rv_old", t(1), create("ex:person-dana", "Dana"),
                              claim("cl_old", "ex:person-dana", "schema:name", literal="Dana N.", at=t(1))))
        old_prov = nb.kg.query(assertion_ids=("cl_old",))[0].provenance
    conn = sqlite3.connect(str(root / "note.sqlite3"))
    conn.execute("DROP INDEX idx_revisions_ledger_run")
    for table, cols in {"notes": ("ledger_seq", "ledger_step_id", "body_artifact_json"),
                        "revisions": ("ledger_run_id", "ledger_seq", "ledger_step_id", "sources_json")}.items():
        for col in cols:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    conn.commit()
    conn.close()
    with Notebook.open(root) as nb:
        assert SCHEMA_VERSION == 2 and nb.store.meta_get("schema_version") == 2
        row = nb.store.revision_row("rv_old")
        assert all(row[c] is None for c in ("ledger_run_id", "ledger_seq", "ledger_step_id", "sources_json"))
        assert nb.revision("rv_old")["ledger"] is None and nb.revision("rv_old")["sources"] == []
        assert nb.kg.query(assertion_ids=("cl_old",))[0].provenance == old_prov
        nb.apply_revision(dict(rev("rv_new", t(2), claim("cl_new", "ex:person-dana", "schema:name",
                                                          literal="D.", at=t(2))), ledger=STEP))
        assert nb.store.revision_row("rv_new")["ledger_seq"] == 7


# ---------------------------------------------------------------------------- 3. the note: capture step, body artifact


def test_a_capture_with_an_artifact_store_keeps_the_body_as_an_artifact_of_the_capture_run(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    text = "Met Dana at Acme; she starts at Globex in March."
    with Notebook.open(tmp_path / "nb") as nb:
        res = nb.capture(text, ledger={"run_id": "run-cap", "seq": 3, "step_id": "st-cap"}, artifact_store=store)
        row = nb.store.note_row(res.note_id)
        assert (row["run_id"], row["ledger_seq"], row["ledger_step_id"]) == ("run-cap", 3, "st-cap")
        body = nb.store.note_sources(res.note_id)[1]
        assert body == {"kind": "artifact", "artifact_id": compute_artifact_id(text.encode(), run_id="run-cap"),
                        "run_id": "run-cap"}
        assert store.load_text(body["artifact_id"]) == text
        meta = store.get_metadata(body["artifact_id"])
        assert meta.run_id == "run-cap" and meta.tags["kind"] == "note_body" and meta.tags["note_id"] == res.note_id
        assert nb.store.note_sources(res.note_id)[0] == {"kind": "step", "run_id": "run-cap", "seq": 3,
                                                         "step_id": "st-cap"}
        # the revision of that note names the step that applied it, then the note's own sources
        nb.apply_revision(dict(rev("rv_1", t(1), create("ex:person-dana", "Dana"),
                                   claim("cl_1", "ex:person-dana", "schema:name", literal="Dana", at=t(1),
                                         note_id=res.note_id), note_id=res.note_id), ledger=STEP))
        prov = Provenance.from_dict(nb.kg.query(assertion_ids=("cl_1",))[0].provenance)
        assert [s.key() for s in prov.sources] == ["step:run-apply#7", "step:run-cap#3",
                                                   f"artifact:{body['artifact_id']}"]
        assert prov.extra["note_id"] == res.note_id
        # a repeated capture writes nothing, not even an artifact
        n = len(store.list_by_run("run-cap"))
        assert nb.capture(text, ledger={"run_id": "run-cap"}, artifact_store=store).status == "duplicate"
        assert len(store.list_by_run("run-cap")) == n


def test_the_same_words_twice_in_one_run_share_one_artifact_whose_tags_stay_the_first_notes(tmp_path):
    """Review N6: a second note with the same body in the same run reuses the artifact, never retags it."""
    store = FileArtifactStore(tmp_path / "artifacts")
    with Notebook.open(tmp_path / "nb") as nb:
        a = nb.capture("Same words.", ledger={"run_id": "run-1", "seq": 3}, artifact_store=store, idempotency_key="a")
        b = nb.capture("Same words.", ledger={"run_id": "run-1", "seq": 5}, artifact_store=store, idempotency_key="b")
        assert a.status == b.status == "new" and a.note_id != b.note_id
        ref_a, ref_b = nb.store.note_sources(a.note_id)[1], nb.store.note_sources(b.note_id)[1]
        assert ref_a == ref_b
        assert store.get_metadata(ref_a["artifact_id"]).tags["note_id"] == a.note_id
        assert len(store.list_by_run("run-1")) == 1


def test_without_an_artifact_store_a_capture_has_no_body_artifact(tmp_path):
    with Notebook.open(tmp_path / "nb") as nb:
        res = nb.capture("A note from the command line.")
        assert nb.store.note_row(res.note_id)["body_artifact_json"] is None
        assert nb.store.note_sources(res.note_id) == []


def test_an_artifact_store_that_fails_never_loses_the_note(tmp_path):
    class Full(FileArtifactStore):
        def store(self, *a, **k):
            raise OSError("disk full")

    with Notebook.open(tmp_path / "nb") as nb:
        res = nb.capture("Keep me.", artifact_store=Full(tmp_path / "artifacts"))
        assert res.status == "new" and any("could not be stored as an artifact (disk full)" in w for w in res.warnings)
        assert nb.store.note_row(res.note_id)["body_artifact_json"] is None


# ---------------------------------------------------------------------------- 4. the tools read the runtime's step


def test_the_bound_tools_read_abstractruntimes_step_context(tmp_path):
    from abstractnote.tools import bind

    store = FileArtifactStore(tmp_path / "artifacts")
    tools = bind(tmp_path / "nb", artifact_store=store)
    outside = tools["note_capture"](text="Written outside any run.")
    with effect_step_context(_step("run-cap", 5, "st-cap")):
        inside = tools["note_capture"](text="Written inside a tool step.")
        ins = tools["note_insight_write"](kind="reminder", title="Pareto", body="Still open.")
    from abstractnote.tools import bound_notebook

    nb = bound_notebook(tools)
    r = nb.store.note_row(outside["note_id"])
    assert r["run_id"] is None and r["ledger_seq"] is None and r["body_artifact_json"] is not None
    assert nb.store.note_sources(outside["note_id"])[0]["kind"] == "artifact"  # not namespaced to any run
    r = nb.store.note_row(inside["note_id"])
    assert (r["run_id"], r["ledger_seq"], r["ledger_step_id"]) == ("run-cap", 5, "st-cap")
    assert nb.store.note_sources(inside["note_id"])[1]["run_id"] == "run-cap"
    r = nb.store.note_row(ins["note_id"])
    assert (r["run_id"], r["ledger_seq"], r["ledger_step_id"]) == ("run-cap", 5, "st-cap")
    nb.close()


def test_a_model_given_run_id_is_never_a_source(tmp_path):
    """Review N2: ``note_insight_write`` takes an optional ``run_id`` from its caller (a model, in an agent).
    Outside a runtime step it is kept as a label only; inside one it must be the step's own run, else refused."""
    from abstractnote.tools import bind, bound_notebook

    tools = bind(tmp_path / "nb")
    out = tools["note_insight_write"](kind="reminder", title="Pareto", body="Still open.", run_id="model-invented")
    nb = bound_notebook(tools)
    assert nb.store.note_row(out["note_id"])["run_id"] == "model-invented"
    assert nb.store.note_sources(out["note_id"]) == []
    assert not Provenance.from_dict({"sources": nb.store.note_sources(out["note_id"]), "actor": "abstractnote"}
                                    ).is_grounded()
    with effect_step_context(_step("run-cap", 5, "st-cap")):
        with pytest.raises(ValueError, match="not the run this step belongs to"):
            tools["note_insight_write"](kind="warning", title="Other", body="Elsewhere.", run_id="model-invented")
        same = tools["note_insight_write"](kind="warning", title="Same", body="Here.", run_id="run-cap")
    assert nb.store.note_sources(same["note_id"]) == [{"kind": "step", "run_id": "run-cap", "seq": 5,
                                                       "step_id": "st-cap"}]
    assert nb.store.q1("SELECT COUNT(*) FROM notes WHERE title='Other'")[0] == 0
    nb.close()


def test_a_step_context_without_its_row_is_kept_without_one(tmp_path):
    from abstractnote.tools import bind, bound_notebook

    tools = bind(tmp_path / "nb")
    with effect_step_context(_step("run-cap", None, "st-cap")):
        out = tools["note_capture"](text="The ledger did not say which row.")
    nb = bound_notebook(tools)
    r = nb.store.note_row(out["note_id"])
    assert (r["run_id"], r["ledger_seq"], r["ledger_step_id"]) == ("run-cap", None, "st-cap")
    assert nb.store.note_sources(out["note_id"]) == [{"kind": "run", "run_id": "run-cap"}]
    nb.close()


# ---------------------------------------------------------------------------- 5. end to end on a real Runtime


@pytest.mark.parametrize("tool_timeout_s", [None, 600.0], ids=["same-thread", "timeout-thread"])
def test_capture_and_inline_processing_on_a_runtime_point_at_their_ledger_rows(tmp_path, tool_timeout_s):
    """The real flows (note-capture runs note-process inline) on a real Runtime with ScriptedLLM: no argument is
    passed by the flow; the tools read the step the runtime installed around the TOOL_CALLS handler.

    ``timeout-thread``: a tool timeout makes AbstractRuntime's ``MappingToolExecutor`` run each tool on a bare
    ``threading.Thread`` (``_call_with_timeout``); the step context must cross it (the executor copies the
    context into that thread)."""
    from abstractruntime.integrations.abstractcore.tool_executor import MappingToolExecutor
    from test_flows_headless import SCRIPT

    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.tools import bind, bound_notebook

    store = FileArtifactStore(tmp_path / "artifacts")
    if tool_timeout_s is None:
        runner = NoteRunner.local(tmp_path / "nb", llm=ScriptedLLM(SCRIPT), artifact_store=store)
    else:
        tools = bind(tmp_path / "nb", artifact_store=store)
        runner = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools, artifact_store=store,
                                     tool_executor=MappingToolExecutor(tools, timeout_s=tool_timeout_s))
    text = "Met Dana about the LUT budget; she wants the Pareto by Friday."
    out = runner.run_capture(text, idempotency_key="k-1", source={"client": "cli"},
                             occurred_at="2026-10-19T09:12:00Z", process_inline=True)
    capture_run = out["run_id"]
    nb = bound_notebook(runner.tools)
    ledger = runner.runtime.ledger_store

    def tool_row(run_id, seq, tool):
        rec = ledger.get_record(run_id, seq)
        assert rec is not None and rec["seq"] == seq
        assert str(getattr(rec["status"], "value", rec["status"])) == "started"
        assert rec["effect"]["type"] == "tool_calls"
        assert tool in [c["name"] for c in rec["effect"]["payload"]["tool_calls"]]
        return rec

    # the note: captured in the capture run's note_capture step, its body an artifact of that run
    note = nb.store.note_row(out["note_id"])
    assert note["run_id"] == capture_run and note["ledger_seq"] is not None
    cap_rec = tool_row(capture_run, note["ledger_seq"], "note_capture")
    assert cap_rec["step_id"] == note["ledger_step_id"]
    body = nb.store.note_sources(out["note_id"])[1]
    assert body["kind"] == "artifact" and body["run_id"] == capture_run
    assert store.load_text(body["artifact_id"]) == text

    # the revision: applied in the note-process child run's note_apply_revision step
    [r] = nb.store.q("SELECT * FROM revisions WHERE note_id=?", (out["note_id"],))
    assert r["ledger_run_id"] and r["ledger_run_id"] != capture_run and r["ledger_seq"] is not None
    apply_rec = tool_row(r["ledger_run_id"], r["ledger_seq"], "note_apply_revision")
    assert apply_rec["step_id"] == r["ledger_step_id"]
    assert runner.runtime.run_store.load(r["ledger_run_id"]).parent_run_id == capture_run

    # the projected claim names both steps and the body artifact, in AbstractMemory's shape
    [cl] = nb.store.q("SELECT claim_id FROM claims WHERE note_id=?", (out["note_id"],))
    prov = Provenance.from_dict(nb.kg.query(assertion_ids=(cl[0],))[0].provenance)
    assert [s.key() for s in prov.sources] == [
        f"step:{r['ledger_run_id']}#{r['ledger_seq']}", f"step:{capture_run}#{note['ledger_seq']}",
        f"artifact:{body['artifact_id']}"]
    assert prov.extra["note_id"] == out["note_id"] and prov.extra["evidence_quote"]


def test_a_note_tool_approved_by_a_person_points_at_the_step_that_waited(tmp_path):
    """Review N1: on the approval path the tool runs inside ``runtime.resume()``, not inside
    ``_execute_effect_with_retry``. AbstractRuntime publishes the step context there too (feat/memory-graph-facade
    5b57d35): the tool is stamped with the TOOL_CALLS step that waited (its STARTED row), never a guess. Against
    an AbstractRuntime without that change this test fails (the note names no run)."""
    from abstractruntime.core.models import RunStatus
    from abstractruntime.integrations.abstractcore.tool_executor import (
        ApprovalToolExecutor,
        MappingToolExecutor,
        ToolApprovalPolicy,
    )
    from test_flows_headless import SCRIPT

    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.tools import bind, bound_notebook

    store = FileArtifactStore(tmp_path / "artifacts")
    tools = bind(tmp_path / "nb", artifact_store=store)
    gated = ApprovalToolExecutor(delegate=MappingToolExecutor(tools), policy=ToolApprovalPolicy(auto_approve_tools=set()))
    runner = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools, tool_executor=gated, artifact_store=store)
    runner.runtime.set_tool_executor_for_resume(gated)
    run_id = runner.start("note-capture", {"text": "Met Dana about the LUT budget.", "idempotency_key": "k1"})
    wf = runner.registry.get_or_raise(runner.runtime.get_state(run_id).workflow_id)
    state = runner.runtime.tick(workflow=wf, run_id=run_id, max_steps=200)
    approvals = 0
    while state.status == RunStatus.WAITING and (state.waiting.details or {}).get("mode") == "approval_required":
        approvals += 1
        assert approvals <= 10
        state = runner.runtime.resume(workflow=wf, run_id=run_id, wait_key=state.waiting.wait_key,
                                      payload={"approved": True}, max_steps=200)
    assert approvals >= 1  # the capture really went through a person's approval
    nb = bound_notebook(runner.tools)
    [note] = nb.store.q("SELECT * FROM notes")
    assert note["run_id"] == run_id and note["ledger_seq"] is not None
    rec = runner.runtime.ledger_store.get_record(run_id, note["ledger_seq"])
    assert str(getattr(rec["status"], "value", rec["status"])) == "started"
    assert rec["effect"]["type"] == "tool_calls" and rec["step_id"] == note["ledger_step_id"]
    assert "note_capture" in [c["name"] for c in rec["effect"]["payload"]["tool_calls"]]
    body = nb.store.note_sources(note["note_id"])[1]
    assert body["run_id"] == run_id and store.load_text(body["artifact_id"]) == "Met Dana about the LUT budget."
    nb.close()
