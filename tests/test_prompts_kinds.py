"""Seats are not the packages they own (eval2 D7, W25).

The extraction and linking prompts carry one kind glossary line (a person is a human or an agent or seat; software
is a program, package or service); the linking packet shows each mention and candidate as ``name — kind``; the
linking prompt says a person is never the software it works on, and how to name a new entity that shares a name
with a different candidate. Part 2 runs note-process headless on the real note tools: a seat ``gateway`` next to
the software ``gateway`` ends as two live entities."""

from __future__ import annotations

from abstractnote.flows.runner import NoteRunner, ScriptedLLM
from abstractnote.process import prompts
from abstractnote.process.packets import link_batches
from abstractnote.process.schemas import ENTITY_KINDS

GLOSS = "Kinds: person = a human, or an AI agent or seat that writes, decides or acts"


def test_both_prompts_carry_the_one_line_kind_glossary_for_every_kind():
    for name in ("extract", "link"):
        text = prompts.system_prompt(name)
        [line] = [ln for ln in text.splitlines() if ln.startswith("Kinds: ")]
        assert line.startswith(GLOSS)
        assert all(f"{k} = " in line for k in ENTITY_KINDS)
    link = prompts.system_prompt("link")
    assert "a person (a human, an agent or a seat) is never the software, project or artifact it owns" in link
    assert "give the new entity a `name` that tells them apart" in link
    assert "is two mentions with two kinds when the text means both" in prompts.system_prompt("extract")


def test_the_linking_packet_shows_the_mention_and_candidate_kinds():
    ext = {"mentions": [{"ref": "m1", "name": "gateway", "kind": "person", "role": "the seat that signed off"}],
           "claims": [], "tasks": []}
    cand = {"index": 0, "name": "gateway", "kind": "software", "aliases": [], "summary": "The gateway service.",
            "active_claims": []}
    [batch] = link_batches({"note_id": "nt_1"}, ext, [{"ref": "m1", "candidates": [cand]}], [])
    assert batch["mentions"].splitlines()[:2] == ["m1 gateway — person: the seat that signed off",
                                                  "    [0] gateway — software — The gateway service."]


def _ext(kind, role, claim_obj):
    return {"summary": "x", "mentions": [{"ref": "m1", "name": "gateway", "kind": kind, "role": role}],
            "claims": [{"subject": "m1", "predicate": "ex:status", "object": claim_obj, "object_is_literal": True,
                        "evidence": claim_obj}], "tasks": [], "time_refs": [], "topics": ["release"]}


def test_a_seat_and_the_software_with_its_name_stay_two_entities(tmp_path):
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    def link(prompt):
        if "no existing entity matched" in prompt:
            return {"mentions": [{"ref": "m1", "decision": "create", "name": "gateway", "kind": "software",
                                  "summary": "The gateway is the framework's service host."}], "claims": [], "tasks": [],
                    "revision_summary": "The gateway."}
        assert "m1 gateway — person: the seat that signed off" in prompt and "[0] gateway — software" in prompt
        return {"mentions": [{"ref": "m1", "decision": "create", "name": "gateway seat", "kind": "person",
                              "aliases": ["gateway"], "summary": "The gateway seat is the agent that owns the gateway "
                              "package.", "reason": "agent, not package"}], "claims": [], "tasks": [],
                "revision_summary": "The gateway seat signed off."}

    script = {"QuickFiling": {"title": "gateway", "kind": "log", "topics": ["release"], "urgency": "normal",
                              "language": "en", "one_line": "gateway."},
              "Extraction": [_ext("software", "the released package", "released"),
                             _ext("person", "the seat that signed off", "signed off")],
              "Linking": link}
    llm = ScriptedLLM(script)
    runner = NoteRunner.with_llm(llm, bind(tmp_path / "notebook"))
    for at, text in (("2026-07-01T09:00:00Z", "gateway released"), ("2026-07-02T09:00:00Z", "gateway signed off")):
        runner.run_capture(text, idempotency_key=text, source={"client": "cli"}, occurred_at=at)
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    assert GLOSS in [c["system"] for c in llm.calls if c["schema"] == "Linking"][0]
    assert GLOSS in [c["system"] for c in llm.calls if c["schema"] == "Extraction"][0]
    nb = Notebook.open(tmp_path / "notebook")
    try:
        live = {(r[0], r[1]) for r in nb.store.q("SELECT name, kind FROM entities WHERE merged_into IS NULL")}
        assert live == {("gateway", "software"), ("gateway_seat", "person")}
    finally:
        nb.close()
