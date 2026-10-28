"""Run the AbstractNote flows headlessly (docs/cognition.md "Headless execution").

The same compiled flows the gateway runs, on a local AbstractRuntime ``Runtime`` with real effect
handlers: model calls through AbstractCore (or a ``ScriptedLLM`` in tests) and the note tools bound
to a local notebook directory. The recipe follows AbstractRuntime's
``tests/test_kg_learn_and_recall_contract.py``: compile the flow tree, register every flow in a
``WorkflowRegistry``, ``build_effect_handlers(llm=..., tools=MappingToolExecutor(...))``, then drive
the run, resuming parents of SUBWORKFLOW waits the way the gateway runner does.

    runner = NoteRunner.local("/path/to/notebook", provider="mlx", model="mlx-community/...")
    runner.run_capture("Met Dana about the LUT budget")
    runner.run_drain(max_items=20, budget_s=90)
    runner.run_recall("What did I decide about the LUT budget?", effort="standard")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .build import ALL_FLOWS, JSON_DIR, load_schemas


class FlowRunError(RuntimeError):
    """A flow ended in a way a caller must not mistake for success."""


class ToolApprovalWaitError(FlowRunError):
    """A note-tool call parked on a tool-approval wait (never expected: the note tools are auto-approved)."""


# --------------------------------------------------------------------------- models


class ScriptedLLM:
    """A model stand-in that answers each structured call from a script, by schema name.

    ``script`` maps a schema name ("QuickFiling", "Extraction", "Linking", "Answer", "MiningPlan",
    "SliceFindings", "Dossier", "Reflections") to a dict, a list of dicts (used in order), or a
    callable ``(prompt: str) -> dict``. Each answer is validated against the call's response model
    so a test can never feed the flow something a real model's repaired output could not be. An
    unscripted schema or an unstructured call raises.

    The schemas the flows send are strict (every property required, FIX1): a property the script leaves out
    whose schema accepts null is answered null, as a model constrained by that schema writes it
    (:func:`null_filled`); a left-out property that does not accept null still fails the validation.
    """

    def __init__(self, script: Mapping[str, Any]) -> None:
        self.script = dict(script)
        self.calls: List[Dict[str, Any]] = []
        self._cursor: Dict[str, int] = {}
        self._schemas = load_schemas()
        self._fields = {name: frozenset(schema["properties"]) for name, schema in self._schemas.items()}

    def schema_name(self, response_model: Any) -> str:
        fields = frozenset(getattr(response_model, "model_fields", {}) or {})
        for name, props in self._fields.items():
            if props == fields:
                return name
        raise AssertionError(f"ScriptedLLM: no AbstractNote schema has the fields {sorted(fields)}")

    def generate(self, *, prompt: str = "", params: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        model = (params or {}).get("response_model")
        if model is None:
            raise AssertionError("ScriptedLLM answers structured calls only (the AbstractNote flows make no other)")
        name = self.schema_name(model)
        if name not in self.script:
            raise AssertionError(f"ScriptedLLM: no scripted answer for {name}")
        entry = self.script[name]
        if callable(entry):
            value = entry(prompt)
        elif isinstance(entry, list):
            i = self._cursor.get(name, 0)
            if i >= len(entry):
                raise AssertionError(f"ScriptedLLM: the script for {name} ran out after {len(entry)} answers")
            self._cursor[name] = i + 1
            value = entry[i]
        else:
            value = entry
        validated = model.model_validate(null_filled(value, self._schemas[name])).model_dump(mode="json")
        self.calls.append({"schema": name, "prompt": prompt, "system": kwargs.get("system_prompt"), "data": validated})
        return {"data": validated, "content": json.dumps(validated), "metadata": {}, "finish_reason": "stop"}


def _accepts_null(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    t = schema.get("type")
    return t == "null" or (isinstance(t, list) and "null" in t) or None in (schema.get("enum") or [1]) or any(
        _accepts_null(x) for x in schema.get("anyOf") or [])


def null_filled(value: Any, schema: Any) -> Any:
    """``value`` with every property its (strict) ``schema`` lists, accepts null for and ``value`` leaves out set
    to null, in nested objects and array items too: what a model constrained by the strict schema writes."""
    if not isinstance(schema, dict):
        return value
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [null_filled(x, schema["items"]) for x in value]
    props = schema.get("properties")
    if not (isinstance(value, dict) and isinstance(props, dict)):
        return value
    out = {k: (null_filled(v, props[k]) if k in props else v) for k, v in value.items()}
    for k, sub in props.items():
        if k not in out and _accepts_null(sub):
            out[k] = None
    return out


# --------------------------------------------------------------------------- registry


def load_flow_documents(flows_dir: Path = JSON_DIR) -> Dict[str, Dict[str, Any]]:
    return {fid: json.loads((flows_dir / f"{fid}.json").read_text(encoding="utf-8")) for fid in ALL_FLOWS}


def build_registry(flows: Optional[Mapping[str, Mapping[str, Any]]] = None) -> Any:
    """A WorkflowRegistry holding every compiled AbstractNote flow (subflows resolve by flow id)."""
    from abstractruntime.scheduler.registry import WorkflowRegistry
    from abstractruntime.visualflow_compiler import compile_visualflow

    docs = dict(flows or load_flow_documents())
    reg = WorkflowRegistry()
    for fid in ALL_FLOWS:
        reg.register(compile_visualflow(docs[fid]))
    return reg


# --------------------------------------------------------------------------- driving


def _is_tool_approval_wait(waiting: Any) -> bool:
    details = getattr(waiting, "details", None)
    if not isinstance(details, dict):
        return False
    mode = str(details.get("mode") or "").strip().lower()
    return bool(mode) and mode != "executed" and "tool_calls" in details


def drive_until_done(runtime: Any, run_id: str, *, registry: Any = None, max_steps: int = 500, max_rounds: int = 1000) -> Any:
    """Tick a run to a terminal state, driving SUBWORKFLOW waits like the gateway runner.

    A child that ends in any state resumes its parent with ``{sub_run_id, output}`` where a
    non-completed child's output carries ``success: False`` and its ``error`` (the gateway runner's
    shape, runner.py ``_resume_subworkflow_parents``). A tool-approval wait raises
    ``ToolApprovalWaitError``; any other wait raises ``FlowRunError`` (headless runs have nobody to
    answer it).
    """
    from abstractruntime.core.models import RunStatus, WaitReason

    reg = registry or runtime.workflow_registry
    state = runtime.get_state(run_id)
    workflow = reg.get_or_raise(state.workflow_id)
    for _ in range(max_rounds):
        state = runtime.tick(workflow=workflow, run_id=run_id, max_steps=max_steps)
        if state.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
            return state
        if state.status != RunStatus.WAITING:
            continue  # RUNNING after max_steps: keep ticking
        waiting = state.waiting
        if waiting is None:
            raise FlowRunError(f"run {run_id} is waiting without a wait record")
        if _is_tool_approval_wait(waiting):
            names = [str((c or {}).get("name")) for c in (waiting.details or {}).get("tool_calls") or []]
            raise ToolApprovalWaitError(f"run {run_id} waits for approval of {names}; note tools must be auto-approved")
        if waiting.reason != WaitReason.SUBWORKFLOW:
            raise FlowRunError(f"run {run_id} waits for {waiting.reason.value} ({waiting.wait_key}); a headless run cannot answer it")
        details = waiting.details if isinstance(waiting.details, dict) else {}
        child_id = str(details.get("sub_run_id") or "").strip()
        if not child_id:
            raise FlowRunError(f"run {run_id} waits for a subflow without a child run id")
        child = drive_until_done(runtime, child_id, registry=reg, max_steps=max_steps, max_rounds=max_rounds)
        out = dict(child.output) if isinstance(child.output, dict) else {"result": child.output}
        if child.status != RunStatus.COMPLETED:
            out.setdefault("success", False)
            if isinstance(child.error, str) and child.error.strip():
                out.setdefault("error", child.error.strip())
        state = runtime.resume(
            workflow=workflow, run_id=run_id, wait_key=waiting.wait_key,
            payload={"sub_run_id": child_id, "output": out}, max_steps=max_steps,
        )
        if state.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
            return state
    raise FlowRunError(f"run {run_id} did not finish within {max_rounds} rounds")


def flow_output(state: Any) -> Dict[str, Any]:
    """The run's output, or FlowRunError with the flow's own error sentence.

    A node that raises ends a VisualFlow run as COMPLETED with ``{success: False, error, node}``
    (compiler: node exceptions become the run's complete_output), so success is checked here.
    """
    from abstractruntime.core.models import RunStatus

    if state.status != RunStatus.COMPLETED:
        raise FlowRunError(str(state.error or f"the run ended {state.status.value}"))
    out = state.output if isinstance(state.output, dict) else {}
    if out.get("success") is False or (out.get("error") and "response" not in out):
        raise FlowRunError(plain_error(out.get("error")) or "The flow failed.")
    return out


def plain_error(message: Any) -> str:
    """The flow's own sentence without the sandbox's "Runtime error: " wrapper."""
    text = str(message or "").strip()
    for prefix in ("Runtime error: ", "Execution error: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


# --------------------------------------------------------------------------- runner


def _bind_note_tools(notebook_dir: Path, **kwargs: Any) -> Dict[str, Callable[..., Any]]:
    """W1's ``abstractnote.tools.bind``; a missing core lane is an ImportError, never a stub."""
    from abstractnote.tools import bind  # core lane (W1): fails loudly until it exists

    return dict(bind(notebook_dir, **kwargs))


class NoteRunner:
    """The AbstractNote flows on a local Runtime, bound to one notebook directory."""

    def __init__(self, runtime: Any, registry: Any, tools: Mapping[str, Callable[..., Any]]) -> None:
        self.runtime = runtime
        self.registry = registry
        self.tools = dict(tools)

    # -- construction

    @staticmethod
    def _stores(run_dir: Optional[Path]):
        if run_dir is None:
            from abstractruntime.storage.in_memory import InMemoryLedgerStore, InMemoryRunStore

            return InMemoryRunStore(), InMemoryLedgerStore()
        from abstractruntime.storage.json_files import JsonFileRunStore, JsonlLedgerStore

        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        return JsonFileRunStore(run_dir), JsonlLedgerStore(run_dir)

    @classmethod
    def with_llm(cls, llm: Any, tools: Mapping[str, Callable[..., Any]], *, run_dir: Optional[Path] = None,
                 flows: Optional[Mapping[str, Mapping[str, Any]]] = None, tool_executor: Any = None) -> "NoteRunner":
        """Any object with AbstractRuntime's ``generate(**kwargs)`` contract (e.g. ``ScriptedLLM``).

        ``tool_executor`` replaces the plain ``MappingToolExecutor`` (tests use an approval-gated one)."""
        from abstractruntime import Runtime
        from abstractruntime.integrations.abstractcore.effect_handlers import build_effect_handlers
        from abstractruntime.integrations.abstractcore.tool_executor import MappingToolExecutor
        from abstractruntime.storage.artifacts import InMemoryArtifactStore

        registry = build_registry(flows)
        run_store, ledger_store = cls._stores(run_dir)
        artifacts = InMemoryArtifactStore()
        handlers = build_effect_handlers(
            llm=llm, tools=tool_executor or MappingToolExecutor(dict(tools)), artifact_store=artifacts,
            run_store=run_store,
        )
        runtime = Runtime(
            run_store=run_store, ledger_store=ledger_store, workflow_registry=registry,
            artifact_store=artifacts, effect_handlers=handlers,
        )
        return cls(runtime, registry, tools)

    @classmethod
    def with_model(cls, provider: str, model: str, tools: Mapping[str, Callable[..., Any]], *,
                   run_dir: Optional[Path] = None, llm_kwargs: Optional[Dict[str, Any]] = None) -> "NoteRunner":
        """A real model through AbstractCore (``create_local_runtime(provider, model)``)."""
        from abstractruntime.integrations.abstractcore.factory import create_local_runtime
        from abstractruntime.integrations.abstractcore.tool_executor import MappingToolExecutor

        if not str(provider or "").strip() or not str(model or "").strip():
            raise ValueError("A local run needs a provider and a model.")
        registry = build_registry()
        run_store, ledger_store = cls._stores(run_dir)
        runtime = create_local_runtime(
            provider=provider, model=model, llm_kwargs=llm_kwargs, run_store=run_store, ledger_store=ledger_store,
            tool_executor=MappingToolExecutor(dict(tools)),
        )
        runtime.set_workflow_registry(registry)
        return cls(runtime, registry, tools)

    @classmethod
    def local(cls, notebook_dir: str | Path, *, provider: Optional[str] = None, model: Optional[str] = None,
              llm: Any = None, run_dir: Optional[Path] = None, llm_kwargs: Optional[Dict[str, Any]] = None,
              **bind_kwargs: Any) -> "NoteRunner":
        """Bind the note tools to ``notebook_dir`` and pick the model path (``llm`` wins).

        ``llm_kwargs`` go to AbstractCore's provider (for example ``base_url``); the rest to
        ``abstractnote.tools.bind`` (``embedder``, ``allow_paths_under``, ``extractors``, ...)."""
        tools = _bind_note_tools(Path(notebook_dir), **bind_kwargs)
        if llm is not None:
            return cls.with_llm(llm, tools, run_dir=run_dir)
        return cls.with_model(str(provider or ""), str(model or ""), tools, run_dir=run_dir, llm_kwargs=llm_kwargs)

    # -- running

    def start(self, flow_id: str, input_data: Mapping[str, Any], *, session_id: Optional[str] = None) -> str:
        spec = self.registry.get_or_raise(flow_id)
        return self.runtime.start(workflow=spec, vars=dict(input_data), session_id=session_id)

    def run(self, flow_id: str, input_data: Mapping[str, Any], *, session_id: Optional[str] = None) -> Dict[str, Any]:
        run_id = self.start(flow_id, input_data, session_id=session_id)
        state = drive_until_done(self.runtime, run_id, registry=self.registry)
        out = dict(flow_output(state))
        out.setdefault("run_id", run_id)
        return out

    def run_capture(self, text: str, **fields: Any) -> Dict[str, Any]:
        return self.run("note-capture", {"text": text, **fields}, session_id=fields.get("session_id"))

    def run_drain(self, max_items: int = 20, budget_s: float = 90, **fields: Any) -> Dict[str, Any]:
        return self.run("note-drain", {"max_items": max_items, "budget_s": budget_s, **fields})

    def run_recall(self, question: str, effort: str = "standard", **fields: Any) -> Dict[str, Any]:
        return self.run("note-recall", {"question": question, "effort": effort, **fields})

    def run_reflect(self, limit: int = 10, **fields: Any) -> Dict[str, Any]:
        return self.run("note-reflect", {"limit": limit, **fields})

    def ledger(self, run_id: str) -> List[Dict[str, Any]]:
        return list(self.runtime.get_ledger(run_id) or [])


def answer_user_messages(runner: NoteRunner, run_id: str) -> List[str]:
    """The ``answer_user`` messages a run (and its children) wrote to the ledger, in order."""
    out: List[str] = []
    for rec in runner.ledger(run_id):
        if not isinstance(rec, dict):
            continue
        status = rec.get("status")
        if str(getattr(status, "value", status)) != "completed":
            continue
        eff = rec.get("effect")
        if isinstance(eff, dict) and eff.get("type") == "answer_user":
            msg = (eff.get("payload") or {}).get("message")
            if isinstance(msg, str):
                out.append(msg)
    return out


__all__: Sequence[str] = [
    "FlowRunError", "NoteRunner", "ScriptedLLM", "ToolApprovalWaitError", "answer_user_messages",
    "build_registry", "drive_until_done", "flow_output", "load_flow_documents", "plain_error",
]
