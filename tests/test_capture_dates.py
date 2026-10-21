"""Dates on live captures and a person's re-date (ADR 0008 §4, §7; R4 items 1-2; retro-B S6 and P7b).

- a note typed now keeps the client's time (``client``) and is never redated from its text;
- an upload, a pasted text or a capture without any date is a stand-in (``upload``, ``pasted_text``,
  ``capture_time``): the extraction's ``document_date`` dates it (basis ``content``; ``stated_month`` /
  ``stated_year`` when only a month or a year is stated), and the capture answer says so;
- a file's metadata date, else the ``last_modified`` the client sent, dates an upload without a caller's time;
- a note with nothing to read is refused at capture (nothing written, nothing queued);
- an understood note's re-date keeps its statements (both stores agree, no overlap) and marks it, with its
  dependents forward from the earlier date, through the re-understanding lane's API; without that API: refused.
"""

from __future__ import annotations

import pytest

from abstractnote.ingest import dates
from abstractnote.tools.context import ToolContext
from abstractnote.tools.process import note_apply_revision, note_context, note_resolve_candidates
from test_process import extraction, mention, ml, stmt
from test_store_support import nb  # noqa: F401

S6_TEXT = "Meeting notes — 14 March 2019. Oskar Lind confirmed the schedule for the north basin."


def understand(nb, note_id, ext, link=None):
    """note-process's tool steps on a captured note, with a scripted extraction and linking."""
    ctx = ToolContext(nb=nb)
    note_context(ctx, note_id)
    names = [{"ref": m["ref"], "name": m["name"], "kind": m["kind"], "aliases": m.get("aliases") or []}
             for m in ext["mentions"]]
    cands = note_resolve_candidates(ctx, note_id, names)
    linking = link(cands) if link else {
        "mentions": [ml(m["ref"], "create", name=m["name"], kind=m["kind"]) for m in ext["mentions"]],
        "claims": [], "tasks": [], "revision_summary": "Read."}
    return note_apply_revision(ctx, note_id, ext, linking)


def dated(iso, quote):
    ext = extraction([mention("m1", "Oskar Lind", "person")],
                     [stmt("m1", "ex:confirmed", "the schedule", literal=True,
                           evidence="Oskar Lind confirmed the schedule")], summary="Meeting notes.")
    ext["document_date"] = {"iso": iso, "basis": "stated in the text", "quote": quote}
    return ext


# --------------------------------------------------------------------------- the basis of a live capture

def test_a_note_typed_now_keeps_the_client_time_and_its_text_never_moves_it(nb):
    r = nb.capture(S6_TEXT, occurred_at="2026-10-20T13:10:54Z", source={"client": "web"})
    assert (r.date_basis, r.date_sentence) == ("client", None)
    out = understand(nb, r.note_id, dated("2019-03-14", "14 March 2019"))
    note = nb.note(r.note_id)
    assert (note["occurred_at"], note["date_basis"]) == ("2026-10-20T13:10:54Z", "client")
    assert any("not approximate" in w for w in out["warnings"]) and "date_sentence" not in out


@pytest.mark.parametrize("how", ["no date at all", "pasted", "upload"])
def test_an_undated_pasted_or_uploaded_document_takes_its_stated_date(nb, how):
    """B's S6 text body and metadata-less PDF: dated 14 March 2019 from the document, basis content.
    MUTATION: drop the stand-in bases from APPROXIMATE_BASES (the date stays today)."""
    kwargs = {}
    if how == "pasted":
        kwargs = {"occurred_at": "2026-10-20T13:10:54Z", "source": {"client": "web", "origin": {"pasted": True}}}
    elif how == "upload":
        kwargs = {"occurred_at": "2026-10-20T13:10:54Z",
                  "attachments": [{"data": S6_TEXT.encode(), "filename": "minutes.txt", "mime": "text/plain"}]}
    r = nb.capture("" if how == "upload" else S6_TEXT, **kwargs)
    basis = {"no date at all": "capture_time", "pasted": "pasted_text", "upload": "upload"}[how]
    assert r.date_basis == basis and dates.is_approximate(basis) and r.date_sentence is None
    assert nb.note(r.note_id)["date_approximate"] is True  # "Set the date" is offered until it is dated
    out = understand(nb, r.note_id, dated("2019-03-14", "14 March 2019"))
    note = nb.note(r.note_id)
    assert (note["occurred_at"], note["date_basis"], note["date_approximate"]) == (
        "2019-03-14T00:00:00Z", "content", False)
    # W30: the answer says which evidence won and which lost (the client's time, or the capture's moment)
    lost = {"no date at all": "; it was saved on " + dates.long_day(r.received_at),
            "pasted": "; it was pasted on 20 October 2026", "upload": "; it was sent on 20 October 2026"}[how]
    assert out["date_sentence"] == "Dated 14 March 2019 from the document" + lost + "."
    [c] = note["claims"]
    assert c["observed_at"] == c["valid_from"] == "2019-03-14T00:00:00Z"


