"""The flows run headlessly: routing, early answers, the drain loop, recall efforts, reflection.

Part 1 drives the generated flows with a ScriptedLLM and recording test doubles that follow the
tool contract of docs/api.md (they check the FLOW, not the tools). Part 2 runs the same flows on
the core lane's real note tools (``abstractnote.tools.bind``) and skips loudly while that lane
has not landed.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List

import pytest

from abstractnote.flows.runner import (
    FlowRunError,
    NoteRunner,
    ScriptedLLM,
    ToolApprovalWaitError,
    answer_user_messages,
    drive_until_done,
)

EXTRACTION = {
    "summary": "Met Dana about the LUT budget.",
    "mentions": [{"ref": "m1", "name": "Dana", "kind": "person", "aliases": ["Dana"], "role": "met"},
                 {"ref": "m2", "name": "LUT", "kind": "project", "aliases": ["LUT"], "role": "budget"}],
    "claims": [{"subject": "m1", "predicate": "schema:participant", "object": "m2", "object_is_literal": False,
                "certainty": "stated", "stance": "assert", "evidence": "Met Dana about the LUT budget",
                "valid_from": None, "valid_until": None}],
    "tasks": [{"text": "Send the Pareto by Friday", "status": "open", "due": None}],
    "time_refs": [], "topics": ["lut"],
}
LINKING = {
    "mentions": [{"ref": "m1", "decision": "create", "candidate": None, "merge_candidates": [], "name": "Dana",
                  "kind": "person", "aliases": ["Dana"], "summary": "Dana owns the LUT budget decisions.", "reason": "new person"},
                 {"ref": "m2", "decision": "create", "candidate": None, "merge_candidates": [], "name": "LUT",
                  "kind": "project", "aliases": ["LUT"], "summary": "The LUT project and its budget.", "reason": "new project"}],
    "claims": [{"index": 0, "relation_to_existing": "new", "existing": None, "reason": "first mention"}],
    "tasks": [], "revision_summary": "Linked Dana to the LUT project.",
}
SCRIPT = {
    "QuickFiling": {"title": "Dana and the LUT budget", "kind": "event", "topics": ["lut"], "urgency": "normal",
                    "language": "en", "one_line": "Met Dana about the LUT budget"},
    "Extraction": EXTRACTION,
    "Linking": LINKING,
    "Answer": {"answer": "You agreed to send Dana the Pareto by Friday [nt_1].",
               "evidence": [{"note_id": "nt_1", "title": "x", "occurred_at": "x", "excerpt": "she wants the Pareto"},
                            {"note_id": "nt_invented", "title": "x", "occurred_at": "x", "excerpt": "x"}],
               "evolution": {"earliest": "", "latest": "", "turning_points": []}, "open_loops": [], "follow_ups": []},
    "MiningPlan": {"steps": [{"question": "Dana", "since": None, "until": None, "entities": []},
                             {"question": "LUT budget", "since": "2026-01-01", "until": None, "entities": []}],
                   "contradictions_to_test": [], "rounds": 2},
    "SliceFindings": {"facts": [{"text": "Met Dana", "citations": ["nt_1"]}], "changes": [], "gaps": []},
    "Dossier": {"title": "Dana and LUT", "narrative": "Dana asked for the Pareto [nt_1].", "timeline": [],
                "evolution": "", "contradictions": [], "forgotten": [], "suggestions": [], "confidence": "low"},
    "Reflections": {"items": [{"candidate": 0, "kind": "reminder", "title": "Pareto", "body": "Still open."},
                              {"candidate": 42, "kind": "warning", "title": "Invented", "body": "Out of range."}]},
}


class Doubles:
    """Recording doubles of the note tools with the result shapes of docs/api.md."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.queue: List[str] = []
        self.capture_status = "new"
        self.fail_apply_for: set = set()

    def _rec(self, name, fn):
        def tool(**kw):
            self.calls.append((name, kw))
            return fn(**kw)
        tool.__name__ = name
        return tool

    def names(self, prefix: str = "") -> List[str]:
        return [c[0] for c in self.calls if c[0].startswith(prefix)]

    def args(self, name: str) -> List[Dict[str, Any]]:
        return [c[1] for c in self.calls if c[0] == name]

    def tools(self) -> Dict[str, Any]:
        def capture(**k):
            if not str(k.get("text") or "").strip():
                raise ValueError("The note is empty.")
            return {"note_id": "nt_1", "status": self.capture_status, "first_saved_at": "2026-10-01T08:00:00Z",
                    "received_at": "2026-10-19T09:12:01Z", "revisits": [{"note_id": "nt_0", "occurred_at": "2026-09-30T10:00:00Z"}],
                    "similar": [{"note_id": "nt_0", "title": "LUT budget plan", "occurred_at": "2026-09-30", "score": 0.8}],
                    "topics": ["lut"], "attachments": [], "warnings": []}

        def apply(**k):
            if k["note_id"] in self.fail_apply_for:
                raise ValueError("The revision could not be applied.")
            return {"revision_id": "rv_" + k["note_id"], "summary": "Linked Dana to the LUT project.",
                    "entities_created": ["ex:person-dana"], "entities_merged": [], "claims_added": ["cl_1"],
                    "claims_reinforced": [], "claims_superseded": [], "tasks_opened": [], "tasks_closed": [], "warnings": []}

        def lease(**k):
            if not self.queue:
                return {"items": []}
            return {"items": [{"note_id": self.queue.pop(0), "priority": 1, "attempts": 0}]}

        search = {"notes": [{"note_id": "nt_1", "title": "Dana and the LUT budget", "kind": "event",
                             "occurred_at": "2026-10-19T09:12:00Z", "excerpt": "Met Dana"}], "entities": [{"entity_id": "ex:person-dana"}], "claims": [], "facets": {}}
        tools = {
            "note_capture": capture,
            "note_file": lambda **k: {"note_id": k["note_id"], "priority": 1, "status": "queued"},
            "note_context": lambda **k: {"note": {"note_id": k["note_id"], "text": "Met Dana"},
                                         "sections": ["Met Dana about the LUT budget."], "similar_notes": [],
                                         "candidate_entities": [], "open_tasks": [], "topics": ["lut"], "vocabulary": []},
            "note_resolve_candidates": lambda **k: {"mentions": [{"ref": m["ref"], "candidates": []} for m in k["mentions"]]},
            "note_apply_revision": apply,
            "note_queue_lease": lease,
            "note_queue_ack": lambda **k: {"note_id": k["note_id"], "status": "processed" if k["outcome"] == "ok" else "queued"},
            "note_search": lambda **k: copy.deepcopy(search),
            "note_expand": lambda **k: {"entities": [], "claims_timeline": [], "relation_timelines": [], "neighbours": [], "then_now": []},
            "note_insight_write": lambda **k: {"note_id": "nt_insight_" + str(len(self.args("note_insight_write")))},
        }
        tools["note_communities_recompute"] = lambda **k: {"communities": 0, "loose_ends": 0}
        tools["note_communities_pending"] = lambda **k: {"count": 0, "items": []}
        tools["note_communities_label"] = lambda **k: {"labelled": [], "warnings": []}
        for name in ("note_open_loops", "note_contradictions", "note_resurface", "note_anniversaries"):
            tools[name] = lambda **k: {"items": [{"rule": "open_task_older_than", "note_ids": ["nt_1"], "entity_ids": ["ex:person-dana"]}]}
        return {name: self._rec(name, fn) for name, fn in tools.items()}


