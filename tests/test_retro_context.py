"""Context as of the note's date (ADR 0008 §2, §3, §6; R1 of the retrospective programme, 2026-10-20).

Scripted Extraction/Linking through the real processing steps (``test_process.run``): a note is shown each
candidate as it was KNOWN on its date plus a labelled LATER block (every later statement, slot-complete, the
budget's omissions counted and said); ``earlier_state_of`` places a retro statement before the later one
(direction-validated; another predicate spelling adopts the existing predicate); an earlier reinforcement moves a
belief's start back in the note index, in AbstractMemory and in the as-received view, never later and never over
another state; a retro summary is kept as dated history; an exact name or alias match is always a candidate and a
create apart from it lands in review; the revision carries what it saw (``context``) and what it used. The last
test drives the generated note-process flow headless (the packet code the flows carry). Every check goes red
when the mechanism it checks is removed (the mutation list is in untracked/reports/R1.md).
"""

from __future__ import annotations

import re

from abstractnote.process import packets
from abstractnote.process.packets import LATER_LABEL, candidate_card, extraction_packet
from test_process import cand_index, claim_index, extraction, mention, ml, run, stmt
from test_store_support import nb  # noqa: F401

T20, T22, T23, T24, T25, T26 = ("2020-01-15T09:00:00Z", "2022-06-01T09:00:00Z", "2023-02-07T09:00:00Z",
                                "2024-09-15T09:00:00Z", "2025-02-05T09:00:00Z", "2026-03-10T09:00:00Z")
MIRA, ZORN = "ex:person-mira-voss", "ex:org-zorn-labs"
S26 = "Mira Voss is the director of Zorn Labs (since 2026-03)."


def _mira(nb, at, text, claims, *, link_claims=(), summary=None, create=False, predicates_seen=None, **kw):
    """A note about Mira Voss at ``at`` with the given statements (subject m1); ``link_claims(packet, cands)``
    returns the claim decisions. Returns (note_id, packet, cands, out)."""
    ext = extraction([mention("m1", "Mira Voss", "person")], claims)

    def link(packet, cands):
        if create:
            m = ml("m1", "create", name="Mira Voss", kind="person", summary=summary or S26)
        else:
            m = ml("m1", "reuse", cand_index(cands, "m1", MIRA), summary=summary)
        return {"mentions": [m], "claims": list(link_claims(packet, cands)) if callable(link_claims) else [],
                "tasks": [], "revision_summary": "Mira."}

    return run(nb, text, at, ext, link, **kw)


def _mira_cand(cands):
    [m] = [x for x in cands["mentions"] if x["ref"] == "m1"]
    [c] = [c for c in m["candidates"] if c["entity_id"] == MIRA]
    return c


def _role(nb, at, value, *, relation=None, existing=None, predicate="ex:role", create=False, now=None):
    def link_claims(packet, cands):
        if relation is None:
            return []
        return [{"index": 0, "relation_to_existing": relation, "existing": claim_index(packet, cands, existing),
                 "reason": "the role then"}]

    kw = {"now": now} if now else {}
    return _mira(nb, at, f"Mira Voss is {value}.", [stmt("m1", predicate, value, literal=True,
                                                         evidence=f"Mira Voss is {value}")],
                 link_claims=link_claims, create=create, **kw)[3]


def _window(nb, cid):
    r = nb.store.claim_row(cid)
    return (r["predicate"], r["valid_from"], r["valid_until"], r["status"], r["superseded_by"])


# --------------------------------------------------------------------------- 1. the packet as of the note's date

