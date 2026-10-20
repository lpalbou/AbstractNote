"""Quick recall (``note_search``): full-text (FTS5 BM25) plus vector cosine, fused by reciprocal rank.

Runs over notes, entities and claims with filters (since/until on history
time, kinds, entities, topics) and ``as_of`` (only what existed by then), and
returns facets by kind, topic, entity and month. No model is called; a query
vector is one embedder call (none with the hashing embedder's local vectors).

Reciprocal rank orders hits but says nothing about whether the best hit is any good, so every
hit also gets an absolute ``relevance`` in [0, 1] (:func:`relevance`): the share of the query's
words it contains, each word weighted by its rarity among the notes (the square of its BM25 inverse
document frequency, so the words that tell notes apart outweigh "when", "did", "first"; a word in
no note counts for nothing), raised (never lowered) by the vector cosine.

Entity and statement hits must reach :data:`HIT_MIN_SCORE` and be carried by one of the notes
returned (an entity the note mentions, a statement it made or repeated); they are grouped under
those notes, each with the first and the latest date among the matching notes that carry it.

``order="earliest"`` returns the notes that reach :data:`EARLIEST_MIN_SCORE` in history order,
oldest first, so "when did we first mention X" is answerable by searching for X that way (the
words of the question count too, so name the subject).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .. import ids
from ..store.embeddings import top_k, vector_key
from ..store.notes import clamp_limit, fts_terms
from . import shapes

RRF_K = 60
POOL = 200
NOTE_VECTOR_MIN = 0.2
ENTITY_LIMIT = 10
CLAIM_LIMIT = 10
HIT_MIN_SCORE = 0.5     # minimum fused relevance of an entity or statement hit (and of a note dating an entity)
EARLIEST_MIN_SCORE = 0.75  # minimum fused relevance of a note in earliest order (history order, so stricter)
EARLIEST_POOL = 2000    # full-text candidates read for earliest order (history order, not rank, decides)
ORDERS = ("relevance", "earliest")


def _rrf(*ranked: Sequence[str]) -> Dict[str, float]:
    scores: Dict[str, float] = {}
    for lst in ranked:
        for rank, key in enumerate(lst):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def query_words(query: str) -> List[str]:
    """The query's distinct words, as the full-text index splits them (case-folded)."""
    out: List[str] = []
    for w in "".join(ch if ch.isalnum() else " " for ch in str(query or "")).split():
        if w.casefold() not in out:
            out.append(w.casefold())
    return out


def _matches(store: Any, table: str, key: str, word: str) -> Set[str]:
    return {r[0] for r in store.q(f"SELECT {key} FROM {table} WHERE {table} MATCH ?", (f'"{word}"',))}


def word_weights(store: Any, words: Sequence[str]) -> Tuple[Dict[str, float], Dict[str, Set[str]]]:
    """The weight of each word (its BM25 inverse document frequency among the notes, squared; 0 for a
    word no note contains) and the notes containing it."""
    total = store.q1("SELECT COUNT(*) FROM notes_fts")[0] or 0
    weights: Dict[str, float] = {}
    notes: Dict[str, Set[str]] = {}
    for w in words:
        notes[w] = _matches(store, "notes_fts", "note_id", w)
        df = len(notes[w])
        weights[w] = math.log(1.0 + (total - df + 0.5) / (df + 0.5)) ** 2 if df else 0.0
    return weights, notes


def relevance(words_in: Iterable[str], weights: Dict[str, float], cosine: Optional[float]) -> float:
    """Fused relevance in [0, 1]: the weighted share of query words present, raised by the cosine
    (``max(text, (text + cosine) / 2)``), so a vector alone never carries a hit past the floor."""
    total = sum(weights.values())
    text = sum(weights[w] for w in set(words_in) if w in weights) / total if total > 0 else 0.0
    if cosine is None:
        return text
    return max(text, (text + max(0.0, float(cosine))) / 2.0)


def _words_by_hit(store: Any, table: str, key: str, words: Sequence[str]) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for w in words:
        for hit in _matches(store, table, key, w):
            out.setdefault(hit, set()).add(w)
    return out


def _chunks(items: Sequence[str], n: int = 500) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), n):
        yield items[i: i + n]


def _list(value: Any) -> List[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value if str(v).strip()]


