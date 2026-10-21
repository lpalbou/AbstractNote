"""W30: a document's stated dates beat its file date; a document of dated moments becomes dated notes.

The 2026-10-21 incident (the operator's archive test): a diary of dated moments ("### Naming and Recognition (March 5,
2025)", every entry dated March 2025) was captured with its file's modification time (2026-03-20): the stated dates
lost to the file date and one note of twenty dated moments sat at one date. Here (synthetic texts only):

1. the precedence of date evidence (``dates.PRECEDENCE``): a date the document states beats the file's metadata,
   which beats the client's time; the answer says which won and what lost; a person's date and the moment the words
   were written are never moved by a text;
2. a document the extraction reads as a sequence of dated entries (``moments``; the model decides) becomes a
   container (kind ``archive``, the file attached, dated by its earliest entry, no statements of its own) and one
   note per entry (its own text, its stated date, basis ``stated``, ``part_of`` the container, the same provenance),
   each queued and understood as a note of its own in history order; the same document again gives the same parts;
3. lists fold the parts under their container; the day view shows each entry on its day.
"""

from __future__ import annotations

import pytest

from abstractnote.ingest import dates
from abstractnote.process import split
from abstractnote.tools.context import ToolContext
from abstractnote.tools.process import note_apply_revision, note_context
from test_capture_dates import S6_TEXT, dated, understand
from test_process import extraction, mention, stmt
from test_store_support import nb  # noqa: F401

DIARY = """# Key Moments

This document records the moments that shaped the harbour project.

### Naming and Recognition (March 5, 2025)

Oskar Lind named the north basin project Halcyon and asked Mira Sand to lead it.

### The First Survey (March 12, 2025)

Mira Sand surveyed the north basin with the Tern dredger.

### A Change of Plan (March 21, 2025)

Oskar Lind moved the dredging to the south basin.
"""
MOMENTS = [
    {"heading": "Naming and Recognition", "date": "2025-03-05", "basis": "stated in the text",
     "text_span": "Naming and Recognition (March 5, 2025)"},
    {"heading": "The First Survey", "date": "2025-03-12", "basis": "stated in the text",
     "text_span": "### The First Survey (March 12, 2025)"},
    # spacing and case differ from the text: the quote is still found (positional, never a reading of meaning)
    {"heading": "A Change of Plan", "date": "2025-03-21", "basis": "stated in the text",
     "text_span": "a change   of plan (march 21, 2025)"},
]
MTIME = "2026-03-20T04:54:47Z"


def diary_extraction(moments=MOMENTS):
    ext = extraction([mention("m1", "Oskar Lind", "person"), mention("m2", "Halcyon", "project")],
                     [stmt("m1", "ex:named", "m2", evidence="Oskar Lind named the north basin project Halcyon")],
                     summary="A diary of the harbour project's moments in March 2025.")
    ext["moments"] = [dict(m) for m in moments]
    return ext


def import_diary(nb, text=DIARY, key="diary-1", **origin):
    """An imported file dated by its modification time (``file_mtime``), the incident's shape."""
    return nb.capture("", title="Key_Moments.md", occurred_at=MTIME, importer="files", source_key=key,
                      source={"client": "importer", "origin": {"path": "/synthetic/Key_Moments.md",
                                                               "date_basis": "file_mtime", **origin}},
                      attachments=[{"data": text.encode(), "filename": "Key_Moments.md", "mime": "text/markdown"}],
                      title_is_text=False, priority=0)


def apply_split(nb, note_id, ext=None):
    ctx = ToolContext(nb=nb)
    note_context(ctx, note_id)
    return note_apply_revision(ctx, note_id, ext or diary_extraction(), {"mentions": [], "claims": [], "tasks": []})


# --------------------------------------------------------------------------- 1. the precedence of date evidence

def test_the_precedence_table_orders_the_evidence():
    """MUTATION: swap the file-metadata and stand-in ranks (a stated date no longer beats an EXIF date, or the client's
    time beats the file's)."""
    r = dates.rank
    assert r("user") > r("client") == r("record") == r("message") > r("content") == r("stated") == r("front_matter")
    assert r("content") > r("exif") == r("pdf_created") == r("office_created") == r("file_mtime") > r("upload")
    assert r("upload") == r("pasted_text") == r("capture_time") == r("git_added") == r("unrecorded")
    every = {b for _, _, bases in dates.PRECEDENCE for b in bases}
    assert set(dates.APPROXIMATE_BASES) <= every  # every basis the importers write has its place
    wins = {b: dates.stated_date_wins(b) for b in ("exif", "xmp", "pdf_created", "office_created", "file_mtime",
                                                   "upload", "pasted_text", "capture_time", "git_added",
                                                   "stated_month", "stated_year")}
    assert all(wins.values()), wins
    loses = {b: dates.stated_date_wins(b) for b in ("user", "client", "record", "message", "content", "stated",
                                                    "front_matter", "created_line", "date_line")}
    assert not any(loses.values()), loses


