"""Structural validity of model answers (ADR 0008 §8; R4 item 4; process/validate.py).

The corrupt strings below are B's and A's live answers (gw18080 ledgers of 2026-10-20, run ids in the names),
quoted verbatim. A corrupt answer is asked once more with a fresh call; a second one fails the note with the
problem (queue_errors) and nothing of it is applied; the apply step checks again.
"""

from __future__ import annotations

import pytest

from abstractnote.process import schemas, validate

# retro-B S2-B (run 71970eb8), S3-B (87f3d108), S4 (e071068f); retro-A (7765cc77)
B_71970EB8 = ('Zorn Labs has an annual budget of 25k for 2023, approved by the board.","reason":"same organization",'
              '"aliases":["Zorn Labs"]},{')
B_87F3D108 = ('Zorn Labs (ZL) had an annual budget of 18k in 2021, updating the previous figure of 25k, and '
              'reason":"same organization"},{')
B_E071068F = 'Zorn Labs has an annual budget of 30k for 2024.","aliases":["Zorn Labs"],"reason":"same org"},{'
CANDS = {"m1": 1, "m2": 2}


def mention(ref="m1", decision="reuse", candidate=0, summary="Zorn Labs, a lab.", **kw):
    return {"ref": ref, "decision": decision, "candidate": candidate, "merge_candidates": kw.pop("merge", []),
            "name": kw.pop("name", ""), "kind": kw.pop("kind", None), "aliases": kw.pop("aliases", []),
            "summary": summary, "reason": kw.pop("reason", "same lab")}


def answer(mentions=None, claims=(), tasks=(), summary="Linked."):
    return {"mentions": mentions if mentions is not None else [mention()], "claims": list(claims),
            "tasks": list(tasks), "revision_summary": summary}


@pytest.mark.parametrize("corrupt", [B_71970EB8, B_87F3D108, B_E071068F])
def test_the_live_corrupt_summaries_are_invalid(corrupt):
    """MUTATION: drop the fragment check (these pass)."""
    problems = validate.link_problems(answer([mention(summary=corrupt)]), CANDS)
    assert len(problems) == 1 and "holds a piece of the answer's structure" in problems[0], problems
    verdict = validate.link_answer(answer([mention(summary=corrupt)]), {"mentions": {"m1": ["ex:org-zorn-labs"]}})
    assert verdict["valid"] is False and verdict["error"].startswith("The model's linking answer was malformed (")


def test_a_decision_that_names_no_candidate_is_invalid():
    """B's drains: every mention ``reuse`` with ``candidate: null`` and an empty name (run 0d3e1910); A's
    invented refs (eval2 0d53a537 m12..m18). MUTATION: drop the candidate check."""
    for bad in (mention(candidate=None), mention(candidate=1), mention(decision="unsure", candidate=None),
                mention(decision="merge", candidate=None, merge=[0, 0]), mention(ref="m2", decision="merge", merge=[0]),
                mention(ref="m12", candidate=0), mention(candidate=True)):
        assert validate.link_problems(answer([bad]), CANDS), bad
    good = [mention(), mention(ref="m2", decision="merge", candidate=None, merge=[1, 0]),
            mention(ref="m2", decision="create", candidate=None)]
    assert validate.link_problems(answer(good[:2]), CANDS) == []
    assert validate.link_problems(answer(good[2:]), CANDS) == []
    assert validate.link_problems(answer([mention(candidate=None)]), None) == []  # no packet: refs not checked


def test_an_index_answered_twice_in_contradiction_is_invalid_but_one_to_many_is_not():
    """B's S2 claim 2 (``existing: null`` beside ``existing: 2``) is invalid; the watch's one statement superseding
    three deadlines (be8efd3c) and one task closing three open ones (8411574f) are valid. MUTATION: compare whole
    entries (the watch answers become invalid) or drop the duplicate check (B's passes)."""
    b = [{"index": 2, "relation_to_existing": "restates", "existing": None, "reason": ""},
         {"index": 2, "relation_to_existing": "restates", "existing": 2, "reason": ""}]
    assert validate.link_problems(answer(claims=b), CANDS) == ["statement 2 is answered twice, differently"]
    relation = [{"index": 0, "relation_to_existing": "new", "existing": None, "reason": ""},
                {"index": 0, "relation_to_existing": "supersedes", "existing": 0, "reason": ""}]
    assert validate.link_problems(answer(claims=relation), CANDS)
    watch = [{"index": 0, "relation_to_existing": "supersedes", "existing": e, "reason": "new deadline"}
             for e in (3, 8, 5)]
    tasks = [{"index": 0, "closes_open_task": t} for t in (0, 2, 4)]
    assert validate.link_problems(answer(claims=watch, tasks=tasks), CANDS) == []
    half = [{"index": 0, "closes_open_task": 0, "evidence": "done"}, {"index": 0, "closes_open_task": None}]
    assert validate.link_problems(answer(tasks=half), CANDS) == ["task 0 is answered twice, differently"]
    twin = [mention(), mention(candidate=None, decision="create")]
    assert validate.link_problems(answer(twin), CANDS) == ["mention m1 is answered twice, differently"]
    assert validate.link_problems(answer([mention(), mention()]), CANDS) == []  # the same decision twice


