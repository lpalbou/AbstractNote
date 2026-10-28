"""Where a write came from in AbstractRuntime's ledger (backlog 0037; AbstractMemory ADR 0003, decisions 8-11).

A *ledger address* is the run and step of the tool call that wrote something:
``{"run_id": str, "seq"?: int, "step_id"?: str}``. ``seq`` is the ledger row and is kept only when the
runtime gave it (it never invents one). A *source ref* is AbstractMemory's ``SourceRef`` as a plain dict
(``abstractframework/untracked/memory-transition/contract.md`` §1): a ``step`` ref needs ``run_id`` and
``seq``; without ``seq`` the address is a ``run`` ref and its ``step_id`` travels in the provenance's
``extra``. Artifact refs come from the gateway's artifact store (the note body, attachments).

:func:`provenance` builds the provenance of one projected assertion or closure in the contract's shape
(§2): ``{sources, derived_from, actor, reader, reason, extra}``. It is AbstractNote's stamping of its own
writes; when the projection moves to AbstractMemory's ``MemoryGraph.apply_revision`` through the runtime's
memory facade (backlog 0033 phase 2), the facade's ``stamp`` takes it over.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

ACTOR = "abstractnote"


def _blank(value: Any) -> Any:
    """Flows pass "" for an unset pin."""
    return None if value in ("", None) else value


def step_address(run_id: Any = None, seq: Any = None, step_id: Any = None) -> Optional[Dict[str, Any]]:
    """The ledger address ``{run_id, seq?, step_id?}``, or None when no run is named. A ``seq`` or
    ``step_id`` without a run, or a ``seq`` that is not a non-negative integer, is refused (ValueError)."""
    run_id, seq, step_id = _blank(run_id), _blank(seq), _blank(step_id)
    if run_id is None:
        if seq is not None or step_id is not None:
            raise ValueError("A ledger step needs the run it belongs to (run_id).")
        return None
    run_id = str(run_id).strip()
    if not run_id:
        raise ValueError("A ledger run id cannot be blank.")
    out: Dict[str, Any] = {"run_id": run_id}
    if seq is not None:
        if isinstance(seq, bool):
            raise ValueError("The ledger row (seq) must be a whole number.")
        try:
            n = int(seq)
        except (TypeError, ValueError):
            raise ValueError("The ledger row (seq) must be a whole number.") from None
        if n < 0 or str(n) != str(seq).strip():
            raise ValueError("The ledger row (seq) must be a whole number, zero or more.")
        out["seq"] = n
    if step_id is not None:
        out["step_id"] = str(step_id).strip()
    return out


def coerce_address(address: Any) -> Optional[Dict[str, Any]]:
    """A ledger address given as a dict (``{run_id, seq?, step_id?}``) checked by :func:`step_address`; None
    stays None. Anything else is refused (ValueError)."""
    if address is None:
        return None
    if not isinstance(address, Mapping):
        raise ValueError("A ledger address is {run_id, seq?, step_id?}.")
    return step_address(address.get("run_id"), address.get("seq"), address.get("step_id"))


def note_columns(run_id: Any = None, ledger: Any = None) -> Dict[str, Any]:
    """The ledger columns of a note row (``run_id``, ``ledger_seq``, ``ledger_step_id``) from the run id a caller
    passed and/or the address of the step writing it. Both name the same run or the call is refused: a note is
    never filed under a run its step does not belong to (ValueError)."""
    run_id = _blank(run_id)
    address = coerce_address(ledger)
    if address is None:
        return {"run_id": None if run_id is None else str(run_id), "ledger_seq": None, "ledger_step_id": None}
    if run_id is not None and str(run_id).strip() != address["run_id"]:
        raise ValueError(f"The run id {run_id} is not the run this step belongs to ({address['run_id']}).")
    return {"run_id": address["run_id"], "ledger_seq": address.get("seq"), "ledger_step_id": address.get("step_id")}


def runtime_step_reader() -> Callable[[], Optional[Dict[str, Any]]]:
    """A reader of the AbstractRuntime step executing now: ``{run_id, seq?, step_id}`` from
    ``abstractruntime.core.step_context.current_step_context()`` (the runtime installs it around each effect
    handler invocation, its ADR 0009), or None outside a runtime step. ``seq`` is the ledger row of the step's
    STARTED record and is kept only when the runtime reported it. An AbstractRuntime without the step context
    fails here, at bind time (ImportError), never by stamping nothing in silence."""
    from abstractruntime.core.step_context import current_step_context

    def read() -> Optional[Dict[str, Any]]:
        # Reviewer's observation (2026-10-27): ``current_step_context(run_id)`` returns the context only when it
        # belongs to the given run, which shields a writer from an enclosing step of ANOTHER run. A note tool does
        # not know its run (nothing passes it one), so it reads the context unfiltered: the innermost step
        # executing now, which is the tool's own step (a nested runtime shadows, never clobbers, the context).
        ctx = current_step_context()
        if ctx is None:
            return None
        return step_address(ctx.run_id, ctx.seq, ctx.step_id)

    return read


def step_ref(address: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The source ref of a ledger address: ``step`` when the row is known; ``run`` when only the step id is (an
    address read from a runtime step whose ledger did not report the row); None for a bare run id.

    A bare run id is never a source: it may have been typed by a model (``note_insight_write``'s ``run_id``) or
    written by an older capture (``capture(run_id=...)``) with nothing checking it against a ledger. Only an
    address that names its step came from the runtime's step context (or from code that knows the step); a bare
    run id travels in the provenance's ``extra`` as a label (:func:`provenance`)."""
    if not address or not address.get("run_id"):
        return None
    if address.get("seq") is None and not address.get("step_id"):
        return None
    if address.get("seq") is not None:
        ref: Dict[str, Any] = {"kind": "step", "run_id": address["run_id"], "seq": int(address["seq"])}
        if address.get("step_id"):
            ref["step_id"] = address["step_id"]
        return ref
    return {"kind": "run", "run_id": address["run_id"]}


