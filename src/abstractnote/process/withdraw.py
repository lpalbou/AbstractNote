"""Archiving a note withdraws its reading; bringing it back restores it (W28; ADR 0008 §4; data-model.md "Archive").

A person's archive is a statement about the note ("this should not shape what my notebook believes"), so its effects
go with it, the way a withdrawn interpretation's do (R2's inverse table), in the same user revision (``note.archive``):

- its own statements close as a RETRACTION (reason ``note archived``; AbstractMemory journal kind ``retract``), the
  window emptied at the statement's start (``claim.retract`` with ``withdrawn``): no belief at any date;
- statements of other notes it had closed (superseded, retracted, narrowed) re-open to the state the other revisions
  give them (``claim.reopen``), its reinforcements are dropped (``reinforcement.drop``), the starts its earlier
  evidence moved back return (``claim.start_restore``);
- tasks of other notes it closed re-open (``task.reopen``), its own tasks retire (``task.retire``);
- entities only it created and nothing else refers to retire (``entity.retire``);
- its mentions and topics are soft-removed and the summaries it wrote leave the dated history (the ``note.archive``
  op itself, :meth:`abstractnote.store.notes._OpContext.op_note_archive`).

Every withdrawing op keeps what it replaced (``previous``), so ``note.unarchive`` is its exact inverse: each op is
undone while what it set still stands (a statement or task something else changed since is left as it is, with a
warning). The dependents are marked for re-understanding both ways (``source_archived`` / ``source_unarchived``).
Planning is pure (nothing is written): the ops go into one user revision that the notebook applies.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import ids
from ..store.kg import earlier_evidence_id
from ..store.notes import ARCHIVE_REASON, REINTERPRETED, TASK_RETIRED
from .revise import CLOSE_MATCH, _claim_fields, _copy_id, replay_claim_start, replay_claim_state

ARCHIVE_ACTION = "note.archive"
UNARCHIVE_ACTION = "note.unarchive"
UNARCHIVE_REASON = "note brought back from the archive"
_STATE_KEYS = ("status", "valid_until", "superseded_by", "closed_reason", "closed_revision_id")
_TASK_KEYS = ("status", "closed_at", "closed_by_note_id", "closed_revision_id", "recorded_closed_at")


def _op(name: str, **payload: Any) -> Dict[str, Any]:
    return {"op": name, "payload": payload}


def _q(n: int) -> str:
    return ",".join("?" for _ in range(n))


def archive_group(store: Any, note_id: str) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """``(revision_id, ops)`` of the archive in force for an archived note: the latest applied revision with a
    ``note.archive`` op of it, and the ops of that note's group (from its ``note.archive`` op to the next one). None
    when the note was archived without a revision (before W28) or is not archived."""
    for r in store.q("SELECT revision_id, ops_json FROM revisions WHERE applied_index=1 AND abandoned=0 AND ops_json"
                     " LIKE ? AND ops_json LIKE ? ORDER BY seq DESC", ('%"note.archive"%', f'%"{note_id}"%')):
        ops = json.loads(r["ops_json"])
        start = next((i for i, o in enumerate(ops) if o.get("op") == ARCHIVE_ACTION and
                      (o.get("payload") or {}).get("note_id") == note_id), None)
        if start is None:
            continue
        end = next((i for i in range(start + 1, len(ops)) if ops[i].get("op") == ARCHIVE_ACTION), len(ops))
        return r["revision_id"], ops[start:end]
    return None


def is_withdrawn(store: Any, note_id: str) -> bool:
    """The note is archived and its archive withdrew its reading (an archive written before W28 did not)."""
    row = store.note_row(note_id)
    if row is None or row["archived_at"] is None:
        return False
    group = archive_group(store, note_id)
    return bool(group and (group[1][0].get("payload") or {}).get("withdrawn"))


def _previous_status(row: Any) -> str:
    if row["status"] != "archived":
        return row["status"]
    return "processed" if row["processed_at"] else "received"  # archived before its status was kept (pre-W28)


def plan_archive(nb: Any, note_ids: Sequence[str], rid: str, *, reason: Optional[str] = None,
                 duplicate_of: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The ops of one revision ``rid`` that archives ``note_ids`` and withdraws their readings (pure). Notes archived
    together are each other's "elsewhere" for nothing: an entity only they refer to retires, a statement of one that
    another closed re-opens to the state the remaining revisions give it. Returns ``{ops, diff}`` (``diff`` counts
    per kind: claims_retracted, claims_reopened, reinforcements_dropped, starts_restored, tasks_reopened,
    tasks_retired, entities_retired, mentions)."""
    s = nb.store
    batch = list(dict.fromkeys(str(n) for n in note_ids))
    rows = {n: s.note_row(n) for n in batch}
    for n, r in rows.items():
        if r is None:
            raise ValueError(f"The note {n} is not in this notebook.")
    chains = {n: s.interpretation_chain(n) for n in batch}
    every = [rv for n in batch for rv in chains[n]]
    bq = _q(len(batch))
    diff = {k: 0 for k in ("claims_retracted", "claims_reopened", "reinforcements_dropped", "starts_restored",
                           "tasks_reopened", "tasks_retired", "entities_retired", "mentions")}
    ops: List[Dict[str, Any]] = []
    done_claims: set = set()
    done_tasks: set = set()
    done_entities: set = set()
    why = str(reason or "").strip() or ARCHIVE_REASON
    for n in batch:
        row = rows[n]
        queue = s.q1("SELECT state FROM queue WHERE note_id=?", (n,))
        ops.append(_op(ARCHIVE_ACTION, note_id=n, reason=why, withdrawn=True,
                       **({"duplicate_of": duplicate_of[n]} if duplicate_of and duplicate_of.get(n) else {}),
                       previous={"status": _previous_status(row), "duplicate_of": row["duplicate_of"],
                                 "queue_state": queue[0] if queue else None}))
        diff["mentions"] += int(s.q1("SELECT COUNT(*) FROM entity_notes WHERE note_id=?", (n,))[0] or 0)
        chain = chains[n]
        if not chain:
            continue  # never understood (or an insight): nothing it said shapes the graph
        cq = _q(len(chain))
        for r in s.q("SELECT * FROM claims WHERE note_id=? AND status NOT IN ('retracted', ?) ORDER BY seq",
                     (n, REINTERPRETED)):
            ops.append(_op("claim.retract", claim_id=r["claim_id"], note_id=n, withdrawn_note_id=n, withdrawn=True,
                           reason=ARCHIVE_REASON, valid_until=r["valid_from"] or r["observed_at"],
                           assertion_id=r["assertion_id"] or r["claim_id"],
                           previous={k: r[k] for k in _STATE_KEYS}))
            done_claims.add(r["claim_id"])
            diff["claims_retracted"] += 1
        # what its reading closed (the last closer recorded), and what one of its own statements replaced (another
        # revision may have closed it last, e.g. an earlier note's evidence narrowing it): the replay decides
        for x in s.q(f"SELECT * FROM claims WHERE (closed_revision_id IN ({cq}) OR superseded_by IN (SELECT claim_id FROM"
                     f" claims WHERE note_id=?)) AND (note_id IS NULL OR note_id NOT IN ({bq})) AND status!=? ORDER BY"
                     f" seq", [*chain, n, *batch, REINTERPRETED]):
            if x["claim_id"] in done_claims or (x["status"] == "retracted" and x["closed_revision_id"] not in chain):
                continue  # retracted by someone else (a person, another archive): that retraction stands
            st = replay_claim_state(nb, x["claim_id"], every)
            if all(st[k] == x[k] for k in _STATE_KEYS if k != "closed_reason"):
                continue  # the other revisions leave it as it is: nothing to re-open
            copy = _copy_id(rid, x["claim_id"])
            ops.append(_op("claim.reopen", claim_id=x["claim_id"], withdrawn_note_id=n,
                           closed_by_revision=x["closed_revision_id"], assertion_id=copy,
                           previous_assertion_id=x["assertion_id"] or x["claim_id"],
                           empty_at=x["valid_from"] or x["observed_at"],
                           assertion={**_claim_fields(x), "claim_id": copy, "of_claim_id": x["claim_id"],
                                      "valid_until": st["valid_until"]},
                           reason="the note that closed it was archived",
                           previous={**{k: x[k] for k in _STATE_KEYS}, "assertion_id": x["assertion_id"]}, **st))
            done_claims.add(x["claim_id"])
            diff["claims_reopened"] += 1
        for r in s.q(f"SELECT claim_id, note_id, revision_id, at FROM claim_reinforcements WHERE revision_id IN ({cq})"
                     " ORDER BY claim_id, revision_id", chain):
            ops.append(_op("reinforcement.drop", claim_id=r["claim_id"], revision_id=r["revision_id"],
                           note_id=r["note_id"], at=r["at"], withdrawn_note_id=n))
            diff["reinforcements_dropped"] += 1
        moved: Dict[str, List[Dict[str, Any]]] = {}
        for rv in chain:
            r = s.revision_row(rv)
            for o in json.loads(r["ops_json"]) if r is not None and r["applied_index"] else []:
                if o.get("op") == "claim.start_earlier" and (o.get("payload") or {}).get("claim_id"):
                    moved.setdefault(o["payload"]["claim_id"], []).append({**o["payload"], "_note": r["note_id"]})
        for cid in sorted(moved):
            row_c = s.claim_row(cid)
            if row_c is None or cid in done_claims:
                continue
            back = replay_claim_start(nb, cid, every)
            evidence = [{"assertion_id": aid, "empty_at": mp["valid_from"]} for mp in moved[cid]
                        if (aid := earlier_evidence_id(mp, mp["_note"]))]
            ops.append(_op("claim.start_restore", claim_id=cid, observed_at=back["observed_at"],
                           valid_from=back["valid_from"], earlier_assertions=evidence, withdrawn_note_id=n,
                           reason="the note whose earlier evidence moved it was archived",
                           previous={"observed_at": row_c["observed_at"], "valid_from": row_c["valid_from"]},
                           moves=[{k: v for k, v in mp.items() if k != "_note"} for mp in moved[cid]]))
            diff["starts_restored"] += 1
        for t in s.q(f"SELECT * FROM tasks WHERE closed_revision_id IN ({cq}) AND (note_id IS NULL OR note_id NOT IN"
                     f" ({bq})) ORDER BY task_id", [*chain, *batch]):
            if t["task_id"] in done_tasks:
                continue
            ops.append(_op("task.reopen", task_id=t["task_id"], closed_revision_id=t["closed_revision_id"],
                           withdrawn_note_id=n, previous={k: t[k] for k in _TASK_KEYS}))
            done_tasks.add(t["task_id"])
            diff["tasks_reopened"] += 1
        for t in s.q("SELECT * FROM tasks WHERE note_id=? AND status!=? ORDER BY task_id", (n, TASK_RETIRED)):
            ops.append(_op("task.retire", task_id=t["task_id"], reason=ARCHIVE_REASON, withdrawn_note_id=n,
                           previous={k: t[k] for k in _TASK_KEYS}))
            done_tasks.add(t["task_id"])
            diff["tasks_retired"] += 1
        for (eid,) in s.q(f"SELECT entity_id FROM entities WHERE created_revision_id IN ({cq}) AND merged_into IS NULL"
                          " AND retired_revision_id IS NULL ORDER BY entity_id", chain):
            if eid in done_entities or _referenced_elsewhere(s, eid, batch):
                continue
            ops.append(_op("entity.retire", entity_id=eid, note_id=n, withdrawn_note_id=n,
                           reason="only an archived note made it", read_as=[]))
            done_entities.add(eid)
            diff["entities_retired"] += 1
    return {"ops": ops, "diff": diff}


