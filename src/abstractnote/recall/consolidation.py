"""Memory consolidation (backlog 0032, docs/cognition.md "note-consolidate").

A third automation per notebook runs the ``note-consolidate`` flow in stages (:data:`STAGES`): merge duplicate
entities (normalize), understand again the notes an earlier note made out of date (reinterpret, ADR 0008: in history
order, each note once per pass, with progress and ETA; a note whose context did not change since it was understood is
``unchanged`` without a model call), recompute the communities, name the pending or stale ones (labels), refresh the entity
summaries that are placeholders or whose notes or statements changed since the summary (summaries), refresh the
then-versus-now caches of what was touched (then_now) and run the reflect steps (reflect).

Every stage reports its progress through :func:`progress`, stored in the ``consolidation_runs`` table (one row per
consolidation; the open row is the one with ``finished_at`` null). The ETA of a stage is the items it still has to
do times the seconds per item measured so far in that stage (the previous consolidation's measure for that stage
until one item is done).

Which summaries are stale is decided structurally, never from their wording (ADR 0004):

- ``empty``: no summary;
- ``placeholder``: the summary is still the one the entry was created with, and the creating revision carries the
  revision builder's own warning that no summary was written (``process.revise``: "No summary was written for
  <name>; it was described from this note.");
- ``retrospective``: a note dated earlier than what the notebook knew of the entry was applied after its current
  summary (``Notebook.retro_state()["stale_summaries"]``, ADR 0008 §2): what the entry is now must be understood
  again with that earlier foundation;
- ``changed``: a note mention or a statement about the entry (or an entry merged into it) was recorded by a
  revision after the revision of its CURRENT summary (the last one in dated order, ``store.notes.SUMMARY_ORDER``:
  the one its card shows, not the one written last). ``summary_revision_count`` is that summary revision's
  sequence number in the revision log.

The summary packet shows the entry's dated history, earliest first (``history``: the summaries each note wrote for
its own date; ``earlier``: the statements that held once and ended), next to what holds now, so the model writes
what the entry is now, having been what it was.

A refreshed summary is an ``entity.summary`` op in a revision of its own (action ``entity.summary``, ``run_id`` =
the consolidation id, source client ``consolidation``), so the summary history keeps every version and a rebuild
replays it. Each op carries ``as_of`` = the latest dated knowledge it was derived from (the entry's last mention,
or its current summary's date when later): the summary is dated there in the history, so it is the current one
until a note dated later writes another, and a note dated in between is never hidden behind it.

Nothing stops or is left out silently (ADR 0007, root ADR-0026). An entry or a group the model does not answer
for, even when asked a second time, is *skipped for this run*: :func:`progress` records it on its stage
(``stages_json[stage]``: ``skipped``, ``reason``, ``reasons``, ``skipped_ids``) and logs a warning on the
``abstractnote.consolidation`` logger. :func:`end` closes a run that left something out (a skip still pending at
the close, or what the time budget did not reach, ``results.not_reached``) as ``completed_with_gaps`` with
``left_out = {summaries, labels, reasons}``, ``budget_spent`` and a one-sentence ``notice`` for end users; the
next consolidation tries those again. A summary longer than :data:`SUMMARY_MAX_CHARS` is kept whole with an
``over_length`` warning.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .. import ids

STAGES = ("normalize", "reinterpret", "communities", "labels", "summaries", "then_now", "reflect")
DONE = "done"
STATUSES = ("completed", "completed_with_gaps", "failed")
#: Closing statuses of a run that finished its stages (``end`` picks the one that tells the truth).
FINISHED = ("completed", "completed_with_gaps")

SUMMARY_BATCH = 8            # entries per summary model call
LABEL_BATCH = 5              # communities per labelling call (recall.communities.PENDING_LIMIT)
PACKET_CLAIMS = 8            # statements shown per entry
PACKET_NOTES = 5             # note excerpts shown per entry: the ones the current summary does not account for yet
PACKET_NEW_NOTES = 3         # (at most this many, recorded after it, ``new``), the newest, the earliest, then newest
PACKET_HISTORY = 6           # dated summaries shown per entry: the earliest and the latest ones (``omitted.history``)
PACKET_EARLIER = 8           # ended statements shown per entry: the earliest and the latest ones (``omitted.earlier``)
SUMMARY_MAX_CHARS = 600      # a longer answer is kept whole with an over_length warning (the prompt asks for two sentences)
LAST_RUNS = 20               # finished rows kept per notebook (older ones are deleted)
INTERRUPTED = "It stopped before finishing."
BUDGET_REASON = "The time set aside for the consolidation ran out before it got to them."
#: ADR 0008 stage: notes taken per ``note_reinterpret_next`` call (one model-backed re-understanding at a time).
REINTERPRET_BATCH = 1
#: A note understood again this many times in one UTC day waits for the next day (review §6: a stated bound on
#: oscillation; the hold is a skip with its reason, counted in ``left_out``, never silent).
REINTERPRET_PER_DAY = 3
HELD_REASON = "It was understood again three times today; it waits for tomorrow."
FAILED_REASON = "It could not be understood again this time."
#: A note a drain or an inline understanding holds (its queue claim or lease, R4) waits for the next pass.
BUSY_REASON = "It is being understood right now; it is read again next time."

_log = logging.getLogger("abstractnote.consolidation")

#: The revision builder's warning for a created entry without a summary (abstractnote.process.revise).
PLACEHOLDER_WARNING = "No summary was written for {name}; it was described from this note."

_PROMPTS = Path(__file__).resolve().parent.parent / "process" / "prompts"
SUMMARY_PROMPT = "entity_summaries"

_CERTAINTY_RANK = {"stated": 0, "probable": 1, "reported": 2, "possible": 3, "disputed": 4}


def summary_system_prompt() -> str:
    """The static system prompt of the summary step (``process/prompts/entity_summaries.system.md``)."""
    return (_PROMPTS / f"{SUMMARY_PROMPT}.system.md").read_text(encoding="utf-8").strip() + "\n"


def summary_template() -> str:
    """The user-message template of the summary step (``{{entries}}``)."""
    return (_PROMPTS / f"{SUMMARY_PROMPT}.md").read_text(encoding="utf-8").rstrip()


SUMMARY_TEMPLATE_VARS = frozenset({"entries"})


# --------------------------------------------------------------------------- rows


def _now_ts() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _row_dict(r: Any) -> Dict[str, Any]:
    return {k: r[k] for k in r.keys()}


def open_row(nb: Any) -> Optional[Dict[str, Any]]:
    r = nb.store.q1("SELECT * FROM consolidation_runs WHERE finished_at IS NULL ORDER BY started_ts DESC LIMIT 1")
    return _row_dict(r) if r is not None else None


def _require_open(nb: Any) -> Dict[str, Any]:
    row = open_row(nb)
    if row is None:
        raise ValueError("No consolidation is running; begin one first.")
    return row


def _stages(row: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(row.get("stages_json") or "{}") or {}


def _ids(row: Dict[str, Any], col: str) -> List[str]:
    return list(json.loads(row.get(col) or "[]") or [])


def _update(nb: Any, cid: str, **cols: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    with nb.store.tx() as c:
        c.execute(f"UPDATE consolidation_runs SET {sets} WHERE consolidation_id=?", (*cols.values(), cid))


def _add_ids(nb: Any, cid: str, col: str, new: Iterable[str]) -> None:
    with nb.store.tx() as c:
        r = c.execute(f"SELECT {col} FROM consolidation_runs WHERE consolidation_id=?", (cid,)).fetchone()
        have = list(json.loads(r[0] or "[]") or []) if r else []
        for x in new:
            if x and x not in have:
                have.append(x)
        c.execute(f"UPDATE consolidation_runs SET {col}=? WHERE consolidation_id=?", (json.dumps(have), cid))


# --------------------------------------------------------------------------- begin / progress / end


def begin(nb: Any) -> Dict[str, Any]:
    """Open a consolidation row (an earlier row still open is closed as ``interrupted``). Returns
    ``{consolidation_id, started_at, planned_merges, pending}``."""
    from ..store.names import merge_plan

    nb.require_embedder()
    ts = _now_ts()
    now = _iso(ts)
    cid = "cs_" + uuid.uuid4().hex[:20]
    with nb.store.tx() as c:
        c.execute("UPDATE consolidation_runs SET finished_at=?, status='interrupted', error=? WHERE finished_at IS"
                  " NULL", (now, INTERRUPTED))
        c.execute("INSERT INTO consolidation_runs(consolidation_id, started_at, started_ts, updated_ts, status,"
                  " stage, stage_done, stage_total, stages_json) VALUES (?,?,?,?, 'running', NULL, 0, 0, '{}')",
                  (cid, now, ts, ts))
        old = [r[0] for r in c.execute("SELECT consolidation_id FROM consolidation_runs WHERE finished_at IS NOT NULL"
                                       " ORDER BY started_ts DESC LIMIT -1 OFFSET ?", (LAST_RUNS,)).fetchall()]
        c.executemany("DELETE FROM consolidation_runs WHERE consolidation_id=?", [(x,) for x in old])
    planned = sum(len(g["from_ids"]) for g in merge_plan(nb.store))
    return {"consolidation_id": cid, "started_at": now, "planned_merges": planned, "pending": pending(nb)}


def _previous_rate(nb: Any, stage: str, cid: str) -> Optional[float]:
    for r in nb.store.q("SELECT stages_json FROM consolidation_runs WHERE consolidation_id!=? AND status IN"
                        " ('completed', 'completed_with_gaps') ORDER BY started_ts DESC LIMIT 5", (cid,)):
        s = (json.loads(r[0] or "{}") or {}).get(stage) or {}
        if isinstance(s.get("seconds_per_item"), (int, float)):
            return float(s["seconds_per_item"])
    return None


def _skips(skipped: Any, reason: Optional[str]) -> List[Dict[str, str]]:
    """``skipped`` = ``[id]`` or ``[{id, reason}]`` -> ``[{id, reason}]`` (``reason`` is the default sentence)."""
    if skipped in (None, ""):
        return []
    if not isinstance(skipped, (list, tuple)):
        raise ValueError("skipped must be a list of ids or of {id, reason}.")
    default = " ".join(str(reason or "").split()) or "No reason was given."
    out: List[Dict[str, str]] = []
    for x in skipped:
        if isinstance(x, dict):
            xid, why = str(x.get("id") or "").strip(), " ".join(str(x.get("reason") or "").split()) or default
        else:
            xid, why = str(x or "").strip(), default
        if not xid:
            raise ValueError("A skipped item has no id.")
        out.append({"id": xid, "reason": why})
    return out


def progress(nb: Any, stage: str, done: Any, total: Any, note: Optional[str] = None, skipped: Any = None,
             reason: Optional[str] = None) -> Dict[str, Any]:
    """Record the current stage's progress on the open consolidation. The first call of a stage starts its clock;
    ``seconds_per_item`` = the stage's elapsed time / ``done`` once ``done`` > 0. ``skipped`` (``[id]`` or
    ``[{id, reason}]``, ``reason`` the default sentence) are the items this stage leaves out for this run: they are
    added to the stage's ``skipped_ids`` (``skipped`` = their number, ``reason`` the latest sentence, ``reasons`` the
    distinct ones) and logged as a warning. Returns the row's progress ``{consolidation_id, stage, stage_done,
    stage_total, seconds_per_item, eta_s, elapsed_s, skipped}``."""
    if stage not in STAGES:
        raise ValueError(f"The stage must be one of {', '.join(STAGES)}.")
    new_skips = _skips(skipped, reason)
    try:
        d, t = int(done or 0), int(total or 0)
    except (TypeError, ValueError):
        raise ValueError("done and total must be whole numbers.") from None
    if d < 0 or t < 0:
        raise ValueError("done and total cannot be negative.")
    t = max(t, d)
    row = _require_open(nb)
    ts = _now_ts()
    stages = _stages(row)
    s = dict(stages.get(stage) or {})
    if not s:
        s = {"started_ts": ts, "started_at": _iso(ts)}
    s.update({"done": d, "total": t, "updated_ts": ts})
    if d > 0:
        s["seconds_per_item"] = round((ts - float(s["started_ts"])) / d, 3)
    if new_skips:
        have = list(s.get("skipped_ids") or [])
        known = {x["id"] for x in have}
        added = [x for x in new_skips if x["id"] not in known and not known.add(x["id"])]
        have.extend(added)
        reasons = list(s.get("reasons") or [])
        for x in added:
            if x["reason"] not in reasons:
                reasons.append(x["reason"])
        s.update({"skipped_ids": have, "skipped": len(have), "reasons": reasons})
        if added:
            s["reason"] = added[-1]["reason"]
            for why in dict.fromkeys(x["reason"] for x in added):
                _log.warning("consolidation %s, stage %s: skipped for this run (%d): %s. Reason: %s",
                             row["consolidation_id"], stage, len(added),
                             ", ".join(x["id"] for x in added if x["reason"] == why), why)
    stages[stage] = s
    cols: Dict[str, Any] = {"stage": stage, "stage_done": d, "stage_total": t, "updated_ts": ts,
                            "stages_json": json.dumps(stages)}
    if note is not None:
        cols["note"] = " ".join(str(note).split()) or None
    _update(nb, row["consolidation_id"], **cols)
    return {"consolidation_id": row["consolidation_id"], "stage": stage, "stage_done": d, "stage_total": t,
            "seconds_per_item": s.get("seconds_per_item"), "eta_s": _eta(nb, row["consolidation_id"], stage, s),
            "elapsed_s": int(ts - float(row["started_ts"])), "skipped": int(s.get("skipped") or 0)}


def _eta(nb: Any, cid: str, stage: str, s: Dict[str, Any]) -> Optional[int]:
    left = int(s.get("total") or 0) - int(s.get("done") or 0)
    if left <= 0:
        return 0
    rate = s.get("seconds_per_item")
    if not isinstance(rate, (int, float)):
        rate = _previous_rate(nb, stage, cid)
    return None if rate is None else int(math.ceil(left * float(rate)))


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _left_out(nb: Any, stages: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """What the run leaves for the next one, checked at the close: the skipped entries still stale and groups still
    waiting for a name (the reflect stage may have named a skipped group), plus what the time budget did not reach
    (``extra.not_reached``), never more than :func:`pending` shows."""
    from .communities import pending_ids

    reached = extra.get("not_reached") if isinstance(extra.get("not_reached"), dict) else {}
    budget = bool(extra.get("budget_spent"))
    stale = {x["entity_id"] for x in _stale(nb)}
    waiting = set(pending_ids(nb))
    def still_stale(eid: str) -> bool:
        return eid in stale or (nb.store.entity_row(eid) is not None and nb.store.canonical(eid) in stale)

    pend = nb.store.pending_marks()
    marked = {m["note_id"] for m in pend}  # ADR 0008: notes still waiting to be read again
    out: Dict[str, Any] = {"summaries": 0, "labels": 0, "reinterpret": 0, "propagated": 0, "propagated_from": [],
                           "reasons": []}
    for kind, now_pending, still in (("summaries", stale, still_stale), ("labels", waiting, waiting.__contains__),
                                     ("reinterpret", marked, marked.__contains__)):
        skipped = [x for x in (stages.get(kind) or {}).get("skipped_ids") or [] if isinstance(x, dict)]
        left = [x for x in skipped if still(x["id"])]
        unreached = _count(reached.get(kind)) if budget else 0
        out[kind] = min(len(left) + unreached, len(now_pending))
        for why in dict.fromkeys(x["reason"] for x in left):
            if why not in out["reasons"]:
                out["reasons"].append(why)
        if unreached and BUDGET_REASON not in out["reasons"]:
            out["reasons"].append(BUDGET_REASON)
    # ADR 0008 §4a: of the notes left, those waiting only because an idea they rely on was understood differently
    prop = [m for m in pend if m["reason_code"] == "propagated"]
    if out["reinterpret"] and prop:
        out["propagated"] = min(len(prop), out["reinterpret"])
        counts: Dict[str, int] = {}
        for m in prop:
            for e in m["sources"]:
                counts[e] = counts.get(e, 0) + 1
        from .propagation import display_name

        out["propagated_from"] = [{"entity_id": e, "name": display_name(nb.store, e), "notes": n}
                                  for e, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return out


def notice(left_out: Optional[Dict[str, Any]]) -> Optional[str]:
    """The end-user sentence of a run that left something out ("12 summaries could not be refreshed this time; they
    will be tried again next time."), None when nothing was."""
    lo = left_out if isinstance(left_out, dict) else {}
    s, g, r = _count(lo.get("summaries")), _count(lo.get("labels")), _count(lo.get("reinterpret"))
    parts = ([f"{s} {'summary' if s == 1 else 'summaries'}"] if s else []) + \
        ([f"{g} theme {'name' if g == 1 else 'names'}"] if g else [])
    told = None
    if parts:
        told = (f"{' and '.join(parts)} could not be refreshed this time; {'it' if s + g == 1 else 'they'} will be"
                " tried again next time.")
    if r:  # ADR 0008: notes an earlier note changed, still waiting to be understood again
        one = r == 1
        p = min(_count(lo.get("propagated")), r)
        which = (f", of which {p} {'follows' if p == 1 else 'follow'} from ideas understood differently" if p and not one
                 else (" because an idea it relies on is understood differently" if p else ""))
        again = (f"{r} {'note' if one else 'notes'} could not be understood again this time{which};"
                 f" {'it' if one else 'they'} will be read again next time.")
        told = f"{told} {again[0].upper()}{again[1:]}" if told else again
    return told


def end(nb: Any, status: str, results: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Close the open consolidation: ``status`` completed (or completed_with_gaps) or failed; ``results`` adds to the
    counts read from the stages (``merges`` = normalize done, ``labelled`` = labels done, ``summaries_refreshed`` =
    summaries done). A failed run's ``results.error`` is its sentence. A finished run also reports ``left_out =
    {summaries, labels, reinterpret, reasons}`` (:func:`_left_out`; ``results.budget_spent`` and ``results.not_reached:
    {summaries, labels, reinterpret}`` say what the time budget stopped) and is closed as ``completed_with_gaps`` when anything
    was left out, ``completed`` otherwise, whatever the caller asked. Returns the finished row's ``last`` view."""
    if status not in STATUSES:
        raise ValueError("The status must be completed, completed_with_gaps or failed.")
    row = _require_open(nb)
    extra = dict(results or {}) if isinstance(results, dict) else {}
    stages = _stages(row)

    def done(stage: str) -> int:
        return int((stages.get(stage) or {}).get("done") or 0)

    out = {"merges": done("normalize"), "communities": None, "labelled": done("labels"),
           "summaries_refreshed": done("summaries"), "insights": 0}
    out.update(pass_counts(nb, row["consolidation_id"]))  # ADR 0008: read from the marks (truth), never the caller
    for k in ("merges", "communities", "labelled", "summaries_refreshed", "insights"):
        if isinstance(extra.get(k), (int, float)) and not isinstance(extra.get(k), bool):
            out[k] = int(extra[k])
    error = str(extra.get("error") or "").strip() or None if status == "failed" else None
    if status == "failed" and error is None:
        error = "The consolidation could not finish."
    if status in FINISHED:
        lo = _left_out(nb, stages, extra)
        out["left_out"] = lo
        out["budget_spent"] = bool(extra.get("budget_spent"))
        status = "completed_with_gaps" if lo["summaries"] or lo["labels"] or lo["reinterpret"] else "completed"
        if status == "completed_with_gaps":
            _log.warning("consolidation %s completed with gaps: %d summaries, %d group names and %d notes to understand"
                         " again left for the next run%s. Reasons: %s", row["consolidation_id"], lo["summaries"],
                         lo["labels"], lo["reinterpret"], " (the time budget was spent)" if out["budget_spent"] else "",
                         " | ".join(lo["reasons"]))
    ts = _now_ts()
    cols: Dict[str, Any] = {"finished_at": _iso(ts), "updated_ts": ts, "status": status,
                            "results_json": json.dumps(out), "error": error}
    if status in FINISHED:
        cols.update({"stage": DONE, "stage_done": 0, "stage_total": 0})
    _update(nb, row["consolidation_id"], **cols)
    return last(nb) or {}


# --------------------------------------------------------------------------- reads


def _last_view(r: Dict[str, Any]) -> Dict[str, Any]:
    res = json.loads(r.get("results_json") or "{}") or {}
    lo = res.get("left_out") if isinstance(res.get("left_out"), dict) else None
    return {"consolidation_id": r["consolidation_id"], "started_at": r["started_at"], "finished_at": r["finished_at"],
            "status": r["status"], "error": r.get("error"), "merges": res.get("merges"),
            "communities": res.get("communities"), "labelled": res.get("labelled"),
            "summaries_refreshed": res.get("summaries_refreshed"), "insights": res.get("insights"),
            "left_out": lo, "budget_spent": bool(res.get("budget_spent")),
            "reinterpreted": res.get("reinterpreted"), "unchanged": res.get("unchanged"),
            "reinterpret_failed": res.get("reinterpret_failed"),
            "notice": notice(lo) if r["status"] == "completed_with_gaps" else None}


def last(nb: Any) -> Optional[Dict[str, Any]]:
    """The latest finished consolidation: ``{consolidation_id, started_at, finished_at, status, error, merges,
    communities, labelled, summaries_refreshed, insights, left_out: {summaries, labels, reasons} | null,
    budget_spent, notice}`` or None (``left_out`` is null for a failed or interrupted run and for rows written
    before ADR 0007; ``notice`` is the end-user sentence of a ``completed_with_gaps`` run)."""
    r = nb.store.q1("SELECT * FROM consolidation_runs WHERE finished_at IS NOT NULL ORDER BY started_ts DESC LIMIT 1")
    return _last_view(_row_dict(r)) if r is not None else None


def run_view(nb: Any, row: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The open consolidation's progress: ``{consolidation_id, started_at, stage, stage_done, stage_total, eta_s,
    elapsed_s, note}`` (``run_id`` is added by the gateway view) or None."""
    row = row or open_row(nb)
    if row is None:
        return None
    stage = row["stage"] or STAGES[0]
    s = _stages(row).get(stage) or {"done": 0, "total": 0}
    return {"consolidation_id": row["consolidation_id"], "started_at": row["started_at"], "stage": stage,
            "stage_done": int(row["stage_done"] or 0), "stage_total": int(row["stage_total"] or 0),
            "eta_s": _eta(nb, row["consolidation_id"], stage, s),
            "elapsed_s": max(0, int(_now_ts() - float(row["started_ts"]))), "note": row.get("note"),
            "propagated": _propagated_in_pass(nb, row["consolidation_id"]) if stage == "reinterpret" else None}


def _propagated_in_pass(nb: Any, pass_id: str) -> Dict[str, Any]:
    """``{notes, names, sources}`` of the reinterpret stage in progress (ADR 0008 §4a): of the notes this pass reads
    again (taken or still waiting), those it reads only because an idea they rely on was understood differently,
    and the names of those ideas (the most frequent first, at most three; ``sources`` counts them all)."""
    per: Dict[str, Dict[str, Any]] = {}
    for r in nb.store.q("SELECT note_id, reason_code, source_entity_id FROM reinterpret_marks WHERE pass_id=? OR"
                        " (pass_id IS NULL AND consumed_at IS NULL)", (pass_id,)):
        d = per.setdefault(r["note_id"], {"direct": False, "sources": []})
        if (r["reason_code"] or "direct") == "direct":
            d["direct"] = True
        elif r["source_entity_id"] and r["source_entity_id"] not in d["sources"]:
            d["sources"].append(r["source_entity_id"])
    counts: Dict[str, int] = {}
    n = 0
    for d in per.values():
        if d["direct"]:
            continue
        n += 1
        for e in d["sources"]:
            counts[e] = counts.get(e, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    from .propagation import display_name

    return {"notes": n, "names": [display_name(nb.store, e) for e, _ in top[:3]], "sources": len(top)}


def history(nb: Any, limit: int = 10) -> List[Dict[str, Any]]:
    from ..store.notes import clamp_limit

    rows = nb.store.q("SELECT * FROM consolidation_runs WHERE finished_at IS NOT NULL ORDER BY started_ts DESC"
                      " LIMIT ?", (clamp_limit(limit or 10, 10, LAST_RUNS),))  # a clamp is logged (ADR 0007)
    return [_last_view(_row_dict(r)) for r in rows]


_pending_cache: Dict[str, Tuple[Any, Dict[str, int]]] = {}


def pending(nb: Any) -> Dict[str, int]:
    """``{stale_labels, unlabelled, stale_summaries}``: what a consolidation would do now (cached until the
    revision log or the communities change)."""
    key = str(nb.root)
    seq = nb.store.q1("SELECT COALESCE(MAX(seq), 0) FROM revisions WHERE applied_index=1")[0]
    meta = nb.store.meta_get("communities") or {}
    labelled = nb.store.q1("SELECT COUNT(*), SUM(label IS NULL), SUM(stale_label) FROM communities")
    marks = nb.store.q1("SELECT COALESCE(MAX(mark_id), 0), COUNT(consumed_at), COUNT(pass_id) FROM reinterpret_marks")
    stamp = (seq, meta.get("computed_at") if isinstance(meta, dict) else None, tuple(labelled), tuple(marks))
    hit = _pending_cache.get(key)
    if hit is not None and hit[0] == stamp:
        return dict(hit[1])
    out = {"stale_labels": int(labelled[2] or 0), "unlabelled": int(labelled[1] or 0),
           "stale_summaries": len(_stale(nb)), "reinterpret": len(nb.store.pending_marks())}
    _pending_cache[key] = (stamp, out)
    return dict(out)


def state(nb: Any) -> Dict[str, Any]:
    """``{run, last, pending, reinterpret}`` of a notebook (headless part of ``GET /note/consolidation``):
    ``reinterpret`` = what waits to be understood again and why, before the stage runs (ADR 0008, review 3.2)."""
    return {"run": run_view(nb), "last": last(nb), "pending": pending(nb), "reinterpret": nb.reinterpret_state()}


# --------------------------------------------------------------------------- the reinterpret stage (ADR 0008)


def pass_counts(nb: Any, pass_id: str) -> Dict[str, int]:
    """``{reinterpreted, unchanged, reinterpret_failed}``: the notes this pass understood again, found unchanged
    (no revision written) and could not understand again (their marks wait for the next pass)."""
    row = nb.store.q1("SELECT COUNT(DISTINCT CASE WHEN outcome='reinterpreted' THEN note_id END),"
                      " COUNT(DISTINCT CASE WHEN outcome='unchanged' THEN note_id END),"
                      " COUNT(DISTINCT CASE WHEN consumed_at IS NULL AND error IS NOT NULL THEN note_id END)"
                      " FROM reinterpret_marks WHERE pass_id=?", (pass_id,))
    return {"reinterpreted": int(row[0] or 0), "unchanged": int(row[1] or 0), "reinterpret_failed": int(row[2] or 0)}


def _reinterpreted_today(nb: Any, note_id: str) -> int:
    day = ids.utc_now()[:10]
    return int(nb.store.q1("SELECT COUNT(DISTINCT consumed_revision_id) FROM reinterpret_marks WHERE note_id=? AND"
                           " outcome='reinterpreted' AND consumed_at>=?", (note_id, day))[0] or 0)


def digest_unchanged(nb: Any, note_id: str) -> bool:
    """The no-call check (ADR 0008 §4, review 1.5): the note's context read again now (as of its date: the same
    lookups, the mention queries its last reading asked) shows the same entities, statements, summaries and similar
    notes as the
    context its current interpretation recorded, leaving out what that interpretation itself wrote (its entities,
    statements and summaries), and what was recorded after the note's date (a later statement or note is no
    foundation). True: the foundation did not change for this note, nothing to understand again. A
    revision without a record (written before schema 3, ``context_unknown``) is never unchanged."""
    from ..process.context import load_packet, note_context, save_packet
    from ..process.resolve import resolve_candidates
    from ..process.revise import reinterpret_view
    from ..store.notebook import context_digest

    s = nb.store
    cur = s.current_interpretation(note_id)
    if cur is None:
        return False
    stored = s.reading_record(note_id, cur)  # the revision's record, or what an unchanged re-reading confirmed
    if not isinstance(stored, dict) or not stored.get("looked_up"):
        return False
    # ADR 0008 §4a: a note waiting because an idea it relies on was understood differently never saw that idea's
    # card, so the digest below cannot see the change: it is settled without a call only when each source idea, as
    # known at the note's date, is what its current reading recorded (``related``); never recorded: it is read
    from .propagation import related_sources, related_state

    note_at = s.note_row(note_id)["occurred_at"]
    recorded = stored.get("related") or {}
    for eid, _rid in related_sources(s, note_id):
        if recorded.get(eid) is None or recorded[eid] != related_state(s, eid, note_at):
            return False
    seen = note_context(nb, note_id)
    # the similar notes it would be shown now, kept with the packet as the tools' note_context keeps them (R6: without
    # them the digest of now had no similar notes, so a note with an earlier similar note was never settled)
    packet = load_packet(nb, note_id)
    if packet is not None:
        packet["similar_notes"] = [x.get("note_id") for x in seen.get("similar_notes") or []
                                   if isinstance(x, dict) and x.get("note_id")]
        save_packet(nb, note_id, packet)
    reinterpret_view(nb, note_id, seen)
    if stored.get("mention_queries"):
        resolve_candidates(nb, note_id, stored["mention_queries"])
    now = context_digest(s, s.note_row(note_id), load_packet(nb, note_id))
    chain = set(s.interpretation_chain(note_id))
    cq = ",".join("?" for _ in chain)
    own_entities = {r[0] for r in s.q(f"SELECT entity_id FROM entities WHERE created_revision_id IN ({cq})", list(chain))}
    own_claims = {r[0] for r in s.q("SELECT claim_id FROM claims WHERE note_id=?", (note_id,))}

    as_of = s.note_row(note_id)["occurred_at"]

    def known_then(rows: Sequence[str], table: str, key: str, col: str) -> set:
        # forward only (ADR 0008 §4): what was recorded after the note's date (the LATER block) is no foundation
        out = set()
        for x in rows:
            r = s.q1(f"SELECT {col} FROM {table} WHERE {key}=?", (x,))
            if r is not None and r[0] and r[0] <= as_of:
                out.add(x)
        return out

    def norm(d: Dict[str, Any]) -> Tuple[set, set, set]:
        # entities first seen after the note's date (later growth: a new vector neighbour) are no foundation either
        return (known_then([e for e in d.get("entities") or [] if e not in own_entities], "entities", "entity_id",
                           "COALESCE(first_seen, created_at)"),
                known_then([c for c in d.get("claims") or [] if c not in own_claims], "claims", "claim_id", "observed_at"),
                known_then(d.get("notes") or [], "notes", "note_id", "occurred_at"))

    (e1, c1, n1), (e2, c2, n2) = norm(stored), norm(now)
    if e1 != e2 or c1 != c2 or n1 != n2:
        return False
    s1, s2 = stored.get("summaries") or {}, now.get("summaries") or {}
    return all(s1.get(e) == s2.get(e) for e in e2 if s2.get(e) not in chain)


def reinterpret_next(nb: Any, limit: Any = REINTERPRET_BATCH, failed: Any = None) -> Dict[str, Any]:
    """The next notes of the open consolidation's ``reinterpret`` stage (``note_reinterpret_next``): the pass takes
    pending marks in history order (date, received time, id), never a note it took already; a note whose context did
    not change is consumed ``unchanged`` here without a model call, a note understood again
    :data:`REINTERPRET_PER_DAY` times today is held (skipped with its reason); the rest are returned for the model
    (``note-process``, whose apply reads them again). ``failed`` (``[id]`` or ``[{id, reason}]``) records the notes
    the last round could not understand again (their marks stay pending for the next pass). ``limit`` 0 only counts.
    Returns ``{items: [{note_id, occurred_at, reasons}], unchanged: [note_id], held: [{id, reason}], done, total,
    pending, pass_id}`` (``done`` = understood again, unchanged, failed or held in this pass)."""
    row = _require_open(nb)
    pid = row["consolidation_id"]
    for f in _skips(failed, FAILED_REASON):
        nb.store.fail_mark(f["id"], pid, f["reason"])
        nb.queue.release(f["id"], "reinterpret", pid)
    try:
        n = int(REINTERPRET_BATCH if limit in (None, "") else limit)
    except (TypeError, ValueError):
        raise ValueError("The limit must be a whole number.") from None
    if not 0 <= n <= 5:
        _log.warning("reinterpret_next: the limit %d is outside 0..5; %d was applied", n, max(0, min(n, 5)))
    n = max(0, min(n, 5))
    items: List[Dict[str, Any]] = []
    unchanged: List[str] = []
    held: List[Dict[str, str]] = []
    while len(items) < n:
        pend = nb.store.pending_marks(pass_id=pid, limit=1)
        if not pend:
            break
        m = pend[0]
        nid = m["note_id"]
        nb.store.take_mark(nid, pid)
        if _reinterpreted_today(nb, nid) >= REINTERPRET_PER_DAY:
            nb.store.fail_mark(nid, pid, HELD_REASON)
            held.append({"id": nid, "reason": HELD_REASON})
            _log.warning("consolidation %s, stage reinterpret: %s held: %s", pid, nid, HELD_REASON)
            continue
        if digest_unchanged(nb, nid):
            nb.store.consume_marks(nid, pid, "unchanged", None, calls=0)
            unchanged.append(nid)
            continue
        # the note is read again outside a drain: claimed on its queue row (R4, ADR 0008 §8) until the apply (or the
        # failure the next round reports) releases it; a drain's lease or an inline claim on it holds it for now
        if not nb.queue.claim(nid, "reinterpret", pid):
            nb.store.fail_mark(nid, pid, BUSY_REASON)
            held.append({"id": nid, "reason": BUSY_REASON})
            _log.warning("consolidation %s, stage reinterpret: %s held: %s", pid, nid, BUSY_REASON)
            continue
        items.append({"note_id": nid, "occurred_at": m["occurred_at"], "reasons": m["reasons"]})
    counts = pass_counts(nb, pid)
    left = len(nb.store.pending_marks(pass_id=pid))
    done = counts["reinterpreted"] + counts["unchanged"] + counts["reinterpret_failed"]
    return {"items": items, "unchanged": unchanged, "held": held, "done": done,
            "total": done + len(items) + left, "pending": left + len(items), "pass_id": pid, **counts}


# --------------------------------------------------------------------------- stale summaries


def _canon(nb: Any) -> Tuple[Dict[str, str], Dict[str, Any]]:
    from .communities import _canonical_map

    return _canonical_map(nb)


def _stale(nb: Any) -> List[Dict[str, Any]]:
    """Every live entry whose summary is empty, a placeholder, behind an earlier note applied since (retrospective)
    or older than its notes and statements, in the order they are refreshed: empty and placeholders first, then
    the retrospective ones, then the most changed, then the most mentioned."""
    from ..store.notes import LIVE_ENTITY, claim_reinterpreted_sql

    s = nb.store
    canon, live = _canon(nb)
    seq_of = {r[0]: int(r[1]) for r in s.q("SELECT revision_id, seq FROM revisions WHERE applied_index=1")}
    # the CURRENT summary of each entry: the last one in dated order (the one entities.summary shows), never the one
    # written last: a retrospective note's own summary (an earlier date) must not hide that the entry changed, and
    # a summary of a superseded interpretation is out of the history (store.notes.summary_superseded_sql)
    summaries: Dict[str, Tuple[int, str]] = {e: (seq_of.get(r["revision_id"], 0), r["revision_id"])
                                             for e, r in s.current_summaries().items()}
    # retrospective insertions marked since the current summary (W23's marks; ADR 0008 §2)
    retro = {x["entity_id"]: int(x["marked_seq"]) for x in nb.retro_state()["stale_summaries"]}
    new_notes: Dict[str, int] = {}
    for r in s.q("SELECT entity_id, revision_id FROM entity_notes"):
        e = canon.get(r[0], r[0])
        if e in live and seq_of.get(r[1], 0) > summaries.get(e, (0, ""))[0]:
            new_notes[e] = new_notes.get(e, 0) + 1
    new_claims: Dict[str, int] = {}
    # a claim its own note's re-interpretation closed is not counted again (its replacement is)
    for r in s.q("SELECT c.subject_id, c.object_id, c.revision_id FROM claims c WHERE c.status!='retracted' AND NOT"
                 f" {claim_reinterpreted_sql('c')}"):
        q = seq_of.get(r[2], 0)
        for x in {canon.get(r[0], r[0]), canon.get(r[1], r[1]) if r[1] else None}:
            if x and x in live and q > summaries.get(x, (0, ""))[0]:
                new_claims[x] = new_claims.get(x, 0) + 1
    rows = {r["entity_id"]: r for r in s.q("SELECT entity_id, name, display_name, summary, created_revision_id,"
                                           f" note_count FROM entities WHERE {LIVE_ENTITY}")}
    created_warnings: Dict[str, List[str]] = {}
    out = []
    for eid, r in rows.items():
        text = " ".join(str(r["summary"] or "").split())
        since = {"notes": new_notes.get(eid, 0), "claims": new_claims.get(eid, 0)}
        seq, rid = summaries.get(eid, (0, ""))
        reason = None
        if not text:
            reason = "empty"
        elif rid and rid == r["created_revision_id"]:
            if rid not in created_warnings:
                w = s.q1("SELECT warnings_json FROM revisions WHERE revision_id=?", (rid,))
                created_warnings[rid] = list(json.loads(w[0] or "[]") or []) if w else []
            names = {r["name"], r["display_name"] or r["name"], *s.aliases(eid)}
            if any(PLACEHOLDER_WARNING.format(name=n) in created_warnings[rid] for n in names):
                reason = "placeholder"
        if reason is None and eid in retro:
            reason = "retrospective"
        if reason is None and (since["notes"] or since["claims"]):
            reason = "changed"
        if reason is None:
            continue
        item = {"entity_id": eid, "reason": reason, "since_summary": since, "summary_revision_count": seq,
                "note_count": int(r["note_count"] or 0)}
        if eid in retro:
            item["retro_marked_seq"] = retro[eid]
        out.append(item)
    rank = {"empty": 0, "placeholder": 0, "retrospective": 1, "changed": 2}
    out.sort(key=lambda x: (rank[x["reason"]], -(x["since_summary"]["notes"] + x["since_summary"]["claims"]),
                            -x["note_count"], x["entity_id"]))
    return out


def _packet(nb: Any, item: Dict[str, Any], canon: Dict[str, str]) -> Dict[str, Any]:
    from ..store.notes import claim_reinterpreted_sql
    from .changes import claim_text
    from .shapes import excerpt

    s = nb.store
    eid = item["entity_id"]
    r = s.entity_row(eid)
    members = s.merged_members(eid)
    marks = ",".join("?" for _ in members)
    claims = s.q(f"SELECT * FROM claims c WHERE c.status='active' AND (c.subject_id IN ({marks}) OR c.object_id IN"
                 f" ({marks})) AND NOT {claim_reinterpreted_sql('c')}", [*members, *members])
    now = ids.utc_now()
    claims = [c for c in claims if not c["valid_until"] or c["valid_until"] > now]
    # the strongest statements that hold now; among equals the most recent first (the past is in ``earlier`` and
    # ``history``), so what the entry is now is never the part cut
    claims = sorted(claims, key=lambda c: (-int(c["reinforced_count"] or 0), _CERTAINTY_RANK.get(c["certainty"], 9),
                                           _desc(c["observed_at"]), c["claim_id"]))
    # the dated history, earliest first: what each note's summary said of the entry as of its own date ...
    history: List[Dict[str, str]] = []
    for h in s.summary_rows(members):
        text = " ".join(str(h["summary"] or "").split())
        if text and (not history or history[-1]["summary"] != text):
            history.append({"date": _day(h["at"]), "summary": text})
    history, history_omitted = _ends(history, PACKET_HISTORY)
    # ... and what held once and ended (a belief replaced or whose window closed), in the order it held
    ended = s.q(f"SELECT * FROM claims c WHERE (c.subject_id IN ({marks}) OR c.object_id IN ({marks})) AND c.valid_until"
                f" IS NOT NULL AND c.valid_until<=? AND c.status IN ('active', 'superseded') AND NOT"
                f" {claim_reinterpreted_sql('c')} ORDER BY COALESCE(c.valid_from, c.observed_at), c.claim_id",
                [*members, *members, now])
    earlier = [{"from": _day(c["valid_from"] or c["observed_at"]), "until": _day(c["valid_until"]),
                "statement": claim_text(nb, c)} for c in ended if (c["valid_from"] or c["observed_at"]) != c["valid_until"]]
    earlier, earlier_omitted = _ends(earlier, PACKET_EARLIER)
    all_notes = list(s.q(f"SELECT n.*, MAX(v.seq) AS mention_seq FROM entity_notes en JOIN notes n ON"
                         f" n.note_id=en.note_id LEFT JOIN revisions v ON v.revision_id=en.revision_id WHERE en.entity_id"
                         f" IN ({marks}) AND n.archived_at IS NULL GROUP BY n.note_id ORDER BY n.occurred_at DESC,"
                         f" n.note_id", members))
    # what the current summary does not account for yet comes first (an earlier project's notes arriving later are
    # what an entry must absorb, ADR 0008 §2), then the newest note, the earliest one, and the newest of the rest
    since = int(item.get("summary_revision_count") or 0)
    fresh = [n for n in all_notes if int(n["mention_seq"] or 0) > since]
    chosen: List[Any] = fresh[:PACKET_NEW_NOTES]
    for n in ([all_notes[0], all_notes[-1]] if all_notes else []) + all_notes:
        if len(chosen) >= PACKET_NOTES:
            break
        if n["note_id"] not in {c["note_id"] for c in chosen}:
            chosen.append(n)
    new_ids = {n["note_id"] for n in fresh}
    notes = sorted(chosen, key=lambda n: (_desc(n["occurred_at"]), n["note_id"]))  # newest first
    return {**item, "name": r["display_name"] or r["name"], "kind": r["kind"], "aliases": s.aliases(eid),
            "summary": r["summary"], "as_of": _day(_as_of(nb, eid)),
            "claims": [claim_text(nb, c) + (f" (since {_day(c['valid_from'])})" if c["valid_from"] else "")
                       for c in claims[:PACKET_CLAIMS]],
            "history": history, "earlier": earlier,
            "notes": [{"title": n["title"] or "", "date": str(n["occurred_at"] or "")[:10],
                       "excerpt": excerpt(n["summary"] or n["one_line"] or n["text"]),
                       **({"new": True} if n["note_id"] in new_ids else {})} for n in notes],
            # a selection, recorded (ADR 0007): what is not shown, by part
            "omitted": {"claims": max(0, len(claims) - PACKET_CLAIMS), "notes": max(0, len(all_notes) - len(notes)),
                        "history": history_omitted, "earlier": earlier_omitted}}


def _desc(ts: Any) -> str:
    """A sort key that orders ISO times newest first (each character inverted)."""
    return "".join(chr(0x10FFFF - ord(ch)) for ch in str(ts or ""))


def _day(ts: Any) -> str:
    return str(ts or "")[:10]


def _ends(items: List[Any], n: int) -> Tuple[List[Any], int]:
    """The earliest item and the latest ``n - 1`` (the start and the recent past of a dated history), and how many
    in between were left out (the packet's ``omitted``, shown to the model, never silent)."""
    if len(items) <= n:
        return items, 0
    return [items[0], *items[len(items) - (n - 1):]], len(items) - n


def _as_of(nb: Any, entity_id: str) -> Optional[str]:
    """The latest dated knowledge of an entry (ADR 0008 §2): its last mention across the entries merged into it, or
    its current summary's date when that is later; None when it has neither. A summary re-derived by the
    consolidation is dated here (``entity.summary`` ``as_of``)."""
    s = nb.store
    members = s.merged_members(entity_id)
    marks = ",".join("?" for _ in members)
    last = s.q1(f"SELECT MAX(at) FROM entity_notes WHERE entity_id IN ({marks})", members)[0]
    cur = s.current_summary_row(entity_id)
    dates = [x for x in (last, cur["at"] if cur is not None else None) if x]
    return max(dates) if dates else None


def stale_summaries(nb: Any, limit: Any = SUMMARY_BATCH) -> Dict[str, Any]:
    """``{count, items, limit_applied}``: the next ``limit`` (0..50) stale entries as packets ``{entity_id, reason,
    since_summary: {notes, claims}, summary_revision_count, note_count, name, kind, aliases, summary, as_of,
    claims: [line], history: [{date, summary}], earlier: [{from, until, statement}], notes: [{title, date, excerpt,
    new?}], omitted: {claims, notes, history, earlier}}`` (the strongest :data:`PACKET_CLAIMS` statements, the
    :data:`PACKET_NOTES` notes chosen by :func:`_packet`, the ends of the dated history and of the ended statements;
    ``omitted`` counts the rest by part). While a consolidation is open, an entry it was already offered is not offered
    again (so an entry the model skips cannot hold the loop), and ``count`` is what remains for it."""
    try:
        n = int(SUMMARY_BATCH if limit in (None, "") else limit)
    except (TypeError, ValueError):
        raise ValueError("The limit must be a whole number.") from None
    if not 0 <= n <= 50:
        _log.warning("stale_summaries: the limit %d is outside 0..50; %d was applied", n, max(0, min(n, 50)))
    n = max(0, min(n, 50))
    row = open_row(nb)
    offered = set(_ids(row, "offered_json")) if row else set()
    stale = [x for x in _stale(nb) if x["entity_id"] not in offered]
    canon, _ = _canon(nb)
    items = [_packet(nb, x, canon) for x in stale[:n]]
    if row is not None and items:
        _add_ids(nb, row["consolidation_id"], "offered_json", [x["entity_id"] for x in items])
    return {"count": len(stale), "items": items, "limit_applied": n}


def set_summaries(nb: Any, items: Sequence[Dict[str, Any]], *, account: Optional[str] = None) -> Dict[str, Any]:
    """Store ``[{entity_id, summary}]`` as one ``entity.summary`` revision (run_id = the open consolidation, when
    there is one), each op dated ``as_of`` the entry's latest dated knowledge (:func:`_as_of`). An unknown or merged entry or an empty summary is left out (``left_out: [{entity_id, reason}]``);
    a summary over :data:`SUMMARY_MAX_CHARS` is kept whole and named in ``over_length`` (and in the revision's
    warnings); every warning is also logged on ``abstractnote.consolidation``. Returns ``{stored: [entity_id],
    revision_id, warnings, left_out, over_length}``."""
    nb.require_embedder()
    if not isinstance(items, (list, tuple)):
        raise ValueError("The summaries must be a list of {entity_id, summary}.")
    warnings: List[str] = []
    left_out: List[Dict[str, str]] = []
    over_length: List[str] = []
    ops: List[Dict[str, Any]] = []
    stored: List[str] = []

    def leave(eid: str, why: str) -> None:
        warnings.append(why)
        left_out.append({"entity_id": eid, "reason": why})

    for it in items:
        if not isinstance(it, dict):
            leave("", "A summary that is not an object was left out.")
            continue
        eid = str(it.get("entity_id") or "")
        row = nb.store.entity_row(eid) if eid else None
        if row is None:
            leave(eid, f"The entry {eid or '(none)'} is not in this notebook; its summary was left out.")
            continue
        if row["merged_into"]:
            leave(eid, f"The entry {eid} was merged into another; its summary was left out.")
            continue
        text = " ".join(str(it.get("summary") or "").split())
        if not text:
            leave(eid, f"The summary of {eid} was empty; it was left out.")
            continue
        if eid in stored:
            continue
        if len(text) > SUMMARY_MAX_CHARS:
            # kept whole (ADR 0007: never dropped for its length); the prompt asks for two sentences
            warnings.append(f"over_length: the summary of {eid} has {len(text)} characters (more than"
                            f" {SUMMARY_MAX_CHARS}); it was kept whole.")
            over_length.append(eid)
        payload = {"entity_id": eid, "summary": text}
        as_of = _as_of(nb, eid)
        if as_of:
            payload["as_of"] = as_of  # dated at the knowledge it was derived from (op_entity_summary)
        ops.append({"op": "entity.summary", "payload": payload})
        stored.append(eid)
    for w in warnings:
        _log.warning("set_summaries: %s", w)
    if not ops:
        return {"stored": [], "revision_id": None, "warnings": warnings, "left_out": left_out,
                "over_length": over_length}
    open_ = open_row(nb)
    cid = open_["consolidation_id"] if open_ else None
    batch = int(nb.store.q1("SELECT COUNT(*) FROM revisions WHERE action='entity.summary' AND run_id IS ?",
                            (cid,))[0]) if cid else 0
    at = ids.utc_now()
    target = f"{cid or 'manual'}|{batch}|{','.join(stored)}"
    rev = {"revision_id": ids.revision_id_for_action("entity.summary", target, at), "note_id": None,
           "action": "entity.summary", "target": target, "at": at, "run_id": cid, "ops": ops,
           "summary": f"Refreshed the summary of {len(stored)} " + ("entry." if len(stored) == 1 else "entries."),
           "source": {"client": "consolidation", "account": account, "consolidation_id": cid},
           "warnings": [w for w in warnings if w.startswith("over_length:")]}
    nb.recover_pending()
    res = nb.apply_revision(rev)
    if cid:
        _add_ids(nb, cid, "touched_json", stored)
    return {"stored": stored, "revision_id": res["revision_id"], "warnings": warnings, "left_out": left_out,
            "over_length": over_length}


# --------------------------------------------------------------------------- the other stages


def normalize(nb: Any) -> Dict[str, Any]:
    """``Notebook.normalize_entities`` (source client ``consolidation``): ``{merges, groups}``; the entries merged
    into are touched (their then-versus-now is refreshed later in the run)."""
    out = nb.normalize_entities(None, client="consolidation")
    row = open_row(nb)
    if row is not None:
        _add_ids(nb, row["consolidation_id"], "touched_json", [m["into_id"] for m in out["merges"]])
    return {"merges": len(out["merges"]), "groups": len(out["groups"])}


def refresh_then_now(nb: Any) -> Dict[str, Any]:
    """Recompute the then-versus-now cache of every entry the open consolidation touched: ``{refreshed}``."""
    from .then_now import then_now

    row = open_row(nb)
    done = 0
    seen = set()
    for eid in _ids(row, "touched_json") if row else []:
        if nb.store.entity_row(eid) is None:
            continue
        c = nb.store.canonical(eid)
        if c in seen:
            continue
        seen.add(c)
        then_now(nb, c)
        done += 1
    return {"refreshed": done}


__all__ = ["DONE", "LABEL_BATCH", "STAGES", "SUMMARY_BATCH", "begin", "end", "history", "last", "normalize",
           "pending", "progress", "refresh_then_now", "run_view", "set_summaries", "stale_summaries", "state",
           "summary_system_prompt", "summary_template"]
