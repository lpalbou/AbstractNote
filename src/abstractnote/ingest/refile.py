"""``abstractnote refile --missing``: the quick filing of understood notes that never got one.

Imports skip the quick filing a live capture gets (eval2: 985 of 988 understood imported notes had no kind and no
filing topics); note-process now files a note without a kind before it reads it, and this repair files the notes
understood before that. One ``note-file`` run per note (the capture's filing packet, prompt, ``QuickFiling`` schema
and settings: one call, thinking off, kind + up to 4 topics), oldest note first, within a count and a time budget.

Nothing is left out without a word (ADR 0007): each repair is one ``refile_runs`` row, updated after every note
(``done``, ``filed``, ``skipped`` with each note's reason, ``kinds``), and closed ``completed`` when nothing is left,
``completed_with_gaps`` when a note could not be filed or the budget did not reach every note (``not_reached``), or
``interrupted`` when the repair itself stopped. The answer says what is left in one sentence.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from .. import ids

_log = logging.getLogger("abstractnote.refile")

DEFAULT_MAX_ITEMS = 50
DEFAULT_BUDGET_S = 600.0

MISSING_SQL = ("FROM notes WHERE kind IS NULL AND insight_kind IS NULL AND archived_at IS NULL AND"
               " processed_at IS NOT NULL")


def missing(nb: Any, limit: Optional[int] = None) -> List[str]:
    """Understood notes without a kind (not insights, not archived), oldest first."""
    sql = f"SELECT note_id {MISSING_SQL} ORDER BY occurred_at, note_id"
    params: List[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [r[0] for r in nb.store.q(sql, params)]


def _row(nb: Any, refile_id: str) -> Dict[str, Any]:
    r = nb.store.q1("SELECT * FROM refile_runs WHERE refile_id=?", (refile_id,))
    out = dict(r)
    for k in ("filed_json", "skipped_json", "kinds_json"):
        out[k[:-5]] = json.loads(out.pop(k) or "[]" if k != "kinds_json" else out.pop(k) or "{}")
    return out


def _update(nb: Any, refile_id: str, **cols: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    with nb.store.tx() as c:
        c.execute(f"UPDATE refile_runs SET {sets} WHERE refile_id=?", [*cols.values(), refile_id])


def notice(done: int, skipped: int, not_reached: int) -> Optional[str]:
    """The one sentence of what is left, None when nothing is."""
    left = skipped + not_reached
    if not left:
        return None
    words = "1 note is" if left == 1 else f"{left} notes are"
    why = []
    if skipped:
        why.append("could not be filed" if skipped == left else f"{skipped} could not be filed")
    if not_reached:
        why.append("were not reached this time" if not_reached == left else f"{not_reached} were not reached this time")
    return f"{words} still without a kind ({' and '.join(why)}); run it again to file them."


def open_refile(nb: Any, *, max_items: int = DEFAULT_MAX_ITEMS, budget_s: float = DEFAULT_BUDGET_S,
                model: Optional[str] = None) -> Dict[str, Any]:
    """Open one repair's ``refile_runs`` row (status ``running``) and answer it; :func:`refile_missing` with its
    ``refile_id`` does the filing (the gateway answers the open row at once and files in the background)."""
    max_items = max(1, int(max_items))
    budget_s = float(budget_s)
    total = int(nb.store.q1(f"SELECT COUNT(*) {MISSING_SQL}")[0])
    refile_id = "rf_" + uuid.uuid4().hex[:16]
    with nb.store.tx() as c:
        c.execute("INSERT INTO refile_runs(refile_id, started_at, status, total, max_items, budget_s, model)"
                  " VALUES (?,?,?,?,?,?,?)", (refile_id, ids.utc_now(), "running", total, max_items, budget_s, model))
    return refile_row(nb, refile_id)


def refile_row(nb: Any, refile_id: str) -> Dict[str, Any]:
    """One repair's row, ``{refile_id, started_at, finished_at, status, total, done, filed, skipped, kinds,
    not_reached, max_items, budget_s, seconds, model, notice}``."""
    out = _row(nb, refile_id)
    out["notice"] = (notice(out["done"], len(out["skipped"]), out["not_reached"])
                     if out["status"] != "running" else None)
    return out


def latest_refile(nb: Any) -> Optional[Dict[str, Any]]:
    """The newest repair's row, None when none ran."""
    r = nb.store.q1("SELECT refile_id FROM refile_runs ORDER BY started_at DESC, rowid DESC LIMIT 1")
    return refile_row(nb, r[0]) if r else None