def test_a_stated_date_beats_the_pdf_creation_date_and_the_answer_says_what_lost(nb):
    """Before W30 an exact file date (pdf_created, exif, office_created) was never moved by the document's own date.
    MUTATION: stated_date_wins only for approximate bases (the note stays dated 5 April 2019, pdf_created)."""
    from test_file_dates import PDF

    r = nb.capture(S6_TEXT, attachments=[{"data": PDF, "mime": "application/pdf", "filename": "minutes.pdf"}])
    assert r.date_basis == "pdf_created"
    out = understand(nb, r.note_id, dated("2019-03-14", "14 March 2019"))
    note = nb.note(r.note_id)
    assert (note["occurred_at"], note["date_basis"]) == ("2019-03-14T00:00:00Z", "content")
    assert out["date_sentence"] == "Dated 14 March 2019 from the document; the file itself is from 5 April 2019."
    [op] = [o for o in nb.revision(out["revision_id"])["ops"] if o["op"] == "note.redate"]
    assert (op["payload"]["from"], op["payload"]["from_basis"]) == ("2019-04-05T08:30:00Z", "pdf_created")


def test_the_incident_a_diary_dated_by_its_file_takes_its_stated_date(nb):
    """The file's modification time loses to the date the document states; the answer names both."""
    r = import_diary(nb, text="Diary, 5 March 2025. Oskar Lind named the north basin project Halcyon.", key="one")
    ext = dated("2025-03-05", "5 March 2025")
    ext["mentions"] = [mention("m1", "Oskar Lind", "person")]
    ext["claims"] = [stmt("m1", "ex:named", "Halcyon", literal=True, evidence="Oskar Lind named the north basin")]
    out = understand(nb, r.note_id, ext)
    assert nb.note(r.note_id)["occurred_at"] == "2025-03-05T00:00:00Z"
    assert out["date_sentence"] == "Dated 5 March 2025 from the document; the file itself is from 20 March 2026."


@pytest.mark.parametrize("basis", ["record", "message", "client"])
def test_the_moment_words_were_written_is_never_moved_by_a_stated_date(nb, basis):
    """A transcript record, a hub message, a note typed now: the dates their text gives are about other things."""
    if basis == "client":
        r = nb.capture(S6_TEXT, occurred_at="2026-10-20T13:10:54Z", source={"client": "web"})
    else:
        r = nb.capture(S6_TEXT, occurred_at="2026-10-20T13:10:54Z", importer="agora", source_key=basis,
                       source={"client": "importer", "origin": {"date_basis": basis}})
    out = understand(nb, r.note_id, dated("2019-03-14", "14 March 2019"))
    assert nb.note(r.note_id)["occurred_at"] == "2026-10-20T13:10:54Z" and "date_sentence" not in out


def test_a_persons_date_beats_the_documents(nb):
    """MUTATION: rank ``user`` below the stated rank (the processing moves the person's date)."""
    r = nb.capture("", occurred_at="2026-10-20T13:10:54Z",
                   attachments=[{"data": S6_TEXT.encode(), "filename": "minutes.txt"}])
    nb.redate_note(r.note_id, "2018-06-01T00:00:00Z", "tester", "the person knows")
    out = understand(nb, r.note_id, dated("2019-03-14", "14 March 2019"))
    note = nb.note(r.note_id)
    assert (note["occurred_at"], note["date_basis"]) == ("2018-06-01T00:00:00Z", "user")
    assert "date_sentence" not in out


def test_the_header_offers_a_stated_date_on_a_file_metadata_date(nb):
    """The extraction is invited to give the text's own date when the note is dated by its file's metadata.
    MUTATION: drop ``date_from_file`` from the note context (the header says "written ... (now, for this note)")."""
    from test_file_dates import PDF
    from abstractnote.process.packets import note_header

    r = nb.capture("the letter", attachments=[{"data": PDF, "mime": "application/pdf", "filename": "letter.pdf"}])
    header = note_header(note_context(ToolContext(nb=nb), r.note_id)["note"])
    assert "this date is from the file's metadata, pdf created)" in header