def test_a_retro_candidate_shows_known_then_and_a_labelled_later_block_with_every_later_statement(nb):
    later = [("ex:role", "director"), ("ex:office", "Lyon"), ("ex:team", "optics"), ("ex:language", "French"),
             ("ex:badge", "B12"), ("ex:floor", "3")]
    out = _mira(nb, T26, "Mira Voss is the director; office Lyon, team optics, French, badge B12, floor 3.",
                [stmt("m1", p, v, literal=True, evidence=v) for p, v in later], create=True)[3]
    assert len(out["claims_added"]) == 6
    _, packet, cands, out22 = _mira(nb, T22, "Mira Voss joined as an intern.",
                                    [stmt("m1", "ex:position", "intern", literal=True, evidence="intern")])
    c = _mira_cand(cands)
    assert c["summary"] is None and [x["summary"] for x in c["later_summaries"]] == [S26]
    assert c["active_claims"] == [] and len(c["later_claims"]) == 6  # all of them, not the 3-per-card cut
    assert all(x["later"] for x in c["later_claims"])
    card = candidate_card(c, ["ex:position"], T22)
    assert "nothing was recorded about it by this note's date" in card[0] and S26 not in card[0]
    block = card[card.index("    " + LATER_LABEL) + 1:]
    assert sum(1 for ln in block if ln.startswith("      #")) == 6
    assert "      summary as of 2026-03-10: " + S26 in block
    # every later claim is indexed: the linking can name it
    from abstractnote.process.context import load_packet

    ids_shown = {x["claim_id"] for x in c["later_claims"]}
    assert len(ids_shown) == 6 and ids_shown <= set(load_packet(nb, _note_of(nb, out22))["existing_claims"])
    # the digest: what the note was understood against (ADR 0008 §3)
    d = cands["context"]
    assert d["as_of"] == T22 and MIRA in d["entities"] and ids_shown <= set(d["later_claims"]) <= set(d["claims"])
    assert MIRA not in d["summaries"]  # nothing was written about her by 2022
    # a note written after everything sees no LATER block at all (live notes are unchanged)
    _, _, cands27, _ = _mira(nb, "2027-01-01T09:00:00Z", "Mira Voss again.", [])
    c27 = _mira_cand(cands27)
    assert c27["later_claims"] == [] and c27["summary"] == S26 and len(c27["active_claims"]) == 7  # + the intern
    assert LATER_LABEL not in "\n".join(candidate_card(c27, (), "2027-01-01T09:00:00Z"))


def _note_of(nb, out):
    return nb.store.revision_row(out["revision_id"])["note_id"]


def test_the_later_block_budget_is_slot_complete_and_its_omissions_are_counted_and_said(nb):
    many = [stmt("m1", f"ex:fact_{k:02d}", f"value {k}", literal=True, evidence=f"value {k}") for k in range(15)]
    many += [stmt("m1", "ex:role", f"role {k}", literal=True, evidence=f"role {k}") for k in range(14)]
    _mira(nb, T26, "Mira Voss: " + ", ".join(f"value {k}" for k in range(15)) + ", "
          + ", ".join(f"role {k}" for k in range(14)) + ".", many, create=True)
    _, packet, cands, out = _mira(nb, T22, "Mira Voss joined as an intern.",
                                  [stmt("m1", "ex:role", "intern", literal=True, evidence="intern")])
    c = _mira_cand(cands)
    on_slot = [x for x in c["later_claims"] if x["predicate"] == "ex:role"]
    assert len(on_slot) == 14  # every statement on the note's own slot, past the LATER budget
    assert len(c["later_claims"]) == 14 and c["later_claims_omitted"] == 15
    card = candidate_card(c, ["ex:role"], T22)
    assert sum(1 for ln in card if ln.startswith("      #")) == 14
    assert "      (+15 later statements and +0 later summaries not shown)" in card
    assert cands["context"]["omitted"][MIRA]["later_claims_omitted"] == 15
    assert any(w.startswith("packet_omitted: " + MIRA) and "later claims 15" in w for w in out["warnings"])


