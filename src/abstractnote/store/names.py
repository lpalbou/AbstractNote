"""One canonical name per entity, at apply time and as a repair (docs/data-model.md "Identity").

``reuse_taken_names`` rewrites a new revision before it is stored: an ``entity.create`` whose canonical name
(``ids.name_key``) already belongs to a live entity becomes a reuse of that entity, whatever the model decided;
the surface forms become aliases and the revision records a warning. Two entities
that differ only by case, spacing or punctuation are never created. This is deterministic normalisation, not an
identity judgement (ADR 0004): ``Gateway`` and ``abstractgateway`` stay the model's and the review's call, and so
does a name that is only an alias of another entity (aliases are the model's words, and often loose: a project
listed with its parts' names as aliases). Alias keys only produce candidates (``process.resolve``).

``merge_plan`` lists the groups of live entities an older notebook holds under one canonical name, plus the
entries whose name is another entity's name with its kind echoed in parentheses (``memory (software)`` next to
``Memory``, canonical ``memory_software`` = ``memory`` + ``_`` + a kind: the linking packet once showed candidates
as ``name (kind)``; structural, see :func:`strip_kind_suffix`); ``Notebook.normalize_entities`` merges each group
into its earliest entity.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .. import ids

_ENTITY_FIELDS = ("entity_id", "from_id", "into_id", "subject_id", "object_id")
CLOSE_MATCH = "skos:closeMatch"  # the review item process/revise.py records for a match kept apart


def strip_kind_suffix(name: Any) -> str:
    """``"memory (software)"`` -> ``"memory"``: a name that ends with one of the entity kinds in parentheses
    (`` (<kind>)``, case-folded) loses that suffix; any other name is returned unchanged. Structural, not
    semantic (ADR 0004): the linking packet used to show candidates as ``name (kind)`` and the model sometimes
    copied that whole form into a name (gate 04 M4). Applied to mention names at apply time
    (``process.revise``) and by :func:`merge_plan` to entries already stored."""
    text = str(name or "")
    body = text.strip()
    if body.endswith(")"):
        head, sep, tail = body[:-1].rpartition(" (")
        if sep and head.strip() and tail.casefold() in ids.ENTITY_KINDS:
            return head.strip()
    return text


def kind_suffix_target(name_key: Optional[str], surface: Any) -> Optional[str]:
    """The canonical name an entry names once its echoed kind is stripped, or None: its surface form ends with
    `` (<kind>)`` and its canonical name is that name's canonical name + ``_<kind>``."""
    stripped = strip_kind_suffix(surface)
    if not name_key or stripped == str(surface or ""):
        return None
    base = ids.name_key(stripped)
    kind = str(surface).strip()[:-1].rpartition(" (")[2].casefold()
    return base if name_key == f"{base}_{kind}" else None