# --------------------------------------------------------------------------- 2. dated parts

def test_a_diary_of_three_dated_moments_becomes_a_container_and_three_dated_notes(nb):
    """MUTATION: return no split from split.plan (one note, March 2026); MUTATION: date the parts by the container."""
    r = import_diary(nb)
    out = apply_split(nb, r.note_id)
    container = nb.note(r.note_id)
    assert container["kind"] == split.CONTAINER_KIND
    assert (container["occurred_at"], container["date_basis"]) == ("2025-03-05T00:00:00Z", "stated")
    assert container["claims"] == [] and container["entities"] == []  # not read for statements itself: its parts are
    assert [a["filename"] for a in container["attachments"]] == ["Key_Moments.md"]  # the file stays with it
    parts = container["children"]
    assert [p["occurred_at"] for p in parts] == ["2025-03-05T00:00:00Z", "2025-03-12T00:00:00Z",
                                                 "2025-03-21T00:00:00Z"]
    assert [p["title"] for p in parts] == ["Naming and Recognition", "The First Survey", "A Change of Plan"]
    first, second, third = (nb.note(p["note_id"]) for p in parts)
    assert first["text"].startswith("### Naming and Recognition (March 5, 2025)")
    assert "Mira Sand to lead it." in first["text"] and "First Survey" not in first["text"]
    assert second["text"] == "### The First Survey (March 12, 2025)\n\nMira Sand surveyed the north basin with the Tern dredger."
    assert third["text"].startswith("### A Change of Plan") and third["text"].endswith("south basin.")
    for p in (first, second, third):
        assert (p["date_basis"], p["date_approximate"], p["part_of"]["note_id"]) == ("stated", False, r.note_id)
        assert p["source"]["importer"] == "files" and p["source"]["origin"]["path"] == "/synthetic/Key_Moments.md"
        assert p["source"]["origin"]["part_of"] == r.note_id and p["kind"] is None  # filed when it is understood
    # each queued as a note of its own, in history order (the queue's order: priority, then date)
    queued = [row["note_id"] for row in nb.store.q("SELECT note_id FROM queue WHERE state='queued'"
                                                    " ORDER BY priority DESC, occurred_at ASC")]
    assert queued == [p["note_id"] for p in parts]
    rev = nb.revision(out["revision_id"])
    assert [o["op"] for o in rev["ops"]] == ["note.redate", "note.split", "note.summary"]
    assert rev["summary"] == ("A document of 3 dated parts, from 5 March 2025 to 21 March 2025; each is read as a note"
                              " of its own.")
    assert out["date_sentence"] == "Dated 5 March 2025 from the document; the file itself is from 20 March 2026."
    assert out["split"]["created"] == 3


def test_the_same_document_again_gives_the_same_parts(nb, tmp_path):
    """Idempotent: a re-import is a duplicate; the same document in another notebook gives the same ids; a split
    applied again makes no new part. MUTATION: put the capture time in the part key (new parts every time)."""
    from abstractnote.store.notebook import Notebook

    r = import_diary(nb)
    apply_split(nb, r.note_id)
    first = [p["note_id"] for p in nb.note(r.note_id)["children"]]
    assert import_diary(nb).status == "duplicate"
    again = split.apply_split(nb, r.note_id, diary_extraction(), split.plan(nb, r.note_id, diary_extraction()))
    assert again["split"] == {**again["split"], "created": 0, "duplicates": 3}
    assert [p["note_id"] for p in nb.note(r.note_id)["children"]] == first
    assert first == [split.part_note_id(r.note_id, i, at) for i, at in
                     enumerate(["2025-03-05", "2025-03-12", "2025-03-21"])]
    other = Notebook.open(tmp_path / "other")
    try:
        r2 = import_diary(other)
        assert r2.note_id == r.note_id
        apply_split(other, r2.note_id)
        assert [p["note_id"] for p in other.note(r2.note_id)["children"]] == first
    finally:
        other.close()


