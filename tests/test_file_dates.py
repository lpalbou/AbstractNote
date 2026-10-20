"""detect_file_date (W23): the date a file carries in its own metadata, shared by ``import files`` and capture."""

from __future__ import annotations

import io
import os
import zipfile

import pytest

from abstractnote.importers import ImportReport
from abstractnote.importers import files as files_importer
from abstractnote.ingest.dates import date_basis, detect_file_date, is_approximate
from test_store_support import nb  # noqa: F401

PDF = (b"%PDF-1.4\n1 0 obj\n<< /Title (A letter) /CreationDate (D:20190405103000+02'00') >>\nendobj\n"
       b"trailer\n<< /Info 1 0 R >>\n%%EOF\n")
PDF_XMP = (b"%PDF-1.7\n<x:xmpmeta><rdf:Description xmp:CreateDate=\"2018-07-01T08:00:00Z\"/></x:xmpmeta>\n%%EOF\n")
PNG_XMP = (b"\x89PNG\r\n\x1a\n...iTXtXML:com.adobe.xmp...<x:xmpmeta><rdf:RDF><rdf:Description>"
           b"<photoshop:DateCreated>2017-02-03T04:05:06+01:00</photoshop:DateCreated></rdf:Description>"
           b"</rdf:RDF></x:xmpmeta>")


def _docx(created: str = "2016-11-12T13:14:15Z") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("docProps/core.xml",
                   '<cp:coreProperties xmlns:dcterms="http://purl.org/dc/terms/">'
                   f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created></cp:coreProperties>')
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def _jpeg_with_exif() -> bytes:
    PIL = pytest.importorskip("PIL.Image")
    img = PIL.new("RGB", (4, 4), (200, 10, 10))
    exif = img.getexif()
    exif[306] = "2015:06:07 08:09:10"  # DateTime
    exif.get_ifd(0x8769)[36867] = "2014:05:06 07:08:09"  # DateTimeOriginal wins
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def test_metadata_dates_by_kind():
    assert detect_file_date(PDF, "application/pdf") == ("2019-04-05T08:30:00Z", "pdf_created")
    assert detect_file_date(PDF_XMP, None, "scan.pdf") == ("2018-07-01T08:00:00Z", "xmp")
    assert detect_file_date(PNG_XMP, "image/png") == ("2017-02-03T03:05:06Z", "xmp")
    assert detect_file_date(_docx(), None, "report.docx") == ("2016-11-12T13:14:15Z", "office_created")
    assert detect_file_date(_jpeg_with_exif(), "image/jpeg") == ("2014-05-06T07:08:09Z", "exif")
    assert detect_file_date(b"just text", "text/plain") is None  # bytes: no file date to fall back on


def test_a_path_falls_back_to_its_modification_time(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("hello")
    os.utime(p, (1_600_000_000, 1_600_000_000))
    assert detect_file_date(p) == ("2020-09-13T12:26:40Z", "file_mtime")
    q = tmp_path / "letter.pdf"
    q.write_bytes(PDF)
    assert detect_file_date(q) == ("2019-04-05T08:30:00Z", "pdf_created")  # metadata before the file date


def test_the_files_importer_uses_the_shared_detector(tmp_path):
    (tmp_path / "letter.pdf").write_bytes(PDF)
    (tmp_path / "report.docx").write_bytes(_docx())
    (tmp_path / "photo.jpg").write_bytes(_jpeg_with_exif())
    plain = tmp_path / "plain.txt"
    plain.write_text("x")
    os.utime(plain, (1_600_000_000, 1_600_000_000))
    report = ImportReport("files")
    got = {d.title: (d.occurred_at, d.source["origin"]["date_basis"])
           for d in files_importer.drafts(report=report, paths=[tmp_path])}
    assert got == {"letter.pdf": ("2019-04-05T08:30:00Z", "pdf_created"),
                   "report.docx": ("2016-11-12T13:14:15Z", "office_created"),
                   "photo.jpg": ("2014-05-06T07:08:09Z", "exif"),
                   "plain.txt": ("2020-09-13T12:26:40Z", "file_mtime")}


def test_capture_dates_an_undated_note_from_its_attachment(nb):
    r = nb.capture("the letter", attachments=[{"data": PDF, "mime": "application/pdf", "filename": "letter.pdf"}])
    note = nb.note(r.note_id)
    assert note["occurred_at"] == "2019-04-05T08:30:00Z"
    assert (note["date_basis"], note["date_approximate"]) == ("pdf_created", False)
    assert r.date_sentence == "Dated 5 April 2019 from the document."
    # a date the client gives always wins; on an upload it is the moment of the upload (ADR 0008 §7): a stand-in
    # the document's own stated date may correct
    r2 = nb.capture("see attached", occurred_at="2026-10-20T10:00:00Z",
                    attachments=[{"data": _docx(), "filename": "report.docx"}])
    n2 = nb.note(r2.note_id)
    assert (n2["occurred_at"], n2["date_basis"], n2["date_approximate"]) == ("2026-10-20T10:00:00Z", "upload", True)


def test_date_basis_of_stored_notes():
    imported = {"source_json": '{"client": "importer", "importer": "files", "origin": {"date_basis": "git_added"}}'}
    assert date_basis(imported) == "git_added" and is_approximate("git_added")
    assert date_basis({"source_json": '{"client": "importer", "importer": "agora"}'}) == "unrecorded"
    assert is_approximate("unrecorded") and not is_approximate("client") and not is_approximate("content")
    redated = {"source_json": '{"client": "importer", "origin": {"date_basis": "file_mtime"},'
                              ' "redated": {"basis": "content"}}'}
    assert date_basis(redated) == "content"
