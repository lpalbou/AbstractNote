"""A document of dated moments becomes a container note and one note per moment (W30; docs/data-model.md "Dated
parts").

The extraction says when a text is a sequence of separately dated entries (``moments``: heading, stated date,
basis, the words it begins with); the model decides, no rule reads the text for dates. When the note may be split
(:func:`can_split`: a document dated by a stand-in, its file, or a stated date; never a note typed now, a transcript
record, a person-dated note, a part itself, or a note captured with ``no_split``) and at least
:data:`MIN_MOMENTS` entries are found in its words (:func:`locate_moments`: the quoted opening words, matched
exactly, then with case and spacing ignored), the apply step:

- captures one note per entry (its own text: from its opening words to the next entry's, or to the end of its
  body), dated by the entry's stated date (basis ``stated``; ``stated_month`` / ``stated_year`` for a month or a
  year), ``part_of`` the container, the container's provenance plus ``origin.part_of``, ``part``, ``parts``,
  ``heading``, ``quote``; its id comes from the container id, the entry's index and its date, so the same document
  again gives the same parts (a duplicate). Each is queued and understood as a note of its own, in history order;
- writes the container's one revision: ``note.redate`` to its earliest entry (basis ``stated``, when that beats the
  container's own date evidence, ``dates.PRECEDENCE``), ``note.split`` (the parts it made), ``note.summary`` with
  kind ``archive``. The container is not read for statements itself: its parts are.

:data:`FLOW_FUNCTIONS` are copied into the note-process flow (``flows/build.py`` ``split_code``) so the flow's
"dated parts?" branch and the apply step locate the entries with the very same source.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import ids

#: A document is split when its extraction names at least this many dated entries found in its words.
MIN_MOMENTS = 2
#: The kind of a container note (system-assigned, never chosen by the quick filing).
CONTAINER_KIND = "archive"
#: The title of a part is its heading, at most this long.
TITLE_CHARS = 120
#: Why a document with dated entries is read as one note (warnings; ADR 0007: never silently).
NOT_FOUND = ("The document's dated parts could not all be found in its words; it was read as one note.")
NOT_SPLIT_PART = "This note is one dated part of a document already; its dated entries were read as one note."
NOT_SPLIT_ASKED = "The dated parts of this document were kept in one note, as asked."
NOT_SPLIT_DATED = "This note's date is not a document's; its dated entries were read as one note."
TOO_FEW = "Only one dated part was found in this document; it was read as one note."
NOT_SPLIT_REREAD = ("This note was understood as one note before; its dated parts are not split when it is read"
                    " again.")


# ----------------------------------------------------------------------------- flow functions (no imports)

def _folded(text):
    """``(folded, positions)``: ``text`` lower-cased with every run of whitespace as one space, and for each folded
    character the index of the original character it came from."""
    out = []
    pos = []
    space = False
    for i, ch in enumerate(str(text or "")):
        if ch.isspace():
            if not space and out:
                out.append(" ")
                pos.append(i)
            space = True
            continue
        space = False
        for c in ch.lower():
            out.append(c)
            pos.append(i)
    return "".join(out), pos


def _line_start(text, start):
    """``start`` moved back to the beginning of its line when only markup (spaces, ``#``, ``-``, ``*``, ``>``)
    precedes it there: a part begins with its heading line, the previous part does not end with ``### ``."""
    i = start
    while i > 0 and text[i - 1] in " \t#-*>":
        i -= 1
    if i == 0 or text[i - 1] == "\n":
        return i
    return start


def locate_moments(texts, moments):
    """Where each dated entry begins: ``[(text index, offset) or None]``, in the entries' order. An entry is found
    by its ``text_span`` (else its ``heading``): exactly, then with case and spacing ignored, searching forward from
    the previous entry (entries are in the text's order). Positional matching of the model's own quote; it never
    reads meaning."""
    found = []
    cur_t, cur_o = 0, 0
    bodies = [str(t or "") for t in (texts or [])]
    folded = [None] * len(bodies)
    for mo in moments or []:
        m = mo if isinstance(mo, dict) else {}
        hit = None
        for quote in (str(m.get("text_span") or "").strip(), str(m.get("heading") or "").strip()):
            if not quote:
                continue
            for t in range(cur_t, len(bodies)):
                start = cur_o if t == cur_t else 0
                i = bodies[t].find(quote, start)
                if i < 0:
                    if folded[t] is None:
                        folded[t] = _folded(bodies[t])
                    ftext, fpos = folded[t]
                    fq = _folded(quote)[0].strip()
                    j = -1
                    if fq:
                        fstart = 0
                        while fstart < len(fpos) and fpos[fstart] < start:
                            fstart += 1
                        j = ftext.find(fq, fstart)
                    i = fpos[j] if j >= 0 else -1
                if i >= 0:
                    hit = (t, _line_start(bodies[t], i))
                    break
            if hit is not None:
                break
        found.append(hit)
        if hit is not None:
            cur_t, cur_o = hit[0], hit[1] + 1
    return found


def split_plan(texts, moments, can_split):
    """The flow's and the apply step's decision: ``{split, found, reason}``. ``split`` is true when the note may be
    split and at least :data:`MIN_MOMENTS` entries were given and every one is found in ``texts``."""
    ms = [m for m in (moments or []) if isinstance(m, dict)]
    if not ms:
        return {"split": False, "found": [], "reason": ""}
    found = locate_moments(texts, ms)
    if not can_split:
        return {"split": False, "found": found, "reason": "not_splittable"}
    if len(ms) < MIN_MOMENTS:
        return {"split": False, "found": found, "reason": "too_few"}
    for f in found:  # (the flow's sandbox has no any())
        if f is None:
            return {"split": False, "found": found, "reason": "not_found"}
    return {"split": True, "found": found, "reason": ""}


#: The functions the note-process flow's "dated parts?" node defines (flows/build.py ``split_code``).
FLOW_FUNCTIONS = ("_folded", "_line_start", "locate_moments", "split_plan")


# ----------------------------------------------------------------------------- the apply step

def _source(row: Any) -> Dict[str, Any]:
    import json

    try:
        return json.loads(row["source_json"] or "{}") or {}
    except (TypeError, ValueError):
        return {}


def can_split(row: Any) -> bool:
    """True when a stored note may become a container of dated parts: a document (its date is a stand-in, its
    file's, or one its text states: ``dates.rank`` at most the stated rank), not a part itself, not an insight, not
    captured with ``source.origin.no_split``."""
    from ..ingest.dates import STATED_RANK, date_basis, rank

    if row is None or row["part_of"] or row["insight_kind"]:
        return False
    origin = _source(row).get("origin")
    if isinstance(origin, dict) and origin.get("no_split"):
        return False
    return rank(date_basis(row)) <= STATED_RANK


def not_split_reason(row: Any, reason: str) -> Optional[str]:
    """The warning of a note whose dated entries were read as one note (None: nothing to say)."""
    if reason == "not_found":
        return NOT_FOUND
    if reason == "too_few":
        return TOO_FEW
    if reason == "not_splittable":
        if row["part_of"]:
            return NOT_SPLIT_PART
        origin = _source(row).get("origin")
        if isinstance(origin, dict) and origin.get("no_split"):
            return NOT_SPLIT_ASKED
        return NOT_SPLIT_DATED
    return None


def bodies(nb: Any, note_id: str) -> List[str]:
    """The texts a note's entries are found in: its own text, then each attachment's extracted text."""
    row = nb.store.note_row(note_id)
    out = [str(row["text"] or "")]
    for a in nb.store.attachments_of(note_id):
        path = nb.root / "extracted" / f"{a['sha256']}.txt"
        out.append(path.read_text(encoding="utf-8") if a["extracted_chars"] and path.exists() else "")
    return out


def part_note_id(container_id: str, index: int, at: str) -> str:
    """A part's id: from the container's id, the entry's index and its stated day (the same reading of the same
    document gives the same parts)."""
    return ids.note_id_for_key(part_key(container_id, index, at))


def part_key(container_id: str, index: int, at: str) -> str:
    return f"part:{container_id}:{int(index)}:{str(at)[:10]}"


def is_container(nb: Any, note_id: str) -> bool:
    """True when the note was split into dated parts (it has parts)."""
    return nb.store.q1("SELECT 1 FROM notes WHERE part_of=? LIMIT 1", (note_id,)) is not None


def _moments(ext: Any) -> List[Dict[str, Any]]:
    raw = ext.get("moments") if isinstance(ext, dict) else None
    return [m for m in (raw or []) if isinstance(m, dict)]


def plan(nb: Any, note_id: str, extraction: Any) -> Dict[str, Any]:
    """The split decision for a stored note and its (merged) extraction: ``{split, reason, warning, parts}``;
    ``parts`` (when ``split``) are ``[{index, heading, at, basis, text, quote}]`` in the text's order. An entry whose
    date is not a date, or is in the future, makes the document one note (``not_found``'s sentence names dates)."""
    from ..ingest.dates import CONTENT, STATED, document_date

    row = nb.store.note_row(note_id)
    if row is None:
        raise ValueError("That note is not in this notebook.")
    moments = _moments(extraction)
    out: Dict[str, Any] = {"split": False, "reason": "", "warning": None, "parts": []}
    if not moments:
        return out
    texts = bodies(nb, note_id)
    decided = split_plan(texts, moments, can_split(row))
    if not decided["split"]:
        out.update(reason=decided["reason"], warning=not_split_reason(row, decided["reason"]))
        return out
    now = ids.utc_now()
    dated = []
    for m in moments:
        try:
            at, basis = document_date(str(m.get("date") or ""))
        except ValueError:
            out.update(reason="bad_date", warning=f"A dated part's date {m.get('date')!r} is not a date; the document"
                                                  " was read as one note.")
            return out
        if at > now:
            out.update(reason="bad_date", warning="A dated part's date is in the future; the document was read as one"
                                                  " note.")
            return out
        dated.append((at, STATED if basis == CONTENT else basis))
    found = decided["found"]
    parts = []
    for k, (m, (t, start), (at, basis)) in enumerate(zip(moments, found, dated)):
        nxt = found[k + 1] if k + 1 < len(found) else None
        end = nxt[1] if nxt is not None and nxt[0] == t else len(texts[t])
        text = texts[t][start:end].strip()
        heading = " ".join(str(m.get("heading") or "").split()) or " ".join(text.split())[:TITLE_CHARS]
        parts.append({"index": k, "heading": heading[:TITLE_CHARS], "at": at, "basis": basis, "text": text,
                      "quote": str(m.get("text_span") or "").strip()})
    out.update(split=True, parts=parts)
    return out


def _child_source(container: Any, part: Dict[str, Any], count: int) -> Dict[str, Any]:
    src = dict(_source(container))
    src.pop("redated", None)  # the container's own re-date is not the part's
    origin = dict(src.get("origin") or {}) if isinstance(src.get("origin"), dict) else {}
    origin.pop("pasted", None)
    origin.pop("no_split", None)
    origin.update({"part_of": container["note_id"], "part": part["index"] + 1, "parts": count,
                   "heading": part["heading"], "quote": part["quote"], "date_basis": part["basis"]})
    src["origin"] = origin
    return src


def apply_split(nb: Any, note_id: str, extraction: Any, decided: Dict[str, Any], *,
                run_id: Optional[str] = None) -> Dict[str, Any]:
    """Capture the parts and apply the container's revision (see the module docstring). Returns the apply result
    with ``split: {parts: [{note_id, title, occurred_at, date_basis, status}], created, duplicates}`` and the
    ``date_sentence`` of the container's new date."""
    from ..ingest.capture import capture
    from ..ingest.dates import date_basis, redate_sentence, stated_date_wins
    from ..store.queue import PRIORITY_NORMAL
    from .context import load_packet
    from .revise import REDATE_MIN, _op, used_of, with_retro_counts

    nb.recover()
    container = nb.store.note_row(note_id)
    parts = decided["parts"]
    prio = min(int(container["priority"]), PRIORITY_NORMAL)
    made = []
    for part in parts:
        res = capture(nb, part["text"], title=part["heading"], occurred_at=part["at"],
                      source=_child_source(container, part, len(parts)), priority=prio,
                      idempotency_key=part_key(note_id, part["index"], part["at"]), part_of=note_id,
                      default_client=container["client"] or "importer",
                      vision=nb.extractors.get("vision"), stt=nb.extractors.get("stt"))
        made.append({"note_id": res.note_id, "title": part["heading"], "occurred_at": res.occurred_at,
                     "date_basis": res.date_basis, "status": res.status})
    earliest = min(parts, key=lambda p: p["at"])
    basis = date_basis(container)
    ops: List[Dict[str, Any]] = []
    at = container["occurred_at"]
    if stated_date_wins(basis) and abs(ids.parse_time(earliest["at"]) - ids.parse_time(at)) > REDATE_MIN:
        ops.append(_op("note.redate", note_id=note_id, **{"from": at}, from_basis=basis, to=earliest["at"],
                       basis=earliest["basis"], quote=earliest["quote"] or earliest["heading"]))
        at = earliest["at"]
    ops.append(_op("note.split", note_id=note_id, parts=[
        {"note_id": m["note_id"], "at": p["at"], "basis": p["basis"], "heading": p["heading"]}
        for m, p in zip(made, parts)]))
    summary_text = str((extraction or {}).get("summary") or "").strip() if isinstance(extraction, dict) else ""
    ops.append(_op("note.summary", note_id=note_id, summary=summary_text or None, kind=CONTAINER_KIND,
                   language=None))
    days = sorted(p["at"] for p in parts)
    from ..ingest.dates import long_day

    sentence = (f"A document of {len(parts)} dated parts, from {long_day(days[0])} to {long_day(days[-1])}; each is"
                " read as a note of its own.")
    attempt = nb.queue.attempt_for(note_id)
    while True:
        r = nb.store.revision_row(ids.revision_id_for_note(note_id, attempt))
        if r is None or not r["abandoned"]:
            break
        attempt += 1
    digest = (load_packet(nb, note_id) or {}).get("digest")
    warnings = [str(w) for w in ((extraction or {}).get("warnings") or []) if isinstance(w, str) and w.strip()] \
        if isinstance(extraction, dict) else []
    rev = {"revision_id": ids.revision_id_for_note(note_id, attempt), "note_id": note_id, "attempt": attempt,
           "at": at, "run_id": run_id, "ops": ops, "summary": sentence, "warnings": warnings, "context": digest,
           "used": used_of(ops), "stale_context": []}
    out = with_retro_counts(nb, nb.apply_revision(rev))
    dated = redate_sentence(ops)
    if dated:
        out["date_sentence"] = dated
    out["split"] = {"parts": made, "created": sum(1 for m in made if m["status"] == "new"),
                    "duplicates": sum(1 for m in made if m["status"] == "duplicate")}
    return out


__all__ = ["CONTAINER_KIND", "FLOW_FUNCTIONS", "MIN_MOMENTS", "apply_split", "bodies", "can_split", "is_container",
           "locate_moments", "part_key", "part_note_id", "plan", "split_plan"]
