"""Retrospective ingestion (W23): older notes and archives absorbed into a graph built from later ones.

Scripted Extraction/Linking through the real processing steps (``test_process.run``): beliefs placed in
history order (``claim.insert_before``), retroactive contradictions, an alias archive moving ``first_seen``
back, tasks an older note cannot close, document dates (``note.redate``), the apply-time guards of the
2026-10-20 data check (D1 merges, D2 unrelated supersessions, D3 task closes, D8 values as entities), and
the AbstractMemory projection agreeing with the note index across the three dates.
"""

from __future__ import annotations

import json

from abstractnote.process.revise import MERGE_REVIEW_NOTES
from test_process import cand_index, claim_index, extraction, mention, ml, run, stmt
from test_store_support import nb  # noqa: F401

T26, T22, T24 = "2026-03-01T09:00:00Z", "2022-03-01T09:00:00Z", "2024-03-01T09:00:00Z"
CASTOR = "ex:project-castor"


def _reuse_or_create(cands, ref, eid, name, kind, summary="Castor, the persistent entity."):
    try:
        return ml(ref, "reuse", cand_index(cands, ref, eid))
    except AssertionError:
        return ml(ref, "create", name=name, kind=kind, summary=summary)


def castor(nb, at, model, *, existing=None, relation="supersedes", certainty="stated", now=None):
    """A note at ``at`` saying Castor runs on ``model``; linked to the claim ``existing`` when given."""
    ext = extraction([mention("m1", "Castor", "project")],
                     [stmt("m1", "ex:runs_on", model, literal=True, certainty=certainty,
                           evidence=f"Castor runs on {model}")])

    def link(packet, cands):
        claims = []
        if existing is not None:
            claims = [{"index": 0, "relation_to_existing": relation,
                       "existing": claim_index(packet, cands, existing), "reason": "the model changed"}]
        return {"mentions": [_reuse_or_create(cands, "m1", CASTOR, "Castor", "project")], "claims": claims,
                "tasks": [], "revision_summary": f"Castor on {model}."}

    kwargs = {"now": now} if now else {}
    note_id, _, _, out = run(nb, f"Castor runs on {model}.", at, ext, link, **kwargs)
    return note_id, out


def row(nb, cid):
    r = nb.store.claim_row(cid)
    return {"status": r["status"], "from": r["valid_from"], "until": r["valid_until"], "by": r["superseded_by"]}


def ops_of(nb, out):
    return [op["op"] for op in nb.revision(out["revision_id"])["ops"]]


def beliefs(nb, at):
    return sorted(c["claim_id"] for c in nb.beliefs_at(at, entity_id=CASTOR))


def assert_no_negative_window(nb):
    bad = nb.store.q("SELECT claim_id, valid_from, valid_until FROM claims WHERE valid_until IS NOT NULL AND"
                     " valid_from IS NOT NULL AND valid_until < valid_from")
    assert [tuple(r) for r in bad] == []


# --------------------------------------------------------------------------- 2026 -> 2022 -> 2024

def three_dates(nb):
    _, o26 = castor(nb, T26, "Qwen", now="2026-10-01T00:00:00Z")
    [c26] = o26["claims_added"]
    _, o22 = castor(nb, T22, "Llama", existing=c26, now="2026-10-02T00:00:00Z")
    [c22] = o22["claims_added"]
    _, o24 = castor(nb, T24, "Mistral", existing=c26, now="2026-10-03T00:00:00Z")
    [c24] = o24["claims_added"]
    return {"c26": c26, "c22": c22, "c24": c24, "o22": o22, "o24": o24}


def test_an_older_note_is_inserted_before_and_never_closes_the_later_belief(nb):
    _, o26 = castor(nb, T26, "Qwen")
    [c26] = o26["claims_added"]
    _, o22 = castor(nb, T22, "Llama", existing=c26)
    [c22] = o22["claims_added"]
    assert ops_of(nb, o22).count("claim.insert_before") == 1 and "claim.supersede" not in ops_of(nb, o22)
    assert o22["claims_inserted_before"] == [c22] and o22["claims_superseded"] == []
    assert row(nb, c26) == {"status": "active", "from": T26, "until": None, "by": None}  # untouched
    assert row(nb, c22) == {"status": "superseded", "from": T22, "until": T26, "by": c26}
    assert beliefs(nb, "2023-01-01") == [c22] and beliefs(nb, "2027-01-01") == [c26]
    assert nb.store.entity_row(CASTOR)["first_seen"] == T22  # first_seen moved back to the older note
    assert CASTOR in o22["retrospective"]


