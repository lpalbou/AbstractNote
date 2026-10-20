"""The consolidation automation and its page (backlog 0032, docs/api.md "Consolidation").

A third automation per notebook runs ``note-consolidate`` on the ``consolidate_schedule`` setting (``{"every":
"1d"}`` by default; ``"off"`` keeps it paused). ``ensure_consolidation`` is called at the end of
``POST /note/processor/ensure`` (``router.ensure_automations``) with the same self-heal rules as the processor and
the reflect automation (kept when it runs the served version with the current tool list, the configured interval
and is active or paused; otherwise archived and created again, paused again when the old one was paused by the
user). ``apply_schedule`` is called by ``PUT /note/settings`` when ``consolidate_schedule`` changes.

``consolidation_view(nb, svc, principal)`` is the ``consolidation`` object of ``GET /note/processing`` and the
body of ``GET /note/consolidation``: ``{automation, run, last, pending}`` read from the notebook's
``consolidation_runs`` rows and the plane's runs.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from ..flows.build import BUNDLE_ID, CONSOLIDATE_DEFAULTS

#: Included by ``router.router`` (prefix ``/note``), so its paths are relative to ``/note``.
router = APIRouter(tags=["note"])

FLOW_ID = "note-consolidate"
REQUEST_ID = "abstractnote-consolidate"
META_KEY = "consolidate_automation_id"
APPLIED_KEY = "consolidate_every_applied"      # the interval the automation was created with
OFF_PAUSED_KEY = "consolidate_paused_by_setting"  # paused because the setting is "off" (not by the user)
DEFAULT_EVERY = "1d"
TITLE = "Consolidate my notes"
ACTIVE_STATUSES = ("running", "waiting")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _r() -> Any:
    from . import router as r

    return r


def _desired(nb: Any) -> Dict[str, Any]:
    """``{every, off, interval}``: the setting, and the interval the automation runs (the last one applied while
    the setting is off)."""
    every = str((nb.settings().get("consolidate_schedule") or {}).get("every") or DEFAULT_EVERY)
    off = every == "off"
    interval = str(nb.meta(APPLIED_KEY) or DEFAULT_EVERY) if off else every
    return {"every": every, "off": off, "interval": interval}


def _trigger_every(svc: Any, automation_id: Optional[str]) -> Optional[str]:
    if not automation_id:
        return None
    from abstractgateway.automation_errors import AutomationError
    from abstractgateway.routes.automations import load_automation_controller

    try:
        controller = load_automation_controller(svc, automation_id)
    except AutomationError as e:
        if e.status_code == 404:
            return None
        raise
    vars_obj = controller.vars if isinstance(controller.vars, dict) else {}
    definition = (vars_obj.get("_meta") or {}).get("automation") or {}
    config = ((definition.get("trigger") or {}).get("config") or {}) if isinstance(definition, dict) else {}
    every = config.get("every") if isinstance(config, dict) else None
    return str(every) if every else None


def consolidation_health(nb: Any, svc: Any, served: Optional[str] = None, *, served_known: bool = False
                         ) -> Dict[str, Any]:
    """``{id, state, target, healthy, reason, paused, schedule: {every}}`` of the consolidate automation; also
    unhealthy ("The schedule changed.") when it runs another interval than the setting asks for."""
    r = _r()
    if not served_known:
        served = r._served_version(svc)
    automation_id = nb.meta(META_KEY)
    health = dict(r._health(svc, automation_id, FLOW_ID, served))
    want = _desired(nb)
    if health["healthy"] and not want["off"]:
        running = _trigger_every(svc, automation_id)
        if running != want["interval"]:
            health.update(healthy=False, reason="The schedule changed.")
    health["schedule"] = {"every": want["every"]}
    return health


def _command(svc: Any, principal: Any, automation_id: str, kind: str) -> None:
    from abstractgateway.routes.automations import _command as command

    command(svc, principal, automation_id, f"abstractnote-consolidate-{kind}-{automation_id}-{time.time_ns()}",
            f"automation.{kind}", {})


def ensure_consolidation(nb: Any, svc: Any, principal: Any, out: Dict[str, Any]) -> Dict[str, Any]:
    """Create or repair the consolidate automation (idempotent); adds ``consolidate`` / ``consolidate_id`` to
    ``out`` (the ensure response) and ``note-consolidate`` to ``out["created"]`` when it was (re)created."""
    from abstractgateway.routes.automations import CreateAutomationBody

    from abstractnote.tools import TOOL_NAMES

    r = _r()
    served = r._served_version(svc)
    if served is None:
        raise HTTPException(status_code=503, detail="The note workflows are not loaded on this server; restart it "
                                                    "or reinstall AbstractNote, then try again.")
    want = _desired(nb)
    previous = nb.meta(META_KEY)
    health = consolidation_health(nb, svc, served, served_known=True)
    if health["healthy"]:
        if want["off"] and not health["paused"]:
            _command(svc, principal, previous, "pause")
            nb.set_meta(OFF_PAUSED_KEY, True)
        out["consolidate"] = {"id": previous, "action": "kept", "reason": None}
        out["consolidate_id"] = previous
        return out
    user_paused = bool(health.get("paused")) and not nb.meta(OFF_PAUSED_KEY)
    paused = want["off"] or user_paused
    request_id = REQUEST_ID
    if previous:
        if health["state"] not in ("missing", "archived"):
            r._archive(svc, principal, previous)
        generation = int(nb.meta(f"{META_KEY}_generation") or 0) + 1
        request_id = f"{REQUEST_ID}-{generation}"
        nb.set_meta(f"{META_KEY}_generation", generation)
    interval = want["interval"]
    # The first occurrence is one interval after creation: opening a notebook never starts a model call
    # ("Consolidate now" = automation.run_now).
    schedule = {"kind": "every", "every": interval, **r._after_one_interval(interval)}
    body = CreateAutomationBody(
        request_id=request_id, title=TITLE,
        target={"bundle_ref": BUNDLE_ID, "flow_id": FLOW_ID,
                "input_data": {**CONSOLIDATE_DEFAULTS, **r._model_inputs(nb.settings().get("answering_model")),
                               "_runtime": {"allowed_tools": list(TOOL_NAMES)}}},
        trigger={"source_id": "schedule", "source_version": 2, "config": schedule},
        context={"mode": "independent"},
    )
    automation_id = r._create_or_refind(svc, principal, body)
    if paused:
        _command(svc, principal, automation_id, "pause")
    nb.set_meta(OFF_PAUSED_KEY, bool(want["off"]))
    nb.set_meta(META_KEY, automation_id)
    nb.set_meta(APPLIED_KEY, interval)
    entry: Dict[str, Any] = {"id": automation_id, "action": "recreated" if previous else "created",
                             "reason": health["reason"] if previous else None}
    if previous:
        entry["previous_id"] = previous
    out["consolidate"] = entry
    out["consolidate_id"] = automation_id
    out.setdefault("created", []).append(FLOW_ID)
    return out


def apply_schedule(nb: Any, svc: Any, principal: Any) -> Optional[Dict[str, Any]]:
    """After ``PUT /note/settings {consolidate_schedule}``: re-create the automation with the new interval, pause it
    for ``off``, resume it when it was paused only because of ``off``. Nothing when it was never created (the next
    ensure creates it from the setting)."""
    if not nb.meta(META_KEY):
        return None
    out: Dict[str, Any] = {}
    ensure_consolidation(nb, svc, principal, out)
    want = _desired(nb)
    automation_id = out["consolidate_id"]
    if not want["off"] and nb.meta(OFF_PAUSED_KEY):
        if bool((_r()._read_automation(svc, automation_id) or {}).get("paused")):
            _command(svc, principal, automation_id, "resume")
        nb.set_meta(OFF_PAUSED_KEY, False)
    return out["consolidate"]


# --------------------------------------------------------------------------- the view


def _ts(value: Any) -> Optional[float]:
    """A run store or notebook timestamp (``...+00:00`` or ``...Z``) as epoch seconds."""
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _occurrence(svc: Any, automation_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not automation_id:
        return None
    fn = getattr(getattr(getattr(svc, "host", None), "run_store", None), "latest_occurrence_row", None)
    if not callable(fn):
        raise RuntimeError("This server's run store cannot find an automation's latest run.")
    row = fn(str(automation_id))
    return dict(row) if row else None


def consolidation_view(nb: Any, svc: Any, principal: Any) -> Dict[str, Any]:
    """``{automation: {id, state, paused, healthy, reason, schedule: {every}}, run: {run_id, started_at, stage,
    stage_done, stage_total, eta_s, elapsed_s} | null, last: {finished_at, status, merges, communities, labelled,
    summaries_refreshed, insights, left_out, budget_spent, notice, ...} | null, pending: {stale_labels, unlabelled,
    stale_summaries, reinterpret}, reinterpret: {pending, by_entity, reasons, context_unknown, consumed_today,
    last_marked_at}}`` (``reinterpret``: what waits to be understood again and why, readable before the stage runs,
    ADR 0008 §5; the headless :func:`abstractnote.recall.consolidation.state` shape). ``last.status`` ``completed_with_gaps`` = the run left something for the next one
    (``left_out``; ``notice`` is its end-user sentence), so ``last`` and ``pending`` agree (ADR 0007).

    An open consolidation whose automation run has already ended (it stopped before closing its row) is reported
    as the last one, status ``interrupted``, and no run."""
    from ..recall import consolidation as cons

    health = consolidation_health(nb, svc)
    automation = {k: health.get(k) for k in ("id", "state", "paused", "healthy", "reason", "schedule")}
    row = cons.open_row(nb)
    run = cons.run_view(nb, row) if row else None
    last = cons.last(nb)
    if run is not None:
        occ = _occurrence(svc, automation["id"])
        status = _status(occ.get("status")) if occ else ""
        if occ and status in ACTIVE_STATUSES:
            run["run_id"] = str(occ.get("run_id") or "") or None
        elif occ and status in TERMINAL_STATUSES and \
                (_ts(occ.get("updated_at")) or 0.0) >= float(row["started_ts"]) - 1.0:
            last = {"consolidation_id": row["consolidation_id"], "started_at": row["started_at"],
                    "finished_at": None, "status": "interrupted", "error": cons.INTERRUPTED, "merges": None,
                    "communities": None, "labelled": None, "summaries_refreshed": None, "insights": None,
                    "left_out": None, "budget_spent": False, "notice": None}
            run = None
        else:
            run["run_id"] = None  # started outside the automation (a headless or manual run)
    if run is not None:
        run = {k: run.get(k) for k in ("run_id", "started_at", "stage", "stage_done", "stage_total", "eta_s",
                                       "elapsed_s")}
    return {"automation": automation, "run": run, "last": last, "pending": cons.pending(nb),
            "reinterpret": nb.store.reinterpret_state()}


@router.get("/consolidation")
async def note_consolidation(request: Request) -> Dict[str, Any]:
    """The consolidation of the caller's notebook: its automation, the running stage with its progress and ETA,
    the last result and what is pending (docs/api.md)."""
    return await _r()._run(request, consolidation_view)


__all__ = ["apply_schedule", "consolidation_health", "consolidation_view", "ensure_consolidation", "router"]