@pytest.fixture
def doubles():
    return Doubles()


@pytest.fixture
def runner(doubles):
    return NoteRunner.with_llm(ScriptedLLM(SCRIPT), doubles.tools())


def test_capture_answers_early_and_in_the_output(runner, doubles):
    out = runner.run_capture("Met Dana about the LUT budget.", session_id="s-1", idempotency_key="k-1",
                             context={"attachments": [{"$artifact": "a-1"}]})
    assert out["status"] == "queued" and out["note_id"] == "nt_1"
    assert out["response"].startswith("Saved as event “Dana and the LUT budget”.")
    assert "Related: LUT budget plan." in out["response"] and "You noted this on 2026-09-30." in out["response"]
    assert answer_user_messages(runner, out["run_id"]) == [out["response"]]
    (cap,) = doubles.args("note_capture")
    assert cap == {"text": "Met Dana about the LUT budget.", "idempotency_key": "k-1", "session_id": "s-1",
                   "attachments": [{"$artifact": "a-1"}]}
    (filed,) = doubles.args("note_file")
    assert filed["kind"] == "event" and filed["urgency"] == "normal" and filed["note_id"] == "nt_1"
    assert "note_queue_ack" not in doubles.names()


def test_blank_arguments_are_left_out(runner, doubles):
    # A client form that sends "" for a number must not reach the tool as "" (the runtime refuses it).
    runner.run_capture("Met Dana", priority="", title="", occurred_at="")
    (cap,) = doubles.args("note_capture")
    assert "priority" not in cap and "title" not in cap and "occurred_at" not in cap


