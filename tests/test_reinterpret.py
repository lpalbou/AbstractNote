"""ADR 0008 (lane R2): dependency records, forward-only invalidation marks, ``note.reinterpret`` and the consolidation's
reinterpret stage. Scripted models only (no network): each test names the mechanism it guards, and goes red when that
mechanism is removed (the mutation list is in untracked/reports/R2.md)."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence

import pytest

from abstractnote import ids
from abstractnote.process.merge import merge_extractions
from abstractnote.store.notebook import Notebook
from abstractnote.tools.context import ToolContext
from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates

D05, D10, D20, D25 = "2026-03-05T09:00:00Z", "2026-03-10T09:00:00Z", "2026-03-20T09:00:00Z", "2026-03-25T09:00:00Z"
LUT = "ex:project-lut"


# ----------------------------------------------------------------------------- scripted answers


def mention(ref: str, name: str, kind: str, aliases: Sequence[str] = ()) -> Dict[str, Any]:
    return {"ref": ref, "name": name, "kind": kind, "aliases": list(aliases), "role": ""}


def stmt(s: str, p: str, o: str, *, literal: bool = False, certainty: str = "stated", evidence: str = "") -> Dict[str, Any]:
    return {"subject": s, "predicate": p, "object": o, "object_is_literal": literal, "certainty": certainty,
            "stance": "assert", "evidence": evidence or f"{s} {p} {o}", "valid_from": None, "valid_until": None}


def extraction(mentions: Sequence[Dict[str, Any]], claims: Sequence[Dict[str, Any]] = (),
               tasks: Sequence[Dict[str, Any]] = (), summary: str = "A note.") -> Dict[str, Any]:
    return {"summary": summary, "mentions": list(mentions), "claims": list(claims), "tasks": list(tasks),
            "time_refs": [], "topics": [], "document_date": None}


def ml(ref: str, decision: str, candidate: Optional[int] = None, *, name: str = "", kind: Optional[str] = None,
       aliases: Sequence[str] = (), summary: Optional[str] = "auto") -> Dict[str, Any]:
    return {"ref": ref, "decision": decision, "candidate": candidate, "merge_candidates": [], "name": name,
            "kind": kind, "aliases": list(aliases), "summary": (f"{name or ref}." if summary == "auto" else summary),
            "reason": "test"}


def cand(cands: Dict[str, Any], ref: str, eid: str) -> Optional[int]:
    for m in cands["mentions"]:
        if m["ref"] == ref:
            for c in m["candidates"]:
                if c["entity_id"] == eid:
                    return c["index"]
    return None


def claim_index(packet: Dict[str, Any], cands: Dict[str, Any], cid: str) -> int:
    for c in list(packet.get("existing_claims") or []) + list(cands.get("existing_claims") or []):
        if c["claim_id"] == cid:
            return c["index"]
    raise AssertionError(f"{cid} is not in the packet")


def link(decisions: Dict[str, Any], claims: Sequence[Any] = (), tasks: Sequence[Any] = ()) -> Callable[..., Dict[str, Any]]:
    """``decisions``: ref -> an entity id to reuse (when it is a candidate) or a create spec."""
    def fn(packet: Dict[str, Any], cands: Dict[str, Any]) -> Dict[str, Any]:
        ms = []
        for ref, d in decisions.items():
            if isinstance(d, str):
                i = cand(cands, ref, d)
                assert i is not None, f"{d} is not a candidate of {ref}"
                ms.append(ml(ref, "reuse", i, summary=None))
            else:
                ms.append(ml(ref, "create", **d))
        cs = [c(packet, cands) if callable(c) else c for c in claims]
        return {"mentions": ms, "claims": cs, "tasks": [t(packet) if callable(t) else t for t in tasks],
                "revision_summary": "test"}
    return fn


class Clock:
    def __init__(self) -> None:
        self.t = ids.parse_time("2026-10-20T12:00:00Z")

    def next(self) -> str:
        from datetime import timedelta

        self.t = self.t + timedelta(minutes=1)
        return self.t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(nb: Notebook, text: str, at: str, ext: Dict[str, Any], link_fn: Callable[..., Dict[str, Any]], *,
        reinterpret: bool = False, note_id: Optional[str] = None, now: Optional[str] = None) -> Dict[str, Any]:
    ctx = ToolContext(nb=nb)
    if note_id is None:
        note_id = nb.capture(text, occurred_at=at, now=now).note_id
    packet = note_context(ctx, note_id)
    _, names = merge_extractions([ext])
    cands = note_resolve_candidates(ctx, note_id, names)
    out = note_apply_revision(ctx, note_id, ext, link_fn(packet, cands), reinterpret=reinterpret)
    return {"note_id": note_id, "packet": packet, "cands": cands, "out": out}


def pending(nb: Notebook) -> List[str]:
    return [m["note_id"] for m in nb.store.reinterpret_marks()]


def actions(nb: Notebook, note_id: str) -> List[Optional[str]]:
    return [r[0] for r in nb.store.q("SELECT action FROM revisions WHERE note_id=? AND applied_index=1 ORDER BY seq",
                                     (note_id,))]


@pytest.fixture
def nb(tmp_path):
    notebook = Notebook.open(tmp_path / "nb")
    yield notebook
    notebook.close()


def day20_budget(nb: Notebook, now: str, certainty: str = "stated") -> Dict[str, Any]:
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget", "40k", literal=True, certainty=certainty,
                           evidence="We go with 40k for the LUT budget as decided")])
    return run(nb, "We go with 40k for the LUT budget as decided.", D20, ext,
               link({"m1": {"name": "LUT", "kind": "project"}}), now=now)


def day05_budget(nb: Notebook, now: str) -> Dict[str, Any]:
    ext = extraction([mention("m1", "LUT", "project"), mention("m2", "the board", "org")],
                     [stmt("m1", "ex:budget_status", "placeholder until the board meets", literal=True,
                           evidence="The 40k LUT figure is a placeholder until the board meets")])
    return run(nb, "The 40k LUT figure is a placeholder until the board meets on 2026-04-02.", D05, ext,
               link({"m1": LUT, "m2": {"name": "the board", "kind": "org"}}), now=now)


# ----------------------------------------------------------------------------- dependency records (§3)


def test_every_processing_revision_records_what_it_saw(nb):
    """context_json = {as_of, entities, claims, summaries, mention_queries, looked_up}; the day-5 note saw LUT and
    the day-20 statement. MUTATION: drop the context attach in Notebook.apply_revision."""
    clock = Clock()
    a = day20_budget(nb, clock.next())
    b = day05_budget(nb, clock.next())
    ctx_a = json.loads(nb.store.revision_row(a["out"]["revision_id"])["context_json"])
    assert ctx_a["as_of"] == D20 and ctx_a["looked_up"] is True and ctx_a["entities"] == []
    ctx_b = json.loads(nb.store.revision_row(b["out"]["revision_id"])["context_json"])
    assert LUT in ctx_b["entities"] and a["out"]["claims_added"][0] in ctx_b["claims"]
    assert [q["name"] for q in ctx_b["mention_queries"]] == ["LUT", "the board"]
    assert set(ctx_b["summaries"]) == set(ctx_b["entities"])


def test_a_packet_without_its_keys_fails_loudly(nb):
    """No silent empty record (the R1 seam): a stored packet that lost its keys refuses the apply."""
    from abstractnote.process.context import load_packet, save_packet

    note_id = nb.capture("LUT budget.", occurred_at=D20).note_id
    note_context(ToolContext(nb=nb), note_id)
    packet = load_packet(nb, note_id)
    packet.pop("existing_claims")
    save_packet(nb, note_id, packet)
    with pytest.raises(RuntimeError, match="depended on"):
        note_apply_revision(ToolContext(nb=nb), note_id, extraction([mention("m1", "LUT", "project")]),
                            {"mentions": [ml("m1", "create", name="LUT", kind="project")], "claims": [], "tasks": []})


# ----------------------------------------------------------------------------- marks (§4)


def test_f1_an_earlier_note_marks_the_later_one_forward_only(nb):
    """F1: day 5 arriving after day 20 marks day 20 (reason, by_note_id), never itself; the apply result says how many
    it marked. MUTATION: drop _mark_dependents, or invert the order key (backward marks)."""
    clock = Clock()
    a = day20_budget(nb, clock.next())
    b = day05_budget(nb, clock.next())
    marks = nb.store.reinterpret_marks()
    assert [m["note_id"] for m in marks] == [a["note_id"]]
    assert marks[0]["by_note_id"] == b["note_id"] and "2026-03-05" in marks[0]["reason"] and "LUT" in marks[0]["reason"]
    assert b["out"]["marked"] == 1 and b["out"]["reinterpret_marked"]["by_entity"][0]["entity_id"] == LUT
    # a later note than both marks nothing (forward only: nothing after it used LUT)
    c = run(nb, "LUT review.", "2026-04-01T09:00:00Z", extraction([mention("m1", "LUT", "project")]),
            link({"m1": LUT}), now=clock.next())
    assert c["out"]["marked"] == 0 and pending(nb) == [a["note_id"]]


def test_f1_reinterpretation_revises_the_same_statement_in_place(nb):
    """F1 + review 1.2: re-understood with day 5 as foundation, the 40k statement is planned (certainty possible):
    same (s, p, o) -> claim.revise in place, the belief stays one belief, the KG copy agrees, the history shows both
    readings, as_received before day 5 shows the old one, the mark is consumed. MUTATION: drop the claim.revise
    branch (the add is a silent no-op on the existing id) or the archive's previous values."""
    clock = Clock()
    a = day20_budget(nb, clock.next())
    before = clock.next()
    day05_budget(nb, clock.next())
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget", "40k", literal=True, certainty="possible",
                           evidence="We go with 40k for the LUT budget as decided")])
    r = run(nb, "", D20, ext, link({"m1": LUT}), reinterpret=True, note_id=a["note_id"], now=clock.next())
    cid = a["out"]["claims_added"][0]
    assert actions(nb, a["note_id"]) == [None, "note.reinterpret"]
    assert r["out"]["claims_revised"] == [cid] and r["out"]["claims_added"] == []
    beliefs = [c for c in nb.beliefs_at("2026-03-21", entity_id=LUT) if c["predicate"] == "ex:budget"]
    assert [(c["claim_id"], c["certainty"]) for c in beliefs] == [(cid, "possible")]
    assert cid in nb.kg.active_claim_ids("2026-03-21")
    pre = nb.entity(LUT, as_received=before)
    assert [c["certainty"] for c in pre["beliefs"] if c["predicate"] == "ex:budget"] == ["stated"]
    assert pending(nb) == [] and nb.store.q1("SELECT outcome FROM reinterpret_marks")[0] == "reinterpreted"
    history = nb.note(a["note_id"])["interpretations"]
    assert [h["action"] for h in history] == [None, "note.reinterpret"]
    assert history[0]["superseded_by"] == history[1]["revision_id"] and history[1]["context"]["as_of"] == D20
    kinds = {r[0] for r in nb.kg.journal._conn.execute("SELECT kind FROM memj_closures").fetchall()}
    assert "reinterpreted" in kinds