def test_out_of_order_2026_2022_2024_places_each_belief_in_its_interval(nb):
    w = three_dates(nb)
    c22, c24, c26 = w["c22"], w["c24"], w["c26"]
    assert row(nb, c26) == {"status": "active", "from": T26, "until": None, "by": None}
    assert row(nb, c24) == {"status": "superseded", "from": T24, "until": T26, "by": c26}
    assert row(nb, c22) == {"status": "superseded", "from": T22, "until": T24, "by": c24}  # cut where 2024 starts
    ib = [op["payload"] for op in nb.revision(w["o24"]["revision_id"])["ops"] if op["op"] == "claim.insert_before"]
    assert ib == [{"claim_id": c24, "before_claim_id": c26, "valid_until": T26, "reason": "the model changed",
                   "after_claim_id": c22, "after_valid_until": T24}]
    for at, want in (("2021-01-01", []), ("2023-01-01", [c22]), ("2025-01-01", [c24]), ("2027-01-01", [c26])):
        assert beliefs(nb, at) == want, at
        # AbstractMemory agrees: TripleQuery(active_at) over the triple store
        assert [c for c in nb.kg.active_claim_ids(at) if c in w.values()] == want, at
    assert_no_negative_window(nb)


def test_the_three_dates_survive_a_rebuild_and_read_as_received(nb):
    w = three_dates(nb)
    before = {c: row(nb, c) for c in (w["c22"], w["c24"], w["c26"])}
    nb.rebuild()
    assert {c: row(nb, c) for c in before} == before
    for at in ("2023-01-01", "2025-01-01", "2027-01-01"):
        assert [c for c in nb.kg.active_claim_ids(at) if c in before] == beliefs(nb, at)
    # what the archive held on 2 October (before the 2024 note): the 2022 belief still ran until 2026
    held = {c["claim_id"]: c for c in nb.store.beliefs_at("2025-01-01", entity_id=CASTOR,
                                                           as_received="2026-10-02T12:00:00Z")}
    assert list(held) == [w["c22"]] and held[w["c22"]]["valid_until"] == T26
    # a fourth note, 2023, received last: the 2022 belief now ends in 2023; as received before it, in 2024
    _, o23 = castor(nb, "2023-03-01T09:00:00Z", "Gemma", existing=w["c26"], now="2026-10-04T00:00:00Z")
    [c23] = o23["claims_added"]
    assert row(nb, w["c22"]) == {"status": "superseded", "from": T22, "until": "2023-03-01T09:00:00Z", "by": c23}
    assert row(nb, c23) == {"status": "superseded", "from": "2023-03-01T09:00:00Z", "until": T24, "by": w["c24"]}
    held = {c["claim_id"]: c for c in nb.store.beliefs_at("2022-06-01", entity_id=CASTOR,
                                                           as_received="2026-10-03T12:00:00Z")}
    assert list(held) == [w["c22"]] and held[w["c22"]]["valid_until"] == T24
    assert_no_negative_window(nb)


def test_a_replayed_supersession_never_ends_before_the_claims_start(nb):
    """Revisions recorded before the builder clamped closing dates are replayed by a rebuild: the index still
    never stores a window that ends before it starts."""
    from test_store_support import claim, create, rev

    nb.apply_revision(rev("rv_a", T22, create(CASTOR, "Castor"),
                          claim("cl_future", CASTOR, "ex:runs_on", literal="Qwen", at=T22, valid_from=T26)))
    nb.apply_revision(rev("rv_b", T24, {"op": "claim.supersede", "payload": {
        "claim_id": "cl_future", "by_claim_id": None, "reason": "old", "valid_until": T24}}))
    assert row(nb, "cl_future")["until"] == T26
    assert_no_negative_window(nb)


