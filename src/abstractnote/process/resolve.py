"""``note_resolve_candidates``: up to five existing entities per mention, with how each matched.

Candidates come from three deterministic lookups: exact case-folded name or
alias, full-text over entity names, aliases and summaries (BM25), and vector
neighbours of the mention. The model decides which, if any, is the same thing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .. import ids
from ..store.embeddings import top_k
from ..store.notes import entity_text, fts_terms
from .context import attach_claims, empty_packet, entity_view, load_packet, save_packet

MAX_CANDIDATES = 5
RRF_K = 60
VECTOR_MIN = 0.3


def _exact(nb: Any, names: Sequence[str]) -> List[str]:
    out: List[str] = []
    folds = sorted({ids.fold(n) for n in names if str(n or "").strip()})
    if not folds:
        return out
    q = ",".join("?" for _ in folds)
    rows = nb.store.q(f"SELECT entity_id FROM entity_aliases WHERE alias_fold IN ({q})", folds)
    rows += [r for r in nb.store.q("SELECT entity_id, name FROM entities") if ids.fold(r[1]) in set(folds)]
    for r in rows:
        c = nb.store.canonical(r[0])
        if c not in out:
            out.append(c)
    return out


def _fts(nb: Any, names: Sequence[str]) -> List[str]:
    terms = fts_terms(" ".join(names))
    if not terms:
        return []
    out: List[str] = []
    for r in nb.store.q("SELECT entity_id FROM entities_fts WHERE entities_fts MATCH ? ORDER BY bm25(entities_fts)"
                        " LIMIT 20", (terms,)):
        c = nb.store.canonical(r[0])
        if c not in out:
            out.append(c)
    return out


def resolve_candidates(nb: Any, note_id: str, mentions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    nb.require_embedder()
    if nb.store.note_row(note_id) is None:
        raise ValueError("That note is not in this notebook.")
    if not isinstance(mentions, (list, tuple)):
        raise ValueError("The mentions must be a list of {ref, name, kind, aliases}.")
    packet = load_packet(nb, note_id) or empty_packet()
    entity_rows = nb.store.q("SELECT e.entity_id, v.vector FROM entities e JOIN vectors v ON v.vector_key=e.vector_key"
                             " WHERE e.merged_into IS NULL")
    vectors_in = [(r[0], r[1]) for r in entity_rows]
    query_texts = []
    for m in mentions:
        if not isinstance(m, dict) or not str(m.get("ref") or "").strip() or not str(m.get("name") or "").strip():
            raise ValueError("Every mention needs a ref and a name.")
        query_texts.append(entity_text(str(m["name"]), [str(a) for a in m.get("aliases") or []], None))
    qvecs = nb.cache.embed_queries(query_texts) if vectors_in and query_texts else [None] * len(query_texts)

    added_claims: List[Dict[str, Any]] = []
    out = []
    for m, qvec in zip(mentions, qvecs):
        names = [str(m["name"]), *[str(a) for a in m.get("aliases") or []]]
        exact = _exact(nb, names)
        fts = _fts(nb, names)
        vec_hits = top_k(qvec, vectors_in, 20, VECTOR_MIN) if qvec is not None else []
        vec = [nb.store.canonical(e) for e, _ in vec_hits]
        vec_score = {nb.store.canonical(e): s for e, s in vec_hits}
        scores: Dict[str, float] = {}
        matched: Dict[str, List[str]] = {}
        for label, ranked in (("alias", exact), ("fts", fts), ("vector", vec)):
            for rank, eid in enumerate(ranked):
                scores[eid] = scores.get(eid, 0.0) + 1.0 / (RRF_K + rank + 1)
                if label not in matched.setdefault(eid, []):
                    matched[eid].append(label)
        ordered = sorted(scores, key=lambda e: (0 if "alias" in matched[e] else 1, -scores[e], e))[:MAX_CANDIDATES]
        cands = []
        for i, eid in enumerate(ordered):
            if nb.store.entity_row(eid) is None:
                continue
            view = entity_view(nb, eid)
            score = 1.0 if "alias" in matched[eid] else round(vec_score.get(eid, scores[eid]), 4)
            view.update({"index": len(cands), "score": score, "matched_by": matched[eid],
                         "active_claims": attach_claims(nb, packet, eid, added_claims)})
            cands.append(view)
        packet["mentions"][str(m["ref"])] = [c["entity_id"] for c in cands]
        out.append({"ref": str(m["ref"]), "candidates": cands})
    save_packet(nb, note_id, packet)
    return {"mentions": out, "existing_claims": added_claims}
