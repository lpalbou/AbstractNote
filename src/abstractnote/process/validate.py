"""Structural validity of a model's extraction and linking answers (ADR 0008 §8; docs/cognition.md "Validity").

A structured answer can parse and still be corrupt: the structured-output lane of a remote model sometimes
spills the rest of the answer into a string field (``...approved by the board.","reason":"same
organization","aliases":["Zorn Labs"]},{``), drops or doubles decisions, and names candidates that are not
there. Such an answer is INVALID: note-process asks once more with a fresh call, and a second invalid answer
fails the attempt with the problem as its error (``queue_errors``); the apply step checks again
(:func:`check_apply` raises :class:`InvalidAnswer`), so nothing of an invalid answer is ever applied in part.
:func:`link_answer` and :func:`extraction` answer None for a sound answer, else ``{valid: False, problems,
error}``.

The checks are structural, never about meaning (ADR 0004):

- **A fragment of the answer's own structure inside a prose field.** Only the fields the model writes in its own
  words are read: ``summary``, ``reason``, ``revision_summary``, ``name``, ``aliases`` and (extraction) ``role`` and
  ``topics``; never ``evidence``, ``quote``, a statement's object, a task's text or a time reference, which quote
  the note (a note about a JSON API legitimately holds ``{"key": 1}``). A fragment is one of the answer schema's
  own field names closed by ``":`` (``reason":``, ``"aliases":``), or a closing quote followed by a closing brace
  or bracket and then a comma, another closer or the end (``"},{``, ``"]}``). A fragment that appears verbatim in
  the note's own words (or, for a linking answer, in the extraction it links) is not one.
- **A decision that names no candidate from its list**: ``reuse`` or ``unsure`` whose ``candidate`` is not an index
  of that mention's candidates, ``merge`` whose ``merge_candidates`` are not two different such indexes, or a
  decision for a mention the note does not have. (Until 2026-10-20 such a decision was applied in part: the
  mention was created as new with a warning. It is now invalid.)
- **An index answered twice, contradicting itself**: a mention ref with two different decisions; a statement
  index with two different relations, or once naming an existing statement and once naming none (B's corrupt
  ``{"index": 2, "existing": null}`` beside ``{"index": 2, "existing": 2}``); a task index once closing an open
  task and once closing none. The same relation to SEVERAL existing statements (or one task closing several open
  ones) is one statement linked to many, not a corruption (the watch's "new deadline" superseding three
  deadlines): it stays valid; the builder applies the first link and names the others in a ``multi_link``
  revision warning (ADR 0007).

The schemas a model is handed are strict (every property required, an optional one nullable: ``schemas.strict``),
so an answer carries null where it has nothing to say: null reads as absent here (an entry that differs from its
twin only by null against an empty value is the same answer).

The functions below the constants are copied into the note-process flow's code nodes (``flows/build.py``
``validate_code``), so the flow and the apply step run the very same source; they use no annotations and no
imports beyond ``re``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: The field names of the Linking and Extraction schemas (process/schemas.py); a test keeps them complete.
LINK_KEYS = ("aliases", "candidate", "claims", "closes_open_task", "decision", "evidence", "existing", "index",
             "kind", "mentions", "merge_candidates", "name", "reason", "ref", "relation_to_existing",
             "revision_summary", "summary", "tasks")
EXTRACTION_KEYS = ("aliases", "basis", "certainty", "claims", "date", "document_date", "due", "evidence", "heading",
                   "iso", "kind", "mentions", "moments", "name", "object", "object_is_literal", "predicate", "quote",
                   "ref", "role", "stance", "status", "subject", "summary", "tasks", "text", "text_span", "time_refs",
                   "topics", "valid_from", "valid_until")
#: Problems named in one error sentence at most (the rest are counted).
PROBLEMS_SHOWN = 3


def _fragment(text, keys, context):
    """The first fragment of a JSON answer inside ``text`` (see the module docstring), or None."""
    import re

    s = str(text or "")
    if '"' not in s:
        return None
    key_pat = re.compile(r'(?<![A-Za-z0-9_])(?:' + "|".join(keys) + r')"\s*:')
    close_pat = re.compile(r'"\s*[}\]]\s*(?:,|[}\]]|$)')
    flat = " ".join(str(context or "").split())
    for pat in (key_pat, close_pat):
        for m in pat.finditer(s):
            frag = " ".join(m.group(0).split())
            if frag and frag in flat:
                continue  # the note's (or the extraction's) own words, quoted
            return m.group(0)
    return None


def _label(value, fallback):
    v = " ".join(str(value or "").split())
    if len(v) > 40:
        v = v[:40] + "…"
    return "“" + v + "”" if v else fallback


def _is_index(value, count):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count


def _same_twice(items, key, what, problems, fields=None, target=None):
    """An index answered twice in contradiction: any difference (``fields`` None), else a difference in one of
    ``fields`` or a ``target`` named by one entry and left empty by the other. Each index is reported once. Null,
    an empty string and an empty list are the same absent value."""
    def present(entry):
        return {f: v for f, v in entry.items() if v is not None and v != "" and v != []}

    seen = {}
    reported = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        k = it.get(key)
        if k in seen and k not in reported:
            first = seen[k]
            if fields is None:
                bad = present(first) != present(it)
            else:
                bad = any(first.get(f) != it.get(f) for f in fields)
                if target is not None and (first.get(target) is None) != (it.get(target) is None):
                    bad = True
            if bad:
                reported.add(k)
                problems.append(what + " " + str(k) + " is answered twice, differently")
        seen.setdefault(k, it)


def link_problems(answer, candidates=None, context=""):
    """The structural problems of one Linking answer (empty: valid). ``candidates`` maps each mention ref the note
    offered to its number of candidates (None: the refs are not checked); ``context`` is the text fragments may
    quote (the note's words and its extraction)."""
    problems = []
    a = answer if isinstance(answer, dict) else {}
    mentions = [m for m in (a.get("mentions") or []) if isinstance(m, dict)]
    for m in mentions:
        name = _label(m.get("name"), "mention " + str(m.get("ref")))
        for field in ("summary", "reason", "name"):
            frag = _fragment(m.get(field), LINK_KEYS, context)
            if frag is not None:
                problems.append("the " + field + " of " + name + " holds a piece of the answer's structure ("
                                + frag.strip() + ")")
        for alias in m.get("aliases") or []:
            frag = _fragment(alias, LINK_KEYS, context)
            if frag is not None:
                problems.append("an alias of " + name + " holds a piece of the answer's structure ("
                                + frag.strip() + ")")
        ref = str(m.get("ref"))
        if candidates is not None:
            if ref not in candidates:
                problems.append("a decision names mention " + ref + ", which the note does not have")
                continue
            count = int(candidates.get(ref) or 0)
            decision = m.get("decision")
            if decision in ("reuse", "unsure") and not _is_index(m.get("candidate"), count):
                problems.append("the " + str(decision) + " decision for " + name
                                + " names no candidate from its list")
            if decision == "merge":
                pair = m.get("merge_candidates") or []
                if not (isinstance(pair, list) and len(pair) == 2 and all(_is_index(x, count) for x in pair)
                        and pair[0] != pair[1]):
                    problems.append("the merge decision for " + name + " does not name two of its candidates")
    _same_twice(mentions, "ref", "mention", problems)
    for c in a.get("claims") or []:
        if isinstance(c, dict):
            frag = _fragment(c.get("reason"), LINK_KEYS, context)
            if frag is not None:
                problems.append("the reason for statement " + str(c.get("index"))
                                + " holds a piece of the answer's structure (" + frag.strip() + ")")
    _same_twice(list(a.get("claims") or []), "index", "statement", problems, ("relation_to_existing",), "existing")
    _same_twice(list(a.get("tasks") or []), "index", "task", problems, (), "closes_open_task")
    frag = _fragment(a.get("revision_summary"), LINK_KEYS, context)
    if frag is not None:
        problems.append("the revision summary holds a piece of the answer's structure (" + frag.strip() + ")")
    return problems


def extraction_problems(answer, context=""):
    """The structural problems of one Extraction answer (empty: valid); ``context`` is the text it read."""
    problems = []
    a = answer if isinstance(answer, dict) else {}
    frag = _fragment(a.get("summary"), EXTRACTION_KEYS, context)
    if frag is not None:
        problems.append("the summary holds a piece of the answer's structure (" + frag.strip() + ")")
    mentions = [m for m in (a.get("mentions") or []) if isinstance(m, dict)]
    for m in mentions:
        name = _label(m.get("name"), "mention " + str(m.get("ref")))
        for field in ("name", "role"):
            frag = _fragment(m.get(field), EXTRACTION_KEYS, context)
            if frag is not None:
                problems.append("the " + field + " of " + name + " holds a piece of the answer's structure ("
                                + frag.strip() + ")")
        for alias in m.get("aliases") or []:
            frag = _fragment(alias, EXTRACTION_KEYS, context)
            if frag is not None:
                problems.append("an alias of " + name + " holds a piece of the answer's structure ("
                                + frag.strip() + ")")
    _same_twice(mentions, "ref", "mention", problems)
    for t in a.get("topics") or []:
        frag = _fragment(t, EXTRACTION_KEYS, context)
        if frag is not None:
            problems.append("a topic holds a piece of the answer's structure (" + frag.strip() + ")")
    for i, mo in enumerate(m for m in (a.get("moments") or []) if isinstance(m, dict)):
        for field in ("heading", "text_span"):
            frag = _fragment(mo.get(field), EXTRACTION_KEYS, context)
            if frag is not None:
                problems.append("the " + field.replace("_", " ") + " of dated part " + str(i + 1)
                                + " holds a piece of the answer's structure (" + frag.strip() + ")")
    return problems


def context_text(texts, extraction):
    """What an answer's prose may quote verbatim: the texts the model read and every string of the extraction
    (a linking answer quotes the extraction's names, roles and evidence)."""
    parts = [str(t or "") for t in (texts or [])]
    todo = [extraction]
    while todo:
        x = todo.pop()
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, dict):
            todo.extend(x.values())
        elif isinstance(x, list):
            todo.extend(x)
    return "\n".join(parts)