def _holder_view(store: Any, eid: str, created: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if eid in created:
        return created[eid]
    row = store.entity_row(eid)
    return {"kind": row["kind"], "name": row["name"], "summary": row["summary"],
            "folds": {ids.fold(row["name"]), ids.fold(row["display_name"] or row["name"]),
                      *(ids.fold(a[0]) for a in store.q("SELECT alias FROM entity_aliases WHERE entity_id=?", (eid,)))}}


def _identical_active(store: Any, subject: str, predicate: str, obj_id: Optional[str], literal: Optional[str],
                      at: Optional[str] = None) -> Optional[str]:
    """The same statement already believed on ``at`` (its window open then or starting later, never retracted;
    the covering one first), as ``process.revise`` finds it; without ``at``, the one active now."""
    s = store.merged_members(store.canonical(subject))
    if at:
        sql = (f"SELECT claim_id, valid_from FROM claims WHERE status!='retracted' AND (valid_until IS NULL OR"
               f" valid_until>?) AND predicate=? AND subject_id IN ({','.join('?' for _ in s)})")
        params: List[Any] = [at, predicate, *s]
    else:
        sql = (f"SELECT claim_id, valid_from FROM claims WHERE status='active' AND valid_until IS NULL AND predicate=?"
               f" AND subject_id IN ({','.join('?' for _ in s)})")
        params = [predicate, *s]
    if obj_id:
        o = store.merged_members(store.canonical(obj_id))
        sql += f" AND object_id IN ({','.join('?' for _ in o)})"
        params += o
    else:
        sql += " AND object_id IS NULL AND object_literal=?"
        params.append(literal)
    rows = store.q(sql + " ORDER BY observed_at, claim_id", params)
    covering = [r for r in rows if not at or r[1] is None or r[1] <= at]
    return (covering or rows)[0][0] if rows else None


def _summary_dated_after(store: Any, holder: str, at: Optional[str]) -> bool:
    """Whether the holder's current summary (the latest dated one) describes a later day than ``at``: a summary
    written for an earlier date is then dated knowledge to keep (ADR 0008 §2), not a replacement of the card."""
    row = store.q1("SELECT at FROM entity_summaries WHERE entity_id=? ORDER BY at DESC, rowid DESC LIMIT 1", (holder,))
    return bool(at and row and str(row[0])[:10] > str(at)[:10])


def _alias_close_matches(store: Any, rev: Dict[str, Any], eid: str, name: str, aliases: List[str],
                         taken: set) -> Tuple[List[Dict[str, Any]], List[str]]:
    """ADR 0008 §6 (retro-B fix 7): a created entity one of whose aliases is a live entity's canonical name is kept
    apart (the model created it) with a ``skos:closeMatch`` review item to that entity, never a silent duplicate."""
    ops: List[Dict[str, Any]] = []
    warnings: List[str] = []
    at = rev.get("at")
    for alias in aliases:
        holder = store.name_key_holder(ids.name_key(alias))
        if holder is None or holder == eid:
            continue
        cid = ids.claim_id(rev.get("note_id") or rev.get("revision_id") or "", eid, CLOSE_MATCH, holder)
        if cid in taken:
            continue
        taken.add(cid)
        ops.append({"op": "claim.add", "payload": {
            "claim_id": cid, "subject_id": eid, "predicate": CLOSE_MATCH, "object_id": holder, "object_literal": None,
            "certainty": "possible", "stance": "assert", "observed_at": at, "valid_from": at, "valid_until": None,
            "evidence": alias, "note_id": rev.get("note_id")}})
        warnings.append(f"“{name}” has the alias “{alias}”, the canonical name of {holder}; the match is kept for "
                        "review.")
    return ops, warnings


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
    close_ids = {op["payload"].get("claim_id") for op in ops_in if op["op"] == "claim.add"}
    for op in ops_in:
        name, p = op["op"], dict(op.get("payload") or {})
        remapped = False
        for f in _ENTITY_FIELDS:
            if p.get(f) in redirect:
                p[f] = redirect[p[f]]
                remapped = True
        if name == "claim.insert_before" and p.get("claim_id") in claim_redirect:
            continue  # its statement became a reinforcement of one already believed (or was left out)
        for f in ("claim_id", "by_claim_id", "disputes", "before_claim_id", "after_claim_id"):
            if p.get(f) in claim_redirect:
                p[f] = claim_redirect[p[f]]
        if name == "entity.create" and store.entity_row(p["entity_id"]) is None and p["entity_id"] not in created:
            surface = str(p.get("name") or "").strip()
            aliases = [str(a).strip() for a in p.get("aliases") or [] if str(a or "").strip()]
            key = ids.name_key(surface)
            holder = created_keys.get(key) or store.name_key_holder(key)
            if holder is None:
                created[p["entity_id"]] = {"kind": p["kind"], "name": ids.canonical_name(surface),
                                           "summary": p.get("summary"),
                                           "folds": {ids.fold(surface), *(ids.fold(a) for a in aliases)}}
                created_keys.setdefault(key, p["entity_id"])
                out.append({"op": name, "payload": p})
                extra, said = _alias_close_matches(store, rev, p["entity_id"], surface, aliases, close_ids)
                out.extend(extra)
                warnings.extend(said)
                continue
            redirect[p["entity_id"]] = holder
            view = _holder_view(store, holder, created)
            for alias in [surface, *aliases]:
                if alias and ids.fold(alias) not in view["folds"]:
                    view["folds"].add(ids.fold(alias))
                    out.append({"op": "entity.alias", "payload": {"entity_id": holder, "alias": alias}})
            retro = holder not in created and _summary_dated_after(store, holder, rev.get("at"))
            if p.get("summary") and holder not in created and (not view.get("summary") or retro):
                # kept when the holder has none, and when the holder's current summary describes a later day: the
                # note's own summary is the entity as of its date (dated history; the card stays the latest)
                previous = view.get("summary")
                view["summary"] = view.get("summary") or p["summary"]
                out.append({"op": "entity.summary", "payload": {"entity_id": holder, "summary": p["summary"],
                                                                "previous": previous}})
            sentence = f"“{surface}” has the canonical name {key}, which {holder} already has; it was filed there."
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
                                         p.get("object_literal"), rev.get("at"))
                if same:
                    claim_redirect[p["claim_id"]] = same
                    out.append({"op": "claim.reinforce", "payload": {"claim_id": same,
                                                                     "note_id": p.get("note_id")}})
                    from ..process.revise import start_earlier_op  # the dated placement (no import cycle at load)
                    moved = start_earlier_op(store, store.claim_row(same), rev["at"], p.get("note_id"),
                                             p.get("evidence"), "an earlier note says it already held")
                    if moved is not None:
                        out.append(moved)
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
    """``[{into_id, from_ids, name, names, kinds, kind_suffix}]``: each group of live entities sharing a canonical
    name, to be merged into its earliest entity, joined by the entries whose surface form is that name with its
    kind echoed in parentheses (:func:`kind_suffix_target`; their ids are also listed in ``kind_suffix``). The
    group's entity is the earliest one that has the name itself. A pair a person separated again (an undone
    merge) stays apart."""
    rows = store.q("SELECT entity_id, name_key, name, display_name FROM entities WHERE merged_into IS NULL AND"
                   " name_key IS NOT NULL ORDER BY created_at, recorded_at, entity_id")
    holder: Dict[str, str] = {}
    for r in rows:
        holder.setdefault(r["name_key"], r["entity_id"])
    groups: Dict[str, List[str]] = {}
    suffixed: set = set()
    for r in rows:
        key = r["name_key"]
        base = kind_suffix_target(key, r["display_name"] or r["name"])
        if base is not None and base in holder:
            key = base
            suffixed.add(r["entity_id"])
        groups.setdefault(key, []).append(r["entity_id"])
    plan = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        into = holder[key]
        group = [into, *(e for e in members if e != into)]
        q = ",".join("?" for _ in group)
        apart = store.q(f"SELECT from_id, into_id FROM merges WHERE undone_at IS NOT NULL AND from_id IN ({q})"
                        f" AND into_id IN ({q})", [*group, *group])
        undone = {x for pair in apart for x in pair}
        froms = [e for e in group[1:] if e not in undone]
        if not froms:
            continue
        rows_by = {e: store.entity_row(e) for e in [into, *froms]}
        plan.append({"into_id": into, "from_ids": froms, "name": rows_by[into]["name"],
                     "names": [rows_by[e]["display_name"] or rows_by[e]["name"] for e in [into, *froms]],
                     "kinds": [rows_by[e]["kind"] for e in [into, *froms]],
                     "kind_suffix": [e for e in froms if e in suffixed]})
    return plan