def test_history_order_with_out_of_order_ingestion(nb):
    """Timeline and then-versus-now follow history time (occurred_at, windows), never the order received."""
    w = three_dates(nb)
    tl = nb.entity_timeline(CASTOR)["items"]
    assert [i["at"] for i in tl] == sorted(i["at"] for i in tl)
    notes = [i["note"]["occurred_at"] for i in tl if i["type"] == "note"]
    assert notes == [T22, T24, T26]
    ends = {i["claim_id"]: i["at"] for i in tl if i["type"] == "supersede"}
    assert ends == {w["c22"]: T24, w["c24"]: T26}  # where each belief ended, not when the note arrived
    tn = nb.then_now(CASTOR)
    assert [c["claim_id"] for c in tn["earliest_claims"]][:1] == [w["c22"]]
    assert [c["claim_id"] for c in tn["current_beliefs"]] == [w["c26"]]
    sup = [(t["at"], t["claim_id"]) for t in tn["turning_points"] if t["kind"] == "supersede"]
    assert sup == [(T24, w["c22"]), (T26, w["c24"])]
    buckets = nb.timeline(granularity="month")["buckets"]
    assert [b["bucket"] for b in buckets if b["notes"]] == ["2022-03", "2024-03", "2026-03"]
    first_bucket = [b for b in buckets if b["entities_created"]]
    assert first_bucket[0]["bucket"] == "2022-03"  # the entity appears where its first note is
    # system time stays system time: /changes lists what was received and applied, in that order
    feed = nb.changes(since="2026-01-01T00:00:00Z", limit=50)
    assert feed is not None


def test_retro_insertion_marks_summaries_stale_and_drops_then_now(nb):
    _, o26 = castor(nb, T26, "Qwen")
    [c26] = o26["claims_added"]
    nb.then_now(CASTOR)
    assert nb.store.q1("SELECT COUNT(*) FROM then_now_cache WHERE key=?", ("entity:" + CASTOR,))[0] == 1
    v0 = nb.retro_state()["graph_version"]
    castor(nb, T22, "Llama", existing=c26)
    assert nb.store.q1("SELECT COUNT(*) FROM then_now_cache WHERE key=?", ("entity:" + CASTOR,))[0] == 0
    st = nb.retro_state()
    assert st["graph_version"] == v0 + 1 and CASTOR in [s["entity_id"] for s in st["stale_summaries"]]
    assert st["retro_notes_since_communities"] == 1 and not st["communities_due"]
    assert nb.retro_state(threshold=1)["communities_due"]
    # a summary written after the mark (the consolidation's refresh) clears it
    nb.apply_revision({"revision_id": "rv_refresh", "at": "2026-10-20T00:00:00Z", "note_id": None,
                       "action": "entity.summary", "target": CASTOR, "summary": "refresh",
                       "ops": [{"op": "entity.summary", "payload": {"entity_id": CASTOR, "summary": "Castor, 2022-2026.",
                                                                    "previous": None}}]})
    assert CASTOR not in [s["entity_id"] for s in nb.retro_state()["stale_summaries"]]
    # by hand: Notebook.after_retro_insert
    out = nb.after_retro_insert([CASTOR])
    assert CASTOR in [s["entity_id"] for s in out["stale_summaries"]]


def test_live_notes_a_few_minutes_out_of_order_are_not_retrospective(nb):
    castor(nb, "2026-03-01T09:30:00Z", "Qwen")
    _, out = castor(nb, "2026-03-01T09:10:00Z", "Qwen and more")
    assert out["retrospective"] == [] and nb.retro_state()["insertions"] == 0


def test_a_later_note_still_supersedes_as_before(nb):
    _, o22 = castor(nb, T22, "Llama")
    [c22] = o22["claims_added"]
    _, o26 = castor(nb, T26, "Qwen", existing=c22)
    [c26] = o26["claims_added"]
    assert "claim.insert_before" not in ops_of(nb, o26) and o26["claims_superseded"] == [c22]
    assert row(nb, c22) == {"status": "superseded", "from": T22, "until": T26, "by": c26}
    assert o26["retrospective"] == []


# --------------------------------------------------------------------------- retroactive contradiction

