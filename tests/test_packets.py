"""Bounded, byte-stable prompt packets and the flows that carry them (backlog 0024).

Each check here goes red when the thing it checks is removed: the static system prompts, the
note envelope at the head of every user message, the per-step thinking and output caps, the
per-note prompt-cache namespace, the candidate and claim bounds, and the linking parts.
"""

from __future__ import annotations

import json

import pytest

from abstractnote.flows import build
from abstractnote.flows.runner import NoteRunner, ScriptedLLM
from abstractnote.process import packets, prompts
from abstractnote.process.packets import (
    CANDIDATES_PER_MENTION,
    CLAIMS_PER_CANDIDATE,
    LINK_MENTIONS_PER_CALL,
    extraction_packet,
    link_batches,
    merge_linkings,
    note_envelope,
)

NOTE = {"note_id": "nt_abc", "title": "LUT review", "kind": "decision", "occurred_at": "2026-07-10T08:47:53Z",
        "source": {"importer": "agora", "author": "agent"}, "topics": ["lut"]}


def _claim(i, subject="Dana", predicate="schema:worksFor", obj="Acme"):
    return {"index": i, "claim_id": f"cl_{i}", "subject": subject, "predicate": predicate, "object": obj,
            "object_is_literal": False, "observed_at": "2026-03-01T00:00:00Z", "certainty": "stated",
            "evidence": "x", "line": f"{subject} {predicate} {obj}"}


def _candidate(i, n_claims=8):
    return {"entity_id": f"ex:person-c{i}", "name": f"Candidate {i}", "kind": "person", "aliases": [f"C{i}"],
            "summary": "A colleague. " * 60, "last_seen": "2026-09-01T00:00:00Z", "index": i, "score": 0.5,
            "matched_by": ["vector"], "active_claims": [_claim(100 * i + k) for k in range(n_claims)]}


def _extraction(n_mentions):
    mentions = [{"ref": f"m{i + 1}", "name": f"Thing {i + 1}", "kind": "concept", "aliases": [],
                 "role": "discussed in the review"} for i in range(n_mentions)]
    claims = [{"subject": f"m{i + 1}", "predicate": "schema:isPartOf", "object": "m1", "object_is_literal": False,
               "certainty": "stated", "stance": "assert", "evidence": "a verbatim excerpt " * 3,
               "valid_from": None, "valid_until": None} for i in range(1, n_mentions)]
    return {"summary": "A long review.", "mentions": mentions, "claims": claims,
            "tasks": [{"text": "Send the Pareto", "status": "done", "due": None}], "time_refs": [], "topics": []}


def test_the_envelope_is_constant_and_kept_by_the_runtime():
    env = note_envelope()
    assert env == note_envelope() and env.startswith("<runtime_metadata>") and env.endswith("</runtime_metadata>")
    assert "nt_" not in env and "20" not in env  # no per-call value in the shared prefix
    # the runtime keeps a head envelope byte for byte (it re-stamps only a message without one)
    rt = pytest.importorskip("abstractruntime.integrations.abstractcore.llm_client")
    text = env + "\nText:\nMet Dana."
    assert rt._inject_runtime_grounding_into_text(text, {"local_datetime": "2026-10-19T03:00:00+02:00"}) == text


def test_the_extraction_packet_names_known_things_and_keeps_the_whole_text():
    text = "word " * 5000
    p = extraction_packet(NOTE, {"index": 1, "source": "note", "text": text}, 3,
                          [{"title": "Earlier", "occurred_at": "2026-07-01T00:00:00Z"}] * 9,
                          [_candidate(i) for i in range(30)],
                          [{"index": i, "text": f"task {i}", "due": None} for i in range(30)],
                          [f"topic {i}" for i in range(200)])
    assert set(p) == {"envelope", "header", "known", "text"}
    assert p["text"].endswith(text.rstrip()) and len(p["text"]) >= len(text.rstrip())  # the text is never cut
    assert p["header"].startswith("About this note (not part of its text): nt_abc “LUT review” written 2026-07-10 08:47")
    assert "[part 2 of 3]" in p["header"]
    assert p["known"].count("Candidate ") == packets.KNOWN_NAMES and "colleague" not in p["known"]
    assert p["known"].count("task ") == packets.OPEN_TASKS and p["known"].count("topic ") <= packets.TOPICS
    assert "Existing topics: lut," in p["known"]  # the note's own topics first


