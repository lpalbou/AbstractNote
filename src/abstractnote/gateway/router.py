"""The note read API (docs/api.md "Note read API"), mounted by the gateway at ``/api/gateway/note``.

Every request resolves the caller's own plane through the gateway (``get_gateway_service()`` ->
``svc.config.data_dir / "abstractnote"``): clients never name a user, tenant or runtime, and an
id from another notebook simply is not there (404). Every read delegates to the core lane's
``Notebook`` so "as of" and history have one implementation (review 01 S4). The write actions
here are bookkeeping (user-authored revisions, acknowledgements, queue retries, settings,
automation set-up), never knowledge; knowledge changes go through the flows.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ..flows.build import BUNDLE_ID, DRAIN_DEFAULTS, NOTE_TOOLS
from .tools import notebook_dir

router = APIRouter(prefix="/note", tags=["note"])

PROCESSOR_REQUEST_ID = "abstractnote-processor"
REFLECT_REQUEST_ID = "abstractnote-reflect"

_notebooks: Dict[str, Any] = {}
_notebooks_lock = threading.Lock()


# --------------------------------------------------------------------------- plumbing


def _principal(request: Request) -> Any:
    from abstractgateway.routes.gateway import _principal_from_request

    return _principal_from_request(request)


def _service() -> Any:
    from abstractgateway.service import get_gateway_service

    return get_gateway_service()


def plane_embedder(data_dir: Path) -> Any:
    """The plane's embedding route (``None`` when none is configured: the notebook keeps its pin)."""
    from abstractgateway.memory_store import build_gateway_memory_embedder

    return build_gateway_memory_embedder(base_dir=Path(data_dir))


def notebook_for(data_dir: Path) -> Any:
    """The plane's Notebook, opened once per process (W1's ``Notebook.open``)."""
    from abstractnote.store.notebook import Notebook  # core lane

    path = str(notebook_dir(Path(data_dir)).resolve())
    with _notebooks_lock:
        nb = _notebooks.get(path)
        if nb is None:
            nb = Notebook.open(path, embedder=plane_embedder(Path(data_dir)))
            _notebooks[path] = nb
        return nb


def close_notebooks() -> None:
    """Close every cached notebook (tests, shutdown)."""
    with _notebooks_lock:
        for nb in _notebooks.values():
            try:
                nb.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
        _notebooks.clear()


def _missing(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"That {what} is not in your notebook.")


def _exists(nb: Any, kind: str, ident: str) -> bool:
    """Is this id in the caller's notebook? (an existence check, so an absent id is 404 and only
    genuinely bad input is 400; the core lane signals both with ValueError)."""
    store = nb.store
    if kind == "note":
        return store.note_row(ident) is not None
    if kind == "entity":
        return store.entity_row(ident) is not None
    if kind == "claim":
        return store.claim_row(ident) is not None
    if kind == "task":
        return store.q1("SELECT 1 FROM tasks WHERE task_id=?", (ident,)) is not None
    raise ValueError(kind)


def _require(nb: Any, kind: str, ident: str, what: str) -> None:
    if not _exists(nb, kind, ident):
        raise _missing(what)


def _call(nb: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    fn = getattr(nb, name, None)
    if not callable(fn):  # a core-lane method this version does not have yet: loud, never a fake answer
        raise HTTPException(status_code=501, detail=f"This notebook cannot answer {name} yet.")
    try:
        return fn(*args, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _clean(**kwargs: Any) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def _csv(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    items = [v.strip() for v in str(value).split(",") if v.strip()]
    return items or None


async def _run(request: Request, work: Callable[[Any, Any, Any], Any]) -> Any:
    """Resolve principal + plane on the request, run ``work(nb, svc, principal)`` off the loop."""
    principal = _principal(request)
    svc = _service()
    data_dir = Path(svc.config.data_dir)

    def _job() -> Any:
        return work(notebook_for(data_dir), svc, principal)

    return await asyncio.to_thread(_job)


def _account(principal: Any) -> str:
    return str(getattr(principal, "user_id", "") or "")


# The app-server session proxy marks every request it relays with the app's id (it drops any value
# the browser sent): `X-AbstractFramework-App-Proxy: abstractnote` is the Note web app.
APP_PROXY_HEADER = "X-AbstractFramework-App-Proxy"
APP_CLIENTS = {"abstractnote": "web"}


def action_client(request: Request) -> str:
    """``source.client`` of a user action: ``web`` through the Note web app's proxy, else ``api``.
    Informational only; the signed-in account (the principal) is what is authoritative."""
    app = str(request.headers.get(APP_PROXY_HEADER) or "").strip().lower()
    return APP_CLIENTS.get(app, "api")


def _items(value: Any) -> Dict[str, Any]:
    """Every list answers ``{items, next_cursor}`` (api.md); ``next_cursor`` is null when the list
    does not page."""
    if isinstance(value, dict) and "items" in value:
        return {**value, "next_cursor": value.get("next_cursor")}
    return {"items": list(value or []), "next_cursor": None}


# --------------------------------------------------------------------------- status and changes


def _automation_ids(nb: Any) -> Dict[str, Optional[str]]:
    return {"processor_id": nb.meta("processor_automation_id"), "reflect_id": nb.meta("reflect_automation_id")}


@router.get("/status")
async def note_status(request: Request) -> Dict[str, Any]:
    def work(nb, svc, principal):
        out = dict(_call(nb, "status"))
        out["automations"] = _automation_ids(nb)
        out.pop("notebook", None)  # a server path: not for clients
        return out

    return await _run(request, work)


@router.get("/changes")
async def note_changes(request: Request, since: Optional[str] = None, limit: int = Query(100, ge=1, le=1000),
                       as_received: Optional[str] = None) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "changes", since=since, limit=limit,
                                                        **_clean(as_received=as_received or None)))


@router.get("/overview")
async def note_overview(request: Request, days: int = Query(30, ge=1, le=3660)) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "overview", days=days))


