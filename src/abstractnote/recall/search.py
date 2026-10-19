"""Quick recall (``note_search``): full-text (FTS5 BM25) plus vector cosine, fused by reciprocal rank.

Runs over notes, entities and claims with filters (since/until on history
time, kinds, entities, topics) and ``as_of`` (only what existed by then), and
returns facets by kind, topic, entity and month. No model is called; a query
vector is one embedder call (none with the hashing embedder's local vectors).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .. import ids
from ..store.embeddings import top_k, vector_key
from ..store.notes import clamp_limit, fts_terms
from . import shapes

RRF_K = 60
POOL = 200
NOTE_VECTOR_MIN = 0.2
ENTITY_LIMIT = 10
CLAIM_LIMIT = 10


def _rrf(*ranked: Sequence[str]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for lst in ranked:
        for rank, key in enumerate(lst):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def _list(value: Any) -> List[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value if str(v).strip()]


def search(nb: Any, query: str = "", *, since: Optional[str] = None, until: Optional[str] = None,
           kinds: Any = None, entities: Any = None, topics: Any = None, limit: int = 20,
           as_of: Optional[str] = None) -> Dict[str, Any]:
    limit = clamp_limit(limit, 20, 200)
    query = str(query or "").strip()
    since_n = ids.optional_time(since, field="since")
    until_n = ids.optional_time(until, field="until")
    as_of_n = ids.optional_time(as_of, field="as_of")
    kinds_l, topics_l = _list(kinds), [t.lstrip("#").lower() for t in _list(topics)]
    entity_l = _list(entities)
    store = nb.store

    # ---------------------------------------------------------------- note filter (SQL)
    where = ["n.archived_at IS NULL"]
    params: List[Any] = []
    if since_n:
        where.append("n.occurred_at>=?")
        params.append(since_n)
    bound = min(x for x in (until_n, as_of_n) if x) if (until_n or as_of_n) else None
    if bound:
        where.append("n.occurred_at<=?")
        params.append(bound)
    if kinds_l:
        where.append(f"n.kind IN ({','.join('?' for _ in kinds_l)})")
        params += kinds_l
    if topics_l:
        where.append(f"n.note_id IN (SELECT note_id FROM note_topics WHERE topic IN ({','.join('?' for _ in topics_l)}))")
        params += topics_l
    if entity_l:
        members: List[str] = []
        for e in entity_l:
            members += store.merged_members(store.canonical(e))
        where.append(f"n.note_id IN (SELECT note_id FROM entity_notes WHERE entity_id IN ({','.join('?' for _ in members)}))")
        params += members
    cond = " AND ".join(where)

    terms = fts_terms(query)
    fts_notes: List[str] = []
    snippets: Dict[str, str] = {}
    if terms:
        for r in store.q(
                f"SELECT f.note_id, snippet(notes_fts, 2, '', '', '…', 24) AS snip FROM notes_fts f JOIN notes n"
                f" ON n.note_id=f.note_id WHERE notes_fts MATCH ? AND {cond} ORDER BY bm25(notes_fts) LIMIT ?",
                [terms, *params, POOL]):
            fts_notes.append(r[0])
            snippets[r[0]] = r[1]
    warnings: List[str] = []
    qvec = None
    if query:
        if nb.cache.embedder is None and nb.cache.get(vector_key(query, nb.cache.model_id)) is None:
            warnings.append(nb.cache.missing_embedder + " These results match the words only.")
        else:
            qvec = nb.cache.embed_query(query)
    vec_notes: List[str] = []
    vec_score: Dict[str, float] = {}
    if qvec is not None:
        filtered = len(params) > 0
        allowed = {r[0] for r in store.q(f"SELECT n.note_id FROM notes n WHERE {cond}", params)} if filtered else None
        archived = set() if filtered else {r[0] for r in store.q("SELECT note_id FROM notes WHERE archived_at IS NOT NULL")}
        for nid, s in nb.note_vectors.nearest(qvec, POOL, NOTE_VECTOR_MIN, allowed=allowed, exclude=archived):
            vec_notes.append(nid)
            vec_score[nid] = s
    if query:
        fused = _rrf(fts_notes, vec_notes)
        order = sorted(fused, key=lambda k: (-fused[k], k))
    else:
        order = [r[0] for r in store.q(f"SELECT n.note_id FROM notes n WHERE {cond} ORDER BY n.occurred_at DESC LIMIT ?",
                                       [*params, POOL])]
        fused = {k: 0.0 for k in order}
    fts_set, vec_set = set(fts_notes), set(vec_notes)
    note_items = []
    facet_kind: Dict[str, int] = {}
    facet_topic: Dict[str, int] = {}
    facet_entity: Dict[str, int] = {}
    facet_month: Dict[str, int] = {}
    for i, nid in enumerate(order):
        row = store.note_row(nid)
        if row is None:
            continue
        k = row["kind"] or "unfiled"
        facet_kind[k] = facet_kind.get(k, 0) + 1
        facet_month[row["occurred_at"][:7]] = facet_month.get(row["occurred_at"][:7], 0) + 1
        note_topics = store.note_topics(nid)
        for t in note_topics:
            facet_topic[t] = facet_topic.get(t, 0) + 1
        ents = sorted({store.canonical(r[0]) for r in store.q("SELECT entity_id FROM entity_notes WHERE note_id=?",
                                                               (nid,))})
        for e in ents:
            facet_entity[e] = facet_entity.get(e, 0) + 1
        if len(note_items) < limit:
            item = shapes.note_ref(row)
            if snippets.get(nid):
                item["excerpt"] = " ".join(snippets[nid].split())
            item.update({"score": round(fused[nid], 6), "matched_by": [m for m, s in (("fts", fts_set), ("vector", vec_set))
                                                                         if nid in s],
                         "topics": note_topics, "entity_ids": ents, "insight_kind": row["insight_kind"]})
            note_items.append(item)

    # ---------------------------------------------------------------- entities
    ent_items: List[Dict[str, Any]] = []
    if query:
        e_cond = "e.merged_into IS NULL"
        e_params: List[Any] = []
        if as_of_n:
            e_cond += " AND COALESCE(e.first_seen, e.created_at)<=?"
            e_params.append(as_of_n)
        fts_e = [r[0] for r in store.q(
            f"SELECT f.entity_id FROM entities_fts f JOIN entities e ON e.entity_id=f.entity_id WHERE entities_fts MATCH ?"
            f" AND {e_cond} ORDER BY bm25(entities_fts) LIMIT ?", [terms, *e_params, POOL])] if terms else []
        vec_e: List[str] = []
        if qvec is not None:
            rows = store.q(f"SELECT e.entity_id, v.vector FROM entities e JOIN vectors v ON v.vector_key=e.vector_key"
                           f" WHERE {e_cond}", e_params)
            vec_e = [e for e, _ in top_k(qvec, [(r[0], r[1]) for r in rows], POOL, NOTE_VECTOR_MIN)]
        fe = _rrf(fts_e, vec_e)
        for eid in sorted(fe, key=lambda k: (-fe[k], k))[:ENTITY_LIMIT]:
            b = store.entity_brief(eid)
            if b:
                b.update({"score": round(fe[eid], 6),
                          "matched_by": [m for m, s in (("fts", set(fts_e)), ("vector", set(vec_e))) if eid in s]})
                ent_items.append(b)

    # ---------------------------------------------------------------- claims
    claim_items: List[Dict[str, Any]] = []
    if query:
        c_cond = "1=1"
        c_params: List[Any] = []
        if as_of_n:
            c_cond += " AND c.observed_at<=?"
            c_params.append(as_of_n)
        if since_n:
            c_cond += " AND c.observed_at>=?"
            c_params.append(since_n)
        if until_n:
            c_cond += " AND c.observed_at<=?"
            c_params.append(until_n)
        fts_c = [r[0] for r in store.q(
            f"SELECT f.claim_id FROM claims_fts f JOIN claims c ON c.claim_id=f.claim_id WHERE claims_fts MATCH ?"
            f" AND {c_cond} ORDER BY bm25(claims_fts) LIMIT ?", [terms, *c_params, POOL])] if terms else []
        vec_c: List[str] = []
        if qvec is not None:
            rows = store.q(f"SELECT c.claim_id, v.vector FROM claims c JOIN vectors v ON v.vector_key=c.vector_key"
                           f" WHERE {c_cond}", c_params)
            vec_c = [c for c, _ in top_k(qvec, [(r[0], r[1]) for r in rows], POOL, NOTE_VECTOR_MIN)]
        fc = _rrf(fts_c, vec_c)
        for cid in sorted(fc, key=lambda k: (-fc[k], k))[:CLAIM_LIMIT]:
            row = store.claim_row(cid)
            if row is not None:
                d = store.claim_dict(row)
                if as_of_n:
                    vf, vu = d["valid_from"], d["valid_until"]
                    d["believed_at_as_of"] = (vf is None or vf <= as_of_n) and (vu is None or vu > as_of_n)
                d["score"] = round(fc[cid], 6)
                claim_items.append(d)

    names = {e: store._name(e) for e in facet_entity}
    facets = {
        "kinds": [{"kind": k, "count": v} for k, v in sorted(facet_kind.items(), key=lambda x: (-x[1], x[0]))],
        "topics": [{"topic": k, "count": v} for k, v in sorted(facet_topic.items(), key=lambda x: (-x[1], x[0]))],
        "entities": [{"entity_id": k, "name": names[k], "count": v}
                     for k, v in sorted(facet_entity.items(), key=lambda x: (-x[1], x[0]))[:20]],
        "months": [{"month": k, "count": v} for k, v in sorted(facet_month.items())],
    }
    return {"query": query, "as_of": as_of_n, "notes": note_items, "entities": ent_items, "claims": claim_items,
            "facets": facets, "considered": len(order), "warnings": warnings}