def test_known_then_includes_a_later_statement_said_to_hold_then_and_restating_it_never_moves_its_start_later(nb):
    ext = extraction([mention("m1", "Mira Voss", "person"), mention("m2", "Zorn Labs", "org")],
                     [{**stmt("m1", "ex:leads", "m2", evidence="has led Zorn Labs since 2020"), "valid_from": T20}])
    out = run(nb, "Mira Voss has led Zorn Labs since 2020.", T26, ext, lambda p, c: {
        "mentions": [ml("m1", "create", name="Mira Voss", kind="person"), ml("m2", "create", name="Zorn Labs",
                                                                            kind="org")],
        "claims": [], "tasks": [], "revision_summary": "Mira leads Zorn."})[3]
    [lead] = out["claims_added"]
    ext22 = extraction([mention("m1", "Mira Voss", "person"), mention("m2", "Zorn Labs", "org")],
                       [stmt("m1", "ex:leads", "m2", evidence="Mira Voss leads Zorn Labs")])
    seen = {}

    def link(packet, cands):
        seen["c"] = _mira_cand(cands)
        return {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", MIRA)),
                             ml("m2", "reuse", cand_index(cands, "m2", ZORN))],
                "claims": [{"index": 0, "relation_to_existing": "restates", "existing": claim_index(packet, cands, lead),
                            "reason": "same"}], "tasks": [], "revision_summary": "Mira leads Zorn."}

    out22 = run(nb, "Mira Voss leads Zorn Labs.", T22, ext22, link)[3]
    c = seen["c"]
    assert [x["claim_id"] for x in c["active_claims"]] == [lead] and c["later_claims"] == []  # known then
    assert "(stated later, said to hold since 2020-01-15)" in "\n".join(candidate_card(c, ["ex:leads"], T22))
    r = nb.store.claim_row(lead)
    assert (r["valid_from"], r["observed_at"]) == (T20, T22)  # the start never moves later; first seen moves back
    assert out22["claims_started_earlier"] == [lead] and out22["claims_added"] == []
    [op] = [o["payload"] for o in nb.revision(out22["revision_id"])["ops"] if o["op"] == "claim.start_earlier"]
    assert (op["observed_at"], op["valid_from"], op["previous_valid_from"]) == (T22, T20, T20)  # the op says so too


# --------------------------------------------------------------------------- 2. earlier_state_of

def _three_roles(nb, second_relation="earlier_state_of"):
    o26 = _role(nb, T26, "director", create=True, now="2026-10-01T00:00:00Z")
    [d] = o26["claims_added"]
    o22 = _role(nb, T22, "an intern", relation="earlier_state_of", existing=d, predicate="ex:position",
                now="2026-10-02T00:00:00Z")
    [i] = o22["claims_added"]
    existing = d if second_relation == "earlier_state_of" else i
    o24 = _role(nb, T24, "head of research", relation=second_relation, existing=existing, predicate="ex:job",
                now="2026-10-03T00:00:00Z")
    [h] = o24["claims_added"]
    return d, i, h, o22, o24


def _assert_three_windows(nb, d, i, h):
    assert _window(nb, i) == ("ex:role", T22, T24, "superseded", h)
    assert _window(nb, h) == ("ex:role", T24, T26, "superseded", d)
    assert _window(nb, d) == ("ex:role", T26, None, "active", None)
    for at, want in (("2023-01-01", i), ("2025-01-01", h), ("2027-01-01", d)):
        roles = [c["claim_id"] for c in nb.beliefs_at(at, entity_id=MIRA) if c["predicate"] == "ex:role"]
        assert roles == [want], at
        assert [x for x in nb.kg.active_claim_ids(at) if x in (d, i, h)] == [want], at


def test_s1_intern_head_of_research_director_end_as_three_consecutive_windows(nb):
    d, i, h, o22, o24 = _three_roles(nb)
    _assert_three_windows(nb, d, i, h)
    # another predicate spelling each time: the model's index decided, the existing predicate was adopted
    assert o22["predicates_adopted"] == 1 and o24["predicates_adopted"] == 1
    assert any(w.startswith("predicate_adopted: ex:position -> ex:role") for w in o22["warnings"])
    assert o22["claims_inserted_before"] == [i] and o22["claims_superseded"] == []


def test_s1_the_2024_note_may_name_the_known_then_intern_and_lands_in_the_same_place(nb):
    d, i, h, _, o24 = _three_roles(nb, second_relation="supersedes")
    _assert_three_windows(nb, d, i, h)
    assert o24["claims_inserted_before"] == [h]


def test_s1_live_shape_a_text_role_before_an_entity_relation_keeps_its_predicate_and_the_history_holds(nb):
    """The live 2026 note wrote ``mira ex:leads zorn_labs`` (an entity), the 2022 note ``ex:role "intern"`` (a text):
    the intern is placed before ``leads`` without adopting it (no ``leads "intern"``), and the 2024 note naming the
    known-then intern still lands between the two (the lineage follows the link across the predicates)."""
    ext = extraction([mention("m1", "Mira Voss", "person"), mention("m2", "Zorn Labs", "org")],
                     [stmt("m1", "ex:leads", "m2", evidence="Mira Voss leads Zorn Labs")])
    out = run(nb, "Mira Voss leads Zorn Labs.", T26, ext, lambda p, c: {
        "mentions": [ml("m1", "create", name="Mira Voss", kind="person"), ml("m2", "create", name="Zorn Labs",
                                                                            kind="org")],
        "claims": [], "tasks": [], "revision_summary": "Mira leads Zorn."})[3]
    [lead] = out["claims_added"]
    o22 = _role(nb, T22, "an intern", relation="earlier_state_of", existing=lead)
    [i] = o22["claims_added"]
    assert _window(nb, i) == ("ex:role", T22, T26, "superseded", lead) and o22["predicates_adopted"] == 0
    assert any("another kind of value; it keeps ex:role" in w for w in o22["warnings"])
    o24 = _role(nb, T24, "head of research", relation="supersedes", existing=i)
    [h] = o24["claims_added"]
    assert _window(nb, i)[2] == T24 and _window(nb, h) == ("ex:role", T24, T26, "superseded", lead)
    assert [c["claim_id"] for c in nb.beliefs_at("2027-01-01", entity_id=MIRA)] == [lead]