def test_f2_name_match_marks_and_the_duplicate_is_retired(nb):
    """F2: day 20 created "the calibration plan" and Priya; day 5 defines HCP (alias "the calibration plan") and
    Priya Natt: day 20 is marked (name key match), re-resolves to HCP and Priya Natt, its own duplicates are retired
    (not listed, not in the graph), the two-steps statement hangs on HCP; as_received before shows the duplicate
    with its note. MUTATION: drop the name-key expansion in later_dependents, or entity.retire."""
    clock = Clock()
    ext20 = extraction([mention("m1", "the calibration plan", "artifact"), mention("m2", "Priya", "person")],
                       [stmt("m1", "ex:steps_remaining", "two", literal=True, evidence="two steps remain")])
    a = run(nb, "Reviewed the calibration plan with Priya; two steps remain.", D20, ext20,
            link({"m1": {"name": "the calibration plan", "kind": "artifact"}, "m2": {"name": "Priya", "kind": "person"}}),
            now=clock.next())
    dups = sorted(a["out"]["entities_created"])
    before = clock.next()
    ext05 = extraction([mention("m1", "Helix calibration plan", "artifact", aliases=["HCP", "the calibration plan"]),
                        mention("m2", "Priya Natt", "person", aliases=["Priya"])],
                       [stmt("m1", "ex:written_by", "m2", evidence="Priya Natt wrote")])
    b = run(nb, "The Helix calibration plan (HCP) is the three-step plan Priya Natt wrote.", D05, ext05,
            link({"m1": {"name": "Helix calibration plan", "kind": "artifact", "aliases": ["HCP", "the calibration plan"]},
                  "m2": {"name": "Priya Natt", "kind": "person", "aliases": ["Priya"]}}), now=clock.next())
    assert pending(nb) == [a["note_id"]]
    hcp = next(e for e in b["out"]["entities_created"] if "helix" in e)
    priya = next(e for e in b["out"]["entities_created"] if "priya-natt" in e)
    r = run(nb, "", D20, ext20, link({"m1": hcp, "m2": priya}), reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert r["out"]["entities_created"] == [] and sorted(r["out"]["entities_retired"]) == dups
    live = {e["entity_id"] for e in nb.entities()["items"]}
    assert not set(dups) & live and {hcp, priya} <= live
    assert not set(dups) & {n["id"] for n in nb.graph()["nodes"]}
    steps = [c for c in nb.beliefs_at("2026-03-21", entity_id=hcp) if c["predicate"] == "ex:steps_remaining"]
    assert [c["object_literal"] for c in steps] == ["two"]
    dup_plan = next(e for e in dups if "calibration" in e)
    pre = nb.entity(dup_plan, as_received=before)
    assert pre["note_count"] == 1 and pre["first_seen"] == D20
    assert a["note_id"] in {n["note_id"] for n in nb.entity(hcp)["notes"]}
    # integration R1 x R2: the close-match review item day 5 raised for the duplicate (R1's alias rule) is answered by
    # the re-reading: it neither keeps the duplicate alive nor stays in review; the re-reading raises none for it.
    # MUTATION: drop the CLOSE_MATCH exclusion in plan_reinterpretation, _ReinterpretBuilder.alias_matches or the
    # retired skip in review._close_matches.
    sides = {x["id"] for it in nb.review()["items"] for x in it.get("sides") or []}
    assert not set(dups) & sides
    assert not any("matches" in w and "kept for review" in w for w in r["out"]["warnings"])


def test_an_alias_key_equal_to_a_later_entry_name_marks_it(nb):
    """The F2 match alone (exact keys, ADR 0004): the earlier note's entry carries the alias "the calibration plan"
    (from the linking) without its words writing that phrase; the later note that created an entry of that name is
    marked. MUTATION: drop the name-key expansion in later_dependents."""
    clock = Clock()
    a = run(nb, "Reviewed the calibration plan.", D20, extraction([mention("m1", "the calibration plan", "artifact")]),
            link({"m1": {"name": "the calibration plan", "kind": "artifact"}}), now=clock.next())
    run(nb, "The Helix calibration plan (HCP) has three steps.", D05,
        extraction([mention("m1", "Helix calibration plan", "artifact", aliases=["HCP"])]),
        link({"m1": {"name": "Helix calibration plan", "kind": "artifact", "aliases": ["HCP", "the calibration plan"]}}),
        now=clock.next())
    assert pending(nb) == [a["note_id"]]


def test_an_earlier_note_that_names_a_later_entry_marks_it_even_when_it_made_its_own(nb):
    """The earlier note's words write "Priya" (a later note's entry) but it created "Priya Natt" without that alias:
    the later note is still marked (its Priya may be Priya Natt). MUTATION: drop _names_in_note from the touched
    set."""
    clock = Clock()
    a = run(nb, "Met Priya about the spectrometer.", D20, extraction([mention("m1", "Priya", "person")]),
            link({"m1": {"name": "Priya", "kind": "person"}}), now=clock.next())
    run(nb, "Priya Natt wrote the plan.", D05, extraction([mention("m1", "Priya Natt", "person")]),
        link({"m1": {"name": "Priya Natt", "kind": "person"}}), now=clock.next())
    assert pending(nb) == [a["note_id"]]


def test_f3_an_earlier_task_marks_the_later_close_and_never_closes_it(nb):
    """F3 (review 3.7): the earlier note opens two write-ups and closes nothing; the later note that closed "the
    calibration write-up" is marked by the task text rule even without a shared entity. MUTATION: drop the
    task_closed rule (opened_task_texts)."""
    clock = Clock()
    ext20 = extraction([mention("m1", "lab", "place")],
                       tasks=[{"text": "the calibration write-up", "status": "done", "due": None}])
    a = run(nb, "Closed in the lab: the calibration write-up is done.", D20, ext20,
            link({"m1": {"name": "lab", "kind": "place"}}), now=clock.next())
    ext05 = extraction([mention("m1", "Helix", "project")],
                       tasks=[{"text": "the calibration write-up for Helix", "status": "open", "due": None},
                              {"text": "the optics write-up", "status": "open", "due": None}])
    b = run(nb, "Two write-ups are due for Helix: the calibration write-up for Helix and the optics write-up.", D05,
            ext05, link({"m1": {"name": "Helix", "kind": "project"}}), now=clock.next())
    assert b["out"]["tasks_closed"] == [] and len(nb.tasks()["items"]) == 2
    assert pending(nb) == [a["note_id"]]


def test_f5_a_note_that_only_saw_the_entity_is_not_marked(nb):
    """F5 + review 3.1: the watch note's packet SHOWED Zorn Labs (named in its text) but it did not use it; a 2022
    Zorn note marks the Zorn note only. MUTATION: key invalidation on context_json.entities (seen)."""
    clock = Clock()
    z = run(nb, "Zorn Labs runs the optics wing.", "2026-01-10T09:00:00Z", extraction([mention("m1", "Zorn Labs", "org")]),
            link({"m1": {"name": "Zorn Labs", "kind": "org"}}), now=clock.next())
    w = run(nb, "The watch app shows the Zorn Labs calendar.", "2026-02-10T09:00:00Z",
            extraction([mention("m1", "watch app", "software")]), link({"m1": {"name": "watch app", "kind": "software"}}),
            now=clock.next())
    assert "ex:org-zorn-labs" in json.loads(nb.store.revision_row(w["out"]["revision_id"])["context_json"])["entities"]
    run(nb, "Zorn Labs was founded in a garage.", "2022-05-01T09:00:00Z", extraction([mention("m1", "Zorn Labs", "org")]),
        link({"m1": "ex:org-zorn-labs"}), now=clock.next())
    assert pending(nb) == [z["note_id"]]


def test_same_date_a_new_note_marks_its_peers_a_reinterpretation_only_later_ones(nb):
    """Review 3.3 tie rule: a NEW note marks every note on or after its date (same second included); a
    re-understanding marks only the notes after it in history order (date, received, id), so a pass is one forward
    sweep. MUTATION: strict > for the new note, or >= for the re-understanding."""
    clock = Clock()
    a = day20_budget(nb, clock.next())
    b = run(nb, "LUT review in the same second.", D20, extraction([mention("m1", "LUT", "project")]), link({"m1": LUT}),
            now=clock.next())
    assert pending(nb) == [a["note_id"]]  # b, received later the same second, marks a
    nb.store.consume_reinterpret_mark(a["note_id"])
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget", "40k", literal=True, certainty="possible",
                           evidence="We go with 40k for the LUT budget as decided")])
    run(nb, "", D20, ext, link({"m1": LUT}), reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert pending(nb) == [b["note_id"]]  # a's re-understanding marks b (after it), never a note before it
    nb.store.consume_reinterpret_mark(b["note_id"])
    run(nb, "", D20, extraction([mention("m1", "LUT", "project")], [stmt("m1", "ex:reviewed", "yes", literal=True,
                                                                         evidence="LUT review")]),
        link({"m1": LUT}), reinterpret=True, note_id=b["note_id"], now=clock.next())
    assert pending(nb) == []  # b's re-understanding cannot reach a (earlier in history order)


def test_a_redate_marks_forward_from_the_earlier_date_and_the_note_itself(nb):
    """Review 3.4: a note moved from day 25 to day 5 is a new foundation for the notes in between that used its
    entities. MUTATION: drop the note.redate branch of _mark_dependents."""
    clock = Clock()
    a = run(nb, "LUT kickoff.", D25, extraction([mention("m1", "LUT", "project")]),
            link({"m1": {"name": "LUT", "kind": "project"}}), now=clock.next())
    mid = run(nb, "LUT budget review.", D10, extraction([mention("m1", "LUT", "project")]), link({"m1": LUT}),
              now=clock.next())
    nb.store.consume_reinterpret_mark(a["note_id"])  # mid (earlier) marked a; take it out of the way
    nb.redate_note(a["note_id"], D05, "tester", reason="it was written on day 5")
    assert set(pending(nb)) == {mid["note_id"], a["note_id"]}


def test_a_mark_arriving_while_the_note_is_in_flight_is_kept_for_the_next_pass(nb):
    """Review 3.5: once a pass took a note's mark, a new earlier note marks it again as a NEW row, and the apply of
    the in-flight re-understanding consumes only the mark it took. MUTATION: a unique index over every unconsumed
    mark (the new mark is lost)."""
    clock = Clock()
    a = day20_budget(nb, clock.next())
    b = day05_budget(nb, clock.next())
    taken = nb.store.consume_reinterpret_mark(a["note_id"], "pass-1")
    assert taken["by_note_id"] == b["note_id"]
    c = run(nb, "LUT was named in January.", "2026-01-10T09:00:00Z", extraction([mention("m1", "LUT", "project")]),
            link({"m1": LUT}), now=clock.next())
    mine = [m["by_note_id"] for m in nb.store.reinterpret_marks() if m["note_id"] == a["note_id"]]
    assert mine == [c["note_id"]]  # a new row (day 5, also after January, has its own)
    nb.store.consume_marks(a["note_id"], "pass-1", "unchanged")
    assert [m["by_note_id"] for m in nb.store.reinterpret_marks() if m["note_id"] == a["note_id"]] == [c["note_id"]]


# ----------------------------------------------------------------------------- note.reinterpret (§1)


def test_an_identical_reading_is_unchanged_and_writes_nothing(nb):
    """The structural unchanged key: the same mentions, statements, tasks and topics -> no revision, the mark
    consumed as unchanged. MUTATION: drop the unchanged branch (a revision is written every time)."""
    clock = Clock()
    a = day20_budget(nb, clock.next())
    day05_budget(nb, clock.next())
    count = nb.store.q1("SELECT COUNT(*) FROM revisions")[0]
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget", "40k", literal=True, evidence="We go with 40k for the LUT budget as decided")])
    r = run(nb, "", D20, ext, link({"m1": LUT}), reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert r["out"]["unchanged"] is True and r["out"]["revision_id"] is None
    assert nb.store.q1("SELECT COUNT(*) FROM revisions")[0] == count and actions(nb, a["note_id"]) == [None]
    assert pending(nb) == [] and nb.store.q1("SELECT outcome FROM reinterpret_marks")[0] == "unchanged"


def test_a_statement_the_old_reading_closed_is_reopened_in_both_stores(nb):
    """Review 1.1 (R5-2): day 20 superseded the day-10 30k belief; re-understood, its statement is on another slot
    (a plan), so 30k is believed again after day 20, in the note index AND in AbstractMemory (a re-opened copy).
    MUTATION: drop claim.reopen (the old reading's closure stays)."""
    clock = Clock()
    a = run(nb, "LUT budget 30k.", D10, extraction([mention("m1", "LUT", "project")],
                                                   [stmt("m1", "ex:budget", "30k", literal=True, evidence="LUT budget 30k")]),
            link({"m1": {"name": "LUT", "kind": "project"}}), now=clock.next())
    c30 = a["out"]["claims_added"][0]
    b = run(nb, "We go with 40k for the LUT budget as decided.", D20,
            extraction([mention("m1", "LUT", "project")],
                       [stmt("m1", "ex:budget", "40k", literal=True, evidence="We go with 40k for the LUT budget as decided")]),
            link({"m1": LUT}, [lambda p, c: {"index": 0, "relation_to_existing": "supersedes",
                                             "existing": claim_index(p, c, c30), "reason": "test"}]), now=clock.next())
    assert nb.store.claim_row(c30)["valid_until"] == D20
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget_plan", "40k", literal=True, certainty="possible",
                           evidence="We go with 40k for the LUT budget as decided")])
    before = clock.next()
    r = run(nb, "", D20, ext, link({"m1": LUT}), reinterpret=True, note_id=b["note_id"], now=clock.next())
    assert r["out"]["claims_reopened"] == [c30]
    pre = [c["claim_id"] for c in nb.store.beliefs_at("2026-03-21", entity_id=LUT, as_received=before)
           if c["predicate"] == "ex:budget"]
    assert c30 not in pre  # before the re-understanding, day 20 had closed it
    assert [c["claim_id"] for c in nb.beliefs_at("2026-03-21", entity_id=LUT) if c["predicate"] == "ex:budget"] == [c30]
    assert c30 in nb.kg.active_claim_ids("2026-03-21") and c30 in nb.kg.active_claim_ids("2026-03-11")
    old40 = b["out"]["claims_added"][0]
    assert nb.store.claim_row(old40)["status"] == "reinterpreted" and old40 not in nb.kg.active_claim_ids("2026-03-21")


