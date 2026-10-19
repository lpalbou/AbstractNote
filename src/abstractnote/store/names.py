"""One canonical name per entity, at apply time and as a repair (docs/data-model.md "Identity").

``reuse_taken_names`` rewrites a new revision before it is stored: an ``entity.create`` whose canonical name
(``ids.name_key``) or one of whose alias keys already belongs to a live entity becomes a reuse of that entity,
whatever the model decided; the surface forms become aliases and the revision records a warning. Two entities
that differ only by case, spacing or punctuation are never created. This is deterministic normalisation, not an
identity judgement (ADR 0004): ``Gateway`` and ``abstractgateway`` stay the model's and the review's call.

``merge_plan`` lists the groups of live entities an older notebook holds under one canonical name (or alias
key); ``Notebook.normalize_entities`` merges each group into its earliest entity.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import ids

_ENTITY_FIELDS = ("entity_id", "from_id", "into_id", "subject_id", "object_id")


def _holder_view(store: Any, eid: str, created: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if eid in created:
        return created[eid]
    row = store.entity_row(eid)
    return {"kind": row["kind"], "name": row["name"], "summary": row["summary"],
            "folds": {ids.fold(row["name"]), ids.fold(row["display_name"] or row["name"]),
                      *(ids.fold(a[0]) for a in store.q("SELECT alias FROM entity_aliases WHERE entity_id=?", (eid,)))}}


def _identical_active(store: Any, subject: str, predicate: str, obj_id: Optional[str], literal: Optional[str]
                      ) -> Optional[str]:
    s = store.merged_members(store.canonical(subject))
    sql = (f"SELECT claim_id FROM claims WHERE status='active' AND valid_until IS NULL AND predicate=? AND"
           f" subject_id IN ({','.join('?' for _ in s)})")
    params: List[Any] = [predicate, *s]
    if obj_id:
        o = store.merged_members(store.canonical(obj_id))
        sql += f" AND object_id IN ({','.join('?' for _ in o)})"
        params += o
    else:
        sql += " AND object_id IS NULL AND object_literal=?"
        params.append(literal)
    row = store.q1(sql + " ORDER BY observed_at LIMIT 1", params)
    return row[0] if row else None


def reuse_taken_names(store: Any, rev: Dict[str, Any]) -> Dict[str, Any]:
    """The revision with every create of a taken canonical name turned into a reuse (see the module doc).

    Later ops of the revision follow the reused entity; a statement that becomes identical to an active one
    reinforces it, one that would link an entity to itself is left out. A rename to a canonical name another
    live entity holds is refused (ValueError): that is a merge, which the person or the model decides.
    """
    ops_in = rev.get("ops") or []
    if not any(op["op"] in ("entity.create", "entity.rename") for op in ops_in):
        return rev
    redirect: Dict[str, str] = {}
    claim_redirect: Dict[str, Optional[str]] = {}
    created: Dict[str, Dict[str, Any]] = {}  # entities this revision creates (kept), by id
    created_keys: Dict[str, str] = {}
    warnings: List[str] = list(rev.get("warnings") or [])
    out: List[Dict[str, Any]] = []
    mentioned: set = set()
    for op in ops_in:
        name, p = op["op"], dict(op.get("payload") or {})
        remapped = False
        for f in _ENTITY_FIELDS:
            if p.get(f) in redirect:
                p[f] = redirect[p[f]]
                remapped = True
        for f in ("claim_id", "by_claim_id", "disputes"):
            if p.get(f) in claim_redirect:
                p[f] = claim_redirect[p[f]]
        if name == "entity.create" and store.entity_row(p["entity_id"]) is None and p["entity_id"] not in created:
            surface = str(p.get("name") or "").strip()
            aliases = [str(a).strip() for a in p.get("aliases") or [] if str(a or "").strip()]
            keys = [k for k in dict.fromkeys(ids.name_key(x) for x in [surface, *aliases]) if k]
            holder, key = None, None
            for k in keys:
                holder = created_keys.get(k) or store.name_key_holder(k)
                if holder:
                    key = k
                    break
            if holder is None:
                created[p["entity_id"]] = {"kind": p["kind"], "name": ids.canonical_name(surface),
                                           "summary": p.get("summary"),
                                           "folds": {ids.fold(surface), *(ids.fold(a) for a in aliases)}}
                for k in keys:
                    created_keys.setdefault(k, p["entity_id"])
                out.append({"op": name, "payload": p})
                continue
            redirect[p["entity_id"]] = holder
            view = _holder_view(store, holder, created)
            for alias in [surface, *aliases]:
                if alias and ids.fold(alias) not in view["folds"]:
                    view["folds"].add(ids.fold(alias))
                    out.append({"op": "entity.alias", "payload": {"entity_id": holder, "alias": alias}})
            if p.get("summary") and not view.get("summary") and holder not in created:
                view["summary"] = p["summary"]
                out.append({"op": "entity.summary", "payload": {"entity_id": holder, "summary": p["summary"],
                                                                "previous": None}})
            if holder not in created:
                created_keys.setdefault(key, holder)
            sentence = (f"“{surface}” has the canonical name {key}, which {view['name']} ({holder}) "
                        "already has; it was filed under that entity.")
            if p.get("kind") != view["kind"]:
                sentence += f" kind_conflict: the note said {p.get('kind')}, the notebook keeps {view['kind']}."
            warnings.append(sentence)
            continue
        if name == "entity.rename":
            key = ids.name_key(p.get("name"))
            other = store.name_key_holder(key, exclude=[store.canonical(p["entity_id"])])
            if other is not None:
                raise ValueError(f"Another entity ({other}) is already called {key}; merge the two instead.")
        if name == "entity.merge" and p.get("from_id") == p.get("into_id"):
            continue  # both sides became the same entity
        if name == "entity.mention":
            key = (p.get("entity_id"), p.get("note_id"))
            if key in mentioned:
                continue
            mentioned.add(key)
        if name == "claim.add":
            if p.get("object_id") and p["object_id"] == p["subject_id"]:
                claim_redirect[p["claim_id"]] = None
                warnings.append(f"A statement linked {p['subject_id']} to itself once the names were joined; "
                                "it was left out.")
                continue
            if remapped and store.entity_row(p["subject_id"]) is not None and (
                    not p.get("object_id") or store.entity_row(p["object_id"]) is not None):
                same = _identical_active(store, p["subject_id"], p["predicate"], p.get("object_id"),
                                         p.get("object_literal"))
                if same:
                    claim_redirect[p["claim_id"]] = same
                    out.append({"op": "claim.reinforce", "payload": {"claim_id": same,
                                                                     "note_id": p.get("note_id")}})
                    continue
        if name in ("claim.supersede", "claim.retract", "claim.reinforce") and p.get("claim_id") is None:
            continue  # its statement was left out above
        if name == "claim.supersede" and p.get("claim_id") == p.get("by_claim_id"):
            continue
        out.append({"op": name, "payload": p})
    if not redirect and out == ops_in:
        return rev
    return {**rev, "ops": out, "warnings": warnings}


def merge_plan(store: Any) -> List[Dict[str, Any]]:
    """``[{into_id, from_ids, name, names}]``: each group of live entities sharing a canonical name or an alias
    key, to be merged into its earliest entity. A pair a person separated again (an undone merge) stays apart."""
    plan = []
    for group in store.name_key_groups():
        into = group[0]
        q = ",".join("?" for _ in group)
        apart = store.q(f"SELECT from_id, into_id FROM merges WHERE undone_at IS NOT NULL AND from_id IN ({q})"
                        f" AND into_id IN ({q})", [*group, *group])
        undone = {x for pair in apart for x in pair}
        froms = [e for e in group[1:] if e not in undone]
        if not froms:
            continue
        rows = {e: store.entity_row(e) for e in [into, *froms]}
        plan.append({"into_id": into, "from_ids": froms, "name": rows[into]["name"],
                     "names": [rows[e]["display_name"] or rows[e]["name"] for e in [into, *froms]],
                     "kinds": [rows[e]["kind"] for e in [into, *froms]]})
    return plan


def merge_ops(store: Any, into_id: str, from_id: str) -> Tuple[List[Dict[str, Any]], str]:
    """The ops of one normalisation merge (the merge plus the separated entry's names as aliases) and its summary."""
    into, frm = store.entity_row(into_id), store.entity_row(from_id)
    reason = f"the same canonical name ({frm['name_key'] or frm['name']})"
    if frm["name_key"] != into["name_key"]:
        reason = f"a shared name ({frm['name_key']}, also a name of {into['name']})"
    ops: List[Dict[str, Any]] = [{"op": "entity.merge", "payload": {"from_id": from_id, "into_id": into_id,
                                                                    "reason": reason}}]
    names = [frm["display_name"] or frm["name"], *(a[0] for a in store.q(
        "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY added_at, alias", (from_id,)))]
    for alias in dict.fromkeys(names):
        ops.append({"op": "entity.alias", "payload": {"entity_id": into_id, "alias": alias}})
    return ops, f"Joined {frm['display_name'] or frm['name']} into {into['name']}: {reason}."