def test_a_stated_month_or_year_is_placed_on_its_first_day_and_stays_approximate(nb):
    """MUTATION: parse document_date with ids.normalize_time only ("2019-03" is then not a date)."""
    r = nb.capture("Report, March 2019. Oskar Lind confirmed the schedule.")
    out = understand(nb, r.note_id, dated("2019-03", "March 2019"))
    note = nb.note(r.note_id)
    assert (note["occurred_at"], note["date_basis"], note["date_approximate"]) == (
        "2019-03-01T00:00:00Z", "stated_month", True)
    assert out["date_sentence"] == "Dated March 2019 from the document; it was saved on " + dates.long_day(
        r.received_at) + "."
    r2 = nb.capture("Annual report 2018. Oskar Lind confirmed the schedule.")
    understand(nb, r2.note_id, dated("2018", "Annual report 2018"))
    assert (nb.note(r2.note_id)["occurred_at"], nb.note(r2.note_id)["date_basis"]) == ("2018-01-01T00:00:00Z",
                                                                                       "stated_year")
    with pytest.raises(ValueError):
        dates.document_date("2019-13")  # month 13 is no month (nor a date)


def test_an_upload_takes_its_file_date_then_the_client_mtime_and_says_so(nb):
    """MUTATION: drop the last_modified step of capture_date (the .txt is dated now)."""
    from test_file_dates import PDF

    r = nb.capture("the letter", attachments=[{"data": PDF, "mime": "application/pdf", "filename": "letter.pdf"}])
    assert (r.occurred_at, r.date_basis) == ("2019-04-05T08:30:00Z", "pdf_created")
    assert r.date_sentence == "Dated 5 April 2019 from the document."
    txt = nb.capture("", attachments=[{"data": b"Dredging plan for the north basin.", "filename": "plan.txt",
                                       "last_modified": 1541412000000}])  # the web's File.lastModified (ms)
    assert (txt.occurred_at, txt.date_basis) == ("2018-11-05T10:00:00Z", "file_mtime")
    assert txt.date_sentence == "Dated 5 November 2018 from the file's last change."
    iso = nb.capture("", attachments=[{"data": b"Another plan.", "filename": "b.txt",
                                       "last_modified": "2018-11-06T09:00:00+01:00"}])
    assert (iso.occurred_at, iso.date_basis) == ("2018-11-06T08:00:00Z", "file_mtime")
    with pytest.raises(ValueError, match="last_modified"):
        nb.capture("", attachments=[{"data": b"x y z", "filename": "c.txt", "last_modified": "soon"}])
    bare = nb.capture("", attachments=[{"data": b"A third plan.", "filename": "d.txt"}])
    assert bare.date_basis == "upload" and bare.date_sentence is None


def test_a_watch_recording_keeps_the_watch_time(nb):
    """The watch's attachment is a recording made now, not an uploaded document. MUTATION: drop RECORDING_CLIENTS."""
    r = nb.capture("", occurred_at="2026-10-20T08:00:00Z", source={"client": "watch"},
                   attachments=[{"data": b"ID3....", "filename": "memo.m4a", "mime": "audio/mp4"}])
    assert (r.occurred_at, r.date_basis) == ("2026-10-20T08:00:00Z", "client")


def test_an_import_keeps_its_own_basis(nb):
    r = nb.capture("An old letter.", occurred_at="2018-01-01T00:00:00Z", importer="files", source_key="k",
                   source={"client": "importer", "origin": {"date_basis": "file_mtime"}},
                   attachments=[{"data": b"An old letter.", "filename": "l.txt"}])
    assert r.date_basis == "file_mtime"


# --------------------------------------------------------------------------- nothing to read: refused at capture

def test_a_note_with_nothing_to_read_is_refused_at_capture_and_not_queued(nb):
    """B's S6 docx that extracted 0 characters: refused at capture with one sentence, nothing written or queued.
    MUTATION: drop the readable-text check (the note is queued and fails later in processing)."""
    from test_file_dates import _docx

    before = (nb.store.q1("SELECT COUNT(*) FROM notes")[0], nb.store.q1("SELECT COUNT(*) FROM queue")[0])
    with pytest.raises(ValueError) as err:
        nb.capture("", attachments=[{"data": _docx(), "filename": "minimal.docx"}])
    assert str(err.value) == ("The file “minimal.docx” has no readable text, so the note was not saved; add a few "
                              "words about it and send it again.")
    with pytest.raises(ValueError, match="None of these files"):
        nb.capture("", attachments=[{"data": _docx(), "filename": "a.docx"}, {"data": b"\x00", "filename": "b.bin"}])
    assert (nb.store.q1("SELECT COUNT(*) FROM notes")[0], nb.store.q1("SELECT COUNT(*) FROM queue")[0]) == before
    assert not list((nb.root / "blobs").rglob("*")) or all(p.is_dir() for p in (nb.root / "blobs").rglob("*"))
    # with a few words of its own, the same file is kept
    kept = nb.capture("the minimal report", attachments=[{"data": _docx(), "filename": "minimal.docx"}])
    assert kept.status == "new" and nb.queue.item(kept.note_id)["state"] == "queued"