def test_capture_passes_what_the_note_is_about(runner, doubles):
    about = {"kind": "cue", "id": "nt_cue", "title": "Dana has gone quiet"}
    runner.run_capture("She is on leave.", context={"about": about})
    (cap,) = doubles.args("note_capture")
    assert cap["about"] == about


def test_capture_of_a_retry_says_already_saved(runner, doubles):
    doubles.capture_status = "duplicate"
    out = runner.run_capture("Met Dana", idempotency_key="k-1")
    assert out == {**out, "status": "duplicate", "response": "Already saved on 2026-10-01."}
    assert doubles.names("note_file") == []


def test_capture_inline_processes_and_acks(runner, doubles):
    out = runner.run_capture("Met Dana about the LUT budget.", process_inline=True)
    assert out["status"] == "processed"
    assert out["revision"]["revision_id"] == "rv_nt_1"
    assert out["revision_summary"] == "Linked Dana to the LUT project."
    assert out["response"].endswith("Linked Dana to the LUT project.")
    (ack,) = doubles.args("note_queue_ack")
    assert ack == {"note_id": "nt_1", "outcome": "ok", "revision_id": "rv_nt_1"}
    (applied,) = doubles.args("note_apply_revision")
    assert applied["extraction"] == EXTRACTION and applied["linking"] == LINKING


def test_a_failing_tool_ends_the_run_with_its_own_sentence(runner):
    with pytest.raises(FlowRunError, match=r"^The note is empty\.$"):
        runner.run_capture("   ")


def test_drain_processes_by_lease_until_max_items(runner, doubles):
    doubles.queue[:] = ["nt_a", "nt_b", "nt_c"]
    out = runner.run_drain(max_items=2, budget_s=60)
    assert out["processed"] == 2 and out["failed"] == 0 and out["response"] == "Processed 2 notes."
    assert [a["note_id"] for a in doubles.args("note_queue_ack")] == ["nt_a", "nt_b"]
    assert doubles.queue == ["nt_c"]
    assert len({a["worker_id"] for a in doubles.args("note_queue_lease")}) == 1


def test_drain_stops_when_the_queue_is_empty_and_counts_failures(runner, doubles):
    doubles.queue[:] = ["nt_a", "nt_bad"]
    doubles.fail_apply_for = {"nt_bad"}
    out = runner.run_drain(max_items=20, budget_s=60)
    assert out["processed"] == 1 and out["failed"] == 1
    assert out["errors"] == [{"note_id": "nt_bad", "error": "The revision could not be applied."}]
    acks = doubles.args("note_queue_ack")
    assert [(a["note_id"], a["outcome"]) for a in acks] == [("nt_a", "ok"), ("nt_bad", "error")]
    assert acks[1]["error"] == "The revision could not be applied."
    assert len(doubles.args("note_queue_lease")) == 3  # the third lease found the queue empty


def test_drain_with_nothing_queued(runner):
    assert runner.run_drain()["response"] == "Nothing to process."