def test_a_retroactive_contradiction_becomes_the_earlier_belief(nb):
    _, o25 = castor(nb, "2025-02-01T09:00:00Z", "Qwen")
    [c25] = o25["claims_added"]
    _, o23 = castor(nb, "2023-02-01T09:00:00Z", "Llama", existing=c25, relation="contradicts")
    [c23] = o23["claims_added"]
    assert row(nb, c23) == {"status": "superseded", "from": "2023-02-01T09:00:00Z", "until": "2025-02-01T09:00:00Z",
                            "by": c25}
    assert nb.store.claim_row(c23)["certainty"] == "stated" and row(nb, c25)["status"] == "active"
    assert json.loads(nb.store.claim_row(c25)["disputed_with_json"]) == []


def test_a_disputed_contradiction_within_30_days_stays_a_dispute(nb):
    _, o = castor(nb, "2025-02-01T09:00:00Z", "Qwen")
    [c25] = o["claims_added"]
    _, near = castor(nb, "2025-01-20T09:00:00Z", "Llama", existing=c25, relation="contradicts", certainty="disputed")
    [cn] = near["claims_added"]
    assert nb.store.claim_row(cn)["certainty"] == "disputed" and row(nb, cn)["status"] == "active"
    assert json.loads(nb.store.claim_row(c25)["disputed_with_json"]) == [cn]
    # disputed but two years apart: the older note's view is the earlier belief
    _, far = castor(nb, "2023-01-20T09:00:00Z", "Mistral", existing=c25, relation="contradicts",
                    certainty="disputed")
    [cf] = far["claims_added"]
    assert row(nb, cf)["status"] == "superseded" and row(nb, cf)["by"] == c25


# --------------------------------------------------------------------------- alias archive, other projects

def test_an_archive_naming_a_known_entity_by_alias_moves_its_history_back(nb):
    gw = "ex:software-abstractgateway"
    ext = extraction([mention("m1", "AbstractGateway", "software", ["gateway"])])
    run(nb, "AbstractGateway (the gateway) serves the apps.", T26, ext,
        lambda p, c: {"mentions": [ml("m1", "create", name="AbstractGateway", kind="software",
                                      summary="The framework's server.")], "claims": [], "tasks": [],
                      "revision_summary": "Gateway."})
    nb.then_now(gw)
    old = extraction([mention("m1", "gateway", "software"), mention("m2", "Raspberry Pi", "thing")],
                     [stmt("m1", "ex:ran_on", "m2", evidence="The gateway prototype ran on a Raspberry Pi")])
    _, packet, cands, out = run(
        nb, "From the old project archive: the gateway prototype ran on a Raspberry Pi.", "2021-06-01T09:00:00Z", old,
        lambda p, c: {"mentions": [ml("m1", "reuse", cand_index(c, "m1", gw)),
                                   ml("m2", "create", name="Raspberry Pi", kind="thing")],
                      "claims": [], "tasks": [], "revision_summary": "The prototype."},
        source={"client": "importer", "importer": "markdown", "origin": {"date_basis": "front_matter"}},
        importer="markdown", source_key="old:proto")
    assert out["entities_created"] == ["ex:thing-raspberry-pi"]
    e = nb.store.entity_row(gw)
    assert e["first_seen"] == "2021-06-01T09:00:00Z" and e["last_seen"] == T26 and e["note_count"] == 2
    assert out["retrospective"] == [gw]
    assert nb.store.q1("SELECT COUNT(*) FROM then_now_cache WHERE key=?", ("entity:" + gw,))[0] == 0
    assert nb.then_now(gw)["earliest_claims"][0]["observed_at"] == "2021-06-01T09:00:00Z"
    assert gw in [s["entity_id"] for s in nb.retro_state()["stale_summaries"]]


# --------------------------------------------------------------------------- tasks (D3 and time)

def _task_world(nb):
    ext = extraction([mention("m1", "Dana", "person")], [], [{"text": "Send Dana the Pareto", "status": "open",
                                                              "due": None}])
    run(nb, "Dana wants the Pareto; send it to Dana.", T26, ext,
        lambda p, c: {"mentions": [ml("m1", "create", name="Dana", kind="person")], "claims": [], "tasks": [],
                      "revision_summary": "A task."})
    [task] = nb.tasks()["items"]
    return task