def test_earlier_state_of_is_direction_validated(nb):
    o22 = _role(nb, T22, "an intern", create=True)
    [i] = o22["claims_added"]
    out = _role(nb, T24, "head of research", relation="earlier_state_of", existing=i)
    assert any("earlier state of a statement that is not later than this note" in w for w in out["warnings"])
    [h] = out["claims_added"]
    assert _window(nb, h)[2:] == (None, "active", None) and _window(nb, i)[2:4] == (None, "active")
    # another subject: never an earlier state of it
    ext = extraction([mention("m1", "Tomas Ferr", "person")],
                     [stmt("m1", "ex:role", "intern", literal=True, evidence="Tomas Ferr is an intern")])
    out2 = run(nb, "Tomas Ferr is an intern, as Mira Voss once was.", T20, ext, lambda p, c: {
        "mentions": [ml("m1", "create", name="Tomas Ferr", kind="person")],
        "claims": [{"index": 0, "relation_to_existing": "earlier_state_of", "existing": claim_index(p, c, h),
                    "reason": "?"}], "tasks": [], "revision_summary": "Tomas."})[3]
    assert any("about another subject" in w for w in out2["warnings"]) and out2["claims_inserted_before"] == []


def test_a_new_statement_beside_later_ones_on_its_slot_is_said_parallel(nb):
    [d] = _role(nb, T26, "director", create=True)["claims_added"]
    out = _role(nb, T22, "an intern")
    assert out["parallel_beliefs"] == 1
    assert any(w.startswith("parallel_belief: statement 0") for w in out["warnings"])
    assert _role(nb, "2027-01-01T09:00:00Z", "chair")["parallel_beliefs"] == 0  # nothing later: a plain new


def test_a_supersession_of_a_statement_closed_since_the_note_was_read_is_kept_new_with_a_warning(nb):
    """retro-design-review 4.1: the packet was read while the statement was believed; another note closed it before
    this one applied. Nothing is closed twice and nothing is silent."""
    from abstractnote.process.merge import merge_extractions
    from abstractnote.tools.context import ToolContext
    from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates

    [i] = _role(nb, T22, "an intern", create=True)["claims_added"]
    ctx = ToolContext(nb=nb)
    ext = extraction([mention("m1", "Mira Voss", "person")],
                     [stmt("m1", "ex:role", "head of research", literal=True, evidence="head of research")])
    note_id = nb.capture("Mira Voss is head of research.", occurred_at=T25).note_id
    packet = note_context(ctx, note_id)
    cands = note_resolve_candidates(ctx, note_id, merge_extractions([ext])[1])
    link = {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", MIRA))],
            "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": claim_index(packet, cands, i),
                        "reason": "promoted"}], "tasks": [], "revision_summary": "Mira."}
    _role(nb, T23, "a researcher", relation="supersedes", existing=i)  # closes the intern meanwhile
    out = note_apply_revision(ctx, note_id, ext, link)
    assert any("no longer believed on this note's date" in w for w in out["warnings"])
    assert out["claims_superseded"] == [] and _window(nb, i)[2] == T23


def test_a_supersession_at_the_same_time_says_the_empty_window(nb):
    [d] = _role(nb, T26, "director", create=True)["claims_added"]
    out = _role(nb, T26, "chair", relation="supersedes", existing=d)
    assert _window(nb, d)[1:3] == (T26, T26)
    assert any("same day" in w and "empty window" in w for w in out["warnings"])


# --------------------------------------------------------------------------- 3. reinforcement moves the start back

