"""Processing with scripted Extraction/Linking dicts (no model): context, candidates, revision ops."""

from __future__ import annotations

import pytest

from abstractnote.process import prompts, schemas
from abstractnote.process.merge import merge_extractions
from abstractnote.process.vocabulary import normalize_predicate, vocabulary
from abstractnote.tools.context import ToolContext
from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates
from test_store_support import nb  # noqa: F401


def mention(ref, name, kind, aliases=(), role=""):
    return {"ref": ref, "name": name, "kind": kind, "aliases": list(aliases), "role": role}


def stmt(s, p, o, *, literal=False, certainty="stated", stance="assert", evidence="", valid_from=None):
    return {"subject": s, "predicate": p, "object": o, "object_is_literal": literal, "certainty": certainty,
            "stance": stance, "evidence": evidence or f"{s} {p} {o}", "valid_from": valid_from, "valid_until": None}


def extraction(mentions, claims=(), tasks=(), topics=(), summary="A note."):
    return {"summary": summary, "mentions": list(mentions), "claims": list(claims), "tasks": list(tasks),
            "time_refs": [], "topics": list(topics)}


AUTO = object()


def ml(ref, decision, candidate=None, *, name="", kind=None, merge=(), summary=AUTO, aliases=()):
    """A mention decision; by default it carries a one-sentence summary, as the Linking schema asks."""
    if summary is AUTO:
        summary = f"{name or ref} as this note describes it."
    return {"ref": ref, "decision": decision, "candidate": candidate, "merge_candidates": list(merge), "name": name,
            "kind": kind, "aliases": list(aliases), "summary": summary, "reason": "test"}


def run(nb, text, at, ext, link_fn, **capture):
    """Capture, then the note-process steps with a scripted model: context -> resolve -> link -> apply."""
    ctx = ToolContext(nb=nb)
    note_id = nb.capture(text, occurred_at=at, **capture).note_id
    packet = note_context(ctx, note_id)
    merged, names = merge_extractions([ext] if isinstance(ext, dict) else ext)
    cands = note_resolve_candidates(ctx, note_id, names)
    linking = link_fn(packet, cands)
    out = note_apply_revision(ctx, note_id, ext, linking)
    return note_id, packet, cands, out


def cand_index(cands, ref, entity_id):
    for m in cands["mentions"]:
        if m["ref"] == ref:
            for c in m["candidates"]:
                if c["entity_id"] == entity_id:
                    return c["index"]
    raise AssertionError(f"{entity_id} is not a candidate of {ref}: {cands}")


def claim_index(packet, cands, claim_id):
    for c in packet["existing_claims"] + cands["existing_claims"]:
        if c["claim_id"] == claim_id:
            return c["index"]
    raise AssertionError(f"{claim_id} not in the packet")


def seed(nb):
    """Dana works at Acme; she participates in LUT; a task is open."""
    ext = extraction(
        [mention("m1", "Dana", "person", ["Dana Ng"]), mention("m2", "Acme", "org"), mention("m3", "LUT", "project")],
        [stmt("m1", "ex:works_at", "m2"), stmt("m1", "schema:participant", "m3")],
        [{"text": "Send Dana the Pareto", "status": "open", "due": "2026-01-09"}], ["lut"])
    link = lambda p, c: {"mentions": [ml("m1", "create", name="Dana", kind="person", summary="A colleague at Acme."),
                                      ml("m2", "create", name="Acme", kind="org"),
                                      ml("m3", "create", name="LUT", kind="project")],
                         "claims": [{"index": 0, "relation_to_existing": "new", "existing": None, "reason": ""},
                                    {"index": 1, "relation_to_existing": "new", "existing": None, "reason": ""}],
                         "tasks": [], "revision_summary": "Met Dana from Acme about LUT."}
    return run(nb, "Met Dana Ng from Acme about the LUT project; send her the Pareto.", "2026-01-05T10:00:00Z",
               ext, link)