# --------------------------------------------------------------------------- notes


@router.get("/notes")
async def note_list(
    request: Request, q: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
    kind: Optional[str] = None, topic: Optional[str] = None, entity: Optional[str] = None,
    status: Optional[str] = None, cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "notes", **_clean(
        q=q, since=since, until=until, kind=kind, topic=topic, entity=entity, status=status, cursor=cursor, limit=limit))))


@router.get("/notes/{note_id}")
async def note_get(request: Request, note_id: str) -> Dict[str, Any]:
    def work(nb, svc, p):
        note = _call(nb, "note", note_id)
        if note is None:
            raise _missing("note")
        return note

    return await _run(request, work)


@router.get("/notes/{note_id}/attachments/{attachment_id}")
async def note_attachment(request: Request, note_id: str, attachment_id: str) -> Response:
    def work(nb, svc, p):
        _require(nb, "note", note_id, "note")
        try:
            blob = nb.attachment_bytes(note_id, attachment_id)
        except ValueError:
            raise _missing("attachment")
        return blob

    blob = await _run(request, work)
    filename = str(blob.get("filename") or attachment_id).replace('"', "")
    return Response(
        content=blob["data"], media_type=str(blob.get("mime") or "application/octet-stream"),
        headers={"Content-Disposition": f'inline; filename="{filename}"', "Cache-Control": "private, max-age=3600"},
    )


@router.post("/notes/{note_id}/archive")
async def note_archive(request: Request, note_id: str) -> Dict[str, Any]:
    def work(nb, svc, p):
        _require(nb, "note", note_id, "note")
        return _call(nb, "archive_note", note_id)

    return await _run(request, work)


# --------------------------------------------------------------------------- search and entities


@router.get("/search")
async def note_search(
    request: Request, q: str = Query(..., min_length=1), since: Optional[str] = None, until: Optional[str] = None,
    kinds: Optional[str] = None, entities: Optional[str] = None, topics: Optional[str] = None,
    limit: int = Query(20, ge=1, le=200),
) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "search", q, **_clean(
        since=since, until=until, kinds=_csv(kinds), entities=_csv(entities), topics=_csv(topics), limit=limit)))


@router.get("/entities")
async def note_entities(
    request: Request, q: Optional[str] = None, kind: Optional[str] = None, sort: Optional[str] = None,
    cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=500), include_merged: bool = False,
) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "entities", **_clean(
        q=q, kind=kind, sort=sort, cursor=cursor, limit=limit, include_merged=include_merged or None))))


@router.get("/entities/{entity_id}")
async def note_entity(request: Request, entity_id: str, at: Optional[str] = None,
                      as_received: Optional[str] = None, as_merged: bool = False) -> Dict[str, Any]:
    def work(nb, svc, p):
        _require(nb, "entity", entity_id, "entity")
        if as_merged:
            if at or as_received:
                raise HTTPException(status_code=400, detail="An entry's own record has no date; leave out at.")
            return _call(nb, "entity_record", entity_id)
        return _call(nb, "entity", entity_id, **_clean(at=at or None, as_received=as_received or None))

    return await _run(request, work)


def _entity_or_404(nb: Any, entity_id: str) -> None:
    _require(nb, "entity", entity_id, "entity")


@router.get("/entities/{entity_id}/timeline")
async def note_entity_timeline(request: Request, entity_id: str, since: Optional[str] = None,
                               until: Optional[str] = None) -> Dict[str, Any]:
    def work(nb, svc, p):
        _entity_or_404(nb, entity_id)
        return _call(nb, "entity_timeline", entity_id, **_clean(since=since, until=until))

    return await _run(request, work)


