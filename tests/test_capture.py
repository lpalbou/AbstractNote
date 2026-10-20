"""Capture: ids, duplicates vs revisits, blobs once per content, extraction warnings, paths and artifacts."""

from __future__ import annotations

import pytest

from abstractnote import ids
from abstractnote.ingest.normalise import sections, text_norm
from abstractnote.tools.capture import note_capture, note_file
from abstractnote.tools.context import ToolContext
from test_store_support import nb  # noqa: F401


def test_repeated_idempotency_key_is_a_duplicate_and_writes_nothing(nb):
    a = nb.capture("Call Dana about the Pareto", idempotency_key="k-1", occurred_at="2026-10-19T09:12:00Z",
                   source={"client": "watch"})
    assert a.status == "new" and a.note_id == ids.note_id_for_key("k-1")
    before = nb.store.counts()
    b = nb.capture("Call Dana about the Pareto", idempotency_key="k-1", occurred_at="2026-10-19T09:12:00Z",
                   source={"client": "watch"})
    assert b.status == "duplicate" and b.duplicate_of == a.note_id and b.first_saved_at == a.received_at
    assert nb.store.counts() == before
    assert nb.queue.stats()["queued"] == 1


def test_same_words_on_another_day_are_a_new_note_that_revisits(nb):
    a = nb.capture("Call Dana", occurred_at="2026-10-19T09:12:00Z", source={"client": "watch"})
    b = nb.capture("call   DANA", occurred_at="2026-10-21T18:00:00Z", source={"client": "watch"},
                   location={"lat": 48.85, "lon": 2.35, "label": "Paris"})
    assert b.status == "new" and b.note_id != a.note_id
    assert [r["note_id"] for r in b.revisits] == [a.note_id]
    assert b.revisits[0]["matched_by"] == "same_words"
    note = nb.note(b.note_id)
    assert note["location"]["label"] == "Paris"
    assert [r["note_id"] for r in note["revisits"]] == [a.note_id]
    assert [r["note_id"] for r in nb.note(a.note_id)["revisits"]] == [b.note_id]
    assert set(note["revisits"][0]) == {"note_id", "title", "kind", "occurred_at", "excerpt"}


def test_no_key_same_minute_same_client_is_a_retry(nb):
    a = nb.capture("Buy milk", occurred_at="2026-10-19T09:12:05Z", source={"client": "web"})
    b = nb.capture("Buy milk", occurred_at="2026-10-19T09:12:40Z", source={"client": "web"})
    assert b.status == "duplicate" and b.note_id == a.note_id


def test_import_ids_come_from_the_source_key(nb):
    a = nb.capture("hello", importer="agora", source_key="msg-1", priority=0, occurred_at="2024-01-01T00:00:00Z")
    assert a.note_id == ids.note_id_for_import("agora", "msg-1")
    assert nb.note(a.note_id)["source"]["client"] == "importer"
    assert nb.capture("hello", importer="agora", source_key="msg-1").status == "duplicate"
    assert nb.queue.item(a.note_id)["priority"] == 0


def test_near_duplicate_links_regardless_of_kind(nb):
    a = nb.capture("The LUT budget is forty thousand euros for the prefill work this quarter",
                   occurred_at="2026-01-01T00:00:00Z")
    nb.file_note(a.note_id, kind="decision", title="LUT budget", topics=["#LUT"], urgency="normal")
    b = nb.capture("The LUT budget is forty thousand euros for the prefill work this quarter!",
                   occurred_at="2026-03-01T00:00:00Z")
    nb.file_note(b.note_id, kind="reflection")
    assert any(r["note_id"] == a.note_id and r["matched_by"] in ("same_words", "similar") for r in b.revisits)
    c = nb.capture("The LUT budget is forty thousand euros for the prefill work this quarter, Dana agreed",
                   occurred_at="2026-04-01T00:00:00Z")
    hit = [r for r in c.revisits if r["note_id"] == a.note_id]
    assert hit and hit[0]["matched_by"] == "similar" and hit[0]["score"] >= 0.92
    assert c.similar and c.similar[0]["score"] >= 0.92


def test_same_file_twice_stores_one_blob_and_links(nb, tmp_path):
    data = b"%PDF-fake but treated as bytes"
    a = nb.capture("first", attachments=[{"data": b"hello world", "filename": "a.txt"}])
    b = nb.capture("second", attachments=[{"data": b"hello world", "filename": "copy.txt"}])
    blobs = [p for p in (nb.root / "blobs").rglob("*") if p.is_file()]
    assert len(blobs) == 1
    note_b = nb.note(b.note_id)
    [link] = [x for x in nb.note_links(b.note_id)["outgoing"] if x["predicate"] == "same_bytes_as"]
    assert link["note_id"] == a.note_id and link["matched_by"] == "sha256"
    assert note_b["attachments"][0]["extracted_by"] == "plain"
    assert note_b["attachments"][0]["extracted_text"] == "hello world"
    got = nb.attachment_bytes(b.note_id, note_b["attachments"][0]["attachment_id"])
    assert got["data"] == b"hello world"
    del data


def test_unknown_attachment_type_is_kept_with_a_warning(nb):
    r = nb.capture("", attachments=[{"data": b"\x00\x01binary", "filename": "x.bin", "mime": "application/x-thing"}])
    assert r.status == "new" and r.attachments[0]["extracted_by"] == "none"
    assert r.warnings and "Kept x.bin" in r.warnings[0]
    img = nb.capture("photo", attachments=[{"data": b"\x89PNG..", "filename": "p.png"}])
    assert img.attachments[0]["extracted_by"] == "none" and "image model" in img.warnings[0]
    rec = nb.capture("memo", attachments=[{"data": b"ID3....", "filename": "memo.mp3"}])
    assert rec.attachments[0]["extracted_by"] == "media-metadata" and "speech model" in rec.warnings[0]