def merge_ops(store: Any, into_id: str, from_id: str, *, kind_suffix: bool = False
              ) -> Tuple[List[Dict[str, Any]], str]:
    """The ops of one normalisation merge (the merge plus the separated entry's names as aliases) and its summary.
    ``kind_suffix``: the entry is the name with its kind echoed in parentheses; that form is not kept as an alias."""
    into, frm = store.entity_row(into_id), store.entity_row(from_id)
    reason = f"the same canonical name ({into['name_key'] or into['name']})"
    if kind_suffix:
        reason = f"the same name with its kind in parentheses ({frm['display_name'] or frm['name']})"
    ops: List[Dict[str, Any]] = [{"op": "entity.merge", "payload": {"from_id": from_id, "into_id": into_id,
                                                                    "reason": reason}}]
    names = [] if kind_suffix else [frm["display_name"] or frm["name"]]
    names += [*(a[0] for a in store.q(
        "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY added_at, alias", (from_id,)))]
    if kind_suffix:
        names = [strip_kind_suffix(n) for n in names]
    known = {ids.fold(into["name"]), ids.fold(into["display_name"] or into["name"]), *(ids.fold(a[0]) for a in store.q(
        "SELECT alias FROM entity_aliases WHERE entity_id=?", (into_id,)))}
    for alias in names:
        if str(alias or "").strip() and ids.fold(alias) not in known:
            known.add(ids.fold(alias))
            ops.append({"op": "entity.alias", "payload": {"entity_id": into_id, "alias": alias}})
    return ops, f"Joined {frm['display_name'] or frm['name']} into {into['name']}: {reason}."