def problems_sentence(what, problems, again):
    """One sentence for the person and the queue log: "The model's linking answer was malformed twice (the summary
    of “Zorn Labs” holds a piece of the answer's structure (","reason":)); nothing from it was applied."."""
    shown = list(problems[:PROBLEMS_SHOWN])
    more = len(problems) - len(shown)
    detail = "; ".join(shown) + ("; and " + str(more) + " more" if more > 0 else "")
    return ("The model's " + what + " answer was malformed" + (" twice" if again else "") + " (" + detail
            + "); nothing from it was applied.")


# ----------------------------------------------------------------------------- the apply step's checks

class InvalidAnswer(ValueError):
    """A structurally corrupt model answer reached the apply step: nothing of it is applied."""


def _verdict(what: str, found: List[str]) -> Optional[Dict[str, Any]]:
    if not found:
        return None
    return {"valid": False, "problems": list(found), "error": problems_sentence(what, found, False)}


def link_answer(answer: Any, packet: Optional[Dict[str, Any]], *, context: str = "") -> Optional[Dict[str, Any]]:
    """None when the (merged) Linking answer is structurally sound, else ``{valid: False, problems, error}``.
    ``packet`` is the stored context packet (its ``mentions`` maps each resolved ref to its candidate entities);
    without one the refs are not checked (the revision builder then treats every reference as new and says so)."""
    cands = None
    if isinstance(packet, dict) and isinstance(packet.get("mentions"), dict):
        cands = {str(ref): len(v or []) for ref, v in packet["mentions"].items()}
        if not cands:
            cands = None  # nothing was resolved: the builder says every reference was treated as new
    return _verdict("linking", link_problems(answer, cands, context))