def test_a_supersede_of_a_statement_no_longer_believed_warns(nb):
    """Review 4.1 (R5-7): the packet named a statement; it was closed before the apply; the apply says so and keeps
    the new statement. MUTATION: drop the stale_context record in op_claim_supersede."""
    clock = Clock()
    a = run(nb, "LUT budget 30k.", D10, extraction([mention("m1", "LUT", "project")],
                                                   [stmt("m1", "ex:budget", "30k", literal=True, evidence="LUT budget 30k")]),
            link({"m1": {"name": "LUT", "kind": "project"}}), now=clock.next())
    c30 = a["out"]["claims_added"][0]
    ctx = ToolContext(nb=nb)
    note_id = nb.capture("LUT budget 40k.", occurred_at=D20).note_id
    packet = note_context(ctx, note_id)
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget", "40k", literal=True, evidence="LUT budget 40k")])
    _, names = merge_extractions([ext])
    cands = note_resolve_candidates(ctx, note_id, names)
    nb.retract_claim(c30, "tester", "closed meanwhile")
    out = note_apply_revision(ctx, note_id, ext, {
        "mentions": [ml("m1", "reuse", cand(cands, "m1", LUT), summary=None)],
        "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": claim_index(packet, cands, c30),
                    "reason": "t"}], "tasks": []})
    assert out["claims_superseded"] == [] and out["stale_context"] == [c30]
    assert any("no longer" in w for w in out["warnings"])


