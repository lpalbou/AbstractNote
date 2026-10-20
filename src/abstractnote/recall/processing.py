"""What the processing is doing right now (``GET /note/processing``, ``Notebook.processing``; backlog 0031).

Every number is read, never guessed by a model: the queue, the pace, the recent revisions, the failed notes and
the communities come from the note index (SQL); the active runs, their current step, the last reflect run and
the per-revision durations come from the plane's run store and ledger (``GatewayPlane``). Headless (no plane),
the run-dependent fields are empty or null.

Pace: notes understood = applied processing revisions (``action IS NULL``: user actions such as a retraction or
a merge confirmation are not processing) by ``recorded_at``, the moment the notebook recorded them (not the
note's history time). ``series`` = 24 buckets of 5 minutes (aligned on the UTC clock), the last one being the
current, partial bucket. ``eta_s`` = queued notes / ``per_minute_1h``, null while nothing was understood in
the last hour.

Active runs: the plane's RUNNING or WAITING runs of the note flows (``abstractnote@<version>:<flow>``). The
step is the latest STARTED effect of the run's ledger, mapped by node id (``STEP_OF_NODE``; a node outside the
table, a lease or an acknowledgement, has no step). A run whose latest effect is a subflow (a drain on its
note-process, a recall on its mine, a capture processing inline) takes its note, step, sections and thinking
from that active child (``child_run_id``). Sections: ``sections_total`` = the sections ``note_context``
returned, ``sections_done`` = extractions completed since; both null until the context is read (or when the
ledger holds the sections as an offloaded reference).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import ids
from .shapes import note_ref

BUNDLE_ID = "abstractnote"
FLOWS = ("note-drain", "note-process", "note-reflect", "note-recall", "note-mine", "note-capture", "note-file")
STEPS = ("context", "extract", "resolve", "link", "apply", "filing", "search", "synthesize")

#: Node id (of an effect node: ``llm_call`` / ``tool_calls`` / ``subflow``) -> step, per flow. ``None`` = the
#: node has no step of its own (bookkeeping); ``CHILD`` = a subflow: the step is the active child's.
CHILD = "<child>"
STEP_OF_NODE: Dict[str, Dict[str, Optional[str]]] = {
    "note-process": {"filing_need": "filing", "filing": CHILD, "context": "context", "extract": "extract", "extract_again": "extract",
                     "resolve": "resolve", "link": "link", "link_again": "link", "apply": "apply"},
    # the quick filing of a stored note without a kind (the refile --missing repair runs it per note)
    "note-file": {"filing_context": "filing", "filing": "filing", "file": "filing"},
    "note-capture": {"capture": "filing", "filing": "filing", "file": "filing", "process": CHILD,
                     "inline_ack": None, "inline_fail": None},
    "note-drain": {"lease": None, "process": CHILD, "ack": None, "communities": None},
    "note-recall": {"search": "search", "expand": "search", "synthesize": "synthesize", "mine": CHILD},
    "note-mine": {"search": "search", "step_search": "search", "plan": "synthesize", "findings": "synthesize",
                  "dossier": "synthesize", "write": "synthesize"},
    "note-reflect": {"open_loops": "search", "contradictions": "search", "resurface": "search",
                     "anniversaries": "search", "reflect": "synthesize", "write": "synthesize",
                     "communities": "search", "communities_pending": "search",
                     "communities_names": "synthesize", "communities_label": "synthesize"},
    # The consolidation (backlog 0032) reports its own stages (GET /note/processing "consolidation"); it is not
    # an active note run, so its nodes have no step here (its reflect subflow's steps are the child's).
    "note-consolidate": {**{n: None for n in (
        "begin", "fail_end", "p_norm_start", "normalize", "p_norm_done",
        "re_count", "p_re_start", "re_next", "p_re", "re_final", "p_re_done",
        "p_comm_start", "communities", "p_comm_done",
        "p_labels_start", "communities_pending", "communities_names", "communities_names_again", "communities_label",
        "p_labels", "stale_count", "p_sum_start", "stale", "summaries", "summaries_again", "set_summaries", "p_sum",
        "p_tn_start", "then_now", "p_tn_done",
        "p_reflect_start", "p_reflect_done", "finish")}, "reflect": CHILD,
        # ADR 0008: the note being understood again is a note-process child (its steps are the child's)
        "re_process": CHILD},
}

BUCKET_S = 300
SERIES_BUCKETS = 24  # 2 hours
RECENT_LIMIT = 20
FAILED_LIMIT = 50
ACTIVE_SCAN_LIMIT = 500        # RUNNING / WAITING index rows looked at (the store's active index)
DURATION_SCAN_LIMIT = 300      # newest COMPLETED index rows searched for a revision's note-process run
DURATION_SETTLE_S = 120        # a revision without a run found this long after it was recorded has none
TEXT_DEFAULT_TTL_S = 30.0

_PROCESSING = "applied_index=1 AND action IS NULL AND note_id IS NOT NULL"
_log = logging.getLogger("abstractnote.processing")


# --------------------------------------------------------------------------- small helpers


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(value: Any) -> Optional[datetime]:
    """A run store / ledger timestamp (``2026-10-20T07:20:23.9+00:00`` or ``...Z``) as an aware datetime."""
    if not value:
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def flow_of(workflow_id: Any) -> Optional[str]:
    """``abstractnote@0.1.1:note-drain`` -> ``note-drain``; ``None`` for any other bundle or flow. The bare
    flow id (``note-drain``, the headless runner's registry) is the same flow."""
    wf = str(workflow_id or "")
    bundle, sep, flow = wf.rpartition(":")
    if not sep:
        bundle, flow = BUNDLE_ID, wf
    if bundle.split("@", 1)[0] != BUNDLE_ID or flow not in FLOWS:
        return None
    return flow


def _tool_output(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    res = record.get("result")
    results = res.get("results") if isinstance(res, dict) else None
    first = results[0] if isinstance(results, list) and results else None
    out = first.get("output") if isinstance(first, dict) and first.get("success", True) else None
    return out if isinstance(out, dict) else None


# --------------------------------------------------------------------------- ledger fold


class LedgerFold:
    """What one run's ledger says so far, folded record by record (incremental: the tail feeds new records)."""

    def __init__(self, flow: str) -> None:
        self.flow = flow
        self.node: Optional[str] = None
        self.node_started_at: Optional[str] = None
        self.sections_total: Optional[int] = None
        self.extract_steps: set = set()
        self.note_id: Optional[str] = None  # note-capture: the note its capture tool saved

    def feed(self, record: Dict[str, Any]) -> None:
        status = _status(record.get("status"))
        node = str(record.get("node_id") or "")
        eff = record.get("effect") if isinstance(record.get("effect"), dict) else {}
        etype = str(eff.get("type") or "")
        if status == "started" and etype and etype != "emit_event":
            self.node, self.node_started_at = node, record.get("started_at")
            if self.flow == "note-process" and node == "context":
                self.sections_total, self.extract_steps = None, set()
        elif status == "completed":
            if self.flow == "note-process" and node == "context":
                out = _tool_output(record)
                sections = out.get("sections") if out else None
                self.sections_total = len(sections) if isinstance(sections, list) else None
            elif self.flow == "note-process" and node == "extract":
                self.extract_steps.add(record.get("step_id"))
            elif self.flow == "note-capture" and node == "capture":
                out = _tool_output(record)
                if out and out.get("note_id"):
                    self.note_id = str(out["note_id"])

    def step(self) -> Optional[str]:
        if self.node is None:
            return None
        return STEP_OF_NODE.get(self.flow, {}).get(self.node)

    def sections(self) -> Tuple[Optional[int], Optional[int]]:
        if self.flow != "note-process" or self.sections_total is None:
            return None, None
        done = len(self.extract_steps)
        if self.step() in ("resolve", "link", "apply"):
            done = self.sections_total
        return min(done, self.sections_total), self.sections_total


# --------------------------------------------------------------------------- the plane


class GatewayPlane:
    """Read-only view of one plane's runs for the processing page (the gateway's run store and ledger).

    One instance per plane (``plane_for``); it keeps incremental ledger tails of the active runs, the facts of
    finished runs (immutable) and the per-revision durations, so a poll every few seconds reads only what is new.
    """

    def __init__(self, run_store: Any, ledger_store: Any, *, text_default: Any = None) -> None:
        if not callable(getattr(run_store, "list_run_index", None)):
            raise RuntimeError("This server's run store cannot list runs by status.")
        self.run_store = run_store
        self.ledger_store = ledger_store
        self._text_default_fn = text_default
        self._lock = threading.Lock()
        self._tails: Dict[str, Tuple[Any, LedgerFold]] = {}
        self._finished: Dict[str, Dict[str, Any]] = {}
        self._durations: Dict[str, Optional[float]] = {}
        self._text_default: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
        self._scan_warned = 0.0

    # ---- ledger

    def _tail(self, run_id: str) -> Any:
        from abstractgateway.ledger_tail import resolve_ledger_tail

        return resolve_ledger_tail(self.ledger_store, run_id)

    def _fold(self, run_id: str, flow: str) -> LedgerFold:
        entry = self._tails.get(run_id)
        if entry is None:
            entry = (self._tail(run_id), LedgerFold(flow))
            self._tails[run_id] = entry
        tail, fold = entry
        for _index, record in tail.read_new():
            if isinstance(record, dict):
                fold.feed(record)
        return fold

    # ---- active runs

    def active(self) -> List[Dict[str, Any]]:
        from abstractruntime.core.models import RunStatus

        rows: List[Dict[str, Any]] = []
        for status in (RunStatus.RUNNING, RunStatus.WAITING):
            got = list(self.run_store.list_run_index(status=status, limit=ACTIVE_SCAN_LIMIT))
            if len(got) >= ACTIVE_SCAN_LIMIT and time.time() - self._scan_warned > 600:
                # a scan bound, not a choice: say it (ADR 0007), at most every ten minutes (the page polls)
                self._scan_warned = time.time()
                _log.warning("processing: %d %s runs on this plane reach ACTIVE_SCAN_LIMIT; active note runs past"
                             " the newest %d are not listed", len(got), getattr(status, "value", status),
                             ACTIVE_SCAN_LIMIT)
            rows.extend(got)
        runs: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            flow = flow_of(row.get("workflow_id"))
            rid = str(row.get("run_id") or "")
            if flow and rid and rid not in runs:
                runs[rid] = {"row": row, "flow": flow}
        with self._lock:
            for rid in [r for r in self._tails if r not in runs]:  # finished runs: drop their tails
                self._tails.pop(rid, None)
            facts: Dict[str, Dict[str, Any]] = {}
            for rid, item in runs.items():
                facts[rid] = self._facts(rid, item["flow"], item["row"])
        children: Dict[str, List[str]] = {}
        for rid, item in runs.items():
            parent = str(item["row"].get("parent_run_id") or "")
            if parent in runs:
                children.setdefault(parent, []).append(rid)

        def resolved(rid: str, depth: int = 0) -> Dict[str, Any]:
            f = dict(facts[rid])
            node_step = STEP_OF_NODE.get(f["flow"], {}).get(f.pop("_node") or "", None)
            if node_step == CHILD and depth < 4:
                kids = sorted(children.get(rid, []), key=lambda k: str(runs[k]["row"].get("created_at") or ""))
                if kids:
                    child = resolved(kids[-1], depth + 1)
                    f["child_run_id"] = kids[-1]
                    for key in ("step", "step_started_at", "sections_done", "sections_total"):
                        f[key] = child[key]
                    f["note_id"] = f["note_id"] or child["note_id"]
                    f["thinking"] = f["thinking"] or child["thinking"]
                else:
                    f["step"], f["child_run_id"] = None, None
            return f

        out = [resolved(rid) for rid in runs]
        out.sort(key=lambda a: (str(a.get("started_at") or ""), a["run_id"]))
        return out

    def _facts(self, rid: str, flow: str, row: Dict[str, Any]) -> Dict[str, Any]:
        fold = self._fold(rid, flow)
        run_vars: Dict[str, Any] = {}
        if flow in ("note-process", "note-drain"):
            run = self.run_store.load(rid)
            run_vars = run.vars if run is not None and isinstance(run.vars, dict) else {}
        thinking = str(run_vars.get("thinking") or "").strip().lower() or None
        note_id = run_vars.get("note_id") if flow == "note-process" else fold.note_id
        done, total = fold.sections()
        step = fold.step()
        return {
            "run_id": rid, "flow": flow, "started_at": row.get("created_at"),
            "status": _status(row.get("status")), "parent_run_id": row.get("parent_run_id") or None,
            "note_id": str(note_id) if note_id else None, "_node": fold.node,
            "step": step if step in STEPS else None, "step_started_at": fold.node_started_at,
            "sections_done": done, "sections_total": total,
            "thinking": thinking if thinking in ("on", "off") else None,
        }

    # ---- finished runs

    def _run_facts(self, rid: str) -> Optional[Dict[str, Any]]:
        """``{status, output, vars_written, created_at, updated_at}`` of a run; cached once it is terminal."""
        cached = self._finished.get(rid)
        if cached is not None:
            return cached
        run = self.run_store.load(rid)
        if run is None:
            return None
        status = _status(run.status)
        output = run.output if isinstance(run.output, dict) else {}
        if status == "completed" and output.get("success") is False:
            status = "failed"  # a node failure ends a VisualFlow run COMPLETED with success false
        written = (run.vars or {}).get("reflect_written") if isinstance(run.vars, dict) else None
        facts = {"status": status, "output": output, "written": written if isinstance(written, list) else [],
                 "created_at": run.created_at, "updated_at": run.updated_at}
        if status in ("completed", "failed", "cancelled"):
            self._finished[rid] = facts
        return facts

    def last_run(self, automation_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """The automation's latest occurrence: ``{run_id, started_at, ended_at, status, insights}``."""
        if not automation_id:
            return None
        row = self.run_store.latest_occurrence_row(str(automation_id))
        if not row:
            return None
        rid = str(row.get("run_id") or "")
        with self._lock:
            facts = self._run_facts(rid)
        if facts is None:
            return None
        ids_out = facts["output"].get("insight_note_ids")
        insights = len(ids_out) if isinstance(ids_out, list) else len(facts["written"])
        terminal = facts["status"] in ("completed", "failed", "cancelled")
        return {"run_id": rid, "started_at": facts["created_at"], "status": facts["status"],
                "ended_at": facts["updated_at"] if terminal else None, "insights": insights}

    # ---- durations of recent revisions

    def durations(self, revisions: Iterable[Dict[str, Any]], *, now: datetime) -> Dict[str, Optional[float]]:
        """``{revision_id: seconds}`` of the note-process run that applied each revision (its start to its end),
        found among the newest completed runs and confirmed by the run's own output (``revision_id``)."""
        revs = list(revisions)
        with self._lock:
            missing = [r for r in revs if r["revision_id"] not in self._durations]
            if missing:
                from abstractruntime.core.models import RunStatus

                rows = [row for row in self.run_store.list_run_index(status=RunStatus.COMPLETED,
                                                                      limit=DURATION_SCAN_LIMIT)
                        if flow_of(row.get("workflow_id")) == "note-process"]
                for rev in missing:
                    rid = rev["revision_id"]
                    recorded = _dt(rev["recorded_at"])
                    found: Optional[float] = None
                    for row in rows:
                        start, end = _dt(row.get("created_at")), _dt(row.get("updated_at"))
                        if recorded is None or start is None or end is None:
                            continue
                        if not (start - timedelta(seconds=1) <= recorded <= end + timedelta(seconds=1)):
                            continue
                        facts = self._run_facts(str(row.get("run_id")))
                        if facts and facts["output"].get("revision_id") == rid:
                            found = round((end - start).total_seconds(), 3)
                            break
                    if found is not None or (recorded is not None
                                             and (now - recorded).total_seconds() > DURATION_SETTLE_S):
                        self._durations[rid] = found
            return {r["revision_id"]: self._durations.get(r["revision_id"]) for r in revs}

    # ---- model

    def text_default(self) -> Optional[Dict[str, Any]]:
        """The plane's text-generation default (``input.text``, read by name), cached for 30 s."""
        if self._text_default_fn is None:
            return None
        at, value = self._text_default
        if time.monotonic() - at < TEXT_DEFAULT_TTL_S:
            return value
        row = self._text_default_fn()
        value = {"provider": row.get("provider"), "model": row.get("model")} if isinstance(row, dict) else None
        self._text_default = (time.monotonic(), value)
        return value


_planes: Dict[str, GatewayPlane] = {}
_planes_lock = threading.Lock()


def plane_for(svc: Any) -> GatewayPlane:
    """The ``GatewayPlane`` of a gateway service (one per plane data dir, kept for the process)."""
    from pathlib import Path

    key = str(Path(svc.config.data_dir).resolve())
    with _planes_lock:
        plane = _planes.get(key)
        if plane is None or plane.run_store is not svc.host.run_store:
            data_dir = Path(svc.config.data_dir)

            def _text_default() -> Dict[str, Any]:
                from abstractgateway.core_config import text_default

                return text_default(base_dir=data_dir)

            plane = GatewayPlane(svc.host.run_store, svc.host.ledger_store, text_default=_text_default)
            _planes[key] = plane
        return plane


def reset_planes() -> None:
    with _planes_lock:
        _planes.clear()


# --------------------------------------------------------------------------- the notebook part


def _pace(q: Any, now: datetime) -> Dict[str, Any]:
    end_bucket = int(now.timestamp()) // BUCKET_S
    start = datetime.fromtimestamp((end_bucket - SERIES_BUCKETS + 1) * BUCKET_S, tz=timezone.utc)
    counts = {int(b): int(n) for b, n in q(
        f"SELECT CAST(strftime('%s', recorded_at) AS INTEGER) / {BUCKET_S} AS b, COUNT(*) FROM revisions"
        f" WHERE {_PROCESSING} AND recorded_at >= ? GROUP BY b", (_iso(start),))}
    series = [{"t": _iso(datetime.fromtimestamp(b * BUCKET_S, tz=timezone.utc)), "understood": counts.get(b, 0)}
              for b in range(end_bucket - SERIES_BUCKETS + 1, end_bucket + 1)]

    def since(minutes: int) -> int:
        row = q(f"SELECT COUNT(*) FROM revisions WHERE {_PROCESSING} AND recorded_at >= ? AND recorded_at <= ?",
                (_iso(now - timedelta(minutes=minutes)), _iso(now)))
        return int(row[0][0] or 0)

    hour = since(60)
    return {"per_minute_5m": round(since(5) / 5.0, 2), "per_minute_1h": round(hour / 60.0, 3),
            "series": series, "_hour": hour}


def _recent(q: Any) -> List[Dict[str, Any]]:
    rows = q(
        "SELECT r.revision_id, r.at, r.recorded_at, r.summary, r.result_json, n.note_id, n.title, n.kind,"
        " n.occurred_at, n.summary AS n_summary, n.one_line, n.text FROM revisions r JOIN notes n"
        f" ON n.note_id=r.note_id WHERE r.{_PROCESSING.replace(' AND ', ' AND r.')}"
        " ORDER BY r.recorded_at DESC, r.seq DESC LIMIT ?", (RECENT_LIMIT,))
    out = []
    for r in rows:
        try:
            result = json.loads(r["result_json"] or "{}")
        except ValueError:
            result = {}
        result = result if isinstance(result, dict) else {}

        def n(key: str) -> int:
            v = result.get(key)
            return len(v) if isinstance(v, list) else 0

        ref = note_ref({"note_id": r["note_id"], "title": r["title"], "kind": r["kind"],
                        "occurred_at": r["occurred_at"], "summary": r["n_summary"], "one_line": r["one_line"],
                        "text": r["text"]})
        out.append({"revision_id": r["revision_id"], "note": ref, "at": r["at"], "recorded_at": r["recorded_at"],
                    "summary": r["summary"], "entities_created": n("entities_created"),
                    "claims_added": n("claims_added"), "claims_superseded": n("claims_superseded"),
                    "duration_s": None})
    return out


def _failed(q: Any) -> List[Dict[str, Any]]:
    return [{"note_id": r["note_id"], "title": r["title"] or "", "error": r["last_error"],
             "attempts": int(r["attempts"] or 0), "next_attempt_at": r["next_attempt_at"],
             "failed_at": r["failed_at"]}
            for r in q("SELECT q.note_id, q.last_error, q.attempts, q.next_attempt_at, q.failed_at, n.title"
                       " FROM queue q JOIN notes n ON n.note_id=q.note_id WHERE q.state='failed'"
                       " ORDER BY q.failed_at DESC, q.note_id LIMIT ?", (FAILED_LIMIT,))]


def _communities(nb: Any) -> Dict[str, Any]:
    row = nb.store.q1("SELECT COUNT(*), SUM(label IS NULL), SUM(stale_label) FROM communities")
    meta = nb.meta("communities") or {}
    return {"count": int(row[0] or 0), "unlabelled": int(row[1] or 0), "stale_label": int(row[2] or 0),
            "computed_at": meta.get("computed_at") if isinstance(meta, dict) else None}


def _automation(health: Optional[Dict[str, Any]], automation_id: Optional[str]) -> Dict[str, Any]:
    h = health or {}
    return {"id": h.get("id", automation_id), "state": h.get("state"), "paused": h.get("paused"),
            "healthy": h.get("healthy"), "reason": h.get("reason"), "target": h.get("target")}


def processing(nb: Any, *, plane: Optional[GatewayPlane] = None, automations: Optional[Dict[str, Any]] = None,
               now: Optional[str] = None) -> Dict[str, Any]:
    """The processing page (docs/api.md ``GET /note/processing``). ``automations`` is the router's
    ``automations_health`` (``None`` headless: the states are null)."""
    now_s = ids.normalize_time(now, field="now") if now else ids.utc_now()
    now_dt = ids.parse_time(now_s)
    q = nb.store.q
    autos = automations or {}
    proc_id, refl_id = nb.meta("processor_automation_id"), nb.meta("reflect_automation_id")

    stats = nb.queue.stats()
    pace = _pace(q, now_dt)
    hour = pace.pop("_hour")  # understood in the last hour: eta = queued / (hour / 60) minutes
    queue = {**{k: stats[k] for k in ("queued", "processing", "failed", "done", "by_priority",
                                      "oldest_enqueued_at")},
             "eta_s": int(round(stats["queued"] * 3600 / hour)) if hour > 0 else None}
    recent = _recent(q)
    throughput = nb.processor_throughput()

    processor = {**_automation(autos.get("processor"), proc_id),
                 "schedule": {"every": throughput["interval"], "max_items": throughput["max_items"]},
                 "last_run_at": None, "last_status": None}
    reflect = {**_automation(autos.get("reflect"), refl_id), "last_run_at": None, "last_status": None,
               "insights_last_run": None, "communities": _communities(nb)}
    active: List[Dict[str, Any]] = []
    text_default = None
    if plane is not None:
        last = plane.last_run(proc_id)
        if last:
            processor["last_run_at"], processor["last_status"] = last["started_at"], last["status"]
        last = plane.last_run(refl_id)
        if last:
            reflect["last_run_at"], reflect["last_status"] = last["started_at"], last["status"]
            reflect["insights_last_run"] = last["insights"]
            reflect["last_ended_at"] = last["ended_at"]
        runs = plane.active()
        refs = _note_refs(nb, [a["note_id"] for a in runs if a.get("note_id")])
        for a in runs:
            nid = a.pop("note_id")
            a["note"] = refs.get(nid) if nid else None
            active.append(a)
        durations = plane.durations(recent, now=now_dt)
        for r in recent:
            r["duration_s"] = durations.get(r["revision_id"])
        text_default = plane.text_default()

    settings = nb.settings()
    filing = settings.get("filing_model") if isinstance(settings.get("filing_model"), dict) else {}
    if filing.get("provider") and filing.get("model"):
        model = {"provider": filing["provider"], "model": filing["model"], "source": "notebook"}
    elif text_default and (text_default.get("provider") or text_default.get("model")):
        model = {"provider": text_default.get("provider"), "model": text_default.get("model"), "source": "server"}
    else:
        model = {"provider": filing.get("provider"), "model": filing.get("model"), "source": None}
    model.update({"thinking_bulk": "off", "thinking_live": "on"})

    return {"now": now_s, "processor": processor, "reflect": reflect, "queue": queue, "pace": pace,
            "active": active, "recent": recent, "failed": _failed(q), "model": model}


def _note_refs(nb: Any, note_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    if not note_ids:
        return {}
    marks = ",".join("?" for _ in note_ids)
    return {r["note_id"]: note_ref(r) for r in nb.store.q(
        f"SELECT note_id, title, kind, occurred_at, summary, one_line, text FROM notes WHERE note_id IN ({marks})",
        tuple(note_ids))}