def _close(nb, at, text, evidence):
    ext = extraction([mention("m1", "Dana", "person")], [], [{"text": "Sent the Pareto to Dana", "status": "done",
                                                              "due": None}])

    def link(packet, cands):
        [t] = [t for t in packet["open_tasks"] if t["text"] == "Send Dana the Pareto"]
        item = {"index": 0, "closes_open_task": t["index"]}
        if evidence is not None:
            item["evidence"] = evidence
        return {"mentions": [ml("m1", "reuse", cand_index(cands, "m1", "ex:person-dana"))], "claims": [],
                "tasks": [item], "revision_summary": "Pareto."}

    return run(nb, text, at, ext, link)[3]


def test_an_older_note_cannot_close_a_task_opened_later(nb):
    task = _task_world(nb)
    out = _close(nb, "2025-05-01T09:00:00Z", "Sent the Pareto to Dana.", "Sent the Pareto to Dana")
    assert task["task_id"] not in out["tasks_closed"]
    assert any("older than the open task" in w for w in out["warnings"])
    assert nb.store.q1("SELECT status FROM tasks WHERE task_id=?", (task["task_id"],))[0] == "open"
    later = _close(nb, "2026-04-01T09:00:00Z", "Sent the Pareto to Dana at last.", "Sent the Pareto to Dana")
    assert task["task_id"] in later["tasks_closed"]
    assert nb.store.q1("SELECT closed_at FROM tasks WHERE task_id=?", (task["task_id"],))[0] == "2026-04-01T09:00:00Z"


def test_a_task_closes_only_with_the_notes_own_words(nb):
    task = _task_world(nb)
    for evidence in (None, "", "the Pareto went out yesterday"):  # missing, empty, not the note's words
        out = _close(nb, "2026-04-01T09:00:00Z", f"Dana and the Pareto, again ({len(evidence or '')}).", evidence)
        assert task["task_id"] not in out["tasks_closed"], evidence
        assert any("needs the note's words" in w for w in out["warnings"])
    assert nb.store.q1("SELECT status FROM tasks WHERE task_id=?", (task["task_id"],))[0] == "open"
    assert nb.store.q1("SELECT COUNT(*) FROM tasks WHERE closed_at < opened_at")[0] == 0


# --------------------------------------------------------------------------- document dates (note.redate)

LETTER = "Letter written on 3 March 2021. Castor runs on Llama."


def _letter_ext(quote="written on 3 March 2021", iso="2021-03-03"):
    ext = extraction([mention("m1", "Castor", "project")],
                     [stmt("m1", "ex:runs_on", "Llama", literal=True, evidence="Castor runs on Llama")])
    ext["document_date"] = {"iso": iso, "basis": "stated in the text", "quote": quote}
    return ext


def _letter_link(p, c):
    return {"mentions": [_reuse_or_create(c, "m1", CASTOR, "Castor", "project")], "claims": [], "tasks": [],
            "revision_summary": "A letter."}


IMPORTED = dict(source={"client": "importer", "importer": "files", "origin": {"date_basis": "file_mtime"}},
                importer="files", source_key="letter-1")


def test_a_stated_document_date_redates_an_approximate_import(nb):
    nid = nb.capture(LETTER, occurred_at="2026-10-01T10:00:00Z", **IMPORTED).note_id
    before = nb.note(nid)
    assert (before["date_basis"], before["date_approximate"]) == ("file_mtime", True)
    from abstractnote.process.packets import note_header
    from abstractnote.tools.context import ToolContext
    from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates

    ctx = ToolContext(nb=nb)
    packet = note_context(ctx, nid)
    assert packet["note"]["date_approximate"] is True and "approximate" in note_header(packet["note"])
    cands = note_resolve_candidates(ctx, nid, [{"ref": "m1", "name": "Castor", "kind": "project", "aliases": []}])
    out = note_apply_revision(ctx, nid, _letter_ext(), _letter_link(packet, cands))
    rev = nb.revision(out["revision_id"])
    assert rev["ops"][0] == {"op": "note.redate", "payload": {
        "note_id": nid, "from": "2026-10-01T10:00:00Z", "to": "2021-03-03T00:00:00Z", "basis": "content",
        "quote": "written on 3 March 2021"}}
    assert rev["at"] == "2021-03-03T00:00:00Z"
    note = nb.note(nid)
    assert (note["occurred_at"], note["date_basis"], note["date_approximate"]) == ("2021-03-03T00:00:00Z", "content",
                                                                                  False)
    [c] = note["claims"]
    assert c["observed_at"] == c["valid_from"] == "2021-03-03T00:00:00Z"
    assert nb.store.entity_row(CASTOR)["first_seen"] == "2021-03-03T00:00:00Z"