def interrupt_running(nb: Any, keep: Any = ()) -> int:
    """Close as ``interrupted`` every ``running`` repair not in ``keep`` (the ones this process is still filing):
    a row left running by a process that stopped. Returns how many were closed."""
    rows = [r[0] for r in nb.store.q("SELECT refile_id FROM refile_runs WHERE status='running'") if r[0] not in keep]
    for refile_id in rows:
        row = _row(nb, refile_id)
        _update(nb, refile_id, finished_at=ids.utc_now(), status="interrupted",
                not_reached=max(0, int(row["total"]) - len(row["filed"]) - len(row["skipped"])))
        _log.warning("refile %s was left running by a stopped process: closed as interrupted", refile_id)
    return len(rows)


def refile_missing(nb: Any, file_one: Callable[[str], Dict[str, Any]], *, max_items: int = DEFAULT_MAX_ITEMS,
                   budget_s: float = DEFAULT_BUDGET_S, model: Optional[str] = None,
                   clock: Callable[[], float] = time.monotonic, refile_id: Optional[str] = None) -> Dict[str, Any]:
    """File up to ``max_items`` understood notes without a kind within ``budget_s`` seconds; ``file_one(note_id)``
    runs the note-file flow for one note and answers its output (``{filed, kind, topics}``) or raises with the
    reason. Returns the closed ``refile_runs`` row plus ``notice``. ``refile_id``: a row :func:`open_refile`
    opened (its count and budget are used)."""
    if refile_id is None:
        refile_id = open_refile(nb, max_items=max_items, budget_s=budget_s, model=model)["refile_id"]
    opened = _row(nb, refile_id)
    if opened["status"] != "running":
        raise ValueError("That repair is closed already.")
    max_items, budget_s, total = int(opened["max_items"]), float(opened["budget_s"]), int(opened["total"])
    todo = missing(nb, max_items)
    start = clock()
    filed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    kinds: Dict[str, int] = {}
    done = 0
    status = "interrupted"
    try:
        for note_id in todo:
            if clock() - start >= budget_s:
                break
            t0 = clock()
            try:
                out = file_one(note_id) or {}
            except Exception as exc:  # the flow's own sentence: recorded, the next note is filed
                reason = str(exc).strip() or "The filing failed."
                skipped.append({"note_id": note_id, "reason": reason})
                _log.warning("refile %s: %s was not filed: %s", refile_id, note_id, reason)
            else:
                row = nb.store.note_row(note_id)
                if out.get("filed") and row is not None and row["kind"]:
                    filed.append({"note_id": note_id, "kind": row["kind"], "topics": list(out.get("topics") or []),
                                  "seconds": round(clock() - t0, 1)})
                    kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
                else:
                    reason = "It was not filed: it has a kind already, or nothing to read."
                    skipped.append({"note_id": note_id, "reason": reason})
                    _log.warning("refile %s: %s %s", refile_id, note_id, reason)
            done += 1
            _update(nb, refile_id, done=done, filed_json=json.dumps(filed), skipped_json=json.dumps(skipped),
                    kinds_json=json.dumps(kinds, sort_keys=True))
        status = "running"
    finally:
        not_reached = max(0, total - len(filed) - len(skipped))
        if status == "running":
            status = "completed_with_gaps" if (skipped or not_reached) else "completed"
        else:
            _log.warning("refile %s stopped after %d notes", refile_id, done)
        _update(nb, refile_id, finished_at=ids.utc_now(), status=status, not_reached=not_reached,
                seconds=round(clock() - start, 1))
        if not_reached and status != "interrupted":
            _log.warning("refile %s: %d notes without a kind were not reached (count %d, budget %.0f s)", refile_id,
                         not_reached, max_items, budget_s)
    out = refile_row(nb, refile_id)
    out["response"] = (f"Filed {len(filed)} {'note' if len(filed) == 1 else 'notes'}."
                       + (" " + out["notice"] if out["notice"] else ""))
    return out
