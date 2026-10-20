"""Communities of the knowledge graph (backlog 0030, docs/cognition.md "Communities").

Detection needs no model: Louvain (``networkx``, a fixed seed) over the live entities, an
edge weighing the statements between two entities (claims with an entity object that were not retracted, merged
entries counted for their survivor) plus their co-mentions (each note that mentions ``k`` live entities adds
:func:`co_mention_share` = ``1/((k-1)·log2 k)`` to each pair it mentions, so one long note does not outweigh many
short ones). A community smaller than :data:`MIN_COMMUNITY_SIZE` is left out: its entities are the loose ends (no ``entity_communities`` row).

Detection is deterministic for a given networkx version: the same graph under the same networkx release
always gives the same communities, but Louvain's implementation changes between releases (3.6.1 and 3.7
split the same graph differently), so ``meta.communities`` records the ``networkx`` version of each
computation.

Ids are stable: a new community whose members overlap a previous community by Jaccard >= :data:`KEEP_JACCARD`
keeps that community's id, label, summary and kind (the best overlaps are matched first, each previous community
at most once); any other community gets ``cm_`` + a hash of its sorted member ids. A kept label is marked
``stale_label`` when more than :data:`STALE_CHANGE` of the membership changed since it was labelled (Jaccard
with the members at labelling time below ``1 - STALE_CHANGE``). Once after a networkx change (or after a
computation that recorded no version), the floor is :data:`KEEP_JACCARD_VERSION_CHANGE`, and a labelled
previous community that still matches nothing hands its label, marked stale, to the unmatched new group it
overlaps most; the recompute response lists every kept, carried and dropped label under ``carry_over``
(never silently lost). The model names communities in the reflect flow
(:func:`pending` -> one ``llm_call`` with :data:`COMMUNITY_LABELS_SCHEMA` -> :func:`label`).

The tables are derived, but the labels are the model's work, so ``Notebook.rebuild`` keeps them
(``schema.KEPT_TABLES``).
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .. import ids
from ..store.notes import clamp_limit
from . import shapes

DEFAULT_RESOLUTION = 1.0
SEED = 20261019                 # fixed Louvain seed: same graph, same networkx -> same communities
MIN_COMMUNITY_SIZE = 3          # smaller groups are loose ends
KEEP_JACCARD = 0.6              # overlap with a previous community that keeps its id and label
KEEP_JACCARD_VERSION_CHANGE = 0.5  # the same, once, after the networkx version changed
STALE_CHANGE = 0.2              # share of the membership that may change before a kept label is stale
TOP_ENTITIES = 5                # entities shown per community
PENDING_LIMIT = 5               # communities handed to the model per reflect run
PENDING_CLAIMS = 8              # strongest statements per pending community
PENDING_EXCERPTS = 3            # representative notes per pending community
LABEL_MAX_WORDS = 4
COMMUNITY_KINDS = ["project", "theme", "people", "tooling", "process", "other"]

#: Structured output of the labelling step (top-level field ``communities``, so no other schema has the same
#: fields). Communities are referred to by their INDEX in the pending list, never by id (ADR 0004).
COMMUNITY_LABELS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "communities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "index of the community in the list"},
                    "label": {"type": "string", "description": "a name of at most 4 words"},
                    "summary": {"type": "string", "description": "one sentence: what holds this group together"},
                    "kind": {"type": "string", "enum": COMMUNITY_KINDS},
                },
                "required": ["index", "label", "summary", "kind"],
            },
        },
    },
    "required": ["communities"],
}

#: The labelling step's static system prompt and user-message template (``{{communities}}``). They live here,
#: next to the schema, rather than under ``process/prompts`` (another lane's folder); see the W17 report.
LABEL_SYSTEM_PROMPT = """You name groups of related things found in a person's notebook. Each group in the message has an `index`, its most mentioned entities (name, kind, how many notes mention it, what the notebook knows about it), the strongest statements that link them, and a few representative notes.

For every group, give:
- `index`: the group's index in the message;
- `label`: a name of at most 4 words that a person recognises at a glance (the project, theme, circle of people, tools or process the group is about), never a list of the entity names;
- `summary`: one sentence saying what holds the group together;
- `kind`: project, theme, people, tooling, process or other.

