"""The flows <-> note tools seam (review 01, S1).

Every ``tool_calls`` node in the generated flows is fed by an arguments code node whose input
pins are the tool's argument names. ``check_tool_wiring`` compares them with each tool's
``ToolDefinition`` (AbstractCore ``@tool``): an argument the tool does not declare would be
dropped silently by the runtime's kwargs filter, and a required parameter left unwired would
fail only at run time, so both are reported here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .build import JSON_DIR, ALL_FLOWS


def load_flows(flows_dir: Path = JSON_DIR) -> Dict[str, Dict[str, Any]]:
    return {fid: json.loads((flows_dir / f"{fid}.json").read_text(encoding="utf-8")) for fid in ALL_FLOWS}


def tool_call_sites(flow: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One entry per tool_calls node: {flow, node, tools, arguments, wired}."""
    nodes = {n["id"]: n for n in flow.get("nodes") or []}
    incoming: Dict[str, Dict[str, str]] = {}
    for e in flow.get("edges") or []:
        incoming.setdefault(e["target"], {})[e["targetHandle"]] = e["source"]
    sites = []
    for nid, n in nodes.items():
        d = n.get("data") or {}
        if d.get("nodeType") != "tool_calls":
            continue
        tools = list((d.get("pinDefaults") or {}).get("allowed_tools") or [])
        src_id = incoming.get(nid, {}).get("tool_calls")
        src = nodes.get(src_id or "")
        site: Dict[str, Any] = {"flow": flow.get("id"), "node": nid, "tools": tools, "arguments": None, "wired": set()}
        if src is not None and (src.get("data") or {}).get("nodeType") == "code":
            sd = src["data"]
            args = [p["id"] for p in sd.get("inputs") or [] if p.get("type") != "execution" and p["id"] != "permissions"]
            defaults = set((sd.get("pinDefaults") or {}).keys())
            wired = {a for a in args if a in incoming.get(src["id"], {}) or a in defaults}
            site["arguments"] = args
            site["wired"] = wired
        sites.append(site)
    return sites


def tool_parameters(tools: Iterable[Callable[..., Any]] | Mapping[str, Callable[..., Any]]) -> Dict[str, Dict[str, Any]]:
    """{tool name: ToolDefinition.parameters} from @tool-decorated callables."""
    items = tools.values() if isinstance(tools, Mapping) else tools
    out: Dict[str, Dict[str, Any]] = {}
    for t in items:
        td = getattr(t, "_tool_definition", None)
        if td is None:
            raise TypeError(f"{getattr(t, '__name__', t)!r} is not an AbstractCore @tool (no _tool_definition)")
        out[str(td.name)] = dict(td.parameters or {})
    return out


def check_tool_wiring(flows: Mapping[str, Mapping[str, Any]], parameters: Mapping[str, Mapping[str, Any]],
                      *, only: Optional[set] = None) -> List[str]:
    """Problems between the flows' tool calls and the tools' declared parameters (empty = ok)."""
    problems: List[str] = []
    for flow in flows.values():
        for site in tool_call_sites(flow):
            where = f"{site['flow']}.{site['node']}"
            if len(site["tools"]) != 1:
                problems.append(f"{where}: expected exactly one allowed tool, got {site['tools']}")
                continue
            name = site["tools"][0]
            if only is not None and name not in only:
                continue
            if site["arguments"] is None:
                problems.append(f"{where}: tool_calls is not fed by an arguments code node")
                continue
            params = parameters.get(name)
            if params is None:
                problems.append(f"{where}: tool {name} does not exist")
                continue
            for a in site["arguments"]:
                if a not in params:
                    problems.append(f"{where}: {name} has no parameter {a!r}")
            for p, spec in params.items():
                required = not (isinstance(spec, dict) and "default" in spec)
                if required and p not in site["wired"]:
                    problems.append(f"{where}: required parameter {name}.{p} is not wired")
    return problems