def _approved(nb, at, *, restates=None):
    ext = extraction([mention("m1", "Zorn Labs", "org"), mention("m2", "the board", "org")],
                     [stmt("m1", "ex:approved_by", "m2", evidence="approved by the board")])

    def link(packet, cands):
        def pick(ref, eid, name):
            try:
                return ml(ref, "reuse", cand_index(cands, ref, eid))
            except AssertionError:
                return ml(ref, "create", name=name, kind="org")
        claims = [] if restates is None else [{"index": 0, "relation_to_existing": "restates",
                                               "existing": claim_index(packet, cands, restates), "reason": "same"}]
        return {"mentions": [pick("m1", ZORN, "Zorn Labs"), pick("m2", "ex:org-the-board", "the board")],
                "claims": claims, "tasks": [], "revision_summary": "Approved."}

    return run(nb, "The Zorn Labs budget was approved by the board.", at, ext, link,
               now=f"2026-10-{20 if restates is None else 21}T00:00:00Z")[3]


def test_an_earlier_reinforcement_moves_the_start_back_in_the_index_the_kg_and_the_relation(nb):
    [cid] = _approved(nb, T25)["claims_added"]
    out = _approved(nb, T23, restates=cid)
    assert out["claims_started_earlier"] == [cid] and out["claims_reinforced"] == [cid]
    r = nb.store.claim_row(cid)
    assert (r["observed_at"], r["valid_from"]) == (T23, T23)
    rel = nb.store.q1("SELECT first_seen FROM relations WHERE subject_id=? AND predicate='ex:approved_by'", (ZORN,))
    assert rel[0] == T23
    assert cid in nb.kg.active_claim_ids("2024-01-01") and cid in nb.kg.active_claim_ids("2026-01-01")
    assert cid not in nb.kg.active_claim_ids("2022-01-01")
    held = [c["claim_id"] for c in nb.store.beliefs_at("2024-01-01", entity_id=ZORN,
                                                         as_received="2026-10-20T12:00:00Z")]
    assert cid not in held  # as received before the 2023 note: the belief started in 2025
    assert cid in [c["claim_id"] for c in nb.beliefs_at("2024-01-01", entity_id=ZORN)]
    nb.rebuild()
    r = nb.store.claim_row(cid)
    assert (r["observed_at"], r["valid_from"]) == (T23, T23) and cid in nb.kg.active_claim_ids("2024-01-01")


def test_an_identical_statement_from_an_earlier_note_moves_the_start_back_without_a_relation(nb):
    [cid] = _approved(nb, T25)["claims_added"]
    out = _approved(nb, T23)  # the linking named nothing: the identical statement is found by resolution
    assert out["claims_added"] == [] and out["claims_started_earlier"] == [cid]
    assert nb.store.claim_row(cid)["valid_from"] == T23


def test_live_notes_minutes_apart_move_the_start_without_a_retrospective_mark(nb):
    [cid] = _approved(nb, "2026-10-20T12:10:00Z")["claims_added"]
    before = nb.retro_state()["insertions"]
    out = _approved(nb, "2026-10-20T12:05:00Z", restates=cid)
    assert out["claims_started_earlier"] == [cid] and nb.retro_state()["insertions"] == before
    assert out["retrospective"] == []


def _castor(nb, at, model, *, relation=None, existing=None):
    ext = extraction([mention("m1", "Castor", "project")],
                     [stmt("m1", "ex:runs_on", model, literal=True, evidence=f"Castor runs on {model}")])

    def link(packet, cands):
        try:
            m = ml("m1", "reuse", cand_index(cands, "m1", "ex:project-castor"))
        except AssertionError:
            m = ml("m1", "create", name="Castor", kind="project", summary="Castor, a persistent entity.")
        claims = [] if relation is None else [{"index": 0, "relation_to_existing": relation,
                                               "existing": claim_index(packet, cands, existing), "reason": "model"}]
        return {"mentions": [m], "claims": claims, "tasks": [], "revision_summary": "Castor."}

    return run(nb, f"Castor runs on {model}.", at, ext, link)[3]