@pytest.mark.parametrize("effort", ["quick", "standard", "deep"])
def test_recall_every_effort_answers_on_response(runner, doubles, effort):
    out = runner.run_recall("What did I decide about the LUT budget?", effort=effort)
    assert out["effort"] == effort and out["response"].strip()
    assert out["hits"]["notes"][0]["note_id"] == "nt_1"
    if effort == "quick":
        assert doubles.names("note_expand") == [] and "nt_1" in out["response"]
    if effort == "standard":
        (exp,) = doubles.args("note_expand")
        assert exp == {"note_ids": ["nt_1"], "entity_ids": ["ex:person-dana"],
                       "query": "What did I decide about the LUT budget?"}  # the packet is scored by the question
        # evidence is NoteRef-shaped and limited to notes the search returned (no invented ids)
        assert out["evidence"] == [{"note_id": "nt_1", "title": "Dana and the LUT budget", "kind": "event",
                                    "occurred_at": "2026-10-19T09:12:00Z", "excerpt": "she wants the Pareto"}]
    if effort == "deep":
        assert out["dossier_note_id"].startswith("nt_insight_")
        (written,) = doubles.args("note_insight_write")
        assert written["kind"] == "dossier" and written["evidence_note_ids"] == ["nt_1"]
        assert len(doubles.args("note_search")) == 1 + 1 + 2  # recall, mine's first search, two plan steps


def test_recall_serves_the_agent_contract_prompt_alias(runner):
    # S3 (review 01): a chat client sends `prompt` and reads output.response.
    out = runner.run("note-recall", {"prompt": "What did I decide about the LUT budget?"})
    assert out["response"] == "You agreed to send Dana the Pareto by Friday [nt_1]."


def test_recall_refuses_an_unknown_effort(runner):
    with pytest.raises(FlowRunError, match="Effort must be quick, standard or deep"):
        runner.run_recall("anything", effort="extreme")


def test_reflect_writes_only_insights_for_real_candidates(runner, doubles):
    out = runner.run_reflect(limit=5)
    assert out["response"] == "Wrote 1 insight." and out["considered"] == 4
    (written,) = doubles.args("note_insight_write")
    assert written["kind"] == "reminder" and written["evidence_note_ids"] == ["nt_1"]


def test_an_approval_wait_is_a_loud_failure(doubles):
    from abstractruntime.integrations.abstractcore.tool_executor import (
        ApprovalToolExecutor,
        MappingToolExecutor,
        ToolApprovalPolicy,
    )

    tools = doubles.tools()
    gated = ApprovalToolExecutor(delegate=MappingToolExecutor(tools), policy=ToolApprovalPolicy(auto_approve_tools=set()))
    r = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools, tool_executor=gated)
    run_id = r.start("note-capture", {"text": "Met Dana"})
    with pytest.raises(ToolApprovalWaitError, match="note_capture"):
        drive_until_done(r.runtime, run_id, registry=r.registry)


# --------------------------------------------------------------------------- the core lane's tools


def _core_tools():
    try:
        from abstractnote.tools import bind  # noqa: F401
    except ImportError as e:  # the core lane (W1) has not landed abstractnote.tools yet
        pytest.skip(f"abstractnote.tools is not importable yet ({e}); these tests run once the core lane lands")
    return bind


def test_core_tools_capture_then_drain_then_recall(tmp_path):
    bind = _core_tools()
    tools = bind(tmp_path / "notebook")
    runner = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools)
    cap = runner.run_capture("Met Dana about the LUT budget; she wants the Pareto by Friday.",
                             idempotency_key="k-1", source={"client": "cli"}, occurred_at="2026-10-19T09:12:00Z")
    assert cap["status"] == "queued" and cap["note_id"].startswith("nt_")
    again = runner.run_capture("Met Dana about the LUT budget; she wants the Pareto by Friday.",
                               idempotency_key="k-1", source={"client": "cli"}, occurred_at="2026-10-19T09:12:00Z")
    assert again["status"] == "duplicate" and again["note_id"] == cap["note_id"]
    drained = runner.run_drain(max_items=5, budget_s=60)
    assert drained["processed"] == 1 and drained["failed"] == 0
    assert runner.run_drain()["response"] == "Nothing to process."
    hits = runner.run_recall("Dana", effort="quick")
    assert cap["note_id"] in hits["response"]


