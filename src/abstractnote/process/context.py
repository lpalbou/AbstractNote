"""``note_context``: what the notebook already knows that is relevant to one note.

The packet lists candidate entities (exact name or alias found in the note's
words, then vector neighbours of the note), their active claims and the open
tasks, each with an ``index``. The Linking step refers to existing claims and
open tasks only by those indexes; the packet is stored so that
``note_apply_revision`` can check every reference by set membership.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from .. import ids
from ..ingest.dates import date_basis, is_approximate
from ..ingest.normalise import DEFAULT_SECTION_CHARS, sections
from ..recall.shapes import excerpt
from ..store.embeddings import top_k
from .packets import CLAIMS_PER_CANDIDATE
from .vocabulary import predicate_words, vocabulary

MAX_CANDIDATE_ENTITIES = 15
#: Active claims attached to each candidate entity (and indexed in the packet): the most recent
#: few, one line each in the prompts (backlog 0024 bounds the packet, never the note's text).
MAX_CLAIMS_PER_ENTITY = CLAIMS_PER_CANDIDATE
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


def empty_packet() -> Dict[str, Any]:
    return {"candidate_entities": [], "existing_claims": [], "open_tasks": [], "mentions": {}}


# ----------------------------------------------------------------------------- views

def claim_view(nb: Any, row: Any, index: int) -> Dict[str, Any]:
    subject = nb.store._name(row["subject_id"]) or row["subject_id"]
    obj = (nb.store._name(row["object_id"]) or row["object_id"]) if row["object_id"] else row["object_literal"]
    literal = row["object_id"] is None
    line = f"{subject} {predicate_words(row['predicate'])} " + (f"\u201c{obj}\u201d" if literal else str(obj))
    return {"index": index, "claim_id": row["claim_id"], "subject": subject, "predicate": row["predicate"],
            "object": obj, "object_is_literal": literal, "observed_at": row["observed_at"],
            "certainty": row["certainty"], "evidence": row["evidence"], "line": line}


def active_claim_rows(nb: Any, entity_id: str, limit: int = MAX_CLAIMS_PER_ENTITY) -> List[Any]:
    members = nb.store.merged_members(nb.store.canonical(entity_id))
    q = ",".join("?" for _ in members)
    return nb.store.q(
        f"SELECT * FROM claims WHERE status='active' AND valid_until IS NULL AND (subject_id IN ({q}) OR object_id IN"
        f" ({q})) ORDER BY observed_at DESC, claim_id LIMIT ?", [*members, *members, int(limit)])


def entity_view(nb: Any, entity_id: str) -> Dict[str, Any]:
    e = nb.store.entity_row(entity_id)
    return {"entity_id": e["entity_id"], "name": e["name"], "kind": e["kind"], "aliases": nb.store.aliases(entity_id),
            "summary": e["summary"], "last_seen": e["last_seen"]}


def attach_claims(nb: Any, packet: Dict[str, Any], entity_id: str, views: List[Dict[str, Any]],
                  limit: int = MAX_CLAIMS_PER_ENTITY) -> List[Dict[str, Any]]:
    """Active claims of an entity (most recent first) as indexed views, appending new ones to the packet."""
    out = []
    for row in active_claim_rows(nb, entity_id, limit):
        cid = row["claim_id"]
        if cid in packet["existing_claims"]:
            idx = packet["existing_claims"].index(cid)
        else:
            idx = len(packet["existing_claims"])
            packet["existing_claims"].append(cid)
            views.append(claim_view(nb, row, idx))
        out.append(claim_view(nb, row, idx))
    return out


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


def note_context(nb: Any, note_id: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
    row = nb.store.note_row(note_id)
    if row is None:
        raise ValueError("That note is not in this notebook.")
    budget = int(max_chars) if max_chars else DEFAULT_SECTION_CHARS
    atts = nb.store.attachments_of(note_id)
    source = json.loads(row["source_json"] or "{}")
    secs: List[Dict[str, Any]] = []
    for part in note_parts(row["text"] or "", source):
        for s in sections(part["text"], budget):
            secs.append({"index": len(secs), "source": part["source"], "author": part["author"],
                         "chars": s["chars"], "text": s["text"]})
    full_text = [row["title"] or "", row["text"] or ""]
    for a in atts:
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

    packet = empty_packet()
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
        view = entity_view(nb, eid)
        view.update({"index": len(candidates), "matched_by": how,
                     "active_claims": attach_claims(nb, packet, eid, claim_views)})
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
            "vocabulary": vocabulary()}
