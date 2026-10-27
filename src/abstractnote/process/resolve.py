"""``note_resolve_candidates``: up to three existing entities per mention, with how each matched.

Candidates come from three deterministic lookups: the same canonical name
(``ids.name_key``) as an entity's name or alias, the entity holding the mention's
own canonical name always first, full-text over entity names, aliases and summaries (BM25), and vector
neighbours of the mention. The model decides which, if any, is the same thing.

A candidate found by the exact name or alias key (``match: alias``) is ALWAYS listed, even past
:data:`MAX_CANDIDATES` (ADR 0008 §6); the packet records those matches (``alias_matches``) so that a decision
not to reuse one lands in review as a close match. Each candidate is shown as known on the note's date with its
LATER block (``context.dated_view``), every claim on the predicates the note writes about the mention kept.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .. import ids
from ..store.embeddings import top_k
from ..store.notes import entity_text, fts_terms
from .context import dated_view, empty_digest, empty_packet, load_packet, save_packet
from .vocabulary import normalize_predicate
from .packets import CANDIDATES_PER_MENTION

MAX_CANDIDATES = CANDIDATES_PER_MENTION
#: Active claims returned per candidate; the linking prompt shows at most
#: ``packets.CLAIMS_PER_CANDIDATE`` of them, those about the note's own predicates first.
CLAIMS_PER_CANDIDATE_RETURNED = 8
RRF_K = 60
VECTOR_MIN = 0.3


def _exact(nb: Any, names: Sequence[str]) -> List[str]:
    """Entities whose canonical name or an alias has the canonical name (``ids.name_key``) of one of ``names``:
    the entity holding the mention's own name first, then the other names' holders, then alias matches."""
    out: List[str] = []
    keys = [k for k in dict.fromkeys(ids.name_key(n) for n in names if str(n or "").strip()) if k]
    if not keys:
        return out
    q = ",".join("?" for _ in keys)
    first = nb.store.q(f"SELECT entity_id, name_key FROM entities WHERE name_key IN ({q})"
                       " ORDER BY merged_into IS NOT NULL, key_clash_with IS NOT NULL, created_at, entity_id", keys)
    first.sort(key=lambda r: keys.index(r[1]))
    rows = [r[0] for r in first]
    rows += [r[0] for r in nb.store.q(f"SELECT a.entity_id FROM entity_aliases a JOIN entities e ON"
                                      f" e.entity_id=a.entity_id WHERE a.alias_key IN ({q})"
                                      " ORDER BY e.created_at, e.entity_id", keys)]
    for eid in rows:
        c = nb.store.canonical(eid)
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
    note = nb.store.note_row(note_id)
    packet = load_packet(nb, note_id) or empty_packet(note["occurred_at"])
    packet.setdefault("digest", empty_digest(note["occurred_at"]))
    packet.setdefault("alias_matches", {})
    as_of = packet["digest"].get("as_of") or note["occurred_at"]
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
        # The entity with the mention's own canonical name always comes first (deterministic normalisation,
        # not an identity judgement: Notebook.apply_revision would file a new entity of that name under it anyway).
        own = nb.store.name_key_holder(ids.name_key(m["name"]))
        ranked = sorted(scores, key=lambda e: (0 if e == own else 1, 0 if "alias" in matched[e] else 1,
                                               -scores[e], e))
        # an exact name or alias match is always a candidate, past the cut if need be (ADR 0008 §6)
        ordered = ranked[:MAX_CANDIDATES] + [e for e in ranked[MAX_CANDIDATES:] if "alias" in matched[e]]
        preds = [normalize_predicate(x)[0] for x in (m.get("predicates") or []) if str(x or "").strip()]
        cands = []
        for i, eid in enumerate(ordered):
            if nb.store.entity_row(eid) is None:
                continue
            view = dated_view(nb, packet, eid, added_claims, as_of, CLAIMS_PER_CANDIDATE_RETURNED, preds)
            score = 1.0 if "alias" in matched[eid] else round(vec_score.get(eid, scores[eid]), 4)
            view.update({"index": len(cands), "score": score, "matched_by": matched[eid],
                         "match": "alias" if "alias" in matched[eid] else matched[eid][0]})
            cands.append(view)
        packet["mentions"][str(m["ref"])] = [c["entity_id"] for c in cands]
        packet["alias_matches"][str(m["ref"])] = [c["entity_id"] for c in cands if c["match"] == "alias"]
        out.append({"ref": str(m["ref"]), "candidates": cands})
    save_packet(nb, note_id, packet)
    return {"mentions": out, "existing_claims": added_claims, "context": packet["digest"]}
