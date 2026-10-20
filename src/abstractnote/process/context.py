"""``note_context``: what the notebook already knows that is relevant to one note.

The packet lists candidate entities (exact name or alias found in the note's
words, then vector neighbours of the note), their claims and the open
tasks, each with an ``index``. The Linking step refers to existing claims and
open tasks only by those indexes; the packet is stored so that
``note_apply_revision`` can check every reference by set membership.

Context is dated (ADR 0008 §2): the note's ``occurred_at`` is ``as_of``. A candidate shows what was KNOWN
THEN (its claims observed on or before the note's day whose window covers it, and its latest summary dated on
or before it) and, separately, what was recorded LATER (claims and summaries of a later day), each later claim
indexed so the linking can say a statement is an earlier state of it. The packet keeps the digest of what the
note was understood against (:func:`context_digest`: ``{as_of, entities, claims, summaries, ...}``), the
dependency record a revision stores (ADR 0008 §3).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import ids
from ..ingest.dates import date_basis, is_approximate
from ..ingest.normalise import DEFAULT_SECTION_CHARS, sections
from ..recall.shapes import excerpt
from ..store.embeddings import top_k
from .packets import LATER_CLAIMS_PER_CANDIDATE, LATER_SUMMARIES_PER_CANDIDATE, later_than, slot_first
from .vocabulary import predicate_words, vocabulary

MAX_CANDIDATE_ENTITIES = 15
#: Claims known on the note's date attached to each candidate entity (and indexed in the packet): the most
#: recent few (backlog 0024 bounds the packet, never the note's text). ``note_context`` attaches this many so
#: the extraction packet can show the entity's statement slots (predicate spelling and value).
MAX_CLAIMS_PER_ENTITY = 8
#: Claims recorded LATER than the note attached to each candidate (ADR 0008 §2), the earliest later first: a
#: budget selection, never a cut of a claim; how many were left out is recorded on the view and in the digest.
LATER_CLAIMS_PER_ENTITY = LATER_CLAIMS_PER_CANDIDATE
#: Summaries recorded for a later day than the note, the latest ones (older later ones are counted, not shown).
LATER_SUMMARIES_PER_ENTITY = LATER_SUMMARIES_PER_CANDIDATE
MAX_OPEN_TASKS = 20
SIMILAR_NOTES = 5
ENTITY_VECTOR_MIN = 0.3

_WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> List[str]:
    return _WORD.findall(ids.fold(text))


# ----------------------------------------------------------------------------- packet storage

def load_packet(nb: Any, note_id: str) -> Optional[Dict[str, Any]]:
    row = nb.store.q1("SELECT packet_json FROM context_packets WHERE note_id=?", (note_id,))
    return json.loads(row[0]) if row else None


def save_packet(nb: Any, note_id: str, packet: Dict[str, Any]) -> None:
    with nb.store.lock:
        nb.store.conn.execute("INSERT OR REPLACE INTO context_packets(note_id, created_at, packet_json) VALUES (?,?,?)",
                              (note_id, ids.utc_now(), json.dumps(packet, sort_keys=True)))


def empty_packet(as_of: Optional[str] = None) -> Dict[str, Any]:
    return {"candidate_entities": [], "existing_claims": [], "open_tasks": [], "mentions": {}, "alias_matches": {},
            "digest": empty_digest(as_of)}


def empty_digest(as_of: Optional[str]) -> Dict[str, Any]:
    """What a note is understood against (ADR 0008 §3): the note's date, the entities and claims shown, the
    summary (its revision) shown for each entity as known then, the later claims offered, and what the packet
    budget left out (counts per entity)."""
    return {"as_of": as_of, "entities": [], "claims": [], "summaries": {}, "later_claims": [], "omitted": {}}


def context_digest(nb: Any, note_id: str) -> Optional[Dict[str, Any]]:
    """The digest stored with the note's context packet (``note_context`` then ``note_resolve_candidates``), or
    None when the note's context was never looked up. A packet stored before the digest existed raises: it
    cannot say what the note was understood against."""
    packet = load_packet(nb, note_id)
    if packet is None:
        return None
    if not isinstance(packet.get("digest"), dict):
        raise ValueError("The note's stored context has no digest; look its context up again.")
    return packet["digest"]


def record_view(packet: Dict[str, Any], view: Dict[str, Any], omissions: bool = True) -> None:
    """Add one candidate view to the packet's digest (entities, claims, the known-then summary and, for the
    linking candidates (``omissions``), what the budget left out of the card: the largest count over the mentions
    that show the entity)."""
    d = packet.setdefault("digest", empty_digest(None))
    eid = view["entity_id"]
    if eid not in d["entities"]:
        d["entities"].append(eid)
    if view.get("summary_revision_id"):
        d["summaries"][eid] = view["summary_revision_id"]
    for c in view.get("active_claims") or []:
        if c["claim_id"] not in d["claims"]:
            d["claims"].append(c["claim_id"])
    for c in view.get("later_claims") or []:
        if c["claim_id"] not in d["claims"]:
            d["claims"].append(c["claim_id"])
        if c["claim_id"] not in d["later_claims"]:
            d["later_claims"].append(c["claim_id"])
    left = {k: int(view.get(k) or 0) for k in ("known_claims_omitted", "later_claims_omitted",
                                                 "later_summaries_omitted")}
    if omissions and any(left.values()):
        prev = d["omitted"].get(eid) or {}
        d["omitted"][eid] = {k: max(int(prev.get(k) or 0), v) for k, v in left.items()}


# ----------------------------------------------------------------------------- views

def claim_view(nb: Any, row: Any, index: int, as_of: Optional[str] = None) -> Dict[str, Any]:
    """One claim as the packets show it. The line names the predicate as it is spelled (``ex:annual_budget``),
    so a note about the same slot can reuse the spelling (the model links by index, never by the string)."""
    subject = nb.store._name(row["subject_id"]) or row["subject_id"]
    obj = (nb.store._name(row["object_id"]) or row["object_id"]) if row["object_id"] else row["object_literal"]
    literal = row["object_id"] is None
    line = f"{subject} {row['predicate']} " + (f"\u201c{obj}\u201d" if literal else str(obj))
    return {"index": index, "claim_id": row["claim_id"], "subject": subject, "predicate": row["predicate"],
            "object": obj, "object_is_literal": literal, "observed_at": row["observed_at"],
            "valid_from": row["valid_from"], "valid_until": row["valid_until"], "status": row["status"],
            "certainty": row["certainty"], "evidence": row["evidence"], "line": line,
            "later": later_than(row["observed_at"], as_of)}


def _members_sql(nb: Any, entity_id: str) -> tuple:
    members = nb.store.merged_members(nb.store.canonical(entity_id))
    q = ",".join("?" for _ in members)
    return f"(subject_id IN ({q}) OR object_id IN ({q}))", [*members, *members]


def active_claim_rows(nb: Any, entity_id: str, limit: Optional[int] = MAX_CLAIMS_PER_ENTITY,
                      as_of: Optional[str] = None) -> List[Any]:
    """Claims of an entity believed on ``as_of`` as known then, the most recent first: not retracted, the window
    covering ``as_of``, and either observed on or before its day or said (by a later note) to hold already then
    (``valid_from <= as_of``, retro-design-review 2.2); without ``as_of``, the claims active now. ``limit`` None:
    all of them."""
    where, params = _members_sql(nb, entity_id)
    tail = " LIMIT ?" if limit is not None else ""
    lim = [int(limit)] if limit is not None else []
    if not as_of:
        return nb.store.q(f"SELECT * FROM claims WHERE status='active' AND valid_until IS NULL AND {where}"
                          f" ORDER BY observed_at DESC, claim_id{tail}", [*params, *lim])
    return nb.store.q(
        f"SELECT * FROM claims WHERE status!='retracted' AND (substr(observed_at,1,10)<=? OR (valid_from IS NOT NULL"
        f" AND valid_from<=?)) AND (valid_until IS NULL OR valid_until>?) AND {where}"
        f" ORDER BY observed_at DESC, claim_id{tail}", [str(as_of)[:10], as_of, as_of, *params, *lim])


def later_claim_rows(nb: Any, entity_id: str, as_of: Optional[str], limit: Optional[int] = LATER_CLAIMS_PER_ENTITY
                     ) -> tuple:
    """``(rows, total)``: claims of an entity recorded on a later day than ``as_of`` and not believed then
    (retracted ones left out), the earliest first, at most ``limit`` (None: all); ``total`` counts them all (the
    omission is recorded, ADR-0026)."""
    if not as_of:
        return [], 0
    where, params = _members_sql(nb, entity_id)
    cond = (f"status!='retracted' AND substr(observed_at,1,10)>? AND NOT (valid_from IS NOT NULL AND valid_from<=? AND"
            f" (valid_until IS NULL OR valid_until>?)) AND {where}")
    args = [str(as_of)[:10], as_of, as_of, *params]
    total = nb.store.q1(f"SELECT COUNT(*) FROM claims WHERE {cond}", args)[0]
    tail = " LIMIT ?" if limit is not None else ""
    rows = nb.store.q(f"SELECT * FROM claims WHERE {cond} ORDER BY observed_at, seq, claim_id{tail}",
                      [*args, *([int(limit)] if limit is not None else [])])
    return rows, int(total or 0)


def summary_at(nb: Any, entity_id: str) -> Optional[str]:
    """History date (``at``) of the entity's CURRENT summary, the one ``entities.summary`` shows (the latest in
    history time, as ``op_entity_summary`` picks it); None when it has none recorded."""
    row = nb.store.q1("SELECT at FROM entity_summaries WHERE entity_id=? ORDER BY at DESC, rowid DESC LIMIT 1",
                      (entity_id,))
    return row[0] if row else None


def entity_view(nb: Any, entity_id: str, as_of: Optional[str] = None) -> Dict[str, Any]:
    """An entity as a packet shows it. With ``as_of`` (the note's date) its summary is the latest one dated on or
    before that day (None when nothing was written by then) and ``later_summaries`` lists those of later days
    (the latest :data:`LATER_SUMMARIES_PER_ENTITY`, the rest counted in ``later_summaries_omitted``)."""
    e = nb.store.entity_row(entity_id)
    view = {"entity_id": e["entity_id"], "name": e["name"], "kind": e["kind"], "aliases": nb.store.aliases(entity_id),
            "summary": e["summary"], "summary_at": summary_at(nb, entity_id), "last_seen": e["last_seen"],
            "first_seen": e["first_seen"]}
    rows = nb.store.q("SELECT at, summary, revision_id FROM entity_summaries WHERE entity_id=? ORDER BY at, rowid",
                      (entity_id,))
    current = nb.store.q1("SELECT revision_id FROM entity_summaries WHERE entity_id=? ORDER BY at DESC, rowid DESC"
                          " LIMIT 1", (entity_id,))
    view["summary_revision_id"] = current[0] if current else None
    if not as_of:
        return view
    then = [r for r in rows if not later_than(r[0], as_of)]
    later = [r for r in rows if later_than(r[0], as_of)]
    if rows:  # a summary written for a later day is not what was known then
        view.update(summary=then[-1][1] if then else None, summary_at=then[-1][0] if then else None,
                    summary_revision_id=then[-1][2] if then else None)
    shown = later[-LATER_SUMMARIES_PER_ENTITY:] if LATER_SUMMARIES_PER_ENTITY else []
    view["later_summaries"] = [{"at": r[0], "summary": r[1], "revision_id": r[2]} for r in shown]
    view["later_summaries_omitted"] = len(later) - len(shown)
    view["as_of"] = as_of
    return view


def _index(nb: Any, packet: Dict[str, Any], row: Any, views: List[Dict[str, Any]], as_of: Optional[str]
           ) -> Dict[str, Any]:
    cid = row["claim_id"]
    if cid in packet["existing_claims"]:
        idx = packet["existing_claims"].index(cid)
    else:
        idx = len(packet["existing_claims"])
        packet["existing_claims"].append(cid)
        views.append(claim_view(nb, row, idx, as_of))
    return claim_view(nb, row, idx, as_of)


def attach_claims(nb: Any, packet: Dict[str, Any], entity_id: str, views: List[Dict[str, Any]],
                  limit: int = MAX_CLAIMS_PER_ENTITY, as_of: Optional[str] = None) -> List[Dict[str, Any]]:
    """Claims of an entity believed on the note's date as known then (most recent first) as indexed views,
    appending new ones to the packet."""
    return [_index(nb, packet, row, views, as_of) for row in active_claim_rows(nb, entity_id, limit, as_of)]


def dated_view(nb: Any, packet: Dict[str, Any], entity_id: str, views: List[Dict[str, Any]], as_of: Optional[str],
               known_limit: int = MAX_CLAIMS_PER_ENTITY, predicates: Sequence[str] = (), omissions: bool = True
               ) -> Dict[str, Any]:
    """A candidate entity as known on ``as_of`` plus its LATER block (claims recorded later), each claim indexed in
    the packet, recorded in the packet's digest. The claims on ``predicates`` (the slots the note writes to) are
    all kept, known then and later; the others fill up to ``known_limit`` / :data:`LATER_CLAIMS_PER_ENTITY`
    (those sharing a predicate word first); what is left out is counted on the view (``known_claims_omitted``,
    ``later_claims_omitted``) and in the digest's ``omitted``."""
    view = entity_view(nb, entity_id, as_of)
    known = active_claim_rows(nb, entity_id, None, as_of)
    pick = slot_first([dict(r) for r in known], predicates, known_limit)
    keep = {c["claim_id"] for c in pick}
    view["active_claims"] = [_index(nb, packet, r, views, as_of) for r in known if r["claim_id"] in keep]
    view["known_claims_omitted"] = len(known) - len(keep)
    later, _total = later_claim_rows(nb, entity_id, as_of, None)
    pick = slot_first([dict(r) for r in later], predicates, LATER_CLAIMS_PER_ENTITY)
    keep = {c["claim_id"] for c in pick}
    view["later_claims"] = [_index(nb, packet, r, views, as_of) for r in later if r["claim_id"] in keep]
    view["later_claims_omitted"] = len(later) - len(keep)
    record_view(packet, view, omissions)
    return view


# ----------------------------------------------------------------------------- name index

def name_index(nb: Any) -> Dict[str, List[str]]:
    """Case-folded entity names and aliases -> canonical entity ids (exact matching only)."""
    out: Dict[str, List[str]] = {}
    for r in nb.store.q("SELECT entity_id, name, display_name FROM entities"):
        # The surface form as written, and the canonical name read as words (``laurent_philippe_albou``).
        for form in dict.fromkeys((ids.fold(r[2] or r[1]), ids.fold(r[1].replace("_", " ")))):
            out.setdefault(form, []).append(r[0])
    for r in nb.store.q("SELECT entity_id, alias_fold FROM entity_aliases"):
        out.setdefault(r[1], []).append(r[0])
    return out


def entities_named_in(nb: Any, text: str) -> List[str]:
    """Entities whose exact name or alias (case-folded, as whole words) occurs in the text."""
    token_set = set(words(text))
    if not token_set:
        return []
    folded = " " + " ".join(words(text)) + " "
    found: List[str] = []
    for name, eids in name_index(nb).items():
        parts = words(name)
        if not parts or any(p not in token_set for p in parts):
            continue
        if len(parts) > 1 and (" " + " ".join(parts) + " ") not in folded:
            continue
        for eid in eids:
            c = nb.store.canonical(eid)
            if c not in found:
                found.append(c)
    return found


# ----------------------------------------------------------------------------- note_context

def note_parts(text: str, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The note's text as parts by who wrote them.

    A prompt note (importers) keeps the assistant's answer in ``source.origin.outcome`` by
    ``source.origin.outcome_author`` and also appends it to the text after a separator. When the
    text ends with that stored answer, the part before it is the author's and the answer is a
    part of its own (source ``answer``), so extraction can tell the author's words from what the
    model said. Structural only: the stored fields say where the answer is, no text is parsed."""
    author = str(source.get("author") or "") or None
    origin = source.get("origin") if isinstance(source.get("origin"), dict) else {}
    outcome = origin.get("outcome")
    if isinstance(outcome, str) and outcome.strip() and text.rstrip().endswith(outcome.strip()):
        head = text.rstrip()[: len(text.rstrip()) - len(outcome.strip())]
        head = head.rstrip()
        sep = head.rfind("\n---")
        rest = [ln for ln in head[sep:].splitlines() if ln.strip()] if sep >= 0 else []
        if rest and rest[0].strip() == "---" and len(rest) <= 2 and all(ln.startswith("#") for ln in rest[1:]):
            head = head[:sep]  # the importer's separator: a rule and the answer's heading
        return [p for p in (
            {"source": "note", "author": author, "text": head.strip()},
            {"source": "answer", "author": str(origin.get("outcome_author") or "assistant"), "text": outcome.strip()},
        ) if p["text"]]
    return [{"source": "note", "author": author, "text": text}]


def note_sections(nb: Any, note_id: str, max_chars: Optional[int] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The sections note-process reads (its text, its answer part, its attachments' extracted text, else its title)
    and the texts they come from. No section = the note has no text to read: note-process stops at once and the
    queue records that failure as final (``tools.queue.note_queue_ack``), since no retry can change it."""
    row = nb.store.note_row(note_id)
    if row is None:
        raise ValueError("That note is not in this notebook.")
    budget = int(max_chars) if max_chars else DEFAULT_SECTION_CHARS
    source = json.loads(row["source_json"] or "{}")
    secs: List[Dict[str, Any]] = []
    for part in note_parts(row["text"] or "", source):
        for s in sections(part["text"], budget):
            secs.append({"index": len(secs), "source": part["source"], "author": part["author"],
                         "chars": s["chars"], "text": s["text"]})
    full_text = [row["title"] or "", row["text"] or ""]
    for a in nb.store.attachments_of(note_id):
        path = nb.root / "extracted" / f"{a['sha256']}.txt"
        if not (a["extracted_chars"] and path.exists()):
            continue
        body = path.read_text(encoding="utf-8")
        full_text.append(body)
        for s in sections(body, budget):
            secs.append({"index": len(secs), "source": f"attachment:{a['filename'] or a['attachment_id']}",
                         "chars": s["chars"], "text": s["text"]})
    if not secs and row["title"]:
        secs.append({"index": 0, "source": "title", "chars": len(row["title"]), "text": row["title"]})
    return secs, full_text


def note_context(nb: Any, note_id: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
    secs, full_text = note_sections(nb, note_id, max_chars)
    row = nb.store.note_row(note_id)
    atts = nb.store.attachments_of(note_id)
    source = json.loads(row["source_json"] or "{}")

    about = json.loads(row["about_json"]) if row["about_json"] else None
    note = {
        "note_id": note_id, "title": row["title"], "kind": row["kind"], "occurred_at": row["occurred_at"],
        "language": row["language"], "topics": nb.store.note_topics(note_id),
        "source": {k: source[k] for k in ("client", "importer", "author") if source.get(k)},
        "location": (json.loads(row["location_json"]).get("label") if row["location_json"] else None),
        "attachments": [{"attachment_id": a["attachment_id"], "filename": a["filename"], "mime": a["mime"],
                         "extracted_by": a["extracted_by"]} for a in atts],
        "chars": sum(s["chars"] for s in secs), "about": about,
    }
    basis = date_basis(row)
    if is_approximate(basis):  # only then: the header says so and extraction may give the text's own date
        note.update({"date_basis": basis, "date_approximate": True})

    vector = nb.cache.get(row["vector_key"]) if row["vector_key"] else None
    similar: List[Dict[str, Any]] = []
    if vector is not None:
        for nid, score in nb.note_vectors.nearest(vector, SIMILAR_NOTES * 3, 0.2, exclude=[note_id],
                                                  notes_only=True):
            n = nb.store.note_row(nid)
            if n["archived_at"] or len(similar) >= SIMILAR_NOTES:
                continue
            similar.append({"note_id": nid, "title": n["title"], "occurred_at": n["occurred_at"],
                            "summary": excerpt(n["summary"] or n["one_line"] or n["text"], 200),
                            "score": round(score, 4)})

    as_of = row["occurred_at"]
    note["as_of"] = as_of
    packet = empty_packet(as_of)
    ordered: List[tuple] = []
    if about and about["kind"] == "entity" and nb.store.entity_row(about["id"]) is not None:
        ordered.append((nb.store.canonical(about["id"]), "about"))
    for eid in entities_named_in(nb, "\n".join(full_text)):
        ordered.append((eid, "name_in_text"))
    if vector is not None:
        rows = nb.store.q("SELECT e.entity_id, v.vector FROM entities e JOIN vectors v ON v.vector_key=e.vector_key"
                          " WHERE e.merged_into IS NULL")
        for eid, _score in top_k(vector, [(r[0], r[1]) for r in rows], MAX_CANDIDATE_ENTITIES, ENTITY_VECTOR_MIN):
            ordered.append((eid, "vector"))
    candidates: List[Dict[str, Any]] = []
    claim_views: List[Dict[str, Any]] = []
    for eid, how in ordered:
        if eid in packet["candidate_entities"] or len(candidates) >= MAX_CANDIDATE_ENTITIES:
            continue
        packet["candidate_entities"].append(eid)
        # the extraction packet shows names and statement slots (its own bound, said in its text); omissions that
        # matter are those of the linking cards (note_resolve_candidates)
        view = dated_view(nb, packet, eid, claim_views, as_of, omissions=False)
        view.update({"index": len(candidates), "matched_by": how})
        candidates.append(view)

    task_rows: List[Any] = []
    if about and about["kind"] == "task":
        t = nb.store.q1("SELECT * FROM tasks WHERE task_id=? AND status='open'", (about["id"],))
        if t is not None:
            task_rows.append(t)
    if packet["candidate_entities"]:
        members: List[str] = []
        for eid in packet["candidate_entities"]:
            members.extend(nb.store.merged_members(eid))
        q = ",".join("?" for _ in members)
        task_rows.extend(nb.store.q(
            f"SELECT * FROM tasks WHERE status='open' AND note_id IN (SELECT note_id FROM entity_notes WHERE entity_id"
            f" IN ({q})) ORDER BY opened_at DESC LIMIT ?", [*members, MAX_OPEN_TASKS]))
    task_rows.extend(nb.store.q("SELECT * FROM tasks WHERE status='open' ORDER BY opened_at DESC LIMIT ?",
                                (MAX_OPEN_TASKS,)))
    open_tasks: List[Dict[str, Any]] = []
    for t in task_rows:
        if t["task_id"] in packet["open_tasks"] or len(open_tasks) >= MAX_OPEN_TASKS:
            continue
        packet["open_tasks"].append(t["task_id"])
        open_tasks.append({"index": len(open_tasks), "task_id": t["task_id"], "text": t["text"],
                           "opened_at": t["opened_at"], "due": t["due"], "note_id": t["note_id"]})
    save_packet(nb, note_id, packet)
    return {"note": note, "sections": secs, "similar_notes": similar, "candidate_entities": candidates,
            "existing_claims": claim_views, "open_tasks": open_tasks, "topics": nb.store.topic_names(),
            "vocabulary": vocabulary(), "context": packet["digest"]}