def test_a_document_stays_one_note_when_asked_or_when_it_is_not_a_document(nb):
    """--no-split; a part itself; a note typed now; one entry only; an entry not found in the words; a bad date.
    Each is read as one note with its sentence (never silently). MUTATION: drop the no_split check in can_split."""
    asked = import_diary(nb, key="asked", no_split=True)
    out = understand(nb, asked.note_id, diary_extraction())
    assert nb.note(asked.note_id)["children"] == [] and split.NOT_SPLIT_ASKED in out["warnings"]
    assert nb.note(asked.note_id)["claims"]  # read as one note: its statements are its own

    typed = nb.capture(DIARY, occurred_at="2026-10-20T13:10:54Z", source={"client": "web"})
    out = understand(nb, typed.note_id, diary_extraction())
    assert nb.note(typed.note_id)["children"] == [] and split.NOT_SPLIT_DATED in out["warnings"]

    one = import_diary(nb, key="one")
    out = understand(nb, one.note_id, diary_extraction(MOMENTS[:1]))
    assert nb.note(one.note_id)["children"] == [] and split.TOO_FEW in out["warnings"]

    lost = import_diary(nb, key="lost")
    missing = [*MOMENTS[:2], dict(MOMENTS[2], text_span="A chapter nobody wrote", heading="Nowhere")]
    out = understand(nb, lost.note_id, diary_extraction(missing))
    assert nb.note(lost.note_id)["children"] == [] and split.NOT_FOUND in out["warnings"]

    bad = import_diary(nb, key="bad")
    out = understand(nb, bad.note_id, diary_extraction([*MOMENTS[:2], dict(MOMENTS[2], date="soon")]))
    assert nb.note(bad.note_id)["children"] == [] and any("is not a date" in w for w in out["warnings"])

    # the flow skipped the linking because the parts looked splittable; the apply did not find them: a loud failure
    skipped = import_diary(nb, key="skipped")
    with pytest.raises(ValueError, match="could not all be found"):
        apply_split(nb, skipped.note_id, diary_extraction(missing))


def test_a_part_is_never_split_again(nb):
    """A part's own reading may name dated entries: it is read as one note. MUTATION: drop the part_of check."""
    r = import_diary(nb)
    apply_split(nb, r.note_id)
    part = nb.note(r.note_id)["children"][0]["note_id"]
    ext = diary_extraction([
        {"heading": "Naming", "date": "2025-03-05", "basis": "stated in the text",
         "text_span": "Naming and Recognition"},
        {"heading": "Mira", "date": "2025-03-06", "basis": "stated in the text", "text_span": "asked Mira Sand"}])
    out = understand(nb, part, ext)
    assert nb.note(part)["children"] == [] and split.NOT_SPLIT_PART in out["warnings"]
    header = __import__("abstractnote.process.packets", fromlist=["note_header"]).note_header(
        note_context(ToolContext(nb=nb), part)["note"])
    assert "(one dated part of the document “Key_Moments.md”)" in header


def test_locating_entries_is_positional():
    texts = ["intro\n\n## One (2025-01-01)\nalpha\n\n  ## Two (2025-01-02)\nbeta", "### Three\ngamma"]
    found = split.locate_moments(texts, [{"text_span": "One (2025-01-01)"}, {"text_span": "two (2025-01-02)"},
                                         {"heading": "Three", "text_span": ""}])
    assert found == [(0, texts[0].index("## One")), (0, texts[0].index("  ## Two")), (1, 0)]
    assert split.locate_moments(texts, [{"text_span": "Two"}, {"text_span": "One"}])[1] is None  # order is kept
    assert split.split_plan(texts, [{"text_span": "One"}, {"text_span": "Two"}], True)["split"] is True
    assert split.split_plan(texts, [{"text_span": "One"}, {"text_span": "Two"}], False)["reason"] == "not_splittable"


def test_a_container_read_again_stays_as_it_is(nb):
    """A re-understanding of a container settles its marks as unchanged and writes nothing (its parts are notes of
    their own). MUTATION: drop settle_container (the re-reading applies the container's statements)."""
    r = import_diary(nb)
    apply_split(nb, r.note_id)
    before = nb.store.q1("SELECT COUNT(*) FROM revisions")[0]
    nb.mark_reinterpret([r.note_id], "a test")
    out = note_apply_revision(ToolContext(nb=nb), r.note_id, diary_extraction(), {}, reinterpret=True)
    assert out["unchanged"] is True and out["revision_id"] is None
    assert nb.store.q1("SELECT COUNT(*) FROM revisions")[0] == before
    assert [m for m in nb.store.pending_marks() if m["note_id"] == r.note_id] == []
    assert nb.note(r.note_id)["claims"] == []