@router.get("/entities/{entity_id}/then-now")
async def note_entity_then_now(request: Request, entity_id: str) -> Dict[str, Any]:
    def work(nb, svc, p):
        _entity_or_404(nb, entity_id)
        return _call(nb, "then_now", entity_id)

    return await _run(request, work)


@router.get("/relations/{a}/{b}")
async def note_relation(request: Request, a: str, b: str, since: Optional[str] = None,
                        until: Optional[str] = None) -> Dict[str, Any]:
    def work(nb, svc, p):
        _entity_or_404(nb, a)
        _entity_or_404(nb, b)
        return _call(nb, "relation", a, b, **_clean(since=since, until=until))

    return await _run(request, work)


class UnmergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_id: str
    reason: str = ""


@router.post("/entities/{entity_id}/unmerge")
async def note_unmerge(request: Request, entity_id: str, body: UnmergeBody) -> Dict[str, Any]:
    def work(nb, svc, p):
        _entity_or_404(nb, entity_id)
        _entity_or_404(nb, body.from_id)
        return _call(nb, "unmerge_entity", entity_id, body.from_id, _account(p), body.reason,
                     client=action_client(request))

    return await _run(request, work)


# --------------------------------------------------------------------------- review


@router.get("/review")
async def note_review(request: Request, limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "review", limit=limit)))


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/review/{review_id}/confirm")
async def note_review_confirm(request: Request, review_id: str, body: Optional[ConfirmBody] = None) -> Dict[str, Any]:
    def work(nb, svc, p):
        if _call(nb, "review_item", review_id) is None:
            raise HTTPException(status_code=404, detail="That match is not waiting for review.")
        return _call(nb, "confirm_match", review_id, _account(p), client=action_client(request))

    return await _run(request, work)


# --------------------------------------------------------------------------- claims, tasks, cues


@router.get("/claims/{claim_id}")
async def note_claim(request: Request, claim_id: str) -> Dict[str, Any]:
    def work(nb, svc, p):
        _require(nb, "claim", claim_id, "statement")
        return _call(nb, "claim", claim_id)

    return await _run(request, work)


class RetractBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


@router.post("/claims/{claim_id}/retract")
async def note_claim_retract(request: Request, claim_id: str, body: RetractBody) -> Dict[str, Any]:
    def work(nb, svc, p):
        _require(nb, "claim", claim_id, "statement")
        return _call(nb, "retract_claim", claim_id, _account(p), body.reason, client=action_client(request))

    return await _run(request, work)


class CloseTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: Optional[str] = None


@router.post("/tasks/{task_id}/close")
async def note_task_close(request: Request, task_id: str, body: Optional[CloseTaskBody] = None) -> Dict[str, Any]:
    body = body or CloseTaskBody()

    def work(nb, svc, p):
        _require(nb, "task", task_id, "task")
        return _call(nb, "close_task", task_id, _account(p), body.note, client=action_client(request))

    return await _run(request, work)


@router.get("/tasks")
async def note_tasks(request: Request, status: Optional[str] = "open", entity: Optional[str] = None,
                     limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "tasks", status=status, entity=entity, limit=limit)))


@router.get("/cues")
async def note_cues(request: Request, limit: int = Query(3, ge=1, le=50)) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "cues", limit=limit)))


# --------------------------------------------------------------------------- graph and time


@router.get("/graph")
async def note_graph(
    request: Request, focus: Optional[str] = None, at: Optional[str] = None, depth: int = Query(1, ge=1, le=3),
    since: Optional[str] = None, until: Optional[str] = None, kinds: Optional[str] = None,
    min_count: int = Query(1, ge=1), limit: int = Query(200, ge=1, le=2000), as_received: Optional[str] = None,
) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "graph", **_clean(
        focus=_csv(focus), at=at, depth=depth, since=since, until=until, kinds=_csv(kinds), min_count=min_count,
        limit=limit, as_received=as_received or None)))


@router.get("/timeline")
async def note_timeline(request: Request, since: Optional[str] = None, until: Optional[str] = None,
                        granularity: str = Query("day", pattern="^(day|week|month)$")) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "timeline", since=since, until=until, granularity=granularity))


@router.get("/day/{day}")
async def note_day(request: Request, day: str, tz: Optional[str] = None,
                   as_received: Optional[str] = None) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "day", day, tz, as_received or None))


@router.get("/revisions")
async def note_revisions(request: Request, since: Optional[str] = None, until: Optional[str] = None,
                         entity: Optional[str] = None, cursor: Optional[str] = None,
                         limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "revisions", **_clean(
        since=since, until=until, entity=entity, cursor=cursor, limit=limit))))


@router.get("/topics")
async def note_topics(request: Request) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "topics")))


# --------------------------------------------------------------------------- insights and queue


@router.get("/insights")
async def note_insights(request: Request, kind: Optional[str] = None, acked: Optional[bool] = None,
                        limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _items(_call(nb, "insights", kind=kind, acked=acked, limit=limit)))