def test_new_entities_claims_tasks_topics(nb):
    note_id, packet, cands, out = seed(nb)
    assert sorted(out["entities_created"]) == ["ex:org-acme", "ex:person-dana", "ex:project-lut"]
    assert len(out["claims_added"]) == 2 and len(out["tasks_opened"]) == 1
    assert out["summary"] == "Met Dana from Acme about LUT."
    dana = nb.store.entity_brief("ex:person-dana")
    assert dana["aliases"] == ["Dana Ng"] and dana["summary"] == "A colleague at Acme." and dana["note_count"] == 1
    note = nb.note(note_id)
    assert note["status"] == "processed" and "lut" in note["topics"] and note["summary"] == "A note."
    assert nb.queue.item(note_id)["state"] == "done"
    assert nb.tasks()["items"][0]["due"] == "2026-01-09T00:00:00Z"
    assert any("ex:works_at" in w for w in out["warnings"])  # outside the vocabulary, reported
    assert packet["vocabulary"] and {"predicate", "gloss"} <= set(packet["vocabulary"][0])


def test_alias_reuse_and_candidates(nb):
    seed(nb)
    ext = extraction([mention("m1", "dana ng", "person"), mention("m2", "LUT", "project")],
                     [stmt("m1", "schema:knowsAbout", "m2")])

    def link(packet, cands):
        dana = [c for c in packet["candidate_entities"] if c["entity_id"] == "ex:person-dana"][0]
        assert dana["matched_by"] == "name_in_text" and dana["active_claims"]
        [m1] = [m for m in cands["mentions"] if m["ref"] == "m1"]
        assert m1["candidates"][0]["entity_id"] == "ex:person-dana"
        assert "alias" in m1["candidates"][0]["matched_by"] and m1["candidates"][0]["score"] == 1.0
        return {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", "ex:person-dana"), name="Dana"),
                             ml("m2", "reuse", cand_index(cands, "m2", "ex:project-lut"))],
                "claims": [], "tasks": [], "revision_summary": "Dana knows LUT."}

    _, _, _, out = run(nb, "dana ng knows the LUT internals", "2026-01-06T10:00:00Z", ext, link)
    assert out["entities_created"] == []
    assert nb.store.entity_brief("ex:person-dana")["note_count"] == 2
    assert nb.store.entity_brief("ex:person-dana")["aliases"] == ["Dana Ng"]  # "dana ng" folds to the same alias


def test_merge_keeps_both_records(nb):
    seed(nb)
    # an earlier note created a second record for Dana
    ext0 = extraction([mention("m1", "D. Ng", "person")])
    run(nb, "D. Ng called.", "2026-01-02T10:00:00Z", ext0,
        lambda p, c: {"mentions": [ml("m1", "create", name="D. Ng", kind="person")], "claims": [], "tasks": [],
                      "revision_summary": "A call."})
    ext = extraction([mention("m1", "Dana", "person", ["D. Ng"])])

    def link(packet, cands):
        a = cand_index(cands, "m1", "ex:person-dana")
        b = cand_index(cands, "m1", "ex:person-d-ng")
        return {"mentions": [ml("m1", "merge", merge=[a, b], name="Dana")], "claims": [], "tasks": [],
                "revision_summary": "D. Ng is Dana."}

    _, _, _, out = run(nb, "Dana (D. Ng) confirmed by mail.", "2026-01-07T10:00:00Z", ext, link)
    assert out["entities_merged"] == [{"from_id": "ex:person-d-ng", "into_id": "ex:person-dana"}]
    assert nb.store.entity_row("ex:person-d-ng") is not None
    assert nb.store.canonical("ex:person-d-ng") == "ex:person-dana"
    assert nb.store.entity_brief("ex:person-dana")["note_count"] == 3


