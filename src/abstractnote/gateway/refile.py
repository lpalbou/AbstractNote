"""``POST /note/refile`` and ``GET /note/refile``: the ``refile --missing`` repair through the gateway (docs/api.md
"Filing the notes without a kind").

The CLI files with a local runner; here each note is one ``note-file`` run on the caller's own runtime (the same
bundle version, model setting and tool list as the processor automation), started by the gateway and followed until
it ends. ``POST`` opens the repair's ``refile_runs`` row and answers it at once (202, ``status: running``); the
filing goes on in the background, one note after the other, within ``max_items`` and ``budget_s``, and the row is
updated after every note (``GET`` reads it). One repair at a time per notebook (409); a row left ``running`` by a
gateway that stopped is closed ``interrupted`` by the next ``POST``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..flows.build import BUNDLE_ID
from ..ingest import refile as rf

#: Included by ``router.router`` (prefix ``/note``), so its paths are relative to ``/note``.
router = APIRouter(tags=["note"])

FLOW_ID = "note-file"
POLL_S = 0.5
TERMINAL = ("completed", "failed", "cancelled")

#: The repairs this process is filing: notebook path -> refile_id (one at a time per notebook).
_active: Dict[str, str] = {}
_active_lock = threading.Lock()


class RefileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    missing: StrictBool
    max_items: int = Field(default=rf.DEFAULT_MAX_ITEMS, ge=1, le=500)
    budget_s: float = Field(default=rf.DEFAULT_BUDGET_S, gt=0, le=24 * 3600)


def _r() -> Any:
    from . import router as r

    return r


def _status(state: Any) -> str:
    value = getattr(getattr(state, "status", None), "value", getattr(state, "status", None))
    return str(value or "").lower()


def runtime_filer(svc: Any, served: str, settings: Dict[str, Any], *, deadline: float,
                  clock: Callable[[], float] = time.monotonic, poll_s: Optional[float] = None) -> Callable[[str], Dict[str, Any]]:
    """``file_one(note_id)`` for :func:`abstractnote.ingest.refile.refile_missing`: start ``note-file`` for the note
    on the plane's runtime and wait for it to end; its output, or an error with the flow's own sentence."""
    from abstractnote.flows.runner import flow_output
    from abstractnote.tools import TOOL_NAMES

    inputs = {**_r()._model_inputs(settings.get("filing_model")), "_runtime": {"allowed_tools": list(TOOL_NAMES)}}

    def file_one(note_id: str) -> Dict[str, Any]:
        run_id = svc.host.start_run(flow_id=FLOW_ID, bundle_id=BUNDLE_ID, bundle_version=served,
                                    input_data={**inputs, "note_id": note_id}, actor_id="gateway")
        try:
            svc.runner.nudge(str(run_id))
        except Exception:  # noqa: BLE001 - the runner's poll picks it up anyway
            pass
        while True:
            state = svc.host.run_store.load(str(run_id))
            if state is not None and _status(state) in TERMINAL:
                return flow_output(state)
            if clock() >= deadline:
                raise TimeoutError("Its filing did not end within the time given; it goes on, and the note is filed "
                                   "when it ends.")
            time.sleep(POLL_S if poll_s is None else poll_s)

    return file_one


def _work(nb: Any, svc: Any, refile_id: str, file_one: Callable[[str], Dict[str, Any]], key: str) -> None:
    import logging

    try:
        rf.refile_missing(nb, file_one, refile_id=refile_id)
    except Exception:  # noqa: BLE001 - the row is closed (interrupted) by refile_missing itself; logged here
        logging.getLogger("abstractnote.refile").exception("refile %s stopped", refile_id)
    finally:
        with _active_lock:
            if _active.get(key) == refile_id:
                _active.pop(key, None)


def start_refile(nb: Any, svc: Any, body: RefileBody, *, filer: Optional[Callable[..., Any]] = None,
                 background: bool = True) -> Dict[str, Any]:
    """Open the repair and start filing (``background``: in a thread). ``filer`` builds ``file_one`` (tests)."""
    if not body.missing:
        raise HTTPException(status_code=400, detail="Say which notes to file: missing (the understood notes "
                                                    "without a kind).")
    r = _r()
    served = r._served_version(svc)
    if served is None:
        raise HTTPException(status_code=503, detail="The note workflows are not loaded on this server; restart it "
                                                    "or reinstall AbstractNote, then try again.")
    key = str(nb.store.path)
    with _active_lock:
        if key in _active:
            raise HTTPException(status_code=409, detail="Notes are being filed already; wait for that to end.")
        rf.interrupt_running(nb)
        settings = nb.settings()
        model = r._model_inputs(settings.get("filing_model"))
        row = rf.open_refile(nb, max_items=body.max_items, budget_s=body.budget_s,
                             model="/".join(model[k] for k in ("provider", "model") if k in model) or None)
        _active[key] = row["refile_id"]
    deadline = time.monotonic() + float(body.budget_s)
    file_one = (filer or runtime_filer)(svc, served, settings, deadline=deadline)
    if background:
        threading.Thread(target=_work, args=(nb, svc, row["refile_id"], file_one, key),
                         name=f"abstractnote-refile-{row['refile_id']}", daemon=True).start()
    else:
        _work(nb, svc, row["refile_id"], file_one, key)
    return row


@router.post("/refile", status_code=202)
async def note_refile(request: Request, body: RefileBody) -> JSONResponse:
    """File the understood notes without a kind (``{missing: true}``): 202 with the open repair."""
    row = await _r()._run(request, lambda nb, svc, p: start_refile(nb, svc, body))
    return JSONResponse(status_code=202, content={"refile": row})


@router.get("/refile")
async def note_refile_state(request: Request) -> Dict[str, Any]:
    """The newest repair (``refile: null`` when none ran) and how many understood notes still have no kind."""
    def work(nb: Any, svc: Any, p: Any) -> Dict[str, Any]:
        return {"refile": rf.latest_refile(nb), "missing": int(nb.store.q1(f"SELECT COUNT(*) {rf.MISSING_SQL}")[0])}

    return await _r()._run(request, work)