def test_moving_a_start_back_narrows_the_belief_before_it(nb):
    [llama] = _castor(nb, T20, "Llama")["claims_added"]
    [qwen] = _castor(nb, T25, "Qwen", relation="supersedes", existing=llama)["claims_added"]
    out = _castor(nb, T23, "Qwen", relation="restates", existing=qwen)
    assert out["claims_started_earlier"] == [qwen]
    assert _window(nb, llama)[1:4] == (T20, T23, "superseded") and _window(nb, qwen)[1:3] == (T23, None)
    for at, want in (("2021-01-01", llama), ("2024-01-01", qwen)):
        assert [c["claim_id"] for c in nb.beliefs_at(at, entity_id="ex:project-castor")] == [want]
        assert [x for x in nb.kg.active_claim_ids(at) if x in (llama, qwen)] == [want], at


def test_a_restated_belief_never_moves_back_over_another_state_that_began_in_between(nb):
    [qwen] = _castor(nb, T26, "Qwen")["claims_added"]
    [mistral] = _castor(nb, T24, "Mistral", relation="earlier_state_of", existing=qwen)["claims_added"]
    out = _castor(nb, T22, "Qwen", relation="restates", existing=qwen)
    assert out["claims_started_earlier"] == [] and _window(nb, qwen)[1] == T26
    [early] = out["claims_added"]  # the 2022 statement is its own earlier belief, before Mistral
    assert _window(nb, early)[1:5] == (T22, T24, "superseded", mistral)
    for at, want in (("2023-01-01", early), ("2025-01-01", mistral), ("2027-01-01", qwen)):
        assert [c["claim_id"] for c in nb.beliefs_at(at, entity_id="ex:project-castor")] == [want], at


# --------------------------------------------------------------------------- 4. dated summaries

def test_a_retro_summary_is_kept_as_dated_history_when_its_create_is_rewritten_into_a_reuse(nb):
    _castor(nb, T26, "Qwen")
    ext = extraction([mention("m1", "Castor", "project")])
    out = run(nb, "Castor started.", T22, ext, lambda p, c: {
        "mentions": [ml("m1", "create", name="Castor", kind="project", summary="Castor is a new research project.")],
        "claims": [], "tasks": [], "revision_summary": "Castor."})[3]
    assert any("it was filed there" in w for w in out["warnings"])
    rows = nb.store.q("SELECT at, summary FROM entity_summaries WHERE entity_id='ex:project-castor' ORDER BY at")
    assert [(r[0], r[1]) for r in rows] == [(T22, "Castor is a new research project."),
                                            (T26, "Castor, a persistent entity.")]
    assert nb.store.entity_row("ex:project-castor")["summary"] == "Castor, a persistent entity."  # the card stays
    # a later note's rewritten create still keeps the existing summary
    run(nb, "Castor again.", "2027-01-01T09:00:00Z", ext, lambda p, c: {
        "mentions": [ml("m1", "create", name="Castor", kind="project", summary="Something else.")],
        "claims": [], "tasks": [], "revision_summary": "Castor."})
    assert "Something else." not in [r[0] for r in nb.store.q("SELECT summary FROM entity_summaries")]


def test_the_link_message_asks_for_the_entity_as_of_the_note_date():
    from abstractnote.process.packets import link_batches
    from abstractnote.process.prompts import load, render, system_prompt

    ext = {"mentions": [{"ref": "m1", "name": "Mira", "kind": "person"}], "claims": [], "tasks": []}
    [b] = link_batches({"note_id": "nt_1", "occurred_at": T22}, ext, [], [])
    text = render(load("link"), {"batch": b})
    assert "Write every summary as of 2022-06-01, this note's date" in text and "never use what was recorded later" in text
    sysp = system_prompt("link")
    assert "`earlier_state_of`" in sysp and "never `new`" in sysp and "LATER (recorded after this note's date" in sysp
    assert "whatever the predicate is called" in sysp


def test_the_extraction_packet_shows_the_slots_with_their_spelling_and_value():
    cand = {"name": "zorn_labs", "kind": "org",
            "active_claims": [{"subject": "zorn_labs", "predicate": "ex:annual_budget", "object": "25k",
                               "object_is_literal": True, "observed_at": T23}],
            "later_claims": [{"subject": "zorn_labs", "predicate": "ex:budget_amount", "object": "40k",
                              "object_is_literal": True, "observed_at": T25, "later": True}]}
    known = extraction_packet({"note_id": "nt_1", "occurred_at": T24}, {"text": "x"}, 1, [], [cand], [], [])["known"]
    assert "- zorn_labs ex:annual_budget “25k” (2023-02-07)" in known
    assert "- zorn_labs ex:budget_amount “40k” (2025-02-05, recorded later than this note)" in known
    assert "write the same kind of fact with the same predicate" in known