def artifact_ref(ref: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The source ref of a stored artifact reference ``{artifact_id, run_id?}`` (attachments, note body)."""
    if not ref or not str(ref.get("artifact_id") or "").strip():
        return None
    out: Dict[str, Any] = {"kind": "artifact", "artifact_id": str(ref["artifact_id"]).strip()}
    if ref.get("run_id"):
        out["run_id"] = str(ref["run_id"])
    return out


def ref_key(ref: Mapping[str, Any]) -> str:
    """The canonical key of a source ref (contract §1)."""
    kind = ref["kind"]
    if kind == "step":
        return f"step:{ref['run_id']}#{ref['seq']}"
    if kind == "run":
        return f"run:{ref['run_id']}"
    if kind == "artifact":
        return f"artifact:{ref['artifact_id']}"
    raise ValueError(f"AbstractNote writes run, step and artifact refs only, not {kind!r}.")


def unique_refs(refs: Iterable[Optional[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
    """The refs in order, without repeats (by canonical key) or blanks."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for r in refs:
        if not r:
            continue
        key = ref_key(r)
        if key not in seen:
            seen.add(key)
            out.append(dict(r))
    return out


def reader_of(rev: Mapping[str, Any]) -> Optional[str]:
    """The model that exercised judgement for a revision, when its source names one (``source.reader``, or
    ``source.provider`` + ``source.model``); None otherwise."""
    src = rev.get("source") or {}
    if not isinstance(src, Mapping):
        return None
    if src.get("reader"):
        return str(src["reader"])
    if src.get("model"):
        return f"{src['provider']}/{src['model']}" if src.get("provider") else str(src["model"])
    return None


def provenance(rev: Mapping[str, Any], **extra: Any) -> Dict[str, Any]:
    """The provenance of an assertion or closure projected from revision ``rev`` (contract §2).

    ``sources`` = the ledger address of the step that applied the revision (``rev["ledger"]``), then the
    note's own sources recorded on the revision (``rev["sources"]``: the capture step, the body and
    attachment artifacts). ``extra`` always names the revision and its record time, and carries the bare run
    ids that are labels, not sources (``run_id`` of the revision, ``note_run_id`` of its note); the keyword
    arguments add to it (None values are left out)."""
    ledger = rev.get("ledger") or None
    sources = unique_refs([step_ref(ledger), *(rev.get("sources") or [])])
    ext: Dict[str, Any] = {"revision_id": rev["revision_id"], "recorded_at": rev["recorded_at"]}
    if rev.get("note_id"):
        ext["note_id"] = rev["note_id"]
    if ledger and ledger.get("seq") is None and ledger.get("step_id"):
        ext["step_id"] = ledger["step_id"]  # a run ref has no step: the step id waits here for its seq
    if ledger and step_ref(ledger) is None:
        ext["run_id"] = ledger["run_id"]  # a bare run id: a label, not a source (see step_ref)
    if rev.get("note_run_id"):
        ext["note_run_id"] = rev["note_run_id"]  # the note's bare run id (a label: NoteStore.note_run_label)
    ext.update({k: v for k, v in extra.items() if v is not None})
    return {"sources": sources, "derived_from": [], "actor": ACTOR, "reader": reader_of(rev), "reason": None,
            "extra": ext}