def test_restate_reinforces_and_identical_statement_is_not_duplicated(nb):
    _, packet0, _, out0 = seed(nb)
    works = [c for c in out0["claims_added"] if nb.store.claim_row(c)["predicate"] == "ex:works_at"][0]
    ext = extraction([mention("m1", "Dana", "person"), mention("m2", "Acme", "org")],
                     [stmt("m1", "ex:works_at", "m2"), stmt("m1", "ex:works_at", "m2", evidence="again")])

    def link(packet, cands):
        return {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", "ex:person-dana")),
                             ml("m2", "reuse", cand_index(cands, "m2", "ex:org-acme"))],
                "claims": [{"index": 0, "relation_to_existing": "restates",
                            "existing": claim_index(packet, cands, works), "reason": "same"},
                           {"index": 1, "relation_to_existing": "new", "existing": None, "reason": "model missed it"}],
                "tasks": [], "revision_summary": "Dana still at Acme."}

    _, _, _, out = run(nb, "Dana is still at Acme.", "2026-02-01T10:00:00Z", ext, link)
    assert out["claims_added"] == [] and out["claims_reinforced"] == [works]
    assert nb.store.claim_row(works)["reinforced_count"] == 1
    rel = nb.store.q1("SELECT count, last_seen FROM relations WHERE predicate='ex:works_at'")
    assert rel["count"] == 2 and rel["last_seen"] == "2026-02-01T10:00:00Z"


def test_supersede_closes_the_old_claim_in_both_stores(nb):
    _, _, _, out0 = seed(nb)
    works = [c for c in out0["claims_added"] if nb.store.claim_row(c)["predicate"] == "ex:works_at"][0]
    ext = extraction([mention("m1", "Dana", "person"), mention("m2", "Beta", "org")],
                     [stmt("m1", "ex:works_at", "m2", stance="revise")])

    def link(packet, cands):
        return {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", "ex:person-dana")),
                             ml("m2", "create", name="Beta", kind="org")],
                "claims": [{"index": 0, "relation_to_existing": "supersedes",
                            "existing": claim_index(packet, cands, works), "reason": "she moved"}],
                "tasks": [], "revision_summary": "Dana moved to Beta."}

    _, _, _, out = run(nb, "Dana left Acme and joined Beta.", "2026-03-01T10:00:00Z", ext, link)
    assert out["claims_superseded"] == [works]
    old = nb.store.claim_row(works)
    assert old["status"] == "superseded" and old["valid_until"] == "2026-03-01T10:00:00Z"
    assert old["superseded_by"] == out["claims_added"][0]
    for at in ("2026-02-01T00:00:00Z", "2026-04-01T00:00:00Z"):
        assert sorted(c["claim_id"] for c in nb.beliefs_at(at)) == nb.kg.active_claim_ids(at)
    [closure] = nb.kg.journal.closures(assertion_id=works)
    assert closure.kind == "supersede"


def test_contradiction_marks_both_disputed(nb):
    seed(nb)
    ext1 = extraction([mention("m1", "LUT", "project")], [stmt("m1", "ex:budget", "40k", literal=True)])
    _, _, _, o1 = run(nb, "LUT budget is 40k.", "2026-01-10T10:00:00Z", ext1,
                      lambda p, c: {"mentions": [ml("m1", "reuse", cand_index(c, "m1", "ex:project-lut"))],
                                    "claims": [], "tasks": [], "revision_summary": "Budget."})
    budget = o1["claims_added"][0]
    ext2 = extraction([mention("m1", "LUT", "project")], [stmt("m1", "ex:budget", "55k", literal=True)])

    def link(packet, cands):
        return {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", "ex:project-lut"))],
                "claims": [{"index": 0, "relation_to_existing": "contradicts",
                            "existing": claim_index(packet, cands, budget), "reason": "numbers differ"}],
                "tasks": [], "revision_summary": "Conflicting budget."}

    _, _, _, o2 = run(nb, "Finance says the LUT budget is 55k.", "2026-01-11T10:00:00Z", ext2, link)
    new = o2["claims_added"][0]
    assert nb.store.claim_row(new)["certainty"] == "disputed"
    assert nb.store.claim_dict(nb.store.claim_row(budget))["disputed_with"] == [new]
    assert nb.store.claim_row(budget)["status"] == "active"
    from abstractnote.recall.reflect import contradictions

    rules = {i["rule"] for i in contradictions(nb)["items"]}
    assert rules == {"disputed_pair"}


