"""Graph expansion for standard recall: entity cards as of a moment, claim and relation
timelines, neighbours, then-versus-now pairs, and the evidence chain of a claim."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from .. import ids
from ..store.notes import _uj, clamp_limit
from . import shapes
from .then_now import relation_then_now, then_now

MAX_EXPAND_ENTITIES = 10
MAX_RELATION_PAIRS = 10
CARD_NOTES = 10


def _members(nb: Any, entity_id: str) -> List[str]:
    return nb.store.merged_members(nb.store.canonical(entity_id))


def _require_entity(nb: Any, entity_id: str) -> Any:
    row = nb.store.entity_row(entity_id)
    if row is None:
        raise ValueError("That entity is not in this notebook.")
    return row


def entity_card(nb: Any, entity_id: str, at: Optional[str] = None, as_received: Optional[str] = None
                ) -> Dict[str, Any]:
    """The entity as known at ``at`` (default now): name, aliases, summary and its history,
    beliefs valid at ``at``, relations, notes, open tasks and a monthly activity sparkline.

    ``as_received`` builds the card from what the archive held on that date only (notes received by then and
    the revisions they caused, :mod:`abstractnote.store.archive`); it composes with ``at``."""
    from ..store.archive import archive_for

    _require_entity(nb, entity_id)
    at_n = ids.optional_time(at, field="at")
    s = nb.store
    arch = archive_for(s, as_received)
    if arch is not None:
        return _archive_card(nb, arch, entity_id, at_n)
    eid = s.canonical_at(entity_id, at_n)
    row = s.entity_row(eid)
    if at_n and (row["first_seen"] or row["created_at"]) > at_n:
        raise ValueError("That entity was not known yet at that time.")
    members = s.merged_members(eid) if at_n is None else [
        m for m in s.merged_members(s.canonical(eid)) if s.canonical_at(m, at_n) == eid]
    if eid not in members:
        members.insert(0, eid)
    ref = at_n or "9999-12-31T23:59:59Z"
    q = ",".join("?" for _ in members)
    name_row = s.q1("SELECT name FROM entity_names WHERE entity_id=? AND at<=? ORDER BY at DESC, revision_id DESC LIMIT 1",
                    (eid, ref))
    name = name_row[0] if (name_row and at_n) else row["name"]
    aliases: List[str] = []
    for m in members:
        for a in s.aliases(m, at_n):
            if ids.fold(a) != ids.fold(name) and ids.fold(a) not in {ids.fold(x) for x in aliases}:
                aliases.append(a)
        if m != eid:
            mrow = s.entity_row(m)
            if ids.fold(mrow["name"]) != ids.fold(name) and ids.fold(mrow["name"]) not in {ids.fold(x) for x in aliases}:
                aliases.append(mrow["name"])
    history = [{"at": r["at"], "summary": r["summary"], "note_id": r["note_id"], "revision_id": r["revision_id"]}
               for r in s.q(f"SELECT * FROM entity_summaries WHERE entity_id IN ({q}) AND at<=? ORDER BY at, revision_id",
                            [*members, ref])]
    summary = history[-1]["summary"] if history else (row["summary"] if at_n is None else None)
    notes = s.q(f"SELECT DISTINCT n.* FROM entity_notes en JOIN notes n ON n.note_id=en.note_id WHERE en.entity_id IN"
                f" ({q}) AND en.at<=? AND n.archived_at IS NULL ORDER BY n.occurred_at DESC LIMIT ?",
                [*members, ref, CARD_NOTES])
    stats = s.q1(f"SELECT MIN(at), MAX(at), COUNT(DISTINCT note_id) FROM entity_notes WHERE entity_id IN ({q}) AND at<=?",
                 [*members, ref])
    tasks = s.q(f"SELECT * FROM tasks WHERE note_id IN (SELECT note_id FROM entity_notes WHERE entity_id IN ({q}))"
                f" AND opened_at<=? AND (closed_at IS NULL OR closed_at>?) ORDER BY opened_at", [*members, ref, ref])
    months: Dict[str, int] = {}
    for r in s.q(f"SELECT substr(at,1,7), COUNT(DISTINCT note_id) FROM entity_notes WHERE entity_id IN ({q}) AND at<=?"
                 f" GROUP BY substr(at,1,7) ORDER BY 1", [*members, ref]):
        months[r[0]] = r[1]
    return {
        "entity_id": eid, "resolved_from": entity_id if entity_id != eid else None, "as_of": at_n,
        "kind": row["kind"], "name": name, "aliases": aliases, "summary": summary, "summary_history": history,
        "first_seen": stats[0] or row["first_seen"], "last_seen": stats[1] or row["last_seen"],
        "note_count": int(stats[2] or 0), "merged_from": [m for m in members if m != eid],
        "merged_into": row["merged_into"] if at_n is None else None,
        "beliefs": [c for c in s.beliefs_at(at_n or ids.utc_now())
                    if c["subject_id"] in members or c["object_id"] in members],
        "relations": relations_of(nb, eid, at_n),
        "notes": [shapes.note_ref(r) for r in notes],
        "open_tasks": [shapes.task(r) | {"status": "open", "closed_at": None} for r in tasks],
        "sparkline": [{"month": k, "notes": v} for k, v in months.items()],
    }


def _archive_card(nb: Any, arch: Any, entity_id: str, at_n: Optional[str]) -> Dict[str, Any]:
    """:func:`entity_card` from what the archive held at ``arch.at`` (same keys, plus ``as_received``)."""
    s = nb.store
    eid = arch.canonical(entity_id, at_n)
    row = s.entity_row(eid)
    members = arch.members(eid, at_n)
    held = [m for m in members if arch.holds_entity(m)]
    if not held:
        raise ValueError("That entity was not in your notebook yet on that date.")
    ref = at_n or "9999-12-31T23:59:59Z"
    q = ",".join("?" for _ in members)
    in_rev, p_rev = arch.revisions_in("revision_id")
    in_note, p_note = arch.notes_in("en.note_id")
    stats = s.q1(f"SELECT MIN(en.at), MAX(en.at), COUNT(DISTINCT en.note_id) FROM entity_notes en WHERE en.entity_id"
                 f" IN ({q}) AND en.at<=? AND {in_note}", [*members, ref, *p_note])
    created = s.q1(f"SELECT MIN(created_at) FROM entities WHERE entity_id IN ({q}) AND"
                   f" {arch.revisions_in('created_revision_id')[0]}", [*members, arch.at])[0]
    first = min([x for x in (stats[0], created) if x], default=None)
    if at_n and (first is None or first > at_n):
        raise ValueError("That entity was not known yet at that time.")
    name = arch.name(eid, at_n) or row["name"]
    aliases: List[str] = []
    for m in members:
        for (a,) in s.q(f"SELECT alias FROM entity_aliases WHERE entity_id=? AND added_at<=? AND {in_rev}"
                        " ORDER BY added_at, alias", [m, ref, *p_rev]):
            if ids.fold(a) != ids.fold(name) and ids.fold(a) not in {ids.fold(x) for x in aliases}:
                aliases.append(a)
        if m != eid:
            mname = arch.name(m, at_n) or s.entity_row(m)["name"]
            if ids.fold(mname) != ids.fold(name) and ids.fold(mname) not in {ids.fold(x) for x in aliases}:
                aliases.append(mname)
    history = [{"at": r["at"], "summary": r["summary"], "note_id": r["note_id"], "revision_id": r["revision_id"]}
               for r in s.q(f"SELECT * FROM entity_summaries WHERE entity_id IN ({q}) AND at<=? AND {in_rev}"
                            " ORDER BY at, revision_id", [*members, ref, *p_rev])]
    notes = s.q(f"SELECT DISTINCT n.* FROM entity_notes en JOIN notes n ON n.note_id=en.note_id WHERE en.entity_id IN"
                f" ({q}) AND en.at<=? AND n.archived_at IS NULL AND {in_note} ORDER BY n.occurred_at DESC LIMIT ?",
                [*members, ref, *p_note, CARD_NOTES])
    opened, p_open = arch.revisions_in("t.opened_revision_id")
    tasks = []
    for r in s.q(f"SELECT t.* FROM tasks t WHERE t.note_id IN (SELECT en.note_id FROM entity_notes en WHERE"
                 f" en.entity_id IN ({q})) AND t.opened_at<=? AND {opened} ORDER BY t.opened_at",
                 [*members, ref, *p_open]):
        closed = r["closed_at"] is not None and arch.has(r["closed_revision_id"]) and r["closed_at"] <= ref
        if not closed:
            tasks.append(shapes.task(r) | {"status": "open", "closed_at": None})
    months: Dict[str, int] = {}
    for r in s.q(f"SELECT substr(en.at,1,7), COUNT(DISTINCT en.note_id) FROM entity_notes en WHERE en.entity_id IN"
                 f" ({q}) AND en.at<=? AND {in_note} GROUP BY substr(en.at,1,7) ORDER BY 1", [*members, ref, *p_note]):
        months[r[0]] = r[1]
    return {
        "entity_id": eid, "resolved_from": entity_id if entity_id != eid else None, "as_of": at_n,
        "as_received": arch.at, "kind": row["kind"], "name": name, "aliases": aliases,
        "summary": history[-1]["summary"] if history else None, "summary_history": history,
        "first_seen": first, "last_seen": stats[1] or first, "note_count": int(stats[2] or 0),
        "merged_from": [m for m in members if m != eid], "merged_into": None,
        "beliefs": [c for c in s.beliefs_at(at_n or ids.utc_now(), as_received=arch.at)
                    if c["subject_id"] in members or c["object_id"] in members],
        "relations": relations_of(nb, eid, at_n, as_received=arch.at),
        "notes": [shapes.note_ref(r) for r in notes],
        "open_tasks": tasks,
        "sparkline": [{"month": k, "notes": v} for k, v in months.items()],
    }


def relations_of(nb: Any, entity_id: str, at: Optional[str] = None, *, as_received: Optional[str] = None
                 ) -> List[Dict[str, Any]]:
    graph = nb.store.graph(focus=[entity_id], at=at, depth=1, limit=2000, as_received=as_received)
    if as_received:
        from ..store.archive import Archive

        eid = Archive(nb.store, as_received).canonical(entity_id, at)
    else:
        eid = nb.store.canonical_at(entity_id, at)
    names = {n["id"]: n["name"] for n in graph["nodes"]}
    out = []
    for e in graph["edges"]:
        if eid not in (e["source"], e["target"]):
            continue
        other = e["target"] if e["source"] == eid else e["source"]
        out.append({"other_id": other, "other_name": names.get(other), "predicate": e["predicate"],
                    "direction": "out" if e["source"] == eid else "in", "first_seen": e["first_seen"],
                    "last_seen": e["last_seen"], "count": e["count"], "active": e["active"]})
    out.sort(key=lambda r: (-r["count"], r["other_id"], r["predicate"]))
    return out


def _claim_events(nb: Any, rows: Iterable[Any]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        d = nb.store.claim_dict(r)
        closed = None
        if r["closed_revision_id"]:
            rv = nb.store.revision_row(r["closed_revision_id"])
            closed = {"revision_id": r["closed_revision_id"], "at": rv["at"] if rv else None,
                      "note_id": rv["note_id"] if rv else None, "reason": r["closed_reason"],
                      "kind": "retract" if r["status"] == "retracted" else "supersede"}
        d["closed"] = closed
        d["reinforcements"] = [{"note_id": x["note_id"], "at": x["at"]} for x in nb.store.q(
            "SELECT * FROM claim_reinforcements WHERE claim_id=? ORDER BY at", (r["claim_id"],))]
        out.append(d)
    return out


def claims_timeline(nb: Any, entity_id: str, since: Optional[str] = None, until: Optional[str] = None
                    ) -> List[Dict[str, Any]]:
    """Claims about an entity (as subject or object) in history order, with their supersessions."""
    _require_entity(nb, entity_id)
    members = _members(nb, entity_id)
    q = ",".join("?" for _ in members)
    sql = f"SELECT * FROM claims WHERE (subject_id IN ({q}) OR object_id IN ({q}))"
    params: List[Any] = [*members, *members]
    if since:
        sql += " AND observed_at>=?"
        params.append(ids.normalize_time(since, field="since"))
    if until:
        sql += " AND observed_at<=?"
        params.append(ids.normalize_time(until, field="until"))
    return _claim_events(nb, nb.store.q(sql + " ORDER BY observed_at, seq", params))


def entity_timeline(nb: Any, entity_id: str, since: Optional[str] = None, until: Optional[str] = None
                    ) -> Dict[str, Any]:
    """Claims and notes about the entity in history order (``GET /note/entities/{id}/timeline``)."""
    _require_entity(nb, entity_id)
    eid = nb.store.canonical(entity_id)
    members = _members(nb, entity_id)
    q = ",".join("?" for _ in members)
    since_n = ids.optional_time(since, field="since")
    until_n = ids.optional_time(until, field="until")
    items: List[Dict[str, Any]] = []
    for c in claims_timeline(nb, eid, since_n, until_n):
        items.append({"type": "claim", "at": c["observed_at"], "claim": c})
        if c["closed"] and c["closed"]["at"]:
            items.append({"type": c["closed"]["kind"], "at": c["closed"]["at"], "claim_id": c["claim_id"],
                          "note_id": c["closed"]["note_id"], "reason": c["closed"]["reason"],
                          "superseded_by": c["superseded_by"]})
    sql = (f"SELECT DISTINCT n.* FROM entity_notes en JOIN notes n ON n.note_id=en.note_id WHERE en.entity_id IN ({q})"
           " AND n.archived_at IS NULL")
    params: List[Any] = list(members)
    if since_n:
        sql += " AND n.occurred_at>=?"
        params.append(since_n)
    if until_n:
        sql += " AND n.occurred_at<=?"
        params.append(until_n)
    for r in nb.store.q(sql, params):
        items.append({"type": "note", "at": r["occurred_at"], "note": shapes.note_ref(r)})
    for r in nb.store.q(f"SELECT * FROM merges WHERE from_id IN ({q}) OR into_id IN ({q})", [*members, *members]):
        items.append({"type": "merge", "at": r["at"], "from_id": r["from_id"], "into_id": r["into_id"],
                      "reason": r["reason"], "undone_at": r["undone_at"]})
    order = {"note": 0, "claim": 1, "supersede": 2, "retract": 2, "merge": 3}
    items = [i for i in items if (not since_n or i["at"] >= since_n) and (not until_n or i["at"] <= until_n)]
    items.sort(key=lambda i: (i["at"], order.get(i["type"], 9)))
    return {"entity_id": eid, "since": since_n, "until": until_n, "items": items}


def relation(nb: Any, a: str, b: str, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    """The relationship between two entities through time: claims between them in history order,
    supersessions, and a then-versus-now of the link (``GET /note/relations/{a}/{b}``)."""
    _require_entity(nb, a)
    _require_entity(nb, b)
    ma, mb = _members(nb, a), _members(nb, b)
    qa, qb = ",".join("?" for _ in ma), ",".join("?" for _ in mb)
    sql = (f"SELECT * FROM claims WHERE ((subject_id IN ({qa}) AND object_id IN ({qb})) OR"
           f" (subject_id IN ({qb}) AND object_id IN ({qa})))")
    params: List[Any] = [*ma, *mb, *mb, *ma]
    since_n = ids.optional_time(since, field="since")
    until_n = ids.optional_time(until, field="until")
    if since_n:
        sql += " AND observed_at>=?"
        params.append(since_n)
    if until_n:
        sql += " AND observed_at<=?"
        params.append(until_n)
    claims = _claim_events(nb, nb.store.q(sql + " ORDER BY observed_at, seq", params))
    supersessions = [{"claim_id": c["claim_id"], "superseded_by": c["superseded_by"], **(c["closed"] or {})}
                     for c in claims if c["closed"]]
    return {"a": nb.store.entity_brief(nb.store.canonical(a)), "b": nb.store.entity_brief(nb.store.canonical(b)),
            "since": since_n, "until": until_n, "claims": claims, "supersessions": supersessions,
            "then_now": relation_then_now(claims)}


def neighbours(nb: Any, entity_ids: Sequence[str], at: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    seen = {nb.store.canonical_at(e, at) for e in entity_ids}
    out: Dict[str, Dict[str, Any]] = {}
    for e in seen:
        for r in relations_of(nb, e, at):
            if r["other_id"] in seen:
                continue
            n = out.setdefault(r["other_id"], {"entity_id": r["other_id"], "name": r["other_name"], "via": [],
                                               "count": 0})
            n["via"].append({"entity_id": e, "predicate": r["predicate"], "direction": r["direction"]})
            n["count"] += r["count"]
    ranked = sorted(out.values(), key=lambda n: (-n["count"], n["entity_id"]))[: clamp_limit(limit, 20, 200)]
    for n in ranked:
        row = nb.store.entity_row(n["entity_id"])
        n["kind"] = row["kind"] if row else None
    return ranked


def expand(nb: Any, note_ids: Any = None, entity_ids: Any = None, as_of: Optional[str] = None) -> Dict[str, Any]:
    """``note_expand``: cards, claim timelines, relation timelines, neighbours and then-versus-now
    for the given entities plus the entities the given notes mention."""
    as_of_n = ids.optional_time(as_of, field="as_of")
    wanted: List[str] = []
    for e in list(entity_ids or []):
        if nb.store.entity_row(str(e)) is not None:
            c = nb.store.canonical_at(str(e), as_of_n)
            if c not in wanted:
                wanted.append(c)
    for nid in list(note_ids or []):
        for r in nb.store.q("SELECT entity_id FROM entity_notes WHERE note_id=? ORDER BY entity_id", (str(nid),)):
            c = nb.store.canonical_at(r[0], as_of_n)
            if c not in wanted:
                wanted.append(c)
    wanted = wanted[:MAX_EXPAND_ENTITIES]
    cards, timelines, thens = [], [], []
    for e in wanted:
        try:
            cards.append(entity_card(nb, e, as_of_n))
        except ValueError:
            continue  # not known yet at as_of
        tl = claims_timeline(nb, e, until=as_of_n)
        timelines.append({"entity_id": e, "claims": tl})
        thens.append(then_now(nb, e))
    pairs = []
    known = {c["entity_id"] for c in cards}
    for i, a in enumerate(sorted(known)):
        for b in sorted(known)[i + 1:]:
            if nb.store.q1("SELECT 1 FROM relations WHERE (subject_id=? AND object_id=?) OR (subject_id=? AND"
                           " object_id=?)", (a, b, b, a)):
                pairs.append((a, b))
    rel = [relation(nb, a, b, until=as_of_n) for a, b in pairs[:MAX_RELATION_PAIRS]]
    return {"as_of": as_of_n, "entities": cards, "claims_timeline": timelines, "relation_timelines": rel,
            "neighbours": neighbours(nb, list(known), as_of_n), "then_now": thens}


def claim_chain(nb: Any, claim_id: str) -> Dict[str, Any]:
    """Why do I believe this: the claim, what it superseded or was superseded by, the notes and
    attachment excerpts behind it (``GET /note/claims/{id}``)."""
    row = nb.store.claim_row(claim_id)
    if row is None:
        raise ValueError("That statement is not in this notebook.")
    [claim] = _claim_events(nb, [row])
    supersedes = _claim_events(nb, nb.store.q("SELECT * FROM claims WHERE superseded_by=? ORDER BY observed_at",
                                              (claim_id,)))
    superseded_by = None
    if row["superseded_by"]:
        r2 = nb.store.claim_row(row["superseded_by"])
        superseded_by = _claim_events(nb, [r2])[0] if r2 else None
    disputed = [nb.store.claim_dict(x) for x in (nb.store.claim_row(c) for c in _uj(row["disputed_with_json"], []))
                if x is not None]
    note_ids = [row["note_id"]] if row["note_id"] else []
    note_ids += [r["note_id"] for r in nb.store.q("SELECT note_id FROM claim_reinforcements WHERE claim_id=? AND"
                                                  " note_id IS NOT NULL ORDER BY at", (claim_id,))]
    notes = []
    for nid in dict.fromkeys(note_ids):
        n = nb.store.note_row(nid)
        if n is None:
            continue
        ref = shapes.note_ref(n)
        ref["attachments"] = []
        for a in nb.store.attachments_of(nid):
            path = nb.root / "extracted" / f"{a['sha256']}.txt"
            text = path.read_text(encoding="utf-8") if a["extracted_chars"] and path.exists() else ""
            ref["attachments"].append({"attachment_id": a["attachment_id"], "filename": a["filename"],
                                       "mime": a["mime"], "extracted_by": a["extracted_by"],
                                       "excerpt": _around(text, row["evidence"])})
        notes.append(ref)
    rev = nb.store.revision_row(row["revision_id"])
    return {"claim": claim, "supersedes": supersedes, "superseded_by": superseded_by, "disputed_with": disputed,
            "notes": notes, "revision": {k: v for k, v in nb.store.revision_dict(rev).items() if k != "ops"}
            if rev else None}


def _around(text: str, evidence: Optional[str], width: int = 400) -> str:
    """An excerpt of attachment text around the evidence quote when it occurs verbatim, else its start."""
    if not text:
        return ""
    pos = text.find(evidence) if evidence else -1
    if pos < 0:
        return shapes.excerpt(text, width)
    start = max(0, pos - width // 2)
    return ("…" if start else "") + text[start: start + width] + ("…" if start + width < len(text) else "")