def _referenced_elsewhere(s: Any, eid: str, batch: Sequence[str]) -> bool:
    """Something other than the notes being archived refers to the entity (or an entry merged into it): a mention
    by another note, a statement of another note (or a person's) that still says something, or a task of another
    note that names it. A close-match review statement does not keep it (R2's rule)."""
    members = s.merged_members(eid)
    mq, bq = _q(len(members)), _q(len(batch))
    if s.q1(f"SELECT 1 FROM entity_notes WHERE entity_id IN ({mq}) AND note_id NOT IN ({bq}) LIMIT 1",
            [*members, *batch]):
        return True
    return s.q1(f"SELECT 1 FROM claims WHERE (subject_id IN ({mq}) OR object_id IN ({mq})) AND (note_id IS NULL OR"
                f" note_id NOT IN ({bq})) AND status!=? AND predicate!=? LIMIT 1",
                [*members, *members, *batch, REINTERPRETED, CLOSE_MATCH]) is not None


def plan_unarchive(nb: Any, note_ids: Sequence[str], rid: str) -> Dict[str, Any]:
    """The ops of one revision ``rid`` that brings archived notes back (pure): the inverse of each one's archive, op by
    op, while what the archive set still stands. ``{ops, diff, warnings}``."""
    s = nb.store
    ops: List[Dict[str, Any]] = []
    warnings: List[str] = []
    diff = {k: 0 for k in ("claims_reopened", "claims_closed_again", "reinforcements_restored", "starts_moved_back",
                           "tasks_restored", "entities_restored")}
    for n in dict.fromkeys(str(x) for x in note_ids):
        row = s.note_row(n)
        if row is None:
            raise ValueError(f"The note {n} is not in this notebook.")
        if row["archived_at"] is None:
            raise ValueError("That note is not archived.")
        group = archive_group(s, n)
        if group is None:  # archived before W28 without a revision: nothing was withdrawn, nothing to restore
            queued = s.q1("SELECT state FROM queue WHERE note_id=?", (n,))
            ops.append(_op(UNARCHIVE_ACTION, note_id=n, archive_revision_id=None, status=_previous_status(row),
                           duplicate_of=row["duplicate_of"],
                           requeue=bool(queued and row["processed_at"] is None and not row["insight_kind"])))
            continue
        arid, group_ops = group
        head = group_ops[0].get("payload") or {}
        prev = head.get("previous") or {}
        requeue = prev.get("queue_state") in ("queued", "failed") and row["processed_at"] is None
        ops.append(_op(UNARCHIVE_ACTION, note_id=n, archive_revision_id=arid if head.get("withdrawn") else None,
                       status=prev.get("status") or _previous_status(row),
                       duplicate_of=prev.get("duplicate_of", row["duplicate_of"]), requeue=bool(requeue)))
        for o in group_ops[1:]:
            name, p = o.get("op"), o.get("payload") or {}
            back = _inverse(s, rid, arid, n, name, p)
            if back is None:
                continue
            if isinstance(back, str):
                warnings.append(back)
                continue
            ops.append(back[0])
            diff[back[1]] += 1
    return {"ops": ops, "diff": diff, "warnings": warnings}