def test_a_document_date_never_moves_a_live_note_or_an_unquoted_or_near_date(nb):
    live = run(nb, LETTER, "2026-10-01T10:00:00Z", _letter_ext(), _letter_link)
    assert "note.redate" not in ops_of(nb, live[3]) and nb.note(live[0])["occurred_at"] == "2026-10-01T10:00:00Z"
    assert any("not approximate" in w for w in live[3]["warnings"])
    unquoted = run(nb, LETTER, "2026-10-01T10:00:00Z", _letter_ext(quote="dated the third of March"), _letter_link,
                   **dict(IMPORTED, source_key="letter-2"))
    assert "note.redate" not in ops_of(nb, unquoted[3])
    assert any("not quoted" in w for w in unquoted[3]["warnings"])
    near = run(nb, LETTER, "2021-03-03T12:00:00Z", _letter_ext(), _letter_link, **dict(IMPORTED, source_key="l-3"))
    assert "note.redate" not in ops_of(nb, near[3]) and nb.note(near[0])["date_basis"] == "file_mtime"


def test_a_person_sets_the_date_of_a_note(nb):
    nid, out = castor(nb, "2026-10-01T10:00:00Z", "Llama")
    res = nb.redate_note(nid, "2020-05-05", "alice", "the letter's date")
    rev = nb.revision(res["revision_id"])
    assert rev["source"] == {"client": "web", "account": "alice"} and rev["action"] == "note.redate"
    note = nb.note(nid)
    assert (note["occurred_at"], note["date_basis"], note["date_approximate"]) == ("2020-05-05T00:00:00Z", "user",
                                                                                  False)
    [c] = note["claims"]
    assert c["observed_at"] == c["valid_from"] == "2020-05-05T00:00:00Z"
    assert nb.store.entity_row(CASTOR)["first_seen"] == "2020-05-05T00:00:00Z"
    import pytest

    with pytest.raises(ValueError):
        nb.redate_note(nid, "2020-05-05", "alice")
    with pytest.raises(ValueError):
        nb.redate_note("nt_nope", "2020-05-05", "alice")


# --------------------------------------------------------------------------- D1 merges

def _two_people(nb, second="D. Ng", second_aliases=(), kind="person", notes=1):
    """Dana (alias Dana Ng) and a second record ``second`` mentioned in ``notes`` notes; returns its id."""
    ext = extraction([mention("m1", "Dana", "person", ["Dana Ng"])])
    run(nb, "Met Dana Ng.", "2026-01-01T09:00:00Z", ext,
        lambda p, c: {"mentions": [ml("m1", "create", name="Dana", kind="person")], "claims": [], "tasks": [],
                      "revision_summary": "Dana."})
    sid = None
    for i in range(notes):
        def link(p, c, sid=sid):
            first = ml("m1", "create", name=second, kind=kind, aliases=list(second_aliases))
            return {"mentions": [ml("m1", "reuse", cand_index(c, "m1", sid)) if sid else first], "claims": [],
                    "tasks": [], "revision_summary": "A call."}

        o = run(nb, f"{second} called ({i}).", f"2026-01-{2 + i:02d}T09:00:00Z",
                extraction([mention("m1", second, kind)]), link)[3]
        sid = sid or o["entities_created"][0]
    return sid


def _merge(nb, sid, at="2026-02-01T09:00:00Z"):
    ext = extraction([mention("m1", "Dana", "person", ["D. Ng"])])
    return run(nb, "Dana (D. Ng) confirmed by mail.", at, ext,
               lambda p, c: {"mentions": [ml("m1", "merge", merge=[cand_index(c, "m1", "ex:person-dana"),
                                                                   cand_index(c, "m1", sid)], name="Dana")],
                             "claims": [], "tasks": [], "revision_summary": "D. Ng is Dana."})[3]


def _kept_for_review(nb, out, sid):
    assert out["entities_merged"] == [] and nb.store.canonical(sid) == sid
    [cm] = [c for c in out["claims_added"] if nb.store.claim_row(c)["predicate"] == "skos:closeMatch"]
    r = nb.store.claim_row(cm)
    assert (r["subject_id"], r["object_id"]) == (sid, "ex:person-dana")
    assert any(it["kind"] == "close_match" for it in nb.review()["items"])
    assert any("kept for review" in w for w in out["warnings"])


