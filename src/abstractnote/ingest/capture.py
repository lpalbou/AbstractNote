"""Capture and quick filing (docs/data-model.md "Identity and de-duplication").

``capture`` stores a note durably and immediately: normalise, hash, choose the
note id (import source key, idempotency key, or the fallback hash), store the
attachment bytes once per content, extract their text, embed, find revisits
and similar notes, and enqueue. A repeated key returns ``duplicate`` and writes
nothing. ``file_note`` stores the quick-filing answer.

A live capture is dated by :func:`abstractnote.ingest.dates.capture_date` (ADR 0008 §7) and answers its
``occurred_at``, ``date_basis`` and, when the date came from a file, the sentence that says so. A note with no
words of its own whose files give no readable text is refused here, before anything is written
(:data:`NO_READABLE_TEXT`), never failed later by processing. ``inline=True`` (note-capture with
``process_inline``) claims the queue row for the capture's own understanding in the same transaction as the
enqueue, so no drain leases it meanwhile (``Queue.claim``).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic import ValidationError

from .. import ids
from ..model import FILING_KINDS, URGENCY_PRIORITY, CaptureResult, Location, Provenance
from ..store.notes import _j, note_vector_text
from ..store.queue import PRIORITY_NORMAL
from .blobs import BlobStore
from .dates import capture_date, date_basis, date_sentence, detect_file_date
from .extract import extract, guess_mime
from .normalise import normalize_topic, text_norm

NEAR_DUPLICATE_COSINE = 0.92  # data-model.md: near-duplicate notes are linked with ex:revisits
SIMILAR_MIN_COSINE = 0.35     # floor for the "similar notes" list returned to the filing step
SIMILAR_LIMIT = 5
MAX_FILING_TOPICS = 4
#: The refusal of a note that has nothing to read (no words, no title, and no text in any of its files).
NO_READABLE_TEXT = "The file “{name}” has no readable text, so the note was not saved; add a few words about it and send it again."
NO_READABLE_TEXT_MANY = "None of these files has readable text, so the note was not saved; add a few words about them and send them again."

_log = logging.getLogger("abstractnote.capture")


def _source(source: Optional[Dict[str, Any]], default_client: str, account: Optional[str]) -> Dict[str, Any]:
    data = dict(source or {})
    data.setdefault("client", default_client)
    if account is not None:
        data["account"] = account
    try:
        return Provenance(**data).model_dump(exclude_none=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ())) or "source"
        raise ValueError(f"The source is not valid ({loc}: {first.get('msg')}).") from None


ABOUT_KINDS = ("insight", "task", "cue", "entity", "note")


def _about(nb: Any, about: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The subject a note answers or comments (``context.about``): ``{kind, id, title?}``, checked to exist."""
    if not about:
        return None
    if not isinstance(about, dict) or about.get("kind") not in ABOUT_KINDS or not str(about.get("id") or "").strip():
        raise ValueError("The subject of a note needs a kind (insight, task, cue, entity or note) and an id.")
    kind, ref = about["kind"], str(about["id"]).strip()
    if kind == "task":
        found = nb.store.q1("SELECT 1 FROM tasks WHERE task_id=?", (ref,))
    elif kind == "entity":
        found = nb.store.entity_row(ref)
    else:
        found = nb.store.note_row(ref)
    if found is None:
        raise ValueError("The subject this note refers to is not in this notebook.")
    out = {"kind": kind, "id": ref}
    if about.get("title"):
        out["title"] = str(about["title"])
    return out