def extraction(answer: Any, *, context: str = "") -> Optional[Dict[str, Any]]:
    """None when an Extraction answer (merged, or a list of section answers) is structurally sound, else
    ``{valid: False, problems, error}``."""
    parts = answer if isinstance(answer, list) else [answer]
    found: List[str] = []
    for part in parts:
        found.extend(extraction_problems(part, context))
    return _verdict("reading", found)


def link_context(extraction_answer: Any, note_text: str = "") -> str:
    """What a linking answer's prose may quote: the note's words and every string of the extraction it links."""
    return context_text([note_text], extraction_answer)


def note_text(nb: Any, note_id: str) -> str:
    """The note's words the model read: its title, its text and its attachments' extracted text."""
    row = nb.store.note_row(note_id)
    parts = [str(row["title"] or ""), str(row["text"] or "")] if row is not None else []
    for a in nb.store.attachments_of(note_id):
        path = nb.root / "extracted" / f"{a['sha256']}.txt"
        if a["extracted_chars"] and path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def check_apply(nb: Any, note_id: str, extraction_answer: Any, linking_answer: Any, packet: Any) -> None:
    """The apply step's check (``note_apply_revision``): an invalid extraction or linking raises
    :class:`InvalidAnswer` (its one-sentence error) before anything is built or written."""
    words = note_text(nb, note_id)
    for verdict in (extraction(extraction_answer, context=words),
                    link_answer(linking_answer, packet, context=link_context(extraction_answer, words))):
        if verdict is not None:
            raise InvalidAnswer(verdict["error"])


#: The functions note-process's code nodes define (flows/build.py ``validate_code``).
FLOW_FUNCTIONS = ("_fragment", "_label", "_is_index", "_same_twice", "link_problems", "extraction_problems",
                  "context_text", "problems_sentence")

__all__ = ["EXTRACTION_KEYS", "FLOW_FUNCTIONS", "InvalidAnswer", "LINK_KEYS", "check_apply", "context_text",
           "extraction", "extraction_problems", "link_answer", "link_context", "link_problems", "note_text",
           "problems_sentence"]