def test_no_reinterpreted_kind_in_abstractmemory_fails_before_anything_is_written(nb, monkeypatch):
    """F-b: the closure kind is AbstractMemory's; without it the re-understanding refuses loudly and writes nothing
    (never a retraction in its place). MUTATION: map reinterpreted to retract."""
    from abstractnote.store import kg

    clock = Clock()
    a = day20_budget(nb, clock.next())
    day05_budget(nb, clock.next())
    count = nb.store.q1("SELECT COUNT(*) FROM revisions")[0]
    monkeypatch.setattr(kg, "supports_reinterpreted", lambda: False)
    ext = extraction([mention("m1", "LUT", "project")], [stmt("m1", "ex:budget_plan", "40k", literal=True,
                                                               evidence="We go with 40k for the LUT budget as decided")])
    with pytest.raises(RuntimeError, match="reinterpreted"):
        run(nb, "", D20, ext, link({"m1": LUT}), reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert nb.store.q1("SELECT COUNT(*) FROM revisions")[0] == count and pending(nb) == [a["note_id"]]


def test_a_retired_entity_comes_back_when_a_note_mentions_it_again(nb):
    """entity.retire is reversible by a later mention."""
    clock = Clock()
    ext20 = extraction([mention("m1", "the calibration plan", "artifact")])
    a = run(nb, "Reviewed the calibration plan.", D20, ext20,
            link({"m1": {"name": "the calibration plan", "kind": "artifact"}}), now=clock.next())
    dup = a["out"]["entities_created"][0]
    b = run(nb, "The Helix calibration plan, also the calibration plan.", D05,
            extraction([mention("m1", "Helix calibration plan", "artifact", aliases=["the calibration plan"])]),
            link({"m1": {"name": "Helix calibration plan", "kind": "artifact", "aliases": ["the calibration plan"]}}),
            now=clock.next())
    hcp = b["out"]["entities_created"][0]
    run(nb, "", D20, ext20, link({"m1": hcp}), reinterpret=True, note_id=a["note_id"], now=clock.next())
    assert nb.store.entity_row(dup)["retired_revision_id"]
    run(nb, "The calibration plan (the old one) again.", "2026-04-01T09:00:00Z",
        extraction([mention("m1", "the calibration plan", "artifact")]), link({"m1": dup}), now=clock.next())
    assert nb.store.entity_row(dup)["retired_revision_id"] is None and dup in {e["entity_id"] for e in nb.entities()["items"]}


def test_rebuild_replays_reinterpretations_and_marks_nothing_again(nb):
    """A rebuild replays note.reinterpret ops to the same state and keeps the marks table (truth) as it was, never
    marking again. MUTATION: leave store.marking on during the replay, or drop reinterpret_marks from the copy."""
    clock = Clock()
    a = day20_budget(nb, clock.next())
    day05_budget(nb, clock.next())
    ext = extraction([mention("m1", "LUT", "project")],
                     [stmt("m1", "ex:budget_plan", "40k", literal=True, certainty="possible",
                           evidence="We go with 40k for the LUT budget as decided")])
    run(nb, "", D20, ext, link({"m1": LUT}), reinterpret=True, note_id=a["note_id"], now=clock.next())
    snap = lambda: (sorted(tuple(r) for r in nb.store.q("SELECT claim_id, status, certainty, valid_until FROM claims")),  # noqa: E731
                    [tuple(r) for r in nb.store.q("SELECT note_id, outcome FROM reinterpret_marks ORDER BY mark_id")],
                    sorted(r[0] for r in nb.store.q("SELECT note_id FROM note_dependencies")))
    before = snap()
    nb.rebuild()
    assert snap() == before and pending(nb) == []


def test_a_notebook_from_schema_2_is_flagged_and_its_revisions_have_no_record(tmp_path):
    """Review 3.6: the migration records from which revision on the dependency record exists; a revision before it
    has none (context_unknown), so the no-call check never settles it. MUTATION: drop _flag_dependency_record."""
    from abstractnote.recall import consolidation as cons
    from abstractnote.store.schema import DEPENDENCY_RECORD_META

    nbk = Notebook.open(tmp_path / "old")
    try:
        a = day20_budget(nbk, "2026-10-20T12:01:00Z")
        with nbk.store.tx() as c:  # as a schema-2 notebook left it
            c.execute("UPDATE revisions SET context_json=NULL")
            c.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
            c.execute("DELETE FROM meta WHERE key=?", (DEPENDENCY_RECORD_META,))
    finally:
        nbk.close()
    nbk = Notebook.open(tmp_path / "old")
    try:
        flag = nbk.store.meta_get(DEPENDENCY_RECORD_META)
        assert flag["schema"] == 3 and flag["since_seq"] >= 1 and nbk.store.meta_get("schema_version") == 3
        assert nbk.note(a["note_id"])["interpretations"][0]["context_unknown"] is True
        assert cons.digest_unchanged(nbk, a["note_id"]) is False
    finally:
        nbk.close()


# ----------------------------------------------------------------------------- the stage (§5)


def _texts() -> Dict[str, str]:
    return {"d20": "Reviewed the calibration plan with Priya; two steps remain.",
            "d25": "Priya finished the last step of the calibration plan.",
            "d05": "The Helix calibration plan (HCP) is the three-step plan Priya Natt wrote."}


def _stage_llm():
    from abstractnote.flows.runner import ScriptedLLM

    texts = _texts()
    exts = {"d20": extraction([mention("m1", "the calibration plan", "artifact"), mention("m2", "Priya", "person")],
                              [stmt("m1", "ex:steps_remaining", "two", literal=True, evidence="two steps remain")]),
            "d25": extraction([mention("m1", "the calibration plan", "artifact"), mention("m2", "Priya", "person")],
                              [stmt("m2", "ex:finished", "m1", evidence="finished the last step")]),
            "d05": extraction([mention("m1", "Helix calibration plan", "artifact",
                                       aliases=["HCP", "the calibration plan"]),
                               mention("m2", "Priya Natt", "person", aliases=["Priya"])],
                              [stmt("m1", "ex:written_by", "m2", evidence="Priya Natt wrote")])}

    def which(prompt: str) -> str:
        return next(k for k, t in texts.items() if t.split(".")[0] in prompt)

    def linking(prompt: str) -> Dict[str, Any]:
        if "Helix calibration plan" in prompt:  # day 5 defines its own entries (it is the earlier note)
            return {"mentions": [ml("m1", "create", name="Helix calibration plan", kind="artifact",
                                    aliases=["HCP", "the calibration plan"]),
                                 ml("m2", "create", name="Priya Natt", kind="person", aliases=["Priya"])],
                    "claims": [], "tasks": [], "revision_summary": "x"}
        lines = prompt.splitlines()
        ms = []
        for ref, kind in (("m1", "artifact"), ("m2", "person")):
            idx = next((i for i, ln in enumerate(lines) if ln.startswith(ref + " ")), None)
            nxt = lines[idx + 1] if idx is not None and idx + 1 < len(lines) else ""
            ms.append(ml(ref, "reuse", 0, summary=None) if nxt.strip().startswith("[0]")
                      else ml(ref, "create", name=("the calibration plan" if ref == "m1" else "Priya"), kind=kind))
        return {"mentions": ms, "claims": [], "tasks": [], "revision_summary": "x"}

    return ScriptedLLM({"QuickFiling": {"title": "t", "kind": "fact", "topics": [], "urgency": "normal", "language": "en",
                                        "one_line": "x"},
                        "Extraction": lambda p: exts[which(p)], "Linking": linking,
                        "CommunityLabels": {"communities": []}, "EntitySummaries": {"summaries": []},
                        "Reflections": {"items": []}})


def _note_calls(llm: Any) -> int:
    return sum(1 for c in llm.calls if c["schema"] in ("Extraction", "Linking"))


def test_f4_the_stage_cascades_in_history_order_each_once_and_a_second_pass_calls_no_model(tmp_path):
    """F4 + convergence: day 5 after day 20 and day 25 marks both (both used the entries its text names); the stage
    reads day 20 then day 25 (history order), each once; the second pass finds no mark and makes no note call; a
    manual mark of an untouched note is settled unchanged by the digest check with no call. MUTATION: drop the
    pass exclusion in pending_marks (a note taken twice), or the digest check (a call for an unchanged note)."""
    from abstractnote.flows.runner import NoteRunner
    from abstractnote.recall import consolidation as cons
    from abstractnote.tools import bind

    nb_dir = tmp_path / "nb"
    llm = _stage_llm()
    runner = NoteRunner.with_llm(llm, bind(nb_dir))
    for key, at in (("d20", D20), ("d25", D25), ("d05", D05)):
        runner.run_capture(_texts()[key], idempotency_key=key, occurred_at=at, source={"client": "cli"})
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    nbk = Notebook.open(nb_dir)
    try:
        marked = {m["note_id"] for m in nbk.store.reinterpret_marks()}
        by_key = {k: next(r["note_id"] for r in nbk.store.q("SELECT * FROM notes") if r["text"] == t)
                  for k, t in _texts().items()}
        assert len(marked) == 2 and {by_key["d20"], by_key["d25"]} == marked
        assert cons.pending(nbk)["reinterpret"] == 2
    finally:
        nbk.close()
    before = _note_calls(llm)
    out = runner.run("note-consolidate", {})
    first = _note_calls(llm) - before
    nbk = Notebook.open(nb_dir)
    try:
        last = cons.last(nbk)
        assert last["reinterpreted"] + last["unchanged"] == 2 and last["left_out"]["reinterpret"] == 0
        order = [r[0] for r in nbk.store.q("SELECT note_id FROM revisions WHERE action='note.reinterpret' ORDER BY seq")]
        assert order == sorted(order, key=lambda n: nbk.store.note_row(n)["occurred_at"])
        assert all(actions(nbk, n).count("note.reinterpret") <= 1 for n in marked)
        assert nbk.store.reinterpret_marks() == [] and "understood again" in out["response"]
        revs = nbk.store.q1("SELECT COUNT(*) FROM revisions")[0]
    finally:
        nbk.close()
    assert first == 2 * last["reinterpreted"]  # extraction + linking per note read again
    runner.run("note-consolidate", {})
    assert _note_calls(llm) - before - first == 0  # the second pass: nothing marked, no note call
    nbk = Notebook.open(nb_dir)
    try:
        assert nbk.store.q1("SELECT COUNT(*) FROM revisions WHERE action='note.reinterpret'")[0] == last["reinterpreted"]
        assert nbk.store.q1("SELECT COUNT(*) FROM revisions")[0] >= revs
        d05 = by_key["d05"]
        nbk.mark_reinterpret([d05], "asked")  # nothing earlier changed for day 5: the digest settles it
    finally:
        nbk.close()
    calls = _note_calls(llm)
    runner.run("note-consolidate", {})
    assert _note_calls(llm) == calls
    nbk = Notebook.open(nb_dir)
    try:
        assert cons.last(nbk)["unchanged"] == 1 and nbk.store.reinterpret_marks() == []
    finally:
        nbk.close()


def test_later_growth_does_not_change_what_a_note_was_founded_on(nb):
    """The no-call check reads what was known at the note's date: an entry first seen after it (a later note made
    it; it now shows up in the earlier note's context) and a later statement are no foundation, an earlier
    statement is. MUTATION: drop the first_seen / observed_at filters of digest_unchanged."""
    from abstractnote.recall import consolidation as cons

    clock = Clock()
    a = run(nb, "Reviewed the spectrometer plan with Tomas.", D20, extraction([mention("m1", "spectrometer plan", "artifact")]),
            link({"m1": {"name": "spectrometer plan", "kind": "artifact"}}), now=clock.next())
    assert cons.digest_unchanged(nb, a["note_id"]) is True
    plan = a["out"]["entities_created"][0]
    t = run(nb, "Tomas joined the optics team.", "2026-04-01T09:00:00Z", extraction([mention("m1", "Tomas", "person")],
            [stmt("m1", "ex:joined", "optics team", literal=True, evidence="Tomas joined the optics team")]),
            link({"m1": {"name": "Tomas", "kind": "person"}}), now=clock.next())
    assert cons.digest_unchanged(nb, a["note_id"]) is True  # later growth
    run(nb, "Tomas wrote the spectrometer plan.", D10, extraction([mention("m1", "Tomas", "person"),
                                                                   mention("m2", "spectrometer plan", "artifact")],
        [stmt("m1", "ex:wrote", "m2", evidence="Tomas wrote the spectrometer plan")]),
        link({"m1": t["out"]["entities_created"][0], "m2": plan}), now=clock.next())
    assert cons.digest_unchanged(nb, a["note_id"]) is False  # an earlier foundation


def test_the_stage_under_a_spent_budget_leaves_the_marks_and_says_so(tmp_path):
    """W26 contract for the stage (R5-12): budget spent -> completed_with_gaps, left_out.reinterpret = the marks still
    pending, the notice names them. MUTATION: drop not_reached.reinterpret in the flow's results."""
    from abstractnote.flows.runner import NoteRunner
    from abstractnote.recall import consolidation as cons
    from abstractnote.tools import bind

    nb_dir = tmp_path / "nb"
    nbk = Notebook.open(nb_dir)
    clock = Clock()
    try:
        for i in range(3):
            run(nbk, f"LUT budget item {i} decided.", f"2026-03-2{i}T09:00:00Z", extraction([mention("m1", "LUT", "project")]),
                link({"m1": {"name": "LUT", "kind": "project"} if i == 0 else LUT}), now=clock.next())
        run(nbk, "The LUT figure is a placeholder.", D05, extraction([mention("m1", "LUT", "project")]), link({"m1": LUT}),
            now=clock.next())
        assert len(pending(nbk)) == 3
    finally:
        nbk.close()
    NoteRunner.with_llm(_stage_llm(), bind(nb_dir)).run("note-consolidate", {"budget_s": 0.001})
    nbk = Notebook.open(nb_dir)
    try:
        last = cons.last(nbk)
        assert last["status"] == "completed_with_gaps" and last["left_out"]["reinterpret"] == 3 == len(pending(nbk))
        assert "3 notes could not be understood again" in last["notice"]
    finally:
        nbk.close()


def test_a_failed_reinterpretation_is_skipped_with_its_reason_and_its_mark_waits(tmp_path):
    """Review 4.2: one note that cannot be read again (the model fails) does not end the stage; it is skipped with
    its reason (W26 shape), its mark waits for the next pass, the next note is read. MUTATION: drop the failed
    list in reinterpret_next (the error never reaches the mark) or the subflow outcome check."""
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.recall import consolidation as cons
    from abstractnote.tools import bind

    nb_dir = tmp_path / "nb"
    nbk = Notebook.open(nb_dir)
    clock = Clock()
    try:
        a = run(nbk, "LUT item one decided.", D20, extraction([mention("m1", "LUT", "project")]),
                link({"m1": {"name": "LUT", "kind": "project"}}), now=clock.next())
        b = run(nbk, "LUT item two decided.", D25, extraction([mention("m1", "LUT", "project")]), link({"m1": LUT}),
                now=clock.next())
        run(nbk, "The LUT figure is a placeholder.", D05, extraction([mention("m1", "LUT", "project")], [stmt(
            "m1", "ex:budget_status", "placeholder", literal=True, evidence="The LUT figure is a placeholder")]),
            link({"m1": LUT}), now=clock.next())
    finally:
        nbk.close()

    def ext(prompt: str) -> Dict[str, Any]:
        if "item one" in prompt:
            raise RuntimeError("the model is unreachable")
        return extraction([mention("m1", "LUT", "project")], [stmt("m1", "ex:item", "two", literal=True,
                                                                   evidence="LUT item two decided")])

    llm = ScriptedLLM({"Extraction": ext, "Linking": {"mentions": [ml("m1", "reuse", 0, summary=None)], "claims": [],
                                                      "tasks": [], "revision_summary": "x"},
                       "CommunityLabels": {"communities": []}, "EntitySummaries": {"summaries": []},
                       "Reflections": {"items": []}})
    NoteRunner.with_llm(llm, bind(nb_dir)).run("note-consolidate", {})
    nbk = Notebook.open(nb_dir)
    try:
        last = cons.last(nbk)
        assert last["reinterpreted"] == 1 and last["reinterpret_failed"] == 1 and last["status"] == "completed_with_gaps"
        assert last["left_out"]["reinterpret"] == 1
        row = nbk.store.q1("SELECT * FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL", (a["note_id"],))
        assert row is not None and "unreachable" in row["error"]
        assert actions(nbk, b["note_id"]) == [None, "note.reinterpret"] and actions(nbk, a["note_id"]) == [None]
        stage = json.loads(nbk.store.q1("SELECT stages_json FROM consolidation_runs ORDER BY started_ts DESC LIMIT 1")[0])
        assert stage["reinterpret"]["skipped"] == 1 and stage["reinterpret"]["skipped_ids"][0]["id"] == a["note_id"]
    finally:
        nbk.close()