def test_braces_in_quoted_fields_and_the_notes_own_words_are_never_corruption():
    """R5 4.5 / R5-10: evidence, quotes, literal objects and task texts are never read; a fragment the note itself
    contains is its words. MUTATION: widen the check to every string (these become invalid)."""
    ext = {"summary": "The config of the drain.",
           "mentions": [{"ref": "m1", "name": "config", "kind": "artifact", "aliases": [], "role": "the file"}],
           "claims": [{"subject": "m1", "predicate": "ex:holds", "object": '{"max_items": 20, "reason": "x"}',
                       "object_is_literal": True, "evidence": 'the file reads {"name": "drain", "summary": "x"},{'}],
           "tasks": [{"text": 'set "summary": "on" in the file', "status": "open"}],
           "time_refs": [{"text": '"due": tomorrow'}], "topics": [], "document_date": None}
    assert validate.extraction_problems(ext) == []
    assert validate.extraction(ext) is None
    note = 'Our API answers {"name": "drain", "reason": "busy"} when it is busy.'
    quoting = answer([mention(summary='The drain API answers {"name": "drain", "reason": "busy"} when busy.')])
    assert validate.link_problems(quoting, CANDS)  # no context: a fragment
    assert validate.link_problems(quoting, CANDS, validate.link_context(None, note)) == []
    assert validate.link_answer(answer(claims=[{"index": 0, "relation_to_existing": "new", "existing": None,
                                                "reason": 'quoted {"max_items": 20} from the note'}]),
                                {"mentions": {"m1": ["e"]}}) is None


def test_the_extraction_prose_is_checked_too():
    bad = {"summary": 'Zorn Labs.","mentions":[{"ref":"m2"', "mentions": [], "claims": [], "tasks": [],
           "time_refs": [], "topics": []}
    assert validate.extraction_problems(bad)
    role = {"summary": "x", "mentions": [{"ref": "m1", "name": "Zorn", "kind": "org", "aliases": [],
                                          "role": 'the lab","kind":"org"}]'}], "claims": [], "tasks": []}
    assert validate.extraction(role)["valid"] is False
    dup = {"summary": "x", "mentions": [{"ref": "m1", "name": "Zorn", "kind": "org"},
                                        {"ref": "m1", "name": "Mira", "kind": "person"}]}
    assert validate.extraction_problems(dup) == ["mention m1 is answered twice, differently"]


def _keys(schema, out):
    if isinstance(schema, dict):
        for k, v in (schema.get("properties") or {}).items():
            out.add(k)
            _keys(v, out)
        _keys(schema.get("items"), out)
    return out


def test_the_key_lists_cover_the_schemas():
    """A field added to a schema is a fragment the check must know. MUTATION: drop a key from LINK_KEYS."""
    assert _keys(schemas.LINKING, set()) <= set(validate.LINK_KEYS)
    assert _keys(schemas.EXTRACTION, set()) <= set(validate.EXTRACTION_KEYS)


# --------------------------------------------------------------------------- note-process: retry, then fail

EXT = {"summary": "Zorn Labs approved the budget.",
       "mentions": [{"ref": "m1", "name": "Zorn Labs", "kind": "org", "aliases": [], "role": "the lab"}],
       "claims": [], "tasks": [], "time_refs": [], "topics": []}
CLEAN = {"mentions": [{"ref": "m1", "decision": "create", "name": "Zorn Labs", "kind": "org",
                       "summary": "Zorn Labs, a lab."}], "claims": [], "tasks": [], "revision_summary": "Zorn Labs."}
CORRUPT = {"mentions": [{"ref": "m1", "decision": "create", "name": "Zorn Labs", "kind": "org",
                         "summary": B_71970EB8}], "claims": [], "tasks": [], "revision_summary": "Zorn Labs."}
CORRUPT_EXT = dict(EXT, summary='Zorn Labs approved the budget.","mentions":[{"ref":"m1"')
FILING = {"title": "Zorn", "kind": "fact", "topics": [], "urgency": "normal", "language": "en", "one_line": "Zorn."}