# --------------------------------------------------------------------------- 5. identity across time

def _person(nb, name, aliases=(), at=T26, kind="person"):
    ext = extraction([mention("m1", name, kind, list(aliases))])
    return run(nb, f"{name} was there.", at, ext, lambda p, c: {
        "mentions": [ml("m1", "create", name=name, kind=kind, summary=f"{name}, a person.")], "claims": [],
        "tasks": [], "revision_summary": name})


def test_an_alias_equal_to_a_live_name_is_always_a_candidate_and_a_create_apart_lands_in_review(nb):
    _person(nb, "Mira Voss")
    note_id, packet, cands, out = _person(nb, "M. Voss", ["Mira Voss"], at=T22)
    [m] = cands["mentions"]
    [c] = [c for c in m["candidates"] if c["entity_id"] == MIRA]
    assert c["match"] == "alias"
    [created] = out["entities_created"]
    cm = [nb.store.claim_row(x) for x in out["claims_added"]]
    assert [(r["subject_id"], r["predicate"], r["object_id"]) for r in cm] == [(created, "skos:closeMatch", MIRA)]
    assert any(it["kind"] == "close_match" for it in nb.review()["items"])
    assert any("matches ex:person-mira-voss exactly" in w for w in out["warnings"])


def test_alias_matches_are_never_cut_by_the_candidate_count(nb):
    for name in ("Zeta Lab", "Zone Logistics", "Zoo League", "Zinc Line"):
        _person(nb, name, ["ZL"], kind="org")
    _, _, cands, _ = _person(nb, "ZL", kind="org", at=T22)
    [m] = cands["mentions"]
    assert sum(1 for c in m["candidates"] if c["match"] == "alias") == 4 > packets.CANDIDATES_PER_MENTION


def test_a_created_entity_whose_alias_is_a_live_canonical_name_lands_in_review(nb):
    _person(nb, "Zorn Labs", kind="org")
    ext = extraction([mention("m1", "ZL", "org")])
    out = run(nb, "ZL opened a wing.", T22, ext, lambda p, c: {
        "mentions": [ml("m1", "create", name="ZL", kind="org", aliases=["Zorn Labs"], summary="ZL, a lab.")],
        "claims": [], "tasks": [], "revision_summary": "ZL."})[3]
    [created] = out["entities_created"]
    pairs = [(nb.store.claim_row(x)["subject_id"], nb.store.claim_row(x)["object_id"]) for x in out["claims_added"]
             if nb.store.claim_row(x)["predicate"] == "skos:closeMatch"]
    assert pairs == [(created, ZORN)]
    assert any("the canonical name of ex:org-zorn-labs" in w for w in out["warnings"])


def test_retro_aliases_from_the_link_answer_persist_on_the_reused_entity(nb):
    _person(nb, "Zorn Labs", kind="org")
    ext = extraction([mention("m1", "Zorn Laboratories", "org", ["ZL"])])
    run(nb, "Zorn Laboratories (ZL) opened its optics wing.", T20, ext, lambda p, c: {
        "mentions": [ml("m1", "reuse", cand_index(c, "m1", ZORN), aliases=["Zorn Laboratories", "ZL"])],
        "claims": [], "tasks": [], "revision_summary": "Optics wing."})
    assert {"Zorn Laboratories", "ZL"} <= set(nb.store.aliases(ZORN))


# --------------------------------------------------------------------------- 6. what a revision saw and used

def test_the_revision_carries_what_it_saw_and_what_it_used(nb):
    from abstractnote.process.revise import build_revision, used_of

    [d] = _role(nb, T26, "director", create=True)["claims_added"]
    _person(nb, "Tomas Ferr")
    note_id, packet, cands, out = _mira(nb, T22, "Mira Voss joined Zorn as an intern; Tomas Ferr was not there.",
                                        [stmt("m1", "ex:role", "an intern", literal=True, evidence="an intern")],
                                        link_claims=lambda p, c: [{"index": 0, "relation_to_existing":
                                                                   "earlier_state_of", "existing":
                                                                   claim_index(p, c, d), "reason": "before"}])
    assert "ex:person-tomas-ferr" in packet["context"]["entities"]  # SEEN: named in the text
    assert out["used"]["entities"] == [MIRA] and out["used"]["claims"] == [d]  # USED: only what it resolved to
    rev = nb.revision(out["revision_id"])
    assert used_of(rev["ops"]) == out["used"]
    built = build_revision(nb, note_id, extraction([mention("m1", "Mira Voss", "person")]), {}, attempt=9)
    assert built["context"]["as_of"] == T22 and set(built) >= {"context", "used"}