def test_task_close_by_index(nb):
    seed(nb)
    ext = extraction([mention("m1", "Dana", "person")], [], [{"text": "Sent the Pareto to Dana", "status": "done",
                                                              "due": None}])

    def link(packet, cands):
        [task] = [t for t in packet["open_tasks"] if t["text"] == "Send Dana the Pareto"]
        return {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", "ex:person-dana"))], "claims": [],
                "tasks": [{"index": 0, "closes_open_task": task["index"]}], "revision_summary": "Pareto sent."}

    note_id, _, _, out = run(nb, "Sent the Pareto to Dana.", "2026-01-08T10:00:00Z", ext, link)
    assert len(out["tasks_closed"]) == 1 and out["tasks_opened"] == []
    [done] = nb.tasks(status="done")["items"]
    assert done["closed_at"] == "2026-01-08T10:00:00Z"
    assert nb.store.q1("SELECT closed_by_note_id FROM tasks WHERE task_id=?", (done["task_id"],))[0] == note_id


def test_references_outside_the_packet_are_rejected(nb):
    seed(nb)
    ext = extraction([mention("m1", "Dana", "person"), mention("m2", "Zed", "person")],
                     [stmt("m1", "skos:related", "m2"), stmt("m9", "skos:related", "m2")],
                     [{"text": "Call Zed", "status": "open", "due": "next week"}])

    def link(packet, cands):
        return {"mentions": [ml("m1", "reuse", 4), ml("m2", "merge", merge=[0, 7], name="Zed", kind="person"),
                             ml("m5", "reuse", 0)],
                "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": 99, "reason": ""}],
                "tasks": [{"index": 0, "closes_open_task": 42}], "revision_summary": "?"}

    _, _, _, out = run(nb, "Dana and Zed.", "2026-01-12T10:00:00Z", ext, link)
    created = set(out["entities_created"])
    # Candidate 4 does not exist: never guessed, created as new, and the new "Dana" is filed under the
    # entity that already has that canonical name (one canonical name per entity, store.names).
    assert "ex:person-dana-2" not in created and nb.store.entity_row("ex:person-dana-2") is None
    assert any("has the canonical name dana" in w for w in out["warnings"])
    assert out["claims_superseded"] == [] and len(out["claims_added"]) == 1
    assert out["tasks_closed"] == [] and len(out["tasks_opened"]) == 1
    text = " ".join(out["warnings"])
    for needle in ("not in its list", "merge did not name", "m5", "outside the context", "not a mention",
                   "not a date"):
        assert needle in text, needle


def test_unsure_creates_and_records_close_match(nb):
    seed(nb)
    ext = extraction([mention("m1", "Acme Corp", "org")])

    def link(packet, cands):
        return {"mentions": [ml("m1", "unsure", cand_index(cands, "m1", "ex:org-acme"), name="Acme Corp",
                                kind="org")], "claims": [], "tasks": [], "revision_summary": "Maybe Acme."}

    _, _, _, out = run(nb, "Acme Corp sent an invoice.", "2026-01-13T10:00:00Z", ext, link)
    assert out["entities_created"] == ["ex:org-acme-corp"]
    [cm] = out["claims_added"]
    row = nb.store.claim_row(cm)
    assert (row["predicate"], row["object_id"], row["certainty"]) == ("skos:closeMatch", "ex:org-acme", "possible")


def test_reapplying_a_processed_note_is_a_no_op(nb):
    note_id, _, _, out = seed(nb)
    counts = nb.store.counts()
    again = note_apply_revision(ToolContext(nb=nb), note_id, extraction([mention("m1", "Other", "thing")]),
                                {"mentions": [ml("m1", "create", name="Other", kind="thing")]})
    assert again["revision_id"] == out["revision_id"] and again["already_applied"] is True
    assert nb.store.counts() == counts