def _drain(tmp_path, script):
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    llm = ScriptedLLM({"QuickFiling": FILING, **script})
    runner = NoteRunner.with_llm(llm, bind(tmp_path / "nb"))
    cap = runner.run_capture("Zorn Labs approved the budget.", idempotency_key="k", source={"client": "cli"},
                             occurred_at="2026-10-20T12:00:00Z")
    out = runner.run_drain(max_items=1, budget_s=60)
    nb = Notebook.open(tmp_path / "nb")
    try:
        state = {"queue": nb.queue.item(cap["note_id"]), "errors": nb.queue.errors(cap["note_id"]),
                 "revisions": nb.store.q1("SELECT COUNT(*) FROM revisions WHERE note_id=?", (cap["note_id"],))[0],
                 "summary": (nb.store.entity_row("ex:org-zorn-labs") or {"summary": None})["summary"]}
    finally:
        nb.close()
    state["link_prompts"] = [c["prompt"] for c in llm.calls if c["schema"] == "Linking"]
    return out, [c["schema"] for c in llm.calls], state


def test_a_corrupt_linking_answer_is_asked_again_and_the_sound_one_is_applied(tmp_path):
    """MUTATION: wire link straight to keep_linking (the corrupt summary is applied)."""
    out, calls, st = _drain(tmp_path, {"Extraction": EXT, "Linking": [CORRUPT, CLEAN]})
    assert out["processed"] == 1 and calls == ["QuickFiling", "Extraction", "Linking", "Linking"]
    assert st["summary"] == "Zorn Labs, a lab." and st["revisions"] == 1
    # the second call is the same message followed by what was wrong (MUTATION: retry with the bare prompt)
    first, second = st["link_prompts"]
    assert second.startswith(first) and "Your previous answer to this message could not be used: the summary of " \
        "“Zorn Labs” holds a piece of the answer's structure" in second


def test_two_corrupt_linking_answers_fail_the_note_and_nothing_is_applied(tmp_path):
    """MUTATION: keep the second answer whatever it is (the corrupt summary is applied)."""
    out, calls, st = _drain(tmp_path, {"Extraction": EXT, "Linking": [CORRUPT, CORRUPT]})
    assert out["failed"] == 1 and calls.count("Linking") == 2
    assert st["revisions"] == 0 and st["summary"] is None and st["queue"]["state"] == "queued"
    err = st["errors"][0]["error"]
    assert err == ("The model's linking answer was malformed twice (the summary of “Zorn Labs” holds a piece of the "
                   "answer's structure (reason\":)); nothing from it was applied.")
    assert out["errors"][0]["error"] == err


def test_a_corrupt_reading_is_read_again_and_a_second_corrupt_one_fails_the_note(tmp_path):
    out, calls, st = _drain(tmp_path, {"Extraction": [CORRUPT_EXT, EXT], "Linking": CLEAN})
    assert out["processed"] == 1 and calls.count("Extraction") == 2
    out, calls, st = _drain(tmp_path / "2", {"Extraction": [CORRUPT_EXT, CORRUPT_EXT], "Linking": CLEAN})
    assert out["failed"] == 1 and "Linking" not in calls and st["revisions"] == 0
    assert st["errors"][0]["error"].startswith("The model's reading answer was malformed twice (the summary holds")


def test_the_apply_step_refuses_a_corrupt_answer_on_its_own(tmp_path):
    """A caller that skips the flow's check (a headless tool call) still applies nothing.
    MUTATION: drop validate.check_apply from apply_note_revision."""
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools.context import ToolContext
    from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates

    nb = Notebook.open(tmp_path / "nb")
    try:
        ctx = ToolContext(nb=nb)
        nid = nb.capture("Zorn Labs approved the budget.", occurred_at="2026-10-20T12:00:00Z").note_id
        note_context(ctx, nid)
        note_resolve_candidates(ctx, nid, [{"ref": "m1", "name": "Zorn Labs", "kind": "org", "aliases": []}])
        with pytest.raises(validate.InvalidAnswer):
            note_apply_revision(ctx, nid, EXT, CORRUPT)
        with pytest.raises(validate.InvalidAnswer):
            note_apply_revision(ctx, nid, CORRUPT_EXT, CLEAN)
        assert nb.store.q1("SELECT COUNT(*) FROM revisions")[0] == 0
        assert note_apply_revision(ctx, nid, EXT, CLEAN)["entities_created"] == ["ex:org-zorn-labs"]
    finally:
        nb.close()