def test_a_model_merge_without_a_shared_name_goes_to_review(nb):
    sid = _two_people(nb)
    _kept_for_review(nb, _merge(nb, sid), sid)


def test_a_model_merge_of_different_kinds_goes_to_review(nb):
    sid = _two_people(nb, second="Dana Ng", kind="org")
    out = _merge(nb, sid)
    _kept_for_review(nb, out, sid)
    assert any("kinds differ" in w for w in out["warnings"])


def test_a_model_merge_of_a_well_known_entry_goes_to_review(nb):
    sid = _two_people(nb, second_aliases=["Dana Ng"], notes=MERGE_REVIEW_NOTES)
    assert nb.store.entity_row(sid)["note_count"] == MERGE_REVIEW_NOTES
    _kept_for_review(nb, _merge(nb, sid), sid)


def test_a_model_merge_with_a_shared_key_applies_once_per_note(nb):
    sid = _two_people(nb, second_aliases=["Dana Ng"])
    ext = extraction([mention("m1", "Dana", "person", ["D. Ng"]), mention("m2", "Ng", "person")])
    run(nb, "Ng wrote.", "2026-01-20T09:00:00Z", extraction([mention("m1", "Ng", "person")]),
        lambda p, c: {"mentions": [ml("m1", "create", name="Ng", kind="person", aliases=["Dana Ng"])], "claims": [],
                      "tasks": [], "revision_summary": "Ng."})

    def link(p, c):
        dana, ng = cand_index(c, "m1", "ex:person-dana"), cand_index(c, "m1", sid)
        ng2 = cand_index(c, "m2", "ex:person-ng")
        dana2 = cand_index(c, "m2", "ex:person-dana")
        return {"mentions": [ml("m1", "merge", merge=[dana, ng], name="Dana"),
                             ml("m2", "merge", merge=[dana2, ng2], name="Dana")],
                "claims": [], "tasks": [], "revision_summary": "All Dana."}

    out = run(nb, "Dana (D. Ng) and Ng are one person.", "2026-02-01T09:00:00Z", ext, link)[3]
    assert out["entities_merged"] == [{"from_id": sid, "into_id": "ex:person-dana"}]  # the first merge only
    assert nb.store.canonical("ex:person-ng") == "ex:person-ng"
    assert any("one merge per entry" in w for w in out["warnings"])


# --------------------------------------------------------------------------- D2 unrelated supersessions

def test_a_supersession_of_another_subject_or_predicate_is_a_new_statement(nb):
    _, o = castor(nb, T22, "Llama")
    [c22] = o["claims_added"]
    ext = extraction([mention("m1", "Pollux", "project")],
                     [stmt("m1", "ex:runs_on", "Qwen", literal=True, evidence="Pollux runs on Qwen")])

    def link(packet, cands):
        return {"mentions": [ml("m1", "create", name="Pollux", kind="project")],
                "claims": [{"index": 0, "relation_to_existing": "supersedes",
                            "existing": claim_index(packet, cands, c22), "reason": "?"}],
                "tasks": [], "revision_summary": "Pollux."}

    out = run(nb, "Pollux runs on Qwen, unlike Castor.", T26, ext, link)[3]
    assert out["claims_superseded"] == [] and row(nb, c22)["status"] == "active"
    assert any("another subject or relation" in w for w in out["warnings"])
    ext2 = extraction([mention("m1", "Castor", "project")],
                      [stmt("m1", "ex:hosted_by", "OVH", literal=True, evidence="Castor is hosted by OVH")])
    out2 = run(nb, "Castor is hosted by OVH.", T26, ext2, lambda p, c: {
        "mentions": [ml("m1", "reuse", cand_index(c, "m1", CASTOR))],
        "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": claim_index(p, c, c22),
                    "reason": "?"}], "tasks": [], "revision_summary": "Host."})[3]
    assert out2["claims_superseded"] == [] and row(nb, c22)["status"] == "active"