def test_a_rebuild_replays_the_split(nb):
    """``note.split`` is in the revision log: a rebuild keeps the container's kind, date and parts."""
    r = import_diary(nb)
    apply_split(nb, r.note_id)
    parts = [p["note_id"] for p in nb.note(r.note_id)["children"]]
    nb.rebuild()
    note = nb.note(r.note_id)
    assert (note["kind"], note["occurred_at"]) == ("archive", "2025-03-05T00:00:00Z")
    assert [p["note_id"] for p in note["children"]] == parts


# --------------------------------------------------------------------------- 3. lists and the day view

def test_lists_fold_the_parts_and_the_day_view_shows_each_on_its_day(nb):
    """MUTATION: drop the ``part_of IS NULL`` filter of list_notes (the parts are listed beside their container)."""
    r = import_diary(nb)
    apply_split(nb, r.note_id)
    other = nb.capture("Oskar Lind called about the dredger.", occurred_at="2026-10-20T13:10:54Z")
    listed = nb.notes()["items"]
    assert [n["note_id"] for n in listed] == [other.note_id, r.note_id]
    assert [n["parts"] for n in listed] == [0, 3]
    parts = nb.notes(part_of=r.note_id)["items"]
    assert [n["occurred_at"][:10] for n in parts] == ["2025-03-21", "2025-03-12", "2025-03-05"]  # newest first
    assert all(n["part_of"] == r.note_id for n in parts)
    assert len(nb.notes(parts=True)["items"]) == 5
    for day, title in (("2025-03-12", "The First Survey"), ("2025-03-21", "A Change of Plan")):
        assert [n["title"] for n in nb.day(day)["notes"]] == [title]
    assert {n["title"] for n in nb.day("2025-03-05")["notes"]} == {"Naming and Recognition", "Key_Moments.md"}
    assert nb.day("2026-03-20")["notes"] == []  # the file's date no longer holds the diary


# --------------------------------------------------------------------------- the flows (scripted model)

def _scripted_flow(tmp_path, filing):
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.tools import bind

    part_ext = {"summary": "One entry.", "mentions": [{"ref": "m1", "name": "Oskar Lind", "kind": "person",
                                                         "aliases": [], "role": "acts in the entry"}],
                "claims": [], "tasks": [], "time_refs": [], "topics": []}

    def extract(prompt):
        return part_ext if "one dated part of the document" in prompt else diary_extraction()

    link = {"mentions": [{"ref": "m1", "decision": "create", "name": "Oskar Lind", "kind": "person",
                          "summary": "Oskar Lind, the harbour master."}], "claims": [], "tasks": [],
            "revision_summary": "Read the entry."}
    llm = ScriptedLLM({"QuickFiling": filing, "Extraction": extract, "Linking": link})
    return llm, NoteRunner.with_llm(llm, bind(tmp_path / "nb", allow_paths_under=tmp_path / "files"))


FILING = {"title": "Key Moments", "kind": "personal", "topics": [], "urgency": "normal", "language": "en",
          "one_line": "A diary."}


def test_the_capture_flow_splits_a_diary_and_answers_with_the_stated_date(tmp_path):
    """The inline capture answer says the document's date won and the file's lost, once (the capture's own "from the
    file's last change" sentence is replaced); the container's linking is skipped (each part is read by itself).
    MUTATION: drop the dated-parts branch's ``split`` (the container is linked and read as one note)."""
    from abstractnote.store.notebook import Notebook

    llm, runner = _scripted_flow(tmp_path, FILING)
    files = tmp_path / "files"
    files.mkdir()
    path = files / "Key_Moments.md"
    path.write_text(DIARY, encoding="utf-8")
    # the incident's upload: the client sent the file's last change (the web's File.lastModified), no time of its own
    out = runner.run_capture("", idempotency_key="diary", source={"client": "agent"}, process_inline=True,
                             context={"attachments": [{"path": "Key_Moments.md", "last_modified": MTIME}]})
    assert out["status"] == "processed"
    resp = out["response"]
    assert "Dated 5 March 2025 from the document; the file itself is from 20 March 2026." in resp, resp
    assert "from the file's last change" not in resp
    assert "A document of 3 dated parts, from 5 March 2025 to 21 March 2025" in resp
    names = [c["schema"] for c in llm.calls]
    assert names.count("Linking") == 0, names  # the container's reading is not linked
    nb = Notebook.open(tmp_path / "nb")
    try:
        container = nb.note(out["note_id"])
        assert container["kind"] == "archive" and len(container["children"]) == 3
    finally:
        nb.close()
    # the drain then understands each part as a note of its own, in history order
    drained = runner.run_drain(max_items=10, budget_s=60)
    assert drained["processed"] == 3, drained
    nb = Notebook.open(tmp_path / "nb")
    try:
        parts = nb.note(out["note_id"])["children"]
        assert all(nb.note(p["note_id"])["status"] == "processed" for p in parts)
        assert [nb.note(p["note_id"])["occurred_at"][:10] for p in parts] == ["2025-03-05", "2025-03-12",
                                                                              "2025-03-21"]
        lind = [e for e in nb.entities()["items"] if e["name"] == "oskar lind" or e.get("display_name") == "Oskar Lind"]
        assert lind and lind[0]["first_seen"].startswith("2025-03-05")
    finally:
        nb.close()