def search(nb: Any, query: str = "", *, since: Optional[str] = None, until: Optional[str] = None,
           kinds: Any = None, entities: Any = None, topics: Any = None, limit: int = 20,
           as_of: Optional[str] = None, order: Optional[str] = None) -> Dict[str, Any]:
    limit = clamp_limit(limit, 20, 200)
    order_by = str(order or "relevance").strip().lower()
    if order_by not in ORDERS:
        raise ValueError("The order is relevance or earliest.")
    earliest = order_by == "earliest"
    query = str(query or "").strip()
    since_n = ids.optional_time(since, field="since")
    until_n = ids.optional_time(until, field="until")
    as_of_n = ids.optional_time(as_of, field="as_of")
    kinds_l = _list(kinds)
    topics_l = sorted({f for t in _list(topics) for f in (t.lstrip("#").lower(), ids.canonical_name(t.lstrip("#")))})
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
                [terms, *params, EARLIEST_POOL if earliest else POOL]):
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
    words = query_words(query)
    weights, word_notes = word_weights(store, words) if words else ({}, {})
    note_rel: Dict[str, float] = {}
    if query:
        fused = _rrf(fts_notes, vec_notes)
        for nid in fused:
            note_rel[nid] = relevance([w for w in words if nid in word_notes[w]], weights, vec_score.get(nid))
        if earliest:
            keep = [k for k in fused if note_rel[k] >= EARLIEST_MIN_SCORE]
            when = {r[0]: r[1] for chunk in _chunks(keep) for r in store.q(
                f"SELECT note_id, occurred_at FROM notes WHERE note_id IN ({','.join('?' for _ in chunk)})", chunk)}
            order = sorted((k for k in keep if k in when), key=lambda k: (when[k], k))
        else:
            order = sorted(fused, key=lambda k: (-fused[k], k))
    else:
        order = [r[0] for r in store.q(f"SELECT n.note_id FROM notes n WHERE {cond} ORDER BY n.occurred_at"
                                       f" {'ASC' if earliest else 'DESC'} LIMIT ?", [*params, POOL])]
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
            item.update({"score": round(fused[nid], 6), "relevance": round(note_rel.get(nid, 0.0), 4),
                         "matched_by": [m for m, s in (("fts", fts_set), ("vector", vec_set)) if nid in s],
                         "topics": note_topics, "entity_ids": ents, "insight_kind": row["insight_kind"],
                         "matched_entities": [], "matched_claims": []})
            note_items.append(item)
    returned = [n["note_id"] for n in note_items]
    by_note = {n["note_id"]: n for n in note_items}

    # ---------------------------------------------------------------- entities
    # A hit must reach HIT_MIN_SCORE and be carried by a returned note; it is listed under those notes.
    ent_items: List[Dict[str, Any]] = []
    if query and returned:
        carriers: Dict[str, List[str]] = {}
        for nid in returned:
            for e in by_note[nid]["entity_ids"]:
                carriers.setdefault(e, []).append(nid)
        e_cond = "e.merged_into IS NULL"
        e_params: List[Any] = []
        if as_of_n:
            e_cond += " AND COALESCE(e.first_seen, e.created_at)<=?"
            e_params.append(as_of_n)
        fts_e = [r[0] for r in store.q(
            f"SELECT f.entity_id FROM entities_fts f JOIN entities e ON e.entity_id=f.entity_id WHERE entities_fts MATCH ?"
            f" AND {e_cond} ORDER BY bm25(entities_fts) LIMIT ?", [terms, *e_params, POOL])] if terms else []
        vec_e: Dict[str, float] = {}
        if qvec is not None:
            rows = store.q(f"SELECT e.entity_id, v.vector FROM entities e JOIN vectors v ON v.vector_key=e.vector_key"
                           f" WHERE {e_cond}", e_params)
            vec_e = dict(top_k(qvec, [(r[0], r[1]) for r in rows], POOL, NOTE_VECTOR_MIN))
        e_words = _words_by_hit(store, "entities_fts", "entity_id", words) if fts_e else {}
        fe = _rrf(fts_e, sorted(vec_e, key=lambda k: (-vec_e[k], k)))
        scored = []
        for eid in fe:
            canon = store.canonical(eid)
            if canon not in carriers:
                continue
            rel = relevance(e_words.get(eid, ()), weights, vec_e.get(eid) if qvec is not None else None)
            if rel >= HIT_MIN_SCORE:
                scored.append((rel, fe[eid], canon, eid))
        seen: Set[str] = set()
        for rel, rrf, canon, eid in sorted(scored, key=lambda x: (-x[0], -x[1], x[2])):
            if canon in seen or len(ent_items) >= ENTITY_LIMIT:
                continue
            seen.add(canon)
            b = store.entity_brief(canon)
            if not b:
                continue
            first, latest = _first_latest(store, canon, carriers[canon], note_rel)
            b.update({"score": round(rel, 4), "rrf": round(rrf, 6),
                      "matched_by": [m for m, ok in (("fts", eid in set(fts_e)), ("vector", eid in vec_e)) if ok],
                      "note_ids": carriers[canon], "first_at": first, "latest_at": latest})
            ent_items.append(b)
            for nid in carriers[canon]:
                by_note[nid]["matched_entities"].append(canon)

    # ---------------------------------------------------------------- claims
    claim_items: List[Dict[str, Any]] = []
    if query and returned:
        c_carriers: Dict[str, List[str]] = {}
        marks = ",".join("?" for _ in returned)
        for cid, nid in store.q(f"SELECT claim_id, note_id FROM claims WHERE note_id IN ({marks}) UNION SELECT"
                                f" claim_id, note_id FROM claim_reinforcements WHERE note_id IN ({marks})",
                                [*returned, *returned]):
            c_carriers.setdefault(cid, [])
            if nid not in c_carriers[cid]:
                c_carriers[cid].append(nid)
        for cid in c_carriers:
            c_carriers[cid].sort(key=returned.index)
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
        vec_c: Dict[str, float] = {}
        if qvec is not None:
            rows = store.q(f"SELECT c.claim_id, v.vector FROM claims c JOIN vectors v ON v.vector_key=c.vector_key"
                           f" WHERE {c_cond}", c_params)
            vec_c = dict(top_k(qvec, [(r[0], r[1]) for r in rows], POOL, NOTE_VECTOR_MIN))
        c_words = _words_by_hit(store, "claims_fts", "claim_id", words) if fts_c else {}
        fc = _rrf(fts_c, sorted(vec_c, key=lambda k: (-vec_c[k], k)))
        scored_c = []
        for cid in fc:
            if cid not in c_carriers:
                continue
            rel = relevance(c_words.get(cid, ()), weights, vec_c.get(cid) if qvec is not None else None)
            if rel >= HIT_MIN_SCORE:
                scored_c.append((rel, fc[cid], cid))
        for rel, rrf, cid in sorted(scored_c, key=lambda x: (-x[0], -x[1], x[2]))[:CLAIM_LIMIT]:
            row = store.claim_row(cid)
            if row is None:
                continue
            d = store.claim_dict(row)
            if as_of_n:
                vf, vu = d["valid_from"], d["valid_until"]
                d["believed_at_as_of"] = (vf is None or vf <= as_of_n) and (vu is None or vu > as_of_n)
            d.update({"score": round(rel, 4), "rrf": round(rrf, 6), "note_ids": c_carriers[cid]})
            claim_items.append(d)
            for nid in c_carriers[cid]:
                by_note[nid]["matched_claims"].append(cid)

    names = {e: store._name(e) for e in facet_entity}
    facets = {
        "kinds": [{"kind": k, "count": v} for k, v in sorted(facet_kind.items(), key=lambda x: (-x[1], x[0]))],
        "topics": [{"topic": k, "count": v} for k, v in sorted(facet_topic.items(), key=lambda x: (-x[1], x[0]))],
        "entities": [{"entity_id": k, "name": names[k], "count": v}
                     for k, v in sorted(facet_entity.items(), key=lambda x: (-x[1], x[0]))[:20]],
        "months": [{"month": k, "count": v} for k, v in sorted(facet_month.items())],
    }
    return {"query": query, "order": order_by, "as_of": as_of_n, "notes": note_items, "entities": ent_items,
            "claims": claim_items, "facets": facets, "considered": len(order), "warnings": warnings}


def _first_latest(store: Any, entity_id: str, carriers: Sequence[str], note_rel: Dict[str, float]
                  ) -> Tuple[Optional[str], Optional[str]]:
    """The first and the latest date among the notes that match the query (relevance at least
    :data:`HIT_MIN_SCORE`, or returned) and carry the entity."""
    members = store.merged_members(entity_id)
    matching = [k for k, v in note_rel.items() if v >= HIT_MIN_SCORE]
    pool = list(dict.fromkeys([*carriers, *matching]))
    em = ",".join("?" for _ in members)
    first: Optional[str] = None
    latest: Optional[str] = None
    for chunk in _chunks(pool, 400):
        r = store.q1(f"SELECT MIN(n.occurred_at), MAX(n.occurred_at) FROM notes n WHERE n.note_id IN"
                     f" ({','.join('?' for _ in chunk)}) AND n.note_id IN (SELECT note_id FROM entity_notes WHERE"
                     f" entity_id IN ({em}))", [*chunk, *members])
        if r and r[0]:
            first = r[0] if first is None or r[0] < first else first
            latest = r[1] if latest is None or r[1] > latest else latest
    return first, latest
