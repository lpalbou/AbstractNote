"""Is a notebook's processor (or reflect) automation still able to run? (pure; no gateway import)

An automation freezes the concrete workflow it targets at creation (``abstractnote@<version>:<flow>``,
the gateway's ``_resolve_target``), and the gateway serves only the installed version of the
note workflows. After an upgrade and a restart the old target is gone from the registry, the
automation's next occurrence fails ("Workflow ... not found in registry") and the queue stops
draining. ``automation_health`` names that state so ``POST /note/processor/ensure`` can repair it
and ``GET /note/status`` / ``GET /note/queue`` can report it.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

#: Automation states (the runtime's ``automation_status``) in which an automation still runs.
RUNNING_STATES = ("active", "paused")


def expected_workflow_id(bundle_id: str, served_version: str, flow_id: str) -> str:
    return f"{bundle_id}@{served_version}:{flow_id}"


def automation_health(
    *,
    automation_id: Optional[str],
    flow_id: str,
    bundle_id: str,
    served_version: Optional[str],
    state: Optional[str] = None,
    target: Optional[str] = None,
    paused: bool = False,
    allowed_tools: Optional[Iterable[str]] = None,
    expected_tools: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """``{id, state, target, healthy, reason, paused}`` of one automation.

    ``state`` is ``None`` when the automation does not exist (never created, or gone);
    ``served_version`` is ``None`` when the gateway has no note workflows loaded. ``reason`` is
    one sentence when ``healthy`` is false, else ``None``. A paused automation is healthy (the
    user paused it; it resumes on the current workflows). ``allowed_tools`` is the tool list frozen
    in the automation's run input (``_runtime.allowed_tools``); when ``expected_tools`` is given, a
    different set is unhealthy: a tool added since then would be refused at its step ("Tool ... is
    not allowed for this node or run").
    """
    out: Dict[str, Any] = {"id": automation_id or None, "state": state or "missing", "target": target,
                           "healthy": False, "reason": None, "paused": bool(paused)}
    if not automation_id:
        out["reason"] = "It has not been created yet."
    elif state is None:
        out["reason"] = "It no longer exists."
    elif state not in RUNNING_STATES:
        out["reason"] = f"It is {state} and will not run again."
    elif served_version is None:
        out["reason"] = "The note workflows are not loaded on this server."
    elif target != expected_workflow_id(bundle_id, served_version, flow_id):
        out["reason"] = (f"It runs {target or 'an unknown workflow'}, which this server no longer serves "
                         f"(it serves {expected_workflow_id(bundle_id, served_version, flow_id)}).")
    elif expected_tools is not None and set(allowed_tools or ()) != set(expected_tools):
        out["reason"] = "The tool list changed."
    else:
        out["healthy"] = True
    return out


__all__ = ["RUNNING_STATES", "automation_health", "expected_workflow_id"]