def test_linking_parts_are_bounded_and_merge_back():
    ext = _extraction(25)
    resolved = [{"ref": m["ref"], "candidates": [_candidate(i) for i in range(5)]} for m in ext["mentions"]]
    open_tasks = [{"index": i, "text": f"open {i}", "opened_at": "2026-01-01"} for i in range(20)]
    parts = link_batches(NOTE, ext, resolved, open_tasks)
    assert len(parts) == 2  # 25 mentions, 15 per call
    first, second = parts
    assert first["mentions"].count("\nm") + 1 == LINK_MENTIONS_PER_CALL and "m16 " in second["mentions"]
    # at most 3 candidates per mention, at most 3 statements per candidate, summaries trimmed
    assert first["mentions"].count("    [") == LINK_MENTIONS_PER_CALL * CANDIDATES_PER_MENTION
    assert first["mentions"].count("        #") == LINK_MENTIONS_PER_CALL * CANDIDATES_PER_MENTION * CLAIMS_PER_CANDIDATE
    assert "A colleague. A colleague." in first["mentions"] and "…" in first["mentions"]
    # statements go with their subject's part, numbered as in the whole extraction; tasks in part one
    assert "[0] m2 Thing 2 schema:isPartOf m1 Thing 1" in first["claims"] and "[15] m17" in second["claims"]
    assert "[0] done: Send the Pareto" in first["tasks"] and second["tasks"] == "(none)"
    assert first["open_tasks"].count("#") == packets.OPEN_TASKS and second["open_tasks"] == "(none)"
    assert first["envelope"] == second["envelope"] == note_envelope()
    assert "nt_abc" in first["header"] and "[linking part 2 of 2]" in second["header"]
    rendered = [prompts.render(prompts.load("link"), {"batch": p}) for p in parts]
    assert all(len(r) < 25000 for r in rendered), [len(r) for r in rendered]
    merged = merge_linkings([
        {"mentions": [{"ref": "m1", "decision": "create"}], "claims": [{"index": 0}], "tasks": [{"index": 0}],
         "revision_summary": "One."},
        {"mentions": [{"ref": "m16", "decision": "reuse"}, {"ref": "m1", "decision": "reuse"}],
         "claims": [{"index": 15}], "tasks": [], "revision_summary": "Two."},
    ])
    assert [m["ref"] for m in merged["mentions"]] == ["m1", "m16"] and merged["mentions"][0]["decision"] == "create"
    assert [c["index"] for c in merged["claims"]] == [0, 15] and merged["revision_summary"] == "One. Two."


def test_a_note_that_names_nothing_needs_no_linking_call():
    assert link_batches(NOTE, {"summary": "x", "mentions": [], "claims": [], "tasks": []}, [], []) == []
    one = link_batches(NOTE, {"summary": "x", "mentions": [], "claims": [], "tasks": [{"text": "t", "status": "open"}]},
                       [], [])
    assert len(one) == 1 and "[0] open: t" in one[0]["tasks"]
    assert merge_linkings([]) == {"mentions": [], "claims": [], "tasks": [], "revision_summary": ""}


def test_candidate_cards_show_the_statements_the_note_is_about_first():
    cand = _candidate(0, 0)
    cand["active_claims"] = [_claim(1, predicate="schema:knows"), _claim(2, predicate="schema:knows"),
                             _claim(3, predicate="schema:knows"), _claim(4, predicate="schema:worksFor")]
    ext = {"summary": "", "mentions": [{"ref": "m1", "name": "Dana", "kind": "person"},
                                       {"ref": "m2", "name": "Beta", "kind": "org"}],
           "claims": [{"subject": "m1", "predicate": "schema:worksFor", "object": "m2"}], "tasks": []}
    part = link_batches(NOTE, ext, [{"ref": "m1", "candidates": [cand]}], [])[0]
    assert "#4 Dana schema:worksFor Acme" in part["mentions"]  # the one a supersession is about
    assert "#3 " not in part["mentions"]


def _process_prompt_flows():
    flows = build.build_all()
    nodes = {(fid, n["id"]): n for fid, f in flows.items() for n in f["nodes"]}
    return flows, nodes