@router.post("/insights/{note_id}/ack")
async def note_insight_ack(request: Request, note_id: str) -> Dict[str, Any]:
    def work(nb, svc, p):
        _require(nb, "note", note_id, "insight")
        return _call(nb, "ack_insight", note_id)

    return await _run(request, work)


@router.get("/queue")
async def note_queue(request: Request, limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    def work(nb, svc, p):
        out = dict(_call(nb.queue, "summary", limit=limit))
        out["automations"] = _automation_ids(nb)
        out["processor"] = _call(nb, "processor_throughput")
        return out

    return await _run(request, work)


@router.post("/queue/{note_id}/retry")
async def note_queue_retry(request: Request, note_id: str) -> Dict[str, Any]:
    def work(nb, svc, p):
        _require(nb, "note", note_id, "note")
        return _call(nb.queue, "retry", note_id)

    return await _run(request, work)


# --------------------------------------------------------------------------- settings and automations


@router.get("/settings")
async def note_settings(request: Request) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "settings"))


@router.put("/settings")
async def note_settings_put(request: Request, changes: Dict[str, Any]) -> Dict[str, Any]:
    return await _run(request, lambda nb, svc, p: _call(nb, "update_settings", changes))


def _model_inputs(model_setting: Any) -> Dict[str, Any]:
    m = model_setting if isinstance(model_setting, dict) else {}
    return {k: str(m[k]) for k in ("provider", "model") if isinstance(m.get(k), str) and m[k].strip()}


def _existing_automation(svc: Any, automation_id: Optional[str]) -> Optional[str]:
    if not automation_id:
        return None
    from abstractgateway.automation_errors import AutomationError
    from abstractgateway.routes.automations import load_automation_controller

    try:
        load_automation_controller(svc, automation_id)
    except AutomationError as e:
        if e.status_code == 404:
            return None
        raise
    return automation_id


def _after_one_interval(every: str) -> Dict[str, str]:
    """``{start_at}`` one interval from now: the schedule's first occurrence is its next scheduled
    time, not the moment it is created (a fixed-interval schedule anchors its grid on ``start_at``)."""
    from datetime import datetime, timezone

    from abstractruntime.triggers.protocol import format_timestamp, parse_duration

    return {"start_at": format_timestamp(datetime.now(timezone.utc) + parse_duration(every, field="every"))}


def ensure_automations(nb: Any, svc: Any, principal: Any) -> Dict[str, Any]:
    """Create the processor and reflect automations of this notebook when missing (idempotent).

    The processor's first drain runs at once (an empty queue completes without a model call); the
    reflect automation's first occurrence is one interval after creation, so opening an empty
    notebook never starts an answering-model call ("Reflect now" = ``automation.run_now``)."""
    from abstractgateway.routes.automations import CreateAutomationBody, _create

    settings = nb.settings()
    reflect_every = str((settings.get("reflect_schedule") or {}).get("every") or "7d")
    processor = dict(settings["processor_schedule"])
    out: Dict[str, Any] = {}
    created: List[str] = []
    plans = (
        ("processor_automation_id", PROCESSOR_REQUEST_ID, "Process my notes", "note-drain",
         {**DRAIN_DEFAULTS, "max_items": int(processor["max_items"]), **_model_inputs(settings.get("filing_model"))},
         {"kind": "every", "every": str(processor["every"])}),
        ("reflect_automation_id", REFLECT_REQUEST_ID, "Reflect on my notes", "note-reflect",
         {"limit": 10, **_model_inputs(settings.get("answering_model"))},
         {"kind": "every", "every": reflect_every, **_after_one_interval(reflect_every)}),
    )
    for meta_key, request_id, title, flow_id, inputs, schedule in plans:
        automation_id = _existing_automation(svc, nb.meta(meta_key))
        if automation_id is None:
            body = CreateAutomationBody(
                request_id=request_id, title=title,
                target={"bundle_ref": BUNDLE_ID, "flow_id": flow_id,
                        "input_data": {**inputs, "_runtime": {"allowed_tools": list(NOTE_TOOLS)}}},
                trigger={"source_id": "schedule", "source_version": 2, "config": dict(schedule)},
                context={"mode": "independent"},
            )
            automation_id = str(_create(svc, principal, body)["automation_id"])
            nb.set_meta(meta_key, automation_id)
            if flow_id == "note-drain":  # what GET /note/queue reports as the processor's throughput
                nb.set_meta("processor_schedule_applied", processor)
            created.append(flow_id)
        out[meta_key.replace("_automation_id", "_id")] = automation_id
    out["created"] = created
    return out


@router.post("/processor/ensure")
async def note_processor_ensure(request: Request) -> Dict[str, Any]:
    return await _run(request, ensure_automations)