def _location(location: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if location is None:
        return None
    try:
        return Location(**location).model_dump(exclude_none=True)
    except (ValidationError, TypeError):
        raise ValueError("The location needs numeric lat and lon (and optionally accuracy_m and label).") from None


def capture(nb: Any, text: str = "", *, title: Optional[str] = None, occurred_at: Optional[str] = None,
            location: Optional[Dict[str, Any]] = None, source: Optional[Dict[str, Any]] = None,
            priority: Optional[int] = None, attachments: Optional[Sequence[Dict[str, Any]]] = None,
            idempotency_key: Optional[str] = None, session_id: Optional[str] = None, run_id: Optional[str] = None,
            importer: Optional[str] = None, source_key: Optional[str] = None, account: Optional[str] = None,
            about: Optional[Dict[str, Any]] = None,
            default_client: str = "cli", vision: Any = None, stt: Any = None, now: Optional[str] = None,
            inline: bool = False, inline_run_id: Optional[str] = None,
            ) -> CaptureResult:
    """Store one note. ``attachments`` are resolved inputs: ``{"data": bytes, "mime"?, "filename"?, "artifact_ref"?,
    "last_modified"?}`` (``last_modified``: the file's modification time as the client knows it, ISO 8601 or epoch
    milliseconds; the web sends ``File.lastModified``). ``source.origin.pasted`` (a live client's flag that the text
    was pasted) makes the client's time a stand-in the text may correct (basis ``pasted_text``)."""
    text = "" if text is None else str(text)
    atts = list(attachments or [])
    if not text.strip() and not atts:
        raise ValueError("A note needs some text or at least one attachment.")
    if priority is not None and int(priority) not in (0, 1, 2, 3):
        raise ValueError("The priority must be 0 (bulk), 1 (normal), 2 (high) or 3 (urgent).")
    if (importer is None) != (source_key is None):
        raise ValueError("An import needs both the importer name and the source key.")
    nb.require_embedder()  # before anything is written
    now = now or ids.utc_now()
    occurred = ids.normalize_time(occurred_at, field="occurred_at") if occurred_at else now
    src = _source(source, "importer" if importer else default_client, account)
    if importer:
        src["importer"] = importer
    origin = src.get("origin") if isinstance(src.get("origin"), dict) else {}
    if importer or origin.get("date_basis"):
        if not occurred_at and atts:
            # An import that gives no date: the first attachment that carries one in its own metadata dates it
            # (EXIF, XMP, PDF or Office creation date; the same detect_file_date as ``abstractnote import files``).
            for a in atts:
                found = detect_file_date(a["data"], a.get("mime"), a.get("filename"))
                if found:
                    occurred = found[0]
                    src.setdefault("origin", {})["date_basis"] = found[1]
                    break
    else:
        # A live capture (ADR 0008 §7): typed now = the client's time; an upload or a paste = a stand-in the
        # document's own date may correct; without a caller's time, the file's date, else the capture moment.
        occurred, basis = capture_date(ids.normalize_time(occurred_at, field="occurred_at") if occurred_at else None,
                                       atts, pasted=bool(origin.get("pasted")), now=now, client=src.get("client"))
        if basis != "client":
            src.setdefault("origin", {})["date_basis"] = basis
    loc = _location(location)
    about_ref = _about(nb, about)
    norm = text_norm(text)
    blobs = BlobStore(nb.root / "blobs")
    hashes = [ids.sha256_bytes(a["data"]) for a in atts]

    if importer:
        note_id = ids.note_id_for_import(importer, source_key)
    elif idempotency_key:
        note_id = ids.note_id_for_key(str(idempotency_key))
    else:
        note_id = ids.note_id_fallback(norm, hashes, occurred, src["client"])

    existing = nb.store.note_row(note_id)
    if existing is not None:
        return CaptureResult(
            note_id=note_id, status="duplicate", duplicate_of=note_id, first_saved_at=existing["received_at"],
            received_at=existing["received_at"], topics=nb.store.topic_names(),
            attachments=[_att_out(a) for a in nb.store.attachments_of(note_id)],
            occurred_at=existing["occurred_at"], date_basis=date_basis(existing),
        )

    warnings: List[str] = []
    att_rows: List[Dict[str, Any]] = []
    same_bytes: List[Dict[str, Any]] = []
    extracted_texts: List[str] = []
    # Read every file first: a note with nothing to read is refused before anything is written.
    readings = []
    for a, sha in zip(atts, hashes):
        mime = guess_mime(a.get("filename"), a.get("mime"))
        out_path = nb.root / "extracted" / f"{sha}.txt"
        if out_path.exists():
            prev = nb.store.q1("SELECT extracted_by FROM attachments WHERE sha256=? LIMIT 1", (sha,))
            readings.append((mime, out_path.read_text(encoding="utf-8"), prev[0] if prev else "plain", [], False))
        else:
            res = extract(a["data"], mime, a.get("filename"), vision=vision, stt=stt)
            readings.append((mime, res.text, res.extracted_by, list(res.warnings), True))
    if atts and not text.strip() and not (title or "").strip() and not any(r[1].strip() for r in readings):
        if len(atts) == 1:
            raise ValueError(NO_READABLE_TEXT.format(name=atts[0].get("filename") or "file"))
        raise ValueError(NO_READABLE_TEXT_MANY)
    for pos, (a, (mime, ext_text, ext_by, ext_warnings, fresh)) in enumerate(zip(atts, readings)):
        data: bytes = a["data"]
        filename = a.get("filename")
        sha, rel, present = blobs.put(data)
        aid = ids.attachment_id(note_id, sha)
        if present:
            for r in nb.store.q("SELECT attachment_id, note_id FROM attachments WHERE sha256=? AND note_id!=?",
                                (sha, note_id)):
                same_bytes.append({"note_id": r["note_id"], "attachment_id": r["attachment_id"], "this": aid})
        warnings.extend(ext_warnings)
        if fresh and ext_text:
            (nb.root / "extracted" / f"{sha}.txt").write_text(ext_text, encoding="utf-8")
        if ext_text:
            extracted_texts.append(ext_text)
        att_rows.append({
            "attachment_id": aid, "note_id": note_id, "sha256": sha, "size": len(data), "mime": mime,
            "filename": filename, "artifact_ref_json": _j(a["artifact_ref"]) if a.get("artifact_ref") else None,
            "blob_path": rel, "extracted_by": ext_by, "extracted_chars": len(ext_text), "created_at": now,
            "position": pos,
        })

    from ..store.notebook import NOTE_EMBED_BUDGET

    vector_text = note_vector_text(title, text, extracted_texts, NOTE_EMBED_BUDGET)
    vkey = None
    vector = None
    if vector_text.strip():
        [vkey] = nb.cache.ensure([vector_text])
        vector = nb.cache.get(vkey)
        nb._sync_pin()

    revisits: List[Dict[str, Any]] = []
    linked = set()
    text_hash = ids.sha256_text(norm) if norm else None
    if text_hash:
        for r in nb.store.q("SELECT note_id, occurred_at FROM notes WHERE text_hash=? AND note_id!=?"
                            " AND insight_kind IS NULL ORDER BY occurred_at", (text_hash, note_id)):
            revisits.append({"note_id": r["note_id"], "occurred_at": r["occurred_at"], "matched_by": "same_words"})
            linked.add(r["note_id"])
    similar: List[Dict[str, Any]] = []
    if vector is not None:
        hits = nb.note_vectors.nearest(vector, 50, SIMILAR_MIN_COSINE, exclude=[note_id], notes_only=True)
        info = {}
        if hits:
            marks = ",".join("?" for _ in hits)
            info = {r["note_id"]: r for r in nb.store.q(
                f"SELECT note_id, title, occurred_at FROM notes WHERE note_id IN ({marks})", [h[0] for h in hits])}
        for nid, score in hits:
            r = info[nid]
            if score >= NEAR_DUPLICATE_COSINE and nid not in linked:
                revisits.append({"note_id": nid, "occurred_at": r["occurred_at"], "matched_by": "similar",
                                 "score": round(score, 4)})
                linked.add(nid)
            if len(similar) < SIMILAR_LIMIT:
                similar.append({"note_id": nid, "title": r["title"], "occurred_at": r["occurred_at"],
                                "score": round(score, 4)})

    prio = int(priority) if priority is not None else PRIORITY_NORMAL
    size = len(text) + sum(len(t) for t in extracted_texts)
    with nb.store.tx() as c:
        nb.store.insert_note(c, {
            "note_id": note_id, "kind": None, "title": title, "text": text, "text_norm": norm,
            "text_hash": text_hash, "occurred_at": occurred, "received_at": now, "source_json": _j(src),
            "client": src.get("client"), "location_json": _j(loc) if loc else None, "priority": prio,
            "priority_explicit": 1 if priority is not None else 0, "status": "received",
            "session_id": session_id, "run_id": run_id, "idempotency_key": idempotency_key, "size": size,
            "vector_key": vkey, "about_json": _j(about_ref) if about_ref else None,
        })
        if about_ref:
            c.execute("INSERT OR IGNORE INTO note_links(src_note_id, dst_note_id, predicate, score, matched_by,"
                      " detail_json, created_at) VALUES (?,?,?,1.0,?,?,?)",
                      (note_id, about_ref["id"], "dcterms:references", about_ref["kind"], _j(about_ref), now))
        for row in att_rows:
            cols = ",".join(row)
            c.execute(f"INSERT OR IGNORE INTO attachments({cols}) VALUES ({','.join('?' for _ in row)})",
                      list(row.values()))
        for rv in revisits:
            c.execute("INSERT OR IGNORE INTO note_links(src_note_id, dst_note_id, predicate, score, matched_by,"
                      " detail_json, created_at) VALUES (?,?,?,?,?,?,?)",
                      (note_id, rv["note_id"], "ex:revisits", rv.get("score", 1.0), rv["matched_by"], None, now))
        for sb in same_bytes:
            c.execute("INSERT OR IGNORE INTO note_links(src_note_id, dst_note_id, predicate, score, matched_by,"
                      " detail_json, created_at) VALUES (?,?,?,1.0,'sha256',?,?)",
                      (note_id, sb["note_id"], "same_bytes_as",
                       _j({"attachment_id": sb["this"], "earlier_attachment_id": sb["attachment_id"]}), now))
        nb.store.refresh_note_fts(c, note_id, extracted_texts)
        nb.queue.enqueue(c, note_id, prio, occurred, size, now=now)
        if inline:
            # Claimed for this capture's own understanding in the enqueue's transaction (ADR 0008 §8): no drain
            # leases it between the enqueue and the inline note-process (B's S5: one understanding per note).
            nb.queue.claim(note_id, "inline", inline_run_id, conn=c, now=now)

    basis = date_basis({"source_json": src})
    return CaptureResult(
        note_id=note_id, status="new", received_at=now, revisits=revisits, similar=similar,
        topics=nb.store.topic_names(),
        attachments=[_att_out({**r, "artifact_ref": None}) for r in att_rows], warnings=warnings,
        occurred_at=occurred, date_basis=basis, date_sentence=date_sentence(occurred, basis),
    )


def _att_out(a: Dict[str, Any]) -> Dict[str, Any]:
    return {"attachment_id": a["attachment_id"], "sha256": a["sha256"], "mime": a["mime"], "size": a["size"],
            "filename": a.get("filename"), "extracted_by": a["extracted_by"],
            "extracted_chars": a["extracted_chars"]}


def file_note(nb: Any, note_id: str, *, title: Optional[str] = None, kind: Optional[str] = None,
              topics: Optional[Sequence[str]] = None, urgency: Optional[str] = None, language: Optional[str] = None,
              one_line: Optional[str] = None) -> Dict[str, Any]:
    """Store the quick-filing answer; maps urgency to queue priority unless the capture set one."""
    row = nb.store.note_row(note_id)
    if row is None:
        raise ValueError("That note is not in this notebook.")
    if kind is not None and kind not in FILING_KINDS:
        raise ValueError(f"The kind must be one of {', '.join(FILING_KINDS)}.")
    if urgency is not None and urgency not in URGENCY_PRIORITY:
        raise ValueError("The urgency must be bulk, normal, high or urgent.")
    clean_topics: List[str] = []
    for tpc in topics or []:
        t = normalize_topic(tpc)
        if t and t not in clean_topics:
            clean_topics.append(t)
    # a selection (the filing model lists the main topics first), recorded (ADR 0007): the topics beyond
    # MAX_FILING_TOPICS are named in the answer (topics_not_assigned) and logged, never dropped silently
    not_assigned = clean_topics[MAX_FILING_TOPICS:]
    clean_topics = clean_topics[:MAX_FILING_TOPICS]
    if not_assigned:
        _log.warning("file_note %s: a note keeps %d filing topics; not assigned: %s", note_id, MAX_FILING_TOPICS,
                     ", ".join(not_assigned))
    priority = row["priority"]
    if urgency is not None and not row["priority_explicit"]:
        priority = URGENCY_PRIORITY[urgency]
    with nb.store.tx() as c:
        c.execute(
            "UPDATE notes SET title=COALESCE(title, ?), kind=COALESCE(?, kind), language=COALESCE(?, language),"
            " one_line=COALESCE(?, one_line), filing_topics_json=?, priority=? WHERE note_id=?",
            ((title or "").strip() or None, kind, language, one_line, json.dumps(clean_topics), priority,
             note_id))
        for t in clean_topics:
            nb.store.assign_topic(c, note_id, t, by="filing", revision_id=None, at=row["occurred_at"])
        nb.queue.set_priority(c, note_id, priority)
        nb.store.refresh_note_fts(c, note_id)
    status = nb.store.note_row(note_id)["status"]
    return {"note_id": note_id, "priority": priority, "status": status, "topics_not_assigned": not_assigned}