def test_oversized_note_sections_and_merge(nb):
    body = ("## Part\n\n" + "Dana reviewed the LUT plan in detail. " * 300 + "\n\n") * 30
    note_id = nb.capture(body, occurred_at="2026-01-20T10:00:00Z").note_id
    packet = note_context(ToolContext(nb=nb), note_id, max_chars=5000)
    assert len(packet["sections"]) > 1 and "".join(s["text"] for s in packet["sections"]) == body
    parts = [extraction([mention("m1", "Dana", "person"), mention("m2", "LUT", "project")],
                        [stmt("m1", "schema:participant", "m2")], summary="Part one."),
             extraction([mention("m1", "LUT", "project"), mention("m2", "dana", "person", ["D."])],
                        [stmt("m2", "schema:knowsAbout", "m1")], summary="Part two.")]
    merged, names = merge_extractions(parts)
    assert [m["ref"] for m in names] == ["m1", "m2"] and names[0]["aliases"] == ["D."]
    assert merged["claims"][1]["subject"] == "m1" and merged["claims"][1]["object"] == "m2"
    assert merged["summary"] == "Part one. Part two."
    note_resolve_candidates(ToolContext(nb=nb), note_id, names)
    out = note_apply_revision(ToolContext(nb=nb), note_id, parts, {
        "mentions": [ml("m1", "create", name="Dana", kind="person"), ml("m2", "create", name="LUT", kind="project")],
        "claims": [], "tasks": [], "revision_summary": "Long review."})
    assert len(out["entities_created"]) == 2 and len(out["claims_added"]) == 2


def test_invalid_model_output_is_a_plain_error(nb):
    note_id = nb.capture("x", occurred_at="2026-01-20T10:00:00Z").note_id
    with pytest.raises(ValueError, match="Linking"):
        note_apply_revision(ToolContext(nb=nb), note_id, extraction([]), {"mentions": [{"ref": "m1",
                                                                                         "decision": "maybe"}]})
    with pytest.raises(ValueError, match="Extraction"):
        note_apply_revision(ToolContext(nb=nb), note_id, "not json", {})


def test_predicates_normalise_to_the_registry_or_ex():
    assert normalize_predicate("schema:knowsAbout") == ("schema:knowsAbout", None)
    p, w = normalize_predicate("worksFor")
    assert p == "ex:works_for" and "outside the vocabulary" in w
    assert normalize_predicate("ex:budget")[0] == "ex:budget"
    assert any(v["predicate"] == "skos:related" for v in vocabulary())


def test_schemas_match_the_flow_copy_and_prompts_render():
    from abstractnote.flows.build import _skeleton
    from abstractnote.flows.prompts_fallback import FLOW_PROMPT_VARS
    from abstractnote.flows.schemas_fallback import SCHEMAS as FLOW_SCHEMAS

    assert set(schemas.SCHEMAS) == set(FLOW_SCHEMAS)
    for name in schemas.SCHEMAS:
        assert _skeleton(schemas.SCHEMAS[name]) == _skeleton(FLOW_SCHEMAS[name]), name
    for name in prompts.NAMES:
        template = prompts.load(name)
        assert prompts.variables(template) == set(FLOW_PROMPT_VARS[name]), name
        rendered = prompts.render(template, {v: [1, "x"] for v in prompts.variables(template)})
        assert "{{" not in rendered
    with pytest.raises(ValueError):
        prompts.render("{{a}}", {})
    # pydantic mirrors accept what the JSON Schemas describe
    schemas.validate("Linking", {"mentions": [{"ref": "m1", "decision": "reuse", "candidate": 0}], "claims": [],
                                 "tasks": [], "revision_summary": "x"})