# --------------------------------------------------------------------------- 7. the generated flow carries it

def test_the_generated_flow_shows_the_later_block_and_places_s1(tmp_path):
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    def ext(value, predicate):
        return {"summary": f"Mira Voss is {value}.",
                "mentions": [{"ref": "m1", "name": "Mira Voss", "kind": "person", "role": "the person"}],
                "claims": [{"subject": "m1", "predicate": predicate, "object": value, "object_is_literal": True,
                            "evidence": f"Mira Voss is {value}"}], "tasks": [], "time_refs": [], "topics": ["zorn"]}

    prompts_seen = []

    def link(prompt):
        prompts_seen.append(prompt)
        if len(prompts_seen) == 1:
            return {"mentions": [{"ref": "m1", "decision": "create", "name": "Mira Voss", "kind": "person",
                                  "summary": S26}], "claims": [], "tasks": [], "revision_summary": "Mira."}
        assert LATER_LABEL in prompt and "Write every summary as of " in prompt
        later = re.search(r"\n {10}#(\d+) mira_voss ex:role “director”", prompt)
        assert later, prompt
        return {"mentions": [{"ref": "m1", "decision": "reuse", "candidate": 0,
                              "summary": "Mira Voss is a researcher at Zorn Labs."}],
                "claims": [{"index": 0, "relation_to_existing": "earlier_state_of", "existing": int(later.group(1)),
                            "reason": "earlier role"}], "tasks": [], "revision_summary": "Mira's earlier role."}

    script = {"QuickFiling": {"title": "Mira", "kind": "fact", "topics": ["zorn"], "urgency": "normal",
                              "language": "en", "one_line": "Mira's role."},
              "Extraction": [ext("director", "ex:role"), ext("an intern", "ex:position"),
                             ext("head of research", "ex:job")], "Linking": link}
    runner = NoteRunner.with_llm(ScriptedLLM(script), bind(tmp_path / "notebook"))
    for at, value in ((T26, "director"), (T22, "an intern"), (T24, "head of research")):
        runner.run_capture(f"Mira Voss is {value}.", idempotency_key=f"k-{value}", source={"client": "cli"},
                           occurred_at=at)
        assert runner.run_drain(max_items=5, budget_s=60)["processed"] == 1
    nb = Notebook.open(tmp_path / "notebook")
    try:
        rows = {r["object_literal"]: (r["predicate"], r["valid_from"], r["valid_until"])
                for r in nb.store.q("SELECT * FROM claims WHERE subject_id=?", (MIRA,))}
        assert rows == {"an intern": ("ex:role", T22, T24), "head of research": ("ex:role", T24, T26),
                        "director": ("ex:role", T26, None)}
        hist = nb.store.q("SELECT at FROM entity_summaries WHERE entity_id=? ORDER BY at", (MIRA,))
        assert [r[0] for r in hist] == [T22, T24, T26]
    finally:
        nb.close()


def test_the_prompts_cover_the_live_failures_reuse_index_slot_links_and_qualifying_statements():
    """Live S1-S4 (R1, 2026-10-20): with a LATER block gpt-oss-120b answered `reuse` without `candidate` and
    `supersedes` without `existing` (nothing linked); R2's F1/F2: a placeholder qualifier was never extracted, and a
    second value of a slot opened a parallel belief. The static prompts now say each of these explicitly."""
    from abstractnote.process.prompts import system_prompt

    link = system_prompt("link")
    assert "`candidate` is never null for `reuse` or `unsure`" in link and "0 (the first candidate) is an index" in link
    assert "leave out `candidate`," not in link
    assert "is still reused by its index" in link
    assert "is never `new`: link it to that statement" in link and "one belief per fact at a time" in link
    assert "Every relation other than `new` gives `existing`" in link
    extract = system_prompt("extract")
    assert "A sentence that qualifies another fact is a statement too, never dropped" in extract
    assert "placeholder until the board meets" in extract and "`certainty: possible` (not decided)" in extract