# --------------------------------------------------------------------------- a person's re-date (P7b)

def _budget(nb, at, amount, key, existing=None):
    r = nb.capture(f"Zorn Labs budget is {amount}.", occurred_at=at, idempotency_key=key)
    ext = extraction([mention("m1", "Zorn Labs", "org")],
                     [stmt("m1", "ex:annual_budget", amount, literal=True,
                           evidence=f"Zorn Labs budget is {amount}")])

    def link(cands):
        found = [c for m in cands["mentions"] for c in m["candidates"] if c["entity_id"] == "ex:org-zorn-labs"]
        mention_link = ml("m1", "reuse", found[0]["index"]) if found else ml("m1", "create", name="Zorn Labs",
                                                                              kind="org")
        claims = []
        if existing:
            ctx_claims = nb.store.q1("SELECT packet_json FROM context_packets WHERE note_id=?", (r.note_id,))[0]
            import json

            idx = json.loads(ctx_claims)["existing_claims"].index(existing)
            claims = [{"index": 0, "relation_to_existing": "supersedes", "existing": idx, "reason": "new budget"}]
        return {"mentions": [mention_link], "claims": claims, "tasks": [], "revision_summary": "Budget."}

    out = understand(nb, r.note_id, ext, link)
    return r.note_id, out["claims_added"][0]


def _lineage(nb):
    n40, c40 = _budget(nb, "2025-02-05T09:00:00Z", "40k", "b40")
    n25, c25 = _budget(nb, "2023-02-07T09:00:00Z", "25k", "b25", existing=c40)
    n18, c18 = _budget(nb, "2021-09-20T09:00:00Z", "18k", "b18", existing=c40)
    return n40, [c18, c25, c40]


def _beliefs(nb, at):
    return sorted(c["claim_id"] for c in nb.beliefs_at(at, entity_id="ex:org-zorn-labs")
                  if c["predicate"] == "ex:annual_budget")


AT = ("2022-06-01T00:00:00Z", "2024-03-01T00:00:00Z", "2026-06-01T00:00:00Z")


def test_redate_of_an_understood_note_is_refused_while_it_cannot_be_understood_again(nb, monkeypatch):
    """B's P7b: when the notebook cannot understand a note again (an AbstractMemory without the closure kind
    ``reinterpreted``) the re-date is refused and nothing changes (before: 2022 believed 18k AND 40k, and
    AbstractMemory disagreed). MUTATION: drop the refusal (overlap returns)."""
    from abstractnote.store import kg

    n40, claims = _lineage(nb)
    monkeypatch.setattr(kg, "supports_reinterpreted", lambda: False)
    before = {at: _beliefs(nb, at) for at in AT}
    assert all(len(v) == 1 for v in before.values())
    with pytest.raises(dates.RedateRefused) as err:
        nb.redate_note(n40, "2022-01-15T09:00:00Z", "retro", "it was written in 2022")
    assert str(err.value) == dates.NEEDS_REUNDERSTANDING and "date" in str(err.value)
    assert {at: _beliefs(nb, at) for at in AT} == before
    assert nb.note(n40)["occurred_at"] == "2025-02-05T09:00:00Z"


def test_redate_keeps_the_statements_and_marks_forward_from_the_earlier_date(nb):
    """With re-understanding (R2's marks, written by the note.redate revision's projection): the note moves, its
    statements stay where its interpretation placed them in BOTH stores (no overlap, the note index and
    AbstractMemory agree at every date), and the note is marked from min(old, new). MUTATION: drop
    keep_statements from the op (the 40k claim moves to 2022 in the index only: overlap and disagreement)."""
    n40, claims = _lineage(nb)
    with nb.store.tx() as c:  # the earlier notes of the lineage marked it already; start from no mark
        c.execute("DELETE FROM reinterpret_marks")
    out = nb.redate_note(n40, "2022-01-15T09:00:00Z", "retro", "it was written in 2022")
    assert n40 in out["reinterpret"]["notes"] and out["reinterpret"]["count"] == len(out["reinterpret"]["notes"])
    mark = nb.store.q1("SELECT from_at, reason FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL", (n40,))
    assert mark["from_at"] == "2022-01-15T09:00:00Z" and "2022-01-15" in mark["reason"]
    note = nb.note(n40)
    assert (note["occurred_at"], note["date_basis"]) == ("2022-01-15T09:00:00Z", "user")
    for at in AT:
        index = _beliefs(nb, at)
        assert len(index) == 1, (at, index)
        assert sorted(c for c in nb.kg.active_claim_ids(at) if c in claims) == index, at
    nb.redate_note(n40, "2027-01-01T00:00:00Z", "retro")  # moved later: still one waiting mark (never doubled)
    assert nb.store.q1("SELECT COUNT(*) FROM reinterpret_marks WHERE note_id=? AND consumed_at IS NULL", (n40,))[0] == 1
    for at in AT:
        assert len(_beliefs(nb, at)) == 1, at