def test_no_window_ever_ends_before_it_starts(nb):
    # the model gives an end before the start; a statement that stopped before its note was written
    ext = extraction([mention("m1", "Castor", "project")], [
        {**stmt("m1", "ex:runs_on", "Llama", literal=True, evidence="Castor runs on Llama"),
         "valid_from": "2027-01-01", "valid_until": "2024-01-01"},
        {**stmt("m1", "ex:hosted_by", "OVH", literal=True, evidence="Castor runs on Llama", stance="retract"),
         "valid_until": "2024-06-01"}])
    out = run(nb, "Castor runs on Llama.", T26, ext,
              lambda p, c: {"mentions": [ml("m1", "create", name="Castor", kind="project")], "claims": [], "tasks": [],
                            "revision_summary": "Castor."})[3]
    assert any("ends before it starts" in w for w in out["warnings"])
    rows = {r["predicate"]: (r["valid_from"], r["valid_until"]) for r in nb.store.q("SELECT * FROM claims")}
    assert rows["ex:runs_on"] == ("2027-01-01T00:00:00Z", None)
    assert rows["ex:hosted_by"] == (None, "2024-06-01T00:00:00Z")
    # a statement that starts later than the note that replaces it closes empty, never before its start
    [future] = [r["claim_id"] for r in nb.store.q("SELECT claim_id FROM claims WHERE predicate='ex:runs_on'")]
    ext2 = extraction([mention("m1", "Castor", "project")],
                      [stmt("m1", "ex:runs_on", "Qwen", literal=True, evidence="Castor runs on Qwen")])
    run(nb, "Castor runs on Qwen.", "2026-06-01T09:00:00Z", ext2, lambda p, c: {
        "mentions": [ml("m1", "reuse", cand_index(c, "m1", CASTOR))],
        "claims": [{"index": 0, "relation_to_existing": "supersedes", "existing": claim_index(p, c, future),
                    "reason": "?"}], "tasks": [], "revision_summary": "Qwen."})
    assert row(nb, future)["status"] == "superseded" and row(nb, future)["until"] == "2027-01-01T00:00:00Z"
    [kg] = [a for a in nb.kg.query() if a.assertion_id == future]
    assert kg.valid_until == "2027-01-01T00:00:00Z"  # the same end in AbstractMemory
    assert_no_negative_window(nb)


# --------------------------------------------------------------------------- D8 values are never entities

def test_numbers_versions_ids_links_tokens_and_generic_words_are_kept_as_text(nb):
    names = ["0.9.0", "v15", "c1234", "dm#7", "dm:a--b", "https://example.org/x", "a8F3kd92JdkL0qPz", "42", "time"]
    text = "Castor " + " ".join(names) + "."
    ms = [mention("m1", "Castor", "project")] + [mention(f"m{i + 2}", n, "thing") for i, n in enumerate(names)]
    claims = [stmt("m1", "ex:related_to", f"m{i + 2}", evidence="Castor") for i in range(len(names))]
    claims.append(stmt("m2", "ex:is", "m1", evidence="Castor"))  # a value as subject: left out
    out = run(nb, text, T26, extraction(ms, claims),
              lambda p, c: {"mentions": [ml("m1", "create", name="Castor", kind="project")] + [
                  ml(f"m{i + 2}", "create", name=n, kind="thing") for i, n in enumerate(names)],
                  "claims": [], "tasks": [], "revision_summary": "Values."})[3]
    assert out["entities_created"] == [CASTOR]
    objs = sorted(r["object_literal"] for r in nb.store.q("SELECT object_literal FROM claims WHERE subject_id=?",
                                                          (CASTOR,)))
    assert objs == sorted(names)
    assert nb.store.q1("SELECT COUNT(*) FROM claims WHERE predicate='ex:is'")[0] == 0
    assert sum("kept as text" in w for w in out["warnings"]) == len(names)


def test_the_generic_words_list_is_an_editable_file():
    from abstractnote.process.demote import GENERIC_WORDS_FILE, generic_words, literal_reason

    assert GENERIC_WORDS_FILE.name == "generic_words.txt" and "time" in generic_words()
    assert literal_reason("Laurent-Philippe Albou") is None and literal_reason("gateway") is None
    assert literal_reason("AbstractCore") is None and literal_reason("M5") is None
    assert literal_reason("the model") is None  # several words: never the list