def test_model_nodes_carry_static_system_prompts_thinking_and_caps():
    _, nodes = _process_prompt_flows()
    llm = {k: n for k, n in nodes.items() if n["data"]["nodeType"] == "llm_call"}
    steps = {("note-capture", "filing"): "quick_filing", ("note-process", "extract"): "extract",
             ("note-process", "extract_again"): "extract",
             ("note-process", "link"): "link", ("note-recall", "synthesize"): "synthesize",
             ("note-mine", "plan"): "mine_plan", ("note-mine", "findings"): "mine_findings",
             ("note-mine", "dossier"): "mine_dossier", ("note-reflect", "reflect"): "reflect"}
    assert set(llm) == set(steps)
    for key, step in steps.items():
        d = llm[key]["data"]["pinDefaults"]
        assert d["system"] == prompts.system_prompt(step), key
        assert d["max_output_tokens"] == build.STEP_SETTINGS[step]["max_output_tokens"] > 0, key
        assert d.get("thinking") == build.STEP_SETTINGS[step]["thinking"], key
    assert llm[("note-capture", "filing")]["data"]["pinDefaults"]["thinking"] == "off"
    caps = {k[1]: llm[k]["data"]["pinDefaults"]["max_output_tokens"] for k in llm if k[0] in ("note-capture", "note-process")}
    assert caps == {"filing": 400, "extract": 4000, "extract_again": 4000, "link": 6000}


def test_notes_share_one_prompt_cache_key_per_step():
    """The runtime derives one key per (session, flow, node). Notes must SHARE it: a fresh key forks
    the prepared system bloc, which for tools-first chat templates (Qwen3.5/3.6) is 3 tokens long,
    while a shared key's snapshot keeps the system prompt plus the envelope's common head. So the
    process subflow gets no per-note cache scope."""
    flows, nodes = _process_prompt_flows()
    for fid in ("note-drain", "note-capture"):
        pins = {p["id"] for p in nodes[(fid, "process")]["data"]["inputs"]}
        assert "_runtime" not in pins and pins <= {"exec-in", "inherit_context", "note_id", "provider", "model",
                                                     "thinking"}, (fid, pins)


def _seeded_runner(tmp_path):
    from abstractnote.tools import bind

    script = {
        "QuickFiling": {"title": "t", "kind": "event", "topics": [], "urgency": "normal", "language": "en",
                        "one_line": "x"},
        "Extraction": lambda prompt: {"summary": "s", "mentions": [{"ref": "m1", "name": "Dana", "kind": "person",
                                                                    "aliases": [], "role": "met"}],
                                      "claims": [], "tasks": [], "time_refs": [], "topics": []},
        "Linking": {"mentions": [{"ref": "m1", "decision": "create", "candidate": None, "merge_candidates": [],
                                  "name": "Dana", "kind": "person", "aliases": [], "summary": None, "reason": "new"}],
                    "claims": [], "tasks": [], "revision_summary": "Dana."},
    }
    llm = ScriptedLLM(script)
    return NoteRunner.with_llm(llm, bind(tmp_path / "nb")), llm


def test_two_notes_share_every_system_prompt_byte_and_differ_from_the_first_user_byte(tmp_path):
    runner, llm = _seeded_runner(tmp_path)
    runner.run_capture("Met Dana about the LUT budget.", occurred_at="2026-07-10T08:47:53Z")
    runner.run_capture("Dana moved to Beta.", occurred_at="2026-08-01T10:00:00Z")
    out = runner.run_drain(max_items=5, budget_s=60)
    assert out["processed"] == 2
    by_schema = {}
    for c in llm.calls:
        by_schema.setdefault(c["schema"], []).append(c)
    for name in ("QuickFiling", "Extraction", "Linking"):
        calls = by_schema[name]
        assert len(calls) == 2 and calls[0]["system"] == calls[1]["system"] and calls[0]["system"].strip()
        for c in calls:
            assert c["prompt"].startswith(note_envelope() + "\n"), c["prompt"][:80]
            assert "Output JSON only" not in c["prompt"]
            last = c["prompt"].rstrip().splitlines()[-1]
            assert last.startswith("About this note (not part of its text): nt_")  # per-call values at the end
            assert last.split(": ", 1)[1][:30] in c["prompt"][-len(last):]
        assert calls[0]["prompt"].splitlines()[-1] != calls[1]["prompt"].splitlines()[-1]
    # the extraction prompt is the bounded packet, not the context tool's JSON
    assert "candidate_entities" not in by_schema["Extraction"][1]["prompt"]
    assert "Dana (person)" in by_schema["Extraction"][1]["prompt"]  # the first note created Dana