def test_standard_recall_packet_stays_within_the_expand_budget(tmp_path):
    """The note-recall flow passes the question to note_expand, which spends a bounded budget on the
    best-scored notes (W12); the Answer prompt carries that packet, never the unscored results."""
    from abstractnote.recall.expand import EXPAND_BUDGET_CHARS

    bind = _core_tools()
    llm = ScriptedLLM(SCRIPT)
    runner = NoteRunner.with_llm(llm, bind(tmp_path / "notebook"))
    for i in range(30):
        runner.run_capture(f"Met Dana about the LUT budget, round {i}; she wants the Pareto by Friday. " + "x " * 400,
                           occurred_at=f"2026-10-{1 + i % 28:02d}T09:12:00Z")
    runner.run_drain(max_items=30, budget_s=600)
    runner.run_recall("What did Dana want about the LUT budget?", effort="standard")
    (answer,) = [c for c in llm.calls if c["schema"] == "Answer"]
    expansion = answer["prompt"].split("then-versus-now pairs:\n", 1)[1]
    import json as _json

    packet = _json.loads(expansion)
    assert len(expansion) <= EXPAND_BUDGET_CHARS + 2000 and packet["used_chars"] <= EXPAND_BUDGET_CHARS


def test_the_flow_merge_matches_the_core_lane_merge(doubles):
    """note-process merges section extractions in a code node; the core lane has the same rule
    (abstractnote.process.merge). Two sections, overlapping mentions: both must agree exactly."""
    try:
        from abstractnote.process.merge import merge_extractions
    except ImportError as e:
        pytest.skip(f"abstractnote.process.merge is not importable yet ({e})")
    part1 = copy.deepcopy(EXTRACTION)
    part2 = {"summary": "Dana moved the deadline.", "mentions": [
        {"ref": "m1", "name": "dana", "kind": "person", "aliases": ["D."], "role": "moved it"},
        {"ref": "m2", "name": "Pareto", "kind": "artifact", "aliases": ["Pareto"], "role": "the deliverable"}],
        "claims": [{"subject": "m1", "predicate": "ex:moved_deadline_of", "object": "m2", "object_is_literal": False,
                    "certainty": "stated", "stance": "revise", "evidence": "moved the deadline",
                    "valid_from": None, "valid_until": None}],
        "tasks": [], "time_refs": [{"text": "next week", "iso": None}], "topics": ["lut", "deadlines"]}
    tools = doubles.tools()
    tools["note_context"] = doubles._rec("note_context", lambda **k: {
        "note": {"note_id": k["note_id"]}, "sections": ["one", "two"], "similar_notes": [], "candidate_entities": [],
        "open_tasks": [], "topics": [], "vocabulary": []})
    script = dict(SCRIPT, Extraction=[part1, part2])
    r = NoteRunner.with_llm(ScriptedLLM(script), tools)
    r.run("note-process", {"note_id": "nt_1"})
    (applied,) = doubles.args("note_apply_revision")
    merged, mentions = merge_extractions([part1, part2])
    assert applied["extraction"] == merged
    assert doubles.args("note_resolve_candidates")[0]["mentions"] == mentions