A group with a `previous_label` was named before and has changed since; keep that name when it still fits.

Rules:
- Use only what the message says; never invent people, projects or dates.
- Name every group in the message, once.

Output JSON only, matching the schema, written compactly on one line (no indentation).
"""

LABEL_TEMPLATE = """Name these groups.

Groups:
{{communities}}"""

#: The variables the reflect flow wires into :data:`LABEL_TEMPLATE`.
LABEL_TEMPLATE_VARS = frozenset({"communities"})

_log = logging.getLogger(__name__)

_CERTAINTY_RANK = {"stated": 0, "probable": 1, "reported": 2, "possible": 3, "disputed": 4}


# --------------------------------------------------------------------------- graph


def _canonical_map(nb: Any) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """``{entity_id: surviving entity id}`` for every entity, and the rows of the live ones."""
    rows = nb.store.q("SELECT entity_id, merged_into, kind, name, note_count FROM entities")
    into = {r["entity_id"]: r["merged_into"] for r in rows}
    live = {r["entity_id"]: r for r in rows if not r["merged_into"]}
    canon: Dict[str, str] = {}
    for eid in into:
        cur, seen = eid, set()
        while into.get(cur) and cur not in seen:
            seen.add(cur)
            cur = into[cur]
        canon[eid] = cur
    return canon, live


def co_mention_share(k: int) -> float:
    """What a note that mentions ``k`` (>= 2) live entities adds to each pair among them: ``1/((k-1)·log2 k)``.

    A note's total co-mention weight is then ``k / (2·log2 k)``: 1 for a two-entity note, about 13 for a
    200-entity one (``1/(k-1)`` gave it ``k/2``, 100, enough for a single long note to form a community of its
    own; gate 04 §5)."""
    return 1.0 / ((k - 1) * math.log2(k))


def entity_graph(nb: Any) -> Any:
    """The weighted entity graph communities are found in (a ``networkx.Graph``; nodes are live entity ids,
    inserted in sorted order so detection is reproducible). Edge ``weight`` = statements + co-mention weight."""
    import networkx as nx

    canon, live = _canonical_map(nb)
    weights: Dict[Tuple[str, str], float] = {}

    def add(a: Optional[str], b: Optional[str], w: float) -> None:
        if not a or not b:
            return
        a, b = canon.get(a, a), canon.get(b, b)
        if a == b or a not in live or b not in live:
            return
        key = (a, b) if a < b else (b, a)
        weights[key] = weights.get(key, 0.0) + w

    for r in nb.store.q("SELECT subject_id, object_id, COUNT(*) AS n FROM claims WHERE object_id IS NOT NULL"
                        " AND status!='retracted' GROUP BY subject_id, object_id"):
        add(r["subject_id"], r["object_id"], float(r["n"]))
    by_note: Dict[str, set] = {}
    for r in nb.store.q("SELECT en.note_id, en.entity_id FROM entity_notes en JOIN notes n ON n.note_id=en.note_id"
                        " WHERE n.archived_at IS NULL"):
        c = canon.get(r["entity_id"], r["entity_id"])
        if c in live:
            by_note.setdefault(r["note_id"], set()).add(c)
    for members in by_note.values():
        k = len(members)
        if k < 2:
            continue
        ordered = sorted(members)
        share = co_mention_share(k)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                add(a, b, share)
    g = nx.Graph()
    g.add_nodes_from(sorted(live))
    for (a, b), w in sorted(weights.items()):
        g.add_edge(a, b, weight=w)
    return g


def detect(nb: Any, resolution: float = DEFAULT_RESOLUTION) -> Dict[str, Any]:
    """``{graph, groups: [sorted member lists, size >= MIN_COMMUNITY_SIZE], loose: [entity ids]}`` (no writes)."""
    from networkx.algorithms.community import louvain_communities

    g = entity_graph(nb)
    parts = louvain_communities(g, weight="weight", resolution=resolution, seed=SEED) if g.number_of_nodes() else []
    groups, loose = [], []
    for p in parts:
        members = sorted(p)
        if len(members) >= MIN_COMMUNITY_SIZE:
            groups.append(members)
        else:
            loose.extend(members)
    groups.sort(key=lambda m: (-len(m), m))
    return {"graph": g, "groups": groups, "loose": sorted(loose)}


def community_id_for(member_ids: Iterable[str]) -> str:
    """``cm_`` + a hash of the sorted member ids (the id of a community that matched no previous one)."""
    return "cm_" + ids._b32("community", *sorted(member_ids), length=20)


def _jaccard(a: "set[str]", b: "set[str]") -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


def networkx_version() -> str:
    """The installed networkx release (detection is deterministic for a given one)."""
    import networkx

    return str(networkx.__version__)


def _revision_count(nb: Any) -> int:
    return int(nb.store.q1("SELECT COUNT(*) FROM revisions WHERE applied_index=1")[0])


def _check_resolution(resolution: Any) -> float:
    try:
        r = float(DEFAULT_RESOLUTION if resolution in (None, "") else resolution)
    except (TypeError, ValueError):
        raise ValueError("The resolution must be a number above 0.") from None
    if not (r > 0) or r > 100:
        raise ValueError("The resolution must be a number above 0 and at most 100.")
    return r


def recompute(nb: Any, resolution: Any = None) -> Dict[str, Any]:
    """Run detection and store the communities (no model). Returns ``{communities, loose_ends, entities, edges,
    new, labels_kept, stale_labels, unlabelled, resolution, computed_at, networkx, carry_over}``; ``carry_over``
    is null unless the previous computation ran under another networkx version (or recorded none): then it is
    ``{from_networkx, to_networkx, floor, kept, stale, dropped}`` (see the module doc)."""
    nb.require_embedder()  # a notebook opened without its embedding model is read-only
    res = _check_resolution(resolution)
    found = detect(nb, res)
    g = found["graph"]
    version = networkx_version()
    note_counts = {r["entity_id"]: int(r["note_count"] or 0) for r in nb.store.q(
        "SELECT entity_id, note_count FROM entities WHERE merged_into IS NULL")}
    now = ids.utc_now()
    revisions = _revision_count(nb)
    with nb._apply_lock, nb.store.tx() as c:
        prev = {r["community_id"]: r for r in c.execute("SELECT * FROM communities").fetchall()}
        prev_meta = nb.store.meta_get("communities") or {}
        changed = bool(prev) and prev_meta.get("networkx") != version
        floor = KEEP_JACCARD_VERSION_CHANGE if changed else KEEP_JACCARD
        prev_members: Dict[str, set] = {cid: set() for cid in prev}
        for r in c.execute("SELECT entity_id, community_id FROM entity_communities").fetchall():
            prev_members.setdefault(r["community_id"], set()).add(r["entity_id"])
        owner = {e: cid for cid, ms in prev_members.items() for e in ms}
        overlaps = []
        for i, members in enumerate(found["groups"]):
            mset = set(members)
            for cid in sorted({owner[e] for e in members if e in owner}):
                overlaps.append((-_jaccard(mset, prev_members[cid]), i, cid))
        overlaps.sort()
        matched: Dict[int, str] = {}
        jaccard_of: Dict[int, float] = {}
        used: set = set()
        for nj, i, cid in overlaps:
            if -nj >= floor and i not in matched and cid not in used:
                matched[i], used, jaccard_of[i] = cid, used | {cid}, -nj
        carried: Dict[int, str] = {}  # after a version change: unmatched group -> labelled previous community
        if changed:
            for nj, i, cid in overlaps:
                if i not in matched and i not in carried and cid not in used and prev[cid]["label"]:
                    carried[i], used, jaccard_of[i] = cid, used | {cid}, -nj
        carry = {"from_networkx": prev_meta.get("networkx"), "to_networkx": version, "floor": floor,
                 "kept": [], "stale": [], "dropped": []} if changed else None
        c.execute("DELETE FROM entity_communities")
        c.execute("DELETE FROM communities")
        counts = {"new": 0, "labels_kept": 0, "stale_labels": 0, "unlabelled": 0}
        for i, members in enumerate(found["groups"]):
            mset = set(members)
            top = sorted(members, key=lambda e: (-note_counts.get(e, 0), -g.degree(e, weight="weight"), e))
            top = top[:TOP_ENTITIES]
            p = prev.get(matched[i]) if i in matched else None
            if p is not None:
                cid, created_at = p["community_id"], p["created_at"]
                label, summary, kind, labelled_at = p["label"], p["summary"], p["kind"], p["labelled_at"]
                labelled_members = p["labelled_member_ids_json"]
                stale = False
                if label:
                    then = set(json.loads(labelled_members or "[]"))
                    stale = bool(p["stale_label"]) or _jaccard(mset, then) < 1.0 - STALE_CHANGE
                    counts["labels_kept"] += 1
                    if carry is not None and jaccard_of[i] < KEEP_JACCARD:
                        carry["kept"].append({"community_id": cid, "label": label,
                                              "jaccard": round(jaccard_of[i], 4)})
            elif i in carried:
                p = prev[carried[i]]
                cid, created_at = community_id_for(members), now
                label, summary, kind, labelled_at = p["label"], p["summary"], p["kind"], p["labelled_at"]
                labelled_members = p["labelled_member_ids_json"]
                stale = True  # the reflect flow names it again, with this label as the previous one
                counts["new"] += 1
                carry["stale"].append({"community_id": cid, "previous_community_id": p["community_id"],
                                       "label": label, "jaccard": round(jaccard_of[i], 4)})
            else:
                cid, created_at = community_id_for(members), now
                label = summary = kind = labelled_at = labelled_members = None
                stale = False
                counts["new"] += 1
            counts["stale_labels"] += int(stale)
            counts["unlabelled"] += int(not label)
            c.execute("INSERT INTO communities(community_id, label, summary, kind, size, top_entity_ids_json,"
                      " member_hash, created_at, computed_at, labelled_at, labelled_member_ids_json, stale_label,"
                      " revision_count_at_compute) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, label, summary, kind, len(members), json.dumps(top), community_id_for(members), created_at,
                       now, labelled_at, labelled_members, int(stale), revisions))
            c.executemany("INSERT INTO entity_communities(entity_id, community_id) VALUES (?,?)",
                          [(e, cid) for e in members])
        if carry is not None:
            carry["dropped"] = [{"community_id": cid, "label": p["label"]} for cid, p in sorted(prev.items())
                                if p["label"] and cid not in used]
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('communities', ?)", (json.dumps({
            "computed_at": now, "resolution": res, "revision_count_at_compute": revisions,
            "loose_ends": len(found["loose"]), "entities": g.number_of_nodes(), "edges": g.number_of_edges(),
            "networkx": version}),))
    if carry is not None:
        _log.warning("communities: networkx %s -> %s; labels kept from Jaccard %.1f: %d, carried as stale: %d,"
                     " dropped: %d", carry["from_networkx"], version, floor, len(carry["kept"]),
                     len(carry["stale"]), len(carry["dropped"]))
    return {"communities": len(found["groups"]), "loose_ends": len(found["loose"]), "entities": g.number_of_nodes(),
            "edges": g.number_of_edges(), **counts, "resolution": res, "computed_at": now, "networkx": version,
            "carry_over": carry}


# --------------------------------------------------------------------------- reads


def _entity_refs(nb: Any, entity_ids: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for eid in entity_ids:
        r = nb.store.entity_row(nb.store.canonical(eid))
        if r is not None:
            out.append({"entity_id": r["entity_id"], "name": r["name"], "kind": r["kind"],
                        "note_count": int(r["note_count"] or 0)})
    return out


def _item(nb: Any, r: Any) -> Dict[str, Any]:
    return {"community_id": r["community_id"], "label": r["label"], "summary": r["summary"], "kind": r["kind"],
            "size": int(r["size"]), "top_entities": _entity_refs(nb, json.loads(r["top_entity_ids_json"] or "[]")),
            "stale_label": bool(r["stale_label"]), "labelled_at": r["labelled_at"], "created_at": r["created_at"]}


def communities(nb: Any) -> Dict[str, Any]:
    """``GET /note/communities``: ``{computed_at, resolution, networkx, revision_count_at_compute, revisions_since,
    loose_ends, items: [Community]}``, largest first; ``networkx`` is the version the last computation ran under
    (null if it recorded none); ``revisions_since`` counts revisions applied after the last computation (the graph
    moved); everything is null/0/empty before the first computation."""
    meta = nb.store.meta_get("communities") or {}
    at = int(meta.get("revision_count_at_compute") or 0)
    rows = nb.store.q("SELECT * FROM communities ORDER BY size DESC, community_id")
    return {"computed_at": meta.get("computed_at"), "resolution": meta.get("resolution"),
            "networkx": meta.get("networkx"),
            "revision_count_at_compute": at, "revisions_since": max(0, _revision_count(nb) - at) if meta else 0,
            "loose_ends": int(meta.get("loose_ends") or 0), "items": [_item(nb, r) for r in rows]}


def members(nb: Any, community_id: str) -> List[str]:
    """The entity ids of a community, sorted."""
    return [r[0] for r in nb.store.q("SELECT entity_id FROM entity_communities WHERE community_id=? ORDER BY entity_id",
                                     (community_id,))]


def community_of(nb: Any, entity_id: str) -> Optional[Dict[str, Any]]:
    """``{community_id, label, kind, size, stale_label}`` of an entity's community, or None (a loose end, or never
    computed)."""
    r = nb.store.q1("SELECT c.community_id, c.label, c.kind, c.size, c.stale_label FROM entity_communities ec JOIN"
                    " communities c ON c.community_id=ec.community_id WHERE ec.entity_id=?",
                    (nb.store.canonical(entity_id),))
    if r is None:
        return None
    return {"community_id": r["community_id"], "label": r["label"], "kind": r["kind"], "size": int(r["size"]),
            "stale_label": bool(r["stale_label"])}


def community_ids(nb: Any) -> Dict[str, str]:
    """``{entity_id: community_id}`` for every entity in a community."""
    return {r[0]: r[1] for r in nb.store.q("SELECT entity_id, community_id FROM entity_communities")}


def pending_ids(nb: Any) -> List[str]:
    """Every community that waits for a name (no label, or a stale one), in :func:`pending` order."""
    return [r[0] for r in nb.store.q("SELECT community_id FROM communities WHERE label IS NULL OR stale_label=1"
                                     " ORDER BY stale_label DESC, size DESC, community_id")]


def pending(nb: Any, limit: int = PENDING_LIMIT, exclude: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Communities with a stale label, then those without one, largest first within each (a stale label is named
    again at every reflect however many new groups wait, eval2 D14), with what the model needs to name them:
    ``{count, items: [{index, community_id, size, stale_label, previous_label, previous_summary, top_entities:
    [{entity_id, name, kind, note_count, summary}], claims: [{claim_id, text}], excerpts: [NoteRef]}], waiting,
    limit_applied}``. ``exclude``: community ids not to offer (a consolidation's groups skipped for this run, ADR
    0007). ``waiting`` = every group still waiting outside ``exclude``; ``limit_applied`` = the limit used after the
    1..20 clamp (ADR 0007: a clamped request is answered, never silent)."""
    from .changes import claim_text

    limit_applied = clamp_limit(limit, PENDING_LIMIT, 20)
    skip = {str(x) for x in exclude or () if x}
    waiting = [cid for cid in pending_ids(nb) if cid not in skip]
    chosen = waiting[:limit_applied]  # a selection (largest first), the rest stays waiting: ``waiting`` says how many
    by_id = {r["community_id"]: r for r in nb.store.q(
        f"SELECT * FROM communities WHERE community_id IN ({','.join('?' for _ in chosen)})", chosen)} if chosen else {}
    rows = [by_id[cid] for cid in chosen if cid in by_id]
    canon, _ = _canonical_map(nb)
    active = nb.store.q("SELECT * FROM claims WHERE status='active'") if rows else []
    mentions = nb.store.q("SELECT en.note_id, en.entity_id FROM entity_notes en JOIN notes n ON n.note_id=en.note_id"
                          " WHERE n.archived_at IS NULL") if rows else []
    items = []
    for i, r in enumerate(rows):
        mset = set(members(nb, r["community_id"]))
        tops = []
        for eid in json.loads(r["top_entity_ids_json"] or "[]"):
            e = nb.store.entity_row(canon.get(eid, eid))
            if e is not None:
                tops.append({"entity_id": e["entity_id"], "name": e["name"], "kind": e["kind"],
                             "note_count": int(e["note_count"] or 0), "summary": e["summary"]})
        claims = []
        for cr in active:
            s = canon.get(cr["subject_id"], cr["subject_id"])
            o = canon.get(cr["object_id"], cr["object_id"]) if cr["object_id"] else None
            if s in mset and (o is None or o in mset):
                claims.append(cr)
        # links inside the community first, then the most reinforced, the most certain, the earliest
        claims.sort(key=lambda cr: (cr["object_id"] is None, -int(cr["reinforced_count"] or 0),
                                    _CERTAINTY_RANK.get(cr["certainty"], 9), cr["observed_at"] or "", cr["claim_id"]))
        note_hits: Dict[str, set] = {}
        for nr in mentions:
            c = canon.get(nr["entity_id"], nr["entity_id"])
            if c in mset:
                note_hits.setdefault(nr["note_id"], set()).add(c)
        notes = [row for row in (nb.store.note_row(nid) for nid in sorted(note_hits)) if row is not None]
        # the notes that mention the most members, newest first among equals
        notes.sort(key=lambda n: n["occurred_at"] or "", reverse=True)
        notes.sort(key=lambda n: -len(note_hits[n["note_id"]]))
        items.append({
            "index": i, "community_id": r["community_id"], "size": int(r["size"]), "stale_label": bool(r["stale_label"]),
            "previous_label": r["label"], "previous_summary": r["summary"], "top_entities": tops,
            "claims": [{"claim_id": cr["claim_id"], "text": claim_text(nb, cr)} for cr in claims[:PENDING_CLAIMS]],
            "excerpts": [shapes.note_ref(n) for n in notes[:PENDING_EXCERPTS]],
        })
    return {"count": len(items), "items": items, "waiting": len(waiting), "limit_applied": limit_applied}