def test_injected_vision_and_stt_extractors(nb):
    nb.extractors["vision"] = lambda data, mime, name: ("A whiteboard with the LUT plan", "qwen-vl")
    nb.extractors["stt"] = lambda data, mime, name: ("Remember to call Dana", "whisper-small")
    r = nb.capture("", attachments=[{"data": b"img", "filename": "board.jpg"}, {"data": b"aud", "filename": "m.m4a"}])
    by = [a["extracted_by"] for a in r.attachments]
    assert by == ["vision:qwen-vl", "stt:whisper-small"]
    assert nb.notes(q="whiteboard")["items"][0]["note_id"] == r.note_id


def test_markdown_5mb_note_is_stored_whole_and_sectioned(nb):
    para = "## Heading\n\n" + ("A sentence about the prefill plan. " * 40 + "\n\n") * 5
    text = para * 1100
    assert len(text) > 5_000_000
    r = nb.capture(text, title="Big", attachments=[{"data": text.encode(), "filename": "big.md"}])
    stored = nb.store.note_row(r.note_id)
    assert stored["text"] == text
    assert r.attachments[0]["extracted_by"] == "markdown" and r.attachments[0]["extracted_chars"] == len(text)
    secs = sections(text, 12000)
    assert "".join(s["text"] for s in secs) == text
    assert all(s["chars"] <= 12000 for s in secs) and len(secs) > 400


def test_text_norm_and_sections_cover_text():
    assert text_norm("  Hello\n\tWORLD ") == "hello world"
    text = "x" * 30000
    secs = sections(text, 1000)
    assert "".join(s["text"] for s in secs) == text and max(s["chars"] for s in secs) <= 1000


def test_note_file_sets_kind_topics_and_priority(nb):
    r = nb.capture("Pareto due Friday", occurred_at="2026-10-19T09:00:00Z")
    out = nb.file_note(r.note_id, title="Pareto", kind="task", topics=["#LUT", "Release work", "lut"],
                       urgency="urgent", language="en", one_line="Send the Pareto by Friday.")
    assert out == {"note_id": r.note_id, "priority": 3, "status": "queued", "topics_not_assigned": []}
    note = nb.note(r.note_id)
    assert note["topics"] == ["lut", "release_work"] and note["kind"] == "task"  # canonical topic names (eval2 D13)
    assert nb.queue.item(r.note_id)["priority"] == 3
    explicit = nb.capture("bulk import", priority=0)
    assert nb.file_note(explicit.note_id, urgency="urgent")["priority"] == 0
    with pytest.raises(ValueError):
        nb.file_note(r.note_id, kind="insight")
    with pytest.raises(ValueError):
        nb.file_note("nt_missing", kind="idea")


def test_tool_paths_need_an_allowed_root(nb, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("password")
    ctx = ToolContext(nb=nb)
    with pytest.raises(ValueError, match="not accepted"):
        note_capture(ctx, "x", attachments=[{"path": str(secret)}])
    with pytest.raises(ValueError, match="not accepted"):
        note_capture(ctx, "x", attachments=[{"path": "/etc/hosts"}])
    inside = tmp_path / "allowed"
    inside.mkdir()
    (inside / "ok.txt").write_text("fine")
    ctx2 = ToolContext(nb=nb, allow_paths_under=inside)
    with pytest.raises(ValueError, match="outside"):
        note_capture(ctx2, "x", attachments=[{"path": str(secret)}])
    with pytest.raises(ValueError, match="outside"):
        note_capture(ctx2, "x", attachments=[{"path": str(inside / ".." / "secret.txt")}])
    out = note_capture(ctx2, "x", attachments=[{"path": str(inside / "ok.txt")}])
    assert out["attachments"][0]["extracted_by"] == "plain"


def test_tool_artifacts_need_a_reader_and_stamp_the_account(nb):
    ctx = ToolContext(nb=nb, account="user:42")
    with pytest.raises(ValueError, match="artifact reader"):
        note_capture(ctx, "x", attachments=[{"$artifact": "art-1"}])
    seen = []

    def reader(ref):
        seen.append(ref)
        return b"artifact text", "text/plain", "notes.txt"

    ctx = ToolContext(nb=nb, artifact_reader=reader, account="user:42")
    out = note_capture(ctx, "with file", attachments=[{"$artifact": "art-1", "run_id": "run-9"}],
                       source={"client": "web", "account": "someone-else"}, session_id="s-1", idempotency_key="u1")
    assert seen == [{"$artifact": "art-1", "run_id": "run-9"}]
    note = nb.note(out["note_id"])
    assert note["source"]["account"] == "user:42" and nb.store.note_row(out["note_id"])["session_id"] == "s-1"
    assert note["attachments"][0]["artifact_ref"] == {"artifact_id": "art-1", "run_id": "run-9"}
    filed = note_file(ctx, out["note_id"], kind="reference")
    assert filed["status"] == "queued"


def test_capture_validates_input(nb):
    with pytest.raises(ValueError):
        nb.capture("   ")
    with pytest.raises(ValueError):
        nb.capture("x", occurred_at="yesterday")
    with pytest.raises(ValueError):
        nb.capture("x", source={"client": "fax"})
    with pytest.raises(ValueError):
        nb.capture("x", priority=7)
    with pytest.raises(ValueError):
        nb.capture("x", location={"lat": "north"})