def test_the_real_model_path_through_abstractcore(tmp_path, monkeypatch):
    """NoteRunner.local with a provider and a model: AbstractCore over HTTP to the scripted
    OpenAI-compatible server (scripts/stub_llm.py), the core lane's tools, capture -> drain -> recall."""
    import socket
    import subprocess
    import sys
    import time

    _core_tools()
    port = None
    for candidate in range(18860, 18870):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", candidate)) != 0:
                port = candidate
                break
    if port is None:
        pytest.skip("no free scratch port in 18860-18869")
    home = tmp_path / "home"
    home.mkdir()
    dead = "http://127.0.0.1:9/v1"
    for k, v in {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "HF_HOME": str(home / "hf"),
                 "LMSTUDIO_BASE_URL": dead, "OLLAMA_BASE_URL": dead, "OPENAI_API_KEY": "stub",
                 "OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1"}.items():
        monkeypatch.setenv(k, v)
    stub = Path(__file__).resolve().parents[1] / "scripts" / "stub_llm.py"
    logs = tmp_path / "requests"
    proc = subprocess.Popen([sys.executable, str(stub), "--port", str(port), "--log-requests", str(logs)],
                            env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        r = NoteRunner.local(tmp_path / "nb", provider="openai-compatible", model="note-stub",
                             llm_kwargs={"base_url": f"http://127.0.0.1:{port}/v1"})
        cap = r.run_capture("Met Dana about the LUT budget; she wants the Pareto by Friday.", idempotency_key="k-1",
                            source={"client": "cli"})
        assert cap["status"] == "queued" and cap["response"] == "Saved as event “Dana and the LUT budget”."
        assert r.run_drain(max_items=5, budget_s=60)["processed"] == 1
        assert "Pareto" in r.run_recall("What does Dana want?", effort="standard")["response"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    # Backlog 0024, through the real client: every request carries the step's static system prompt,
    # and the note steps' user message starts with the note's own envelope (kept byte for byte).
    import json as _json

    from abstractnote.process import prompts as _prompts

    steps = {"QuickFiling": "quick_filing", "Extraction": "extract", "Linking": "link", "Answer": "synthesize"}
    seen = set()
    for f in sorted(logs.glob("*.json")):
        body = _json.loads(f.read_text())
        schema = f.stem.split("-", 1)[1]
        if schema not in steps:
            continue
        seen.add(schema)
        msgs = body["messages"]
        assert msgs[0]["role"] == "system", f.name
        assert msgs[0]["content"].startswith(_prompts.system_prompt(steps[schema]).strip()), f.name
        if schema != "Answer":
            from abstractnote.process.packets import note_envelope

            user = msgs[-1]["content"]
            assert user.startswith(note_envelope() + "\n"), (f.name, user[:80])  # kept byte for byte
            assert "About this note (not part of its text): nt_" in user, f.name
    assert seen == set(steps)


PINNED = "This notebook is pinned to the embedding model x; rebuild with reembed to change models."


def _failing_after(doubles, tools, name, *, calls_before_failing=0, give_up_after=5, fallback=None):
    """Make tool `name` raise PINNED; after `give_up_after` calls answer `fallback` instead, so a
    flow that swallows the error and loops on ends quickly (and the test can see it looped)."""
    real = tools[name]
    count = {"n": 0}

    def tool(**kw):
        count["n"] += 1
        doubles.calls.append((name, kw))
        if count["n"] <= calls_before_failing:
            return real(**kw)
        if count["n"] > give_up_after:
            return fallback if fallback is not None else real(**kw)
        raise ValueError(PINNED)

    tool.__name__ = name
    tools[name] = tool
    return count


def test_a_tool_failure_inside_the_drain_loop_ends_the_run(doubles):
    # Lead gate finding: inside a while body a raising node does not end the run (the runtime turns
    # it into "branch complete" and loops); the drain must stop with the tool's sentence.
    tools = doubles.tools()
    count = _failing_after(doubles, tools, "note_queue_lease", fallback={"items": []})
    r = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools)
    with pytest.raises(FlowRunError, match=r"^This notebook is pinned to the embedding model x"):
        r.run_drain(max_items=20, budget_s=60)
    assert count["n"] == 1


def test_an_ack_failure_inside_the_drain_loop_ends_the_run(doubles):
    doubles.queue[:] = ["nt_a", "nt_b"]
    tools = doubles.tools()
    count = _failing_after(doubles, tools, "note_queue_ack", fallback={"note_id": "x", "status": "processed"})
    r = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools)
    with pytest.raises(FlowRunError, match=r"^This notebook is pinned"):
        r.run_drain(max_items=20, budget_s=60)
    assert count["n"] == 1 and doubles.queue == ["nt_b"]


def test_a_tool_failure_inside_the_mining_loop_ends_the_run(doubles):
    tools = doubles.tools()
    # the recall search and mine's first search succeed; the first plan-step search fails
    count = _failing_after(doubles, tools, "note_search", calls_before_failing=2, give_up_after=4)
    r = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools)
    with pytest.raises(FlowRunError, match=r"^This notebook is pinned"):
        r.run_recall("Dana", effort="deep")
    assert count["n"] == 3 and doubles.args("note_insight_write") == []