def test_the_cli_keeps_one_note_with_no_split(tmp_path, monkeypatch):
    from abstractnote.cli import main
    from abstractnote.importers.cli import build_parser
    from abstractnote.store.notebook import Notebook

    f = tmp_path / "d.md"
    f.write_text(DIARY, encoding="utf-8")
    assert main(["capture", "--notebook", str(tmp_path / "nb"), "--file", str(f), "--no-split"]) == 0
    nb = Notebook.open(tmp_path / "nb")
    try:
        [row] = nb.store.q("SELECT * FROM notes")
        assert split.can_split(row) is False
    finally:
        nb.close()
    args = build_parser().parse_args(["files", str(f), "--notebook", str(tmp_path / "nb2"), "--no-split"])
    assert args.no_split is True
    from abstractnote.importers import ImportReport, files

    [draft] = list(files.drafts(report=ImportReport(importer="files"), paths=[f], no_split=True))
    assert draft.source["origin"]["no_split"] is True
    [draft] = list(files.drafts(report=ImportReport(importer="files"), paths=[f]))
    assert "no_split" not in draft.source["origin"]


def test_import_files_then_the_drain_splits_and_reads_the_parts_in_history_order(tmp_path):
    """``abstractnote import files`` path: the drain understands the container (split), then its parts, oldest first.
    MUTATION: enqueue the parts at the container's file date (the drain reads them in capture order, not history
    order)."""
    import os

    from abstractnote.importers import run
    from abstractnote.store.notebook import Notebook

    llm, runner = _scripted_flow(tmp_path, FILING)
    files = tmp_path / "files"
    files.mkdir()
    path = files / "Key_Moments.md"
    path.write_text(DIARY, encoding="utf-8")
    os.utime(path, (1773982487, 1773982487))  # 2026-03-20T04:54:47Z, the incident's file date
    nb = Notebook.open(tmp_path / "nb")
    try:
        report = run("files", nb, paths=[path])
        assert report.created == 1
    finally:
        nb.close()
    drained = runner.run_drain(max_items=10, budget_s=60)
    assert drained["processed"] == 4, drained
    reads = [c["prompt"] for c in llm.calls if c["schema"] == "Extraction"]
    order = [next(d for d in ("March 5", "March 12", "March 21") if f"({d}, 2025)" in p.split("About this note")[0])
             for p in reads[1:]]
    assert order == ["March 5", "March 12", "March 21"]  # the parts, in history order, after the container
    nb = Notebook.open(tmp_path / "nb")
    try:
        [container] = nb.notes()["items"]
        assert (container["kind"], container["occurred_at"], container["parts"]) == ("archive", "2025-03-05T00:00:00Z", 3)
    finally:
        nb.close()


def test_a_split_names_parts_that_exist(nb):
    """``note.split`` is refused when a part it names is not a part of the container (a replay never invents one).
    MUTATION: make op_note_split a no-op."""
    r = nb.capture("A document.", occurred_at="2026-10-20T13:10:54Z")
    with pytest.raises(ValueError, match="dated part"):
        nb.apply_revision({"revision_id": "rv_test_split", "note_id": r.note_id, "attempt": 0,
                           "at": "2026-10-20T13:10:54Z", "summary": "split", "warnings": [],
                           "ops": [{"op": "note.split", "payload": {"note_id": r.note_id,
                                                                     "parts": [{"note_id": "nt_missing"}]}}]})