def test_a_prompt_note_labels_the_author_and_the_assistant_answer(tmp_path):
    """A prompt note keeps the answer in source.origin.outcome: the packet labels the two parts."""
    from abstractnote import Notebook
    from abstractnote.process.context import note_context

    nb = Notebook.open(tmp_path / "nb")
    text = "Ship the fix.\n\n---\n\n## Assistant answer (claude-opus)\n\nPortAudio froze the default device."
    nid = nb.capture(text, occurred_at="2026-09-01T10:00:00Z",
                     source={"client": "importer", "importer": "claude", "author": "me",
                             "origin": {"outcome": "PortAudio froze the default device.",
                                        "outcome_author": "claude-opus"}}).note_id
    ctx = note_context(nb, nid)
    assert [(s["source"], s["author"], s["text"]) for s in ctx["sections"]] == [
        ("note", "me", "Ship the fix."), ("answer", "claude-opus", "PortAudio froze the default device.")]
    first = extraction_packet(ctx["note"], ctx["sections"][0], 2, [], [], [], [])
    second = extraction_packet(ctx["note"], ctx["sections"][1], 2, [], [], [], [])
    assert first["text"] == "Text written by me:\nShip the fix."
    assert second["text"].startswith("Answer by claude-opus (what they said; not written by the note's author):\n")
    assert "certainty: reported" in prompts.system_prompt("extract")
    plain = nb.capture("Just a thought about Dana.", occurred_at="2026-09-02T10:00:00Z").note_id
    assert [s["source"] for s in note_context(nb, plain)["sections"]] == ["note"]
    nb.close()


def test_default_valued_fields_may_be_left_out():
    """Backlog 0024: a quarter of the answers' tokens were default values; the schemas let the model
    leave them out, the runtime's model fills them, and an explicit null still means the default."""
    rt = pytest.importorskip("abstractruntime.integrations.abstractcore.effect_handlers")
    from abstractnote.process import schemas

    ext_model = rt._pydantic_model_from_json_schema(schemas.SCHEMAS["Extraction"], name="X")
    data = ext_model.model_validate({
        "summary": "s", "mentions": [{"ref": "m1", "name": "Dana", "kind": "person", "role": "met"}],
        "claims": [{"subject": "m1", "predicate": "ex:deadline", "object": "2026-10-24", "evidence": "by Friday"}],
        "tasks": [{"text": "t", "status": "open"}], "time_refs": [{"text": "Friday"}], "topics": []}).model_dump()
    c = data["claims"][0]
    assert (c["object_is_literal"], c["certainty"], c["stance"], c["valid_from"]) == (False, "stated", "assert", None)
    assert data["mentions"][0]["aliases"] == [] and data["tasks"][0]["due"] is None
    link_model = rt._pydantic_model_from_json_schema(schemas.SCHEMAS["Linking"], name="L")
    link = link_model.model_validate({"mentions": [{"ref": "m1", "decision": "create", "summary": "Dana."}],
                                      "claims": [], "tasks": [], "revision_summary": "x"}).model_dump()
    assert link["mentions"][0]["merge_candidates"] == [] and link["mentions"][0]["candidate"] is None
    with pytest.raises(Exception):  # the decision and the summary stay required
        link_model.model_validate({"mentions": [{"ref": "m1"}], "claims": [], "tasks": [], "revision_summary": "x"})
    mirror = schemas.validate("Extraction", {"summary": "", "mentions": [], "claims": [
        {"subject": "m1", "predicate": "p", "object": "o", "certainty": None, "stance": None, "evidence": "e"}],
        "tasks": [], "time_refs": [], "topics": []})
    assert mirror.claims[0].certainty == "stated" and mirror.claims[0].stance == "assert"
