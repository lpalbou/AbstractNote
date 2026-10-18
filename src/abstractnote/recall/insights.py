"""``note_insight_write``: system-authored insight and synthesis notes with provenance."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from .. import ids
from ..ingest.normalise import text_norm
from ..model import INSIGHT_KINDS
from ..store.notes import _j, note_vector_text


def write_insight(nb: Any, kind: str, title: str, body: str, evidence_note_ids: Optional[Sequence[str]] = None,
                  entity_ids: Optional[Sequence[str]] = None, run_id: Optional[str] = None,
                  author: str = "abstractnote") -> Dict[str, Any]:
    """Store an insight (``dossier`` -> a ``synthesis`` note). Unknown evidence notes or entities are
    left out and named in ``warnings``. Writing the same insight twice returns the same note."""
    if kind not in INSIGHT_KINDS:
        raise ValueError(f"The insight kind must be one of {', '.join(INSIGHT_KINDS)}.")
    title = str(title or "").strip()
    body = str(body or "").strip()
    if not title and not body:
        raise ValueError("An insight needs a title or a body.")
    warnings: List[str] = []
    evidence: List[str] = []
    for nid in evidence_note_ids or []:
        nid = str(nid)
        if nb.store.note_row(nid) is None:
            warnings.append(f"The evidence note {nid} is not in this notebook; it was left out.")
        elif nid not in evidence:
            evidence.append(nid)
    ents: List[str] = []
    for eid in entity_ids or []:
        eid = str(eid)
        if nb.store.entity_row(eid) is None:
            warnings.append(f"The entity {eid} is not in this notebook; it was left out.")
        else:
            c = nb.store.canonical(eid)
            if c not in ents:
                ents.append(c)
    note_id = ids.note_id_for_insight(kind, title, body, evidence)
    if nb.store.note_row(note_id) is not None:
        return {"note_id": note_id, "status": "duplicate", "warnings": warnings}
    now = ids.utc_now()
    vtext = note_vector_text(title, body, [], 8000)
    [vkey] = nb.cache.ensure([vtext]) if vtext.strip() else [None]
    with nb.store.tx() as c:
        nb.store.insert_note(c, {
            "note_id": note_id, "kind": "synthesis" if kind == "dossier" else "insight", "title": title or None,
            "text": body, "text_norm": text_norm(body), "text_hash": None, "occurred_at": now, "received_at": now,
            "processed_at": now, "source_json": _j({"client": "agent", "author": author}), "client": "agent",
            "priority": 1, "status": "processed", "run_id": run_id, "size": len(body), "vector_key": vkey,
            "insight_kind": kind, "evidence_note_ids_json": json.dumps(evidence), "entity_ids_json": json.dumps(ents),
        })
        for nid in evidence:
            c.execute("INSERT OR IGNORE INTO note_links(src_note_id, dst_note_id, predicate, score, matched_by,"
                      " detail_json, created_at) VALUES (?,?,'cito:citesAsEvidence',1.0,'insight',NULL,?)",
                      (note_id, nid, now))
        nb.store.refresh_note_fts(c, note_id, [])
    return {"note_id": note_id, "status": "new", "warnings": warnings}