def test_the_dated_entries_of_every_section_are_merged_in_order():
    """MUTATION: drop the moments from merge_extractions (a long diary's later sections lose their entries)."""
    from abstractnote.process.merge import merge_extractions

    a = dict(diary_extraction(MOMENTS[:2]))
    b = dict(diary_extraction(MOMENTS[2:]))
    merged, _ = merge_extractions([a, b])
    assert [(m["heading"], m["section"]) for m in merged["moments"]] == [
        ("Naming and Recognition", 0), ("The First Survey", 0), ("A Change of Plan", 1)]


def test_a_corrupt_dated_entry_is_an_invalid_answer():
    """A moment's heading holding a piece of the answer's JSON is a corrupt answer (ADR 0008 §8).
    MUTATION: drop the moments from extraction_problems."""
    from abstractnote.process.validate import extraction_problems

    ext = diary_extraction([dict(MOMENTS[0], heading='Naming","date":"2025-03-05'), MOMENTS[1]])
    assert any("dated part 1" in p for p in extraction_problems(ext, DIARY))
    assert extraction_problems(diary_extraction(), DIARY) == []


def test_a_long_diary_read_in_two_sections_is_split_across_them(tmp_path):
    """Entries found in the section the model read them in, located in the whole body; through the flow's merge.
    MUTATION: drop the moments from the flow's MERGE_EXTRACTIONS (the second section's entry is lost: no split)."""
    from abstractnote.flows.runner import NoteRunner, ScriptedLLM
    from abstractnote.store.notebook import Notebook
    from abstractnote.tools import bind

    filler = " ".join(["The basin log continues with soundings and tide notes."] * 70)  # ~3.9k chars per entry
    long_diary = DIARY.replace("lead it.\n", "lead it. " + filler + "\n").replace(
        "Tern dredger.\n", "Tern dredger. " + filler + "\n")

    def extract(prompt):
        text = prompt.split("About this note")[0]
        found = [m for m in MOMENTS if m["heading"] in text]
        return diary_extraction(found)

    link = {"mentions": [], "claims": [], "tasks": [], "revision_summary": "Read."}
    llm = ScriptedLLM({"QuickFiling": FILING, "Extraction": extract, "Linking": link})
    runner = NoteRunner.with_llm(llm, bind(tmp_path / "nb", allow_paths_under=tmp_path / "files"))
    files = tmp_path / "files"
    files.mkdir()
    (files / "Key_Moments.md").write_text(long_diary, encoding="utf-8")
    out = runner.run_capture("", idempotency_key="long", source={"client": "agent"}, process_inline=True,
                             context={"attachments": [{"path": "Key_Moments.md", "last_modified": MTIME}]})
    sections = [c for c in llm.calls if c["schema"] == "Extraction"]
    assert len(sections) >= 2
    nb = Notebook.open(tmp_path / "nb")
    try:
        parts = nb.note(out["note_id"])["children"]
        assert [p["title"] for p in parts] == ["Naming and Recognition", "The First Survey", "A Change of Plan"]
        assert filler in nb.note(parts[1]["note_id"])["text"]
    finally:
        nb.close()


def test_a_document_understood_as_one_note_is_not_split_by_a_re_reading(nb):
    """The flow's branch is closed for it (``can_split`` false in its context) and the re-reading names its dated
    parts in a warning. MUTATION: drop the current-interpretation check from the context's can_split (the flow would
    skip the linking of a re-reading)."""
    r = import_diary(nb)
    plain = diary_extraction()
    plain.pop("moments")
    understand(nb, r.note_id, plain)  # understood as one note (as before W30)
    ctx = ToolContext(nb=nb)
    assert note_context(ctx, r.note_id)["note"]["can_split"] is False
    from abstractnote.tools.process import note_resolve_candidates
    from test_process import ml

    ext = diary_extraction()
    note_resolve_candidates(ctx, r.note_id, [{"ref": m["ref"], "name": m["name"], "kind": m["kind"], "aliases": []}
                                             for m in ext["mentions"]])
    linking = {"mentions": [ml(m["ref"], "create", name=m["name"], kind=m["kind"]) for m in ext["mentions"]],
               "claims": [], "tasks": [], "revision_summary": "Read again."}
    out = note_apply_revision(ctx, r.note_id, ext, linking, reinterpret=True)
    assert nb.note(r.note_id)["children"] == []
    assert out["unchanged"] is True and split.NOT_SPLIT_REREAD in out["warnings"]  # said, never silently