def test_a_missing_summary_is_a_warning_never_a_failed_note(nb):
    """Backlog 0024: the schema asks for summaries, but a missing one never fails the note."""
    seed(nb)
    ctx = ToolContext(nb=nb)
    # create without a summary: applied, with a placeholder from the mention's role and a warning
    nid = nb.capture("Zed called.", occurred_at="2026-02-02T10:00:00Z").note_id
    note_context(ctx, nid)
    out = note_apply_revision(ctx, nid, extraction([mention("m1", "Zed", "person", role="called about the budget")]),
                              {"mentions": [ml("m1", "create", name="Zed", kind="person", summary=None)]})
    assert out["revision_id"] and any("No summary was written for Zed" in w for w in out["warnings"])
    zed = nb.store.q1("SELECT entity_id, summary FROM entities WHERE name='zed'")
    assert zed["summary"] == "Called about the budget."
    # blank summary and no role: the placeholder says where it was seen
    nid1 = nb.capture("Yan too.", title="Calls", occurred_at="2026-02-02T11:00:00Z").note_id
    note_context(ctx, nid1)
    note_apply_revision(ctx, nid1, extraction([mention("m1", "Yan", "person")]),
                        {"mentions": [ml("m1", "create", name="Yan", kind="person", summary="  ")]})
    assert nb.store.q1("SELECT summary FROM entities WHERE name='yan'")[0] == "Mentioned in \u201cCalls\u201d on 2026-02-02."
    # a reused entity the note tells something new about, without a summary: kept, with a warning
    ext = extraction([mention("m1", "Dana", "person"), mention("m2", "LUT", "project")],
                     [stmt("m1", "schema:knowsAbout", "m2")])
    nid2 = nb.capture("Dana knows LUT well.", occurred_at="2026-02-03T10:00:00Z").note_id
    note_context(ctx, nid2)
    _, names = merge_extractions([ext])
    cands = note_resolve_candidates(ctx, nid2, names)
    link = {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", "ex:person-dana"), summary=None),
                         ml("m2", "reuse", cand_index(cands, "m2", "ex:project-lut"), summary="LUT is the project.")]}
    out2 = note_apply_revision(ctx, nid2, ext, link)
    assert out2["revision_id"] and nb.store.note_row(nid2)["status"] == "processed"
    assert any(w == "No new summary was written for Dana; the earlier summary was kept." for w in out2["warnings"])
    assert nb.store.entity_row("ex:person-dana")["summary"] == "A colleague at Acme."
    assert nb.store.entity_row("ex:project-lut")["summary"] == "LUT is the project."
    # an untouched reused entity may keep null, silently
    ext3 = extraction([mention("m1", "Dana", "person")])
    nid3 = nb.capture("Saw Dana.", occurred_at="2026-02-04T10:00:00Z").note_id
    note_context(ctx, nid3)
    cands3 = note_resolve_candidates(ctx, nid3, merge_extractions([ext3])[1])
    out3 = note_apply_revision(ctx, nid3, ext3, {"mentions": [ml("m1", "reuse", cand_index(cands3, "m1",
                                                                                           "ex:person-dana"),
                                                                summary=None)]})
    assert out3["revision_id"] and not any("summary" in w for w in out3["warnings"])


def test_extraction_prompt_steers_facts_to_literal_ex_claims():
    text = prompts.load_system("extract")
    assert "ex:budget_cap" in text and "ex:deadline" in text and "ex:decided" in text
    assert "{{vocabulary}}" in text
    assert "one sentence" in prompts.load_system("link")


def test_system_prompts_are_static_and_carry_the_vocabulary():
    """Backlog 0024: the instructions, the vocabulary and the output shape live in the system prompt
    (identical bytes for every note); the user templates carry only the note and its packet."""
    for name in prompts.NAMES:
        system = prompts.system_prompt(name)
        assert "{{" not in system and "Output JSON only" in system, name
        assert "Output JSON only" not in prompts.load(name), name
    system = prompts.system_prompt("extract")
    for v in vocabulary():
        assert f"- {v['predicate']}" in system
    assert system == prompts.system_prompt("extract")