def test_a_tool_failure_inside_the_reflect_loop_ends_the_run(doubles):
    tools = doubles.tools()
    count = _failing_after(doubles, tools, "note_insight_write", fallback={"note_id": "nt_x"})
    r = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools)
    with pytest.raises(FlowRunError, match=r"^This notebook is pinned"):
        r.run_reflect(limit=5)
    assert count["n"] == 1


def test_a_model_failure_inside_the_section_loop_ends_the_run(doubles):
    def boom(prompt):
        raise RuntimeError("The model is not reachable.")

    r = NoteRunner.with_llm(ScriptedLLM(dict(SCRIPT, Extraction=boom)), doubles.tools())
    with pytest.raises(FlowRunError, match="The model is not reachable"):
        r.run("note-process", {"note_id": "nt_1"})
    assert doubles.args("note_apply_revision") == []


def test_inline_processing_that_fails_ends_the_capture_with_its_sentence(runner, doubles):
    doubles.fail_apply_for = {"nt_1"}
    with pytest.raises(FlowRunError, match=r"^The revision could not be applied\.$"):
        runner.run_capture("Met Dana", process_inline=True)
    assert doubles.args("note_queue_ack") == []  # never acked as processed


def test_cli_capture_pins_the_embedder_and_says_so(tmp_path, capsys):
    """A notebook created by `capture` without embedder flags is pinned to the hashing embedder;
    a later command with another embedder is refused with the plain sentence, and --help says so."""
    try:
        from abstractnote.cli import build_parser, main
    except ImportError as e:
        pytest.skip(f"abstractnote.cli is not importable yet ({e})")
    nb = tmp_path / "nb"
    assert main(["capture", "Met Dana", "--notebook", str(nb)]) == 0
    capsys.readouterr()
    help_text = build_parser()._subparsers._group_actions[0].choices["capture"].format_help()
    assert "pinned to its embedding model when it is created" in " ".join(help_text.split())
    from abstractnote.store.notebook import Notebook

    class Other:
        model_id = "other-embedder"
        dimension = 8

        def embed_texts(self, texts):
            return [[1.0] * 8 for _ in texts]

    with pytest.raises(ValueError, match="pinned to the embedding model"):
        Notebook.open(nb, embedder=Other())


def test_capture_passes_a_saved_answer(runner, doubles):
    answer = {"question": "What did Dana ask for?", "evidence_note_ids": ["nt_0"]}
    runner.run_capture("Dana asked for the Pareto by Friday [nt_0].", context={"answer": answer})
    (cap,) = doubles.args("note_capture")
    assert cap["answer"] == answer


def test_core_tools_file_a_saved_answer_as_a_linked_synthesis(tmp_path):
    bind = _core_tools()
    tools = bind(tmp_path / "notebook")
    runner = NoteRunner.with_llm(ScriptedLLM(SCRIPT), tools)
    first = runner.run_capture("Met Dana about the LUT budget.", idempotency_key="k-a", source={"client": "cli"})
    out = runner.run_capture("Dana wants the Pareto by Friday.", idempotency_key="k-b", source={"client": "web"},
                             context={"answer": {"question": "What does Dana want?",
                                                 "evidence_note_ids": [first["note_id"], "nt_nowhere"]}})
    from abstractnote.store.notebook import Notebook

    with Notebook.open(tmp_path / "notebook") as nb:
        note = nb.note(out["note_id"])
        assert note["kind"] == "synthesis"  # the quick filing ("event") does not override it
        assert note["source"]["client"] == "web"
        links = [dict(r) for r in nb.store.q("SELECT dst_note_id, predicate, matched_by FROM note_links WHERE src_note_id=?",
                                             (out["note_id"],))]
    assert {"dst_note_id": first["note_id"], "predicate": "dcterms:references", "matched_by": "answer"} in links
    assert all(link["dst_note_id"] != "nt_nowhere" for link in links)