# --------------------------------------------------------------------------- the capture answer (headless flow)

def test_the_capture_answer_says_where_the_date_came_from(tmp_path):
    """Inline: "Dated 14 March 2019 from the document." after the understanding redated it; queued: the file's
    date at once. A corrupt answer twice ends the inline capture with its sentence, logged as a failed attempt.
    MUTATION: drop date_sentence from the saved/processed answers."""
    from abstractnote.flows.runner import FlowRunError, NoteRunner, ScriptedLLM
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    ext = {"summary": "Meeting notes.", "mentions": [{"ref": "m1", "name": "Oskar Lind", "kind": "person",
                                                       "aliases": [], "role": "confirmed the schedule"}],
           "claims": [], "tasks": [], "time_refs": [], "topics": [],
           "document_date": {"iso": "2019-03-14", "basis": "stated in the text", "quote": "14 March 2019"}}
    link = {"mentions": [{"ref": "m1", "decision": "create", "name": "Oskar Lind", "kind": "person",
                          "summary": "Oskar Lind, the harbour master."}], "claims": [], "tasks": [],
            "revision_summary": "Oskar Lind confirmed the schedule."}
    corrupt = dict(link, mentions=[dict(link["mentions"][0], summary='Oskar.","reason":"same"},{')])
    filing = {"title": "Meeting notes", "kind": "event", "topics": [], "urgency": "normal", "language": "en",
              "one_line": "Meeting notes."}
    runner = NoteRunner.with_llm(ScriptedLLM({"QuickFiling": filing, "Extraction": ext,
                                              "Linking": [link, corrupt, corrupt]}), bind(tmp_path / "nb"))
    out = runner.run_capture(S6_TEXT, idempotency_key="s6", source={"client": "agent"}, process_inline=True)
    assert out["status"] == "processed"
    assert "Dated 14 March 2019 from the document; it was saved on " in out["response"]
    assert out["response"].index("Dated 14 March 2019") < out["response"].index("Oskar Lind confirmed")
    from test_file_dates import PDF

    pdf = tmp_path / "files" / "letter.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(PDF)
    runner2 = NoteRunner.with_llm(ScriptedLLM({"QuickFiling": filing}),
                                  bind(tmp_path / "nb2", allow_paths_under=pdf.parent))
    queued = runner2.run_capture("the letter", idempotency_key="pdf", source={"client": "agent"},
                                 context={"attachments": [{"path": "letter.pdf"}]})
    assert queued["status"] == "queued" and "Dated 5 April 2019 from the document." in queued["response"]
    with pytest.raises(FlowRunError, match=r"^The model's linking answer was malformed twice"):
        runner.run_capture("Oskar Lind again, 14 March 2019.", idempotency_key="bad", source={"client": "agent"},
                           process_inline=True)
    nb = Notebook.open(tmp_path / "nb")
    try:
        [row] = [r for r in nb.store.q("SELECT * FROM queue") if r["state"] != "done"]
        assert (row["failures"], row["inline_run_id"]) == (1, None)
        assert nb.queue.errors(row["note_id"])[0]["error"].startswith("The model's linking answer was malformed")
    finally:
        nb.close()


def test_the_extraction_header_says_the_date_is_approximate_and_from_where(nb):
    """The model gives a document_date only when the header says the date is approximate (extract.system.md).
    MUTATION: keep the stand-in bases out of APPROXIMATE_BASES (the header says "written ... (now, ...)")."""
    from abstractnote.process.packets import note_header

    ctx = ToolContext(nb=nb)
    up = nb.capture("see the minutes", occurred_at="2026-10-20T13:10:54Z",
                    attachments=[{"data": S6_TEXT.encode(), "filename": "minutes.txt"}]).note_id
    assert "this date is approximate, from the upload)" in note_header(note_context(ctx, up)["note"])
    typed = nb.capture("Typed now.", occurred_at="2026-10-20T13:10:54Z").note_id
    assert "approximate" not in note_header(note_context(ctx, typed)["note"])
    bare = nb.capture("An agent's note without a date.").note_id
    assert "approximate, from the capture time)" in note_header(note_context(ctx, bare)["note"])