def test_a_mention_the_note_never_writes_is_left_out(nb):
    """Eval 4.1 item 6: a name taken from the context (or the header), not written in the note's own
    text, is refused with a warning; its statements are skipped (subject) or kept as text (object)."""
    seed(nb)
    ctx = ToolContext(nb=nb)
    nid = nb.capture("Dana-Ng sent the budget.", title="About LUT", occurred_at="2026-03-01T10:00:00Z").note_id
    note_context(ctx, nid)
    ext = extraction([mention("m1", "Dana Ng", "person"), mention("m2", "LUT", "project"),
                      mention("m3", "budget", "concept")],
                     [stmt("m2", "schema:knowsAbout", "m3"), stmt("m1", "schema:knowsAbout", "m2")])
    out = note_apply_revision(ctx, nid, ext, {"mentions": [ml("m1", "create", name="Dana Ng", kind="person"),
                                                            ml("m2", "create", name="LUT", kind="project"),
                                                            ml("m3", "create", name="budget", kind="concept")]})
    assert "LUT is not written in the note; it was left out." in out["warnings"]  # only in the title
    rev = nb.store.revision_row(out["revision_id"])
    ops = __import__("json").loads(rev["ops_json"])
    created = {o["payload"]["name"] for o in ops if o["op"] == "entity.create"}
    assert created == {"Dana Ng", "budget"}  # "Dana-Ng" is the same words as "Dana Ng" (an alias of Dana: the
    # model's call, never a forced reuse)
    claims = [o["payload"] for o in ops if o["op"] == "claim.add"]
    assert len(claims) == 1 and claims[0]["object_literal"] == "LUT"


def test_flow_flags_about_a_part_reach_the_revision(nb):
    ctx = ToolContext(nb=nb)
    nid = nb.capture("Zed called.", occurred_at="2026-02-02T10:00:00Z").note_id
    note_context(ctx, nid)
    flag = "A part of this note names nothing although it has text, even when read a second time."
    ext = dict(extraction([mention("m1", "Zed", "person")]), warnings=[flag])
    out = note_apply_revision(ctx, nid, ext, {"mentions": [ml("m1", "create", name="Zed", kind="person")]})
    assert flag in out["warnings"]
    merged, _ = merge_extractions([ext, dict(extraction([]), warnings=[flag])])
    assert merged["warnings"] == [flag]


def test_a_reused_entity_gains_only_the_aliases_the_linking_lists(nb):
    seed(nb)
    ctx = ToolContext(nb=nb)
    nid = nb.capture("The flow tool and Dana again.", occurred_at="2026-03-02T10:00:00Z").note_id
    note_context(ctx, nid)
    ext = extraction([mention("m1", "flow tool", "software", aliases=["flow"]), mention("m2", "Dana", "person")])
    _, names = merge_extractions([ext])
    cands = note_resolve_candidates(ctx, nid, names)
    dana = cand_index(cands, "m2", "ex:person-dana")
    before = set(nb.store.aliases("ex:person-dana"))
    note_apply_revision(ctx, nid, ext, {"mentions": [ml("m1", "create", name="flow tool", kind="software"),
                                                      ml("m2", "reuse", dana, aliases=["D."], summary=None)]})
    after = set(nb.store.aliases("ex:person-dana"))
    assert after - before == {"D."}


def test_unsure_without_a_candidate_is_created_with_a_warning(nb):
    ctx = ToolContext(nb=nb)
    nid = nb.capture("Zed called.", occurred_at="2026-02-02T10:00:00Z").note_id
    note_context(ctx, nid)
    out = note_apply_revision(ctx, nid, extraction([mention("m1", "Zed", "person")]),
                              {"mentions": [ml("m1", "unsure", None, name="Zed", kind="person")]})
    assert "Zed was marked unsure without a candidate; it was created as new." in out["warnings"]


def test_the_wording_rules_stay_in_the_system_prompts():
    """Eval 4.1 items 2-9 (backlog 0024 step 3); red when a rule is removed."""
    ext, link, filing = (prompts.system_prompt(n) for n in ("extract", "link", "quick_filing"))
    assert "generic words are never mentions" in ext and "Each mention's name is written in this text" in ext
    assert "never extract the note itself" in ext
    assert "one to three topics" in ext and "one to three topics" in filing
    assert "`schema:mentions`" in ext and "`dcterms:hasPart` only when the text states" in ext
    assert "possible for proposals" in ext and "`stance: retract` and `valid_until`" in ext
    assert "a part, version, file, seat, tool or feature of X is a new entity related to X" in link
    assert "With no candidate, the answer is `create`, never `unsure`." in link
    assert "only other names the note uses for that very entity" in link