def label(nb: Any, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Store the model's names: ``items = [{community_id, label, summary, kind}]`` -> ``{labelled, warnings}``.
    A label of more than :data:`LABEL_MAX_WORDS` words is kept whole with an ``over_length`` warning (ADR 0007: never
    cut); an unknown kind becomes ``other``; an unknown community or an empty label is left out; each with a warning
    (also logged)."""
    nb.require_embedder()
    if not isinstance(items, (list, tuple)):
        raise ValueError("The labels must be a list of {community_id, label, summary, kind}.")
    labelled: List[str] = []
    warnings: List[str] = []
    now = ids.utc_now()
    with nb._apply_lock, nb.store.tx() as c:
        for it in items:
            if not isinstance(it, dict):
                warnings.append("A label that is not an object was left out.")
                continue
            cid = str(it.get("community_id") or "")
            if c.execute("SELECT 1 FROM communities WHERE community_id=?", (cid,)).fetchone() is None:
                warnings.append(f"The community {cid or '(none)'} is not in this notebook; its label was left out.")
                continue
            words = str(it.get("label") or "").split()
            if not words:
                warnings.append(f"The label of {cid} was empty; it was left out.")
                continue
            if len(words) > LABEL_MAX_WORDS:
                warnings.append(f"over_length: the label of {cid} has {len(words)} words (more than {LABEL_MAX_WORDS});"
                                " it was kept whole.")
            kind = str(it.get("kind") or "other")
            if kind not in COMMUNITY_KINDS:
                warnings.append(f"The kind {kind!r} of {cid} is not one of {', '.join(COMMUNITY_KINDS)}; it is other.")
                kind = "other"
            summary = " ".join(str(it.get("summary") or "").split()) or None
            current = [r[0] for r in c.execute("SELECT entity_id FROM entity_communities WHERE community_id=?"
                                               " ORDER BY entity_id", (cid,))]
            c.execute("UPDATE communities SET label=?, summary=?, kind=?, labelled_at=?, labelled_member_ids_json=?,"
                      " stale_label=0 WHERE community_id=?", (" ".join(words), summary, kind, now, json.dumps(current), cid))
            if cid not in labelled:
                labelled.append(cid)
    for w in warnings:
        _log.warning("communities label: %s", w)
    return {"labelled": labelled, "warnings": warnings}