def _inverse(s: Any, rid: str, arid: str, note_id: str, name: str, p: Dict[str, Any]
             ) -> "Optional[Tuple[Dict[str, Any], str] | str]":
    """The op that undoes one withdrawing op of archive ``arid`` (and the diff key it counts under), a warning when
    something else changed it since, or None when there is nothing to undo."""
    prev = p.get("previous") or {}
    if name in ("claim.retract", "claim.reopen"):
        row = s.claim_row(p["claim_id"])
        if row is None:
            return None
        if name == "claim.retract":
            if row["status"] != "retracted" or row["closed_revision_id"] != arid:
                return f"A statement of this note changed since it was archived; it was left as it is ({p['claim_id']})."
        elif row["closed_revision_id"] != p.get("closed_revision_id") or row["status"] != p.get("status"):
            return f"A statement the archive re-opened changed since; it was left as it is ({p['claim_id']})."
        copy = _copy_id(rid, p["claim_id"])
        state = {k: prev.get(k) for k in _STATE_KEYS}
        reopen = name == "claim.retract"
        return (_op("claim.reopen", claim_id=p["claim_id"], withdrawn_note_id=note_id, assertion_id=copy,
                    # the copy a retraction closed empty stays closed; the copy an archive re-opened closes empty
                    previous_assertion_id=None if reopen else (row["assertion_id"] or row["claim_id"]),
                    empty_at=row["valid_from"] or row["observed_at"],
                    assertion={**_claim_fields(row), "claim_id": copy, "of_claim_id": p["claim_id"],
                               "valid_until": state["valid_until"]},
                    reason=UNARCHIVE_REASON, closed_by_revision=row["closed_revision_id"], **state),
                "claims_reopened" if reopen else "claims_closed_again")
    if name == "reinforcement.drop":
        if s.claim_row(p["claim_id"]) is None:
            return None
        return (_op("claim.reinforce", claim_id=p["claim_id"], note_id=p.get("note_id") or note_id, at=p.get("at"),
                    withdrawn_note_id=note_id), "reinforcements_restored")
    if name == "claim.start_restore":
        moves = p.get("moves") or []
        if not moves:
            return None
        mp = sorted(moves, key=lambda m: str(m.get("observed_at") or ""))[0]
        mp = {k: v for k, v in mp.items() if k not in ("after_claim_id", "after_valid_until")}
        aid = earlier_evidence_id(mp, mp.get("note_id"))
        if aid:
            mp["evidence_assertion_id"] = ids.claim_id(rid, p["claim_id"], "claim.start_earlier",
                                                       str(mp.get("valid_from")))
        return (_op("claim.start_earlier", **mp, withdrawn_note_id=note_id), "starts_moved_back")
    if name in ("task.reopen", "task.retire"):
        t = s.q1("SELECT * FROM tasks WHERE task_id=?", (p["task_id"],))
        if t is None:
            return None
        stands = (t["status"] == TASK_RETIRED and t["removed_revision_id"] == arid) if name == "task.retire" else (
            t["status"] == "open" and t["closed_revision_id"] is None)
        if not stands:
            return f"A task the archive changed changed again since; it was left as it is ({p['task_id']})."
        return (_op("task.restore", task_id=p["task_id"], withdrawn_note_id=note_id,
                    **{k: prev.get(k) for k in _TASK_KEYS}), "tasks_restored")
    if name == "entity.retire":
        e = s.entity_row(p["entity_id"])
        if e is None or e["retired_revision_id"] != arid:
            return None  # brought back since (a later mention)
        return (_op("entity.restore", entity_id=p["entity_id"], retired_revision_id=arid, withdrawn_note_id=note_id),
                "entities_restored")
    return None
