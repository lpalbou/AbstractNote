"""A small VisualFlow graph builder for the AbstractNote flow generator.

Modelled on AbstractFlow's ``scripts/wf_common.py`` (node/edge shapes, catalog pins,
the anti-blob ``set_vars`` idiom, per-field subflow pins) but self-contained, because
AbstractFlow is not a Python package and ``abstractnote bundle`` must run anywhere the
package is installed. Every node type used here is a catalog type
(abstractflow/docs/workflow-node-catalog.md); the compiler refuses anything else.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Pin = Tuple[str, str]  # (pin id, pin type)
Src = Tuple[str, str]  # (node id, output pin id)

EXEC_IN = {"id": "exec-in", "label": "", "type": "execution"}
EXEC_OUT = {"id": "exec-out", "label": "", "type": "execution"}

# Pins the executor rewrites on an exec-lane code node's output record (see
# wf_common._EXEC_RESERVED_OUTPUT_PINS): a named output pin may never use them.
EXEC_RESERVED_OUTPUT_PINS = frozenset({"success", "output", "result", "error", "execution"})

# Box model shared with AbstractFlow's canvas and audit_flow_graph.py.
NODE_WIDTH = 320.0
NODE_CHROME_H = 90.0
PIN_ROW_H = 26.0
MIN_HEIGHT = 220.0
COLUMN_GAP_X = 160.0
NODE_GAP_Y = 100.0

_PLACEHOLDER = re.compile(r"\{\{\s*(.*?)\s*\}\}")


def pin(pin_id: str, pin_type: str, label: Optional[str] = None) -> Dict[str, Any]:
    return {"id": pin_id, "label": pin_id if label is None else label, "type": pin_type}


def template_variables(template: str) -> set[str]:
    """Root variable names a ``{{var.path | filter}}`` template reads."""
    out: set[str] = set()
    for expr in _PLACEHOLDER.findall(template or ""):
        root = expr.split("|", 1)[0].strip().split(".", 1)[0].strip()
        if root:
            out.add(root)
    return out


class Flow:
    """One VisualFlow document under construction."""

    def __init__(self, flow_id: str, name: str, description: str, *, interfaces: Sequence[str] = ()) -> None:
        self.doc: Dict[str, Any] = {
            "id": flow_id,
            "name": name,
            "description": description,
            "interfaces": list(interfaces),
            "nodes": [],
            "edges": [],
            "entryNode": "start",
        }
        self._ids: set[str] = set()

    # ------------------------------------------------------------------ raw

    def node(
        self,
        node_id: str,
        node_type: str,
        label: str,
        *,
        inputs: Iterable[Dict[str, Any]] = (),
        outputs: Iterable[Dict[str, Any]] = (),
        pin_defaults: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        if node_id in self._ids:
            raise ValueError(f"{self.doc['id']}: duplicate node id {node_id!r}")
        self._ids.add(node_id)
        data: Dict[str, Any] = {
            "nodeType": node_type,
            "label": label,
            "inputs": list(inputs),
            "outputs": list(outputs),
        }
        if pin_defaults:
            data["pinDefaults"] = pin_defaults
        if extra:
            data.update(extra)
        self.doc["nodes"].append({"id": node_id, "type": node_type, "position": {"x": 0, "y": 0}, "data": data})
        return node_id

    def edge(self, source: str, source_handle: str, target: str, target_handle: str) -> None:
        eid = f"e-{source}-{source_handle}-{target}-{target_handle}".replace(":", "-")
        self.doc["edges"].append(
            {"id": eid, "source": source, "sourceHandle": source_handle, "target": target, "targetHandle": target_handle}
        )

    def then(self, source: str, target: str, handle: str = "exec-out") -> None:
        self.edge(source, handle, target, "exec-in")

    def wire(self, src: Src, target: str, target_pin: str) -> None:
        self.edge(src[0], src[1], target, target_pin)

    # ------------------------------------------------------------ catalog

    def start(self, pins: Sequence[Pin], defaults: Dict[str, Any]) -> str:
        return self.node(
            "start", "on_flow_start", "Start",
            outputs=[EXEC_OUT, *(pin(p, t) for p, t in pins)],
            pin_defaults=dict(defaults),
        )

    def end(self, node_id: str, label: str, values: Dict[str, Tuple[str, Any]]) -> str:
        """on_flow_end; `values` = {pin: (type, Src or literal)}. Each pin becomes run.output[pin]."""
        inputs = [EXEC_IN]
        literals: Dict[str, Any] = {}
        wires: List[Tuple[str, Src]] = []
        for pid, (ptype, value) in values.items():
            inputs.append(pin(pid, ptype))
            if isinstance(value, tuple):
                wires.append((pid, value))
            else:
                literals[pid] = value
        nid = self.node(node_id, "on_flow_end", label, inputs=inputs, pin_defaults=literals or None)
        for pid, src in wires:
            self.wire(src, nid, pid)
        return nid

    def code(
        self,
        node_id: str,
        label: str,
        body: str,
        *,
        inputs: Dict[str, Tuple[str, Any]],
        outputs: Sequence[Pin],
        exec_lane: bool = True,
    ) -> str:
        """A sandboxed code node. `inputs` = {pin: (type, Src or literal default)}.

        Named outputs map the returned dict's keys onto pins. On the exec lane the node runs
        once, in order; the reserved record keys are refused as pin names.
        """
        if exec_lane:
            clash = sorted(p for p, _ in outputs if p in EXEC_RESERVED_OUTPUT_PINS)
            if clash:
                raise ValueError(f"{self.doc['id']}.{node_id}: output pins {clash} are reserved on exec code nodes")
        defaults: Dict[str, Any] = {"permissions": "sandbox"}
        wires: List[Tuple[str, Src]] = []
        in_pins = [EXEC_IN] if exec_lane else []
        for pid, (ptype, value) in inputs.items():
            in_pins.append(pin(pid, ptype))
            if isinstance(value, tuple):
                wires.append((pid, value))
            else:
                defaults[pid] = value
        in_pins.append(pin("permissions", "string"))
        out_pins = ([EXEC_OUT] if exec_lane else []) + [pin(p, t) for p, t in outputs]
        nid = self.node(
            node_id, "code", label, inputs=in_pins, outputs=out_pins, pin_defaults=defaults,
            extra={"functionName": "transform", "codeBody": body.strip() + "\n"},
        )
        for pid, src in wires:
            self.wire(src, nid, pid)
        return nid

    def tool_calls(self, node_id: str, label: str, tool_name: str, calls: Src) -> str:
        nid = self.node(
            node_id, "tool_calls", label,
            inputs=[EXEC_IN, pin("tool_calls", "array"), pin("allowed_tools", "array")],
            outputs=[EXEC_OUT, pin("results", "array"), pin("success", "boolean")],
            pin_defaults={"allowed_tools": [tool_name]},
            extra={"effectConfig": {"allowed_tools": [tool_name]}},
        )
        self.wire(calls, nid, "tool_calls")
        return nid

    def loop_tool_step(
        self,
        step_id: str,
        label: str,
        tool_name: str,
        args: Dict[str, Tuple[str, Any]],
        outputs: Sequence[Pin],
        after: Tuple[str, str],
    ) -> Tuple[str, Tuple[str, str]]:
        """A tool step INSIDE a loop body (while / ForEach): it never raises.

        Inside a control block the runtime turns a node exception into "branch complete" and runs
        the next iteration (compiler ``wrapped``: ``plan.complete_output`` -> return to the active
        control node), so a raising result node would make a drain spin until the while guard. Here
        the result node reports ``failed`` / ``failure`` instead, an ``if`` routes a failure to its
        own ``on_flow_end`` (an end node terminates the run even inside a loop) with
        ``{success: false, error, response}`` = the tool's sentence. Returns ``(out node id, the
        (node, handle) the loop body continues from)``.
        """
        out_id = self._tool_nodes(step_id, label, tool_name, args, outputs, after, raising=False)
        check = self.if_(f"{step_id}_failed", f"{label}: failed?", (out_id, "failed"))
        self.then(out_id, check)
        self.end(f"{step_id}_stopped", f"Stopped: {label.lower()} failed", {
            "response": ("string", (out_id, "failure")), "error": ("string", (out_id, "failure")),
            "success": ("boolean", False),
        })
        self.then(check, f"{step_id}_stopped", "true")
        return out_id, (check, "false")

    def tool_step(
        self,
        step_id: str,
        label: str,
        tool_name: str,
        args: Dict[str, Tuple[str, Any]],
        outputs: Sequence[Pin],
        after: Tuple[str, str],
    ) -> str:
        """One deterministic note tool call, visible as three nodes:

        ``<id>_args`` (code: one input pin per tool argument -> the {name, arguments} call; an
        unset (None) or blank ("") value is left out so the tool's own default applies: the
        runtime type-checks arguments before the call and refuses "" for an integer),
        ``<id>`` (tool_calls, allowlist = this one tool), ``<id>_out`` (code: the tool's
        result object spread onto named pins, plus ``tool_output`` = the whole object; a
        failed call raises its own plain sentence and ends the run with that error).
        Returns the ``_out`` node id; the exec chain continues from its ``exec-out``.
        Never use it inside a loop body: use ``loop_tool_step``.
        """
        return self._tool_nodes(step_id, label, tool_name, args, outputs, after, raising=True)

    def _tool_nodes(self, step_id: str, label: str, tool_name: str, args: Dict[str, Tuple[str, Any]],
                    outputs: Sequence[Pin], after: Tuple[str, str], *, raising: bool) -> str:
        arg_names = list(args)
        lines = ["arguments = {}"]
        for a in arg_names:
            ident = a
            lines.append(f"if {ident} is not None and {ident} != \"\":")
            lines.append(f"    arguments[{json.dumps(a)}] = {ident}")
        lines.append(
            "return {\"tool_calls\": [{\"name\": " + json.dumps(tool_name)
            + ", \"call_id\": " + json.dumps(step_id) + ", \"arguments\": arguments}]}"
        )
        args_id = self.code(
            f"{step_id}_args", f"{label}: arguments", "\n".join(lines),
            inputs=args, outputs=[("tool_calls", "array")],
        )
        self.then(after[0], args_id, after[1])
        call_id = self.tool_calls(step_id, label, tool_name, (args_id, "tool_calls"))
        self.then(args_id, call_id)
        out_body = (
            "r = results[0] if isinstance(results, list) and results else None\n"
            "failure = None\n"
            "out = None\n"
            "if not isinstance(r, dict):\n"
            f"    failure = {json.dumps(tool_name + ' returned no result.')}\n"
            "elif not r.get(\"success\"):\n"
            "    failure = str(r.get(\"error\") or " + json.dumps(tool_name + " failed.") + ")\n"
            "else:\n"
            "    out = r.get(\"output\")\n"
            "    if not isinstance(out, dict):\n"
            f"        failure = {json.dumps(tool_name + ' returned something other than an object.')}\n"
        )
        if raising:
            out_body += (
                "if failure is not None:\n"
                "    raise ValueError(failure)\n"
                "return dict(out, tool_output=out)\n"
            )
            extra_out: List[Pin] = [("tool_output", "object")]
        else:
            out_body += (
                "if failure is not None:\n"
                "    return {\"failed\": True, \"failure\": failure}\n"
                "return dict(out, tool_output=out, failed=False, failure=None)\n"
            )
            extra_out = [("tool_output", "object"), ("failed", "boolean"), ("failure", "string")]
        out_id = self.code(
            f"{step_id}_out", f"{label}: result", out_body,
            inputs={"results": ("array", (call_id, "results"))},
            outputs=[*outputs, *extra_out],
        )
        self.then(call_id, out_id)
        return out_id

    def template(self, node_id: str, label: str, template: str, variables: Dict[str, Src]) -> str:
        used = template_variables(template)
        unknown = sorted(used - set(variables))
        if unknown:
            raise ValueError(
                f"{self.doc['id']}.{node_id}: the prompt uses {unknown}, which this flow does not provide "
                f"(it provides {sorted(variables)})"
            )
        nid = self.node(
            node_id, "string_template", label,
            inputs=[pin("template", "string"), *(pin(v, "any") for v in variables)],
            outputs=[pin("result", "string")],
            pin_defaults={"template": template},
        )
        for v, src in variables.items():
            self.wire(src, nid, v)
        return nid

    def llm(self, node_id: str, label: str, *, schema: Dict[str, Any], prompt: Src, provider: Src, model: Src,
            system: str, temperature: float = 0.2, thinking: Optional[str] = None,
            max_output_tokens: Optional[int] = None, thinking_from: Optional[Src] = None) -> str:
        """One structured model call. ``system`` is the step's STATIC system prompt (same bytes for
        every note, so a local model's prompt cache reuses it); ``thinking`` ("on"/"off") and
        ``max_output_tokens`` are pinned per step (backlog 0024)."""
        defaults: Dict[str, Any] = {"tools": [], "system": system, "temperature": temperature, "resp_schema": schema}
        if thinking is not None:
            defaults["thinking"] = thinking
        if max_output_tokens is not None:
            defaults["max_output_tokens"] = int(max_output_tokens)
        nid = self.node(
            node_id, "llm_call", label,
            inputs=[
                EXEC_IN, pin("provider", "provider_text"), pin("model", "model"), pin("system", "string"),
                pin("prompt", "string"), pin("tools", "tools"), pin("temperature", "number"),
                pin("thinking", "string"), pin("max_output_tokens", "number"),
                pin("resp_schema", "json_schema"),
            ],
            outputs=[
                EXEC_OUT, pin("response", "string"), pin("data", "object"), pin("success", "boolean"),
                pin("meta", "object"), pin("tool_calls", "array"),
            ],
            # tools = [] as a DECLARED default so no host-injected tool list reaches a judgement call.
            pin_defaults=defaults,
        )
        self.wire(prompt, nid, "prompt")
        self.wire(provider, nid, "provider")
        self.wire(model, nid, "model")
        if thinking_from is not None:  # a per-run choice wins over the pinned default
            self.wire(thinking_from, nid, "thinking")
        return nid

    def break_object(self, node_id: str, label: str, obj: Src, fields: Sequence[Pin]) -> str:
        nid = self.node(
            node_id, "break_object", label,
            inputs=[pin("object", "object")],
            outputs=[pin(f, t) for f, t in fields],
            extra={"breakConfig": {"selectedPaths": [f for f, _ in fields]}},
        )
        self.wire(obj, nid, "object")
        return nid

    def answer_user(self, node_id: str, label: str, message: Src) -> str:
        nid = self.node(
            node_id, "answer_user", label,
            inputs=[EXEC_IN, pin("message", "string"), pin("level", "string")],
            outputs=[EXEC_OUT, pin("message", "string")],
            pin_defaults={"level": "message"},
        )
        self.wire(message, nid, "message")
        return nid

    def if_(self, node_id: str, label: str, condition: Src) -> str:
        nid = self.node(
            node_id, "if", label,
            inputs=[EXEC_IN, pin("condition", "boolean")],
            outputs=[pin("true", "execution"), pin("false", "execution")],
        )
        self.wire(condition, nid, "condition")
        return nid

    def switch(self, node_id: str, label: str, value: Src, cases: Sequence[str]) -> str:
        nid = self.node(
            node_id, "switch", label,
            inputs=[EXEC_IN, pin("value", "string")],
            outputs=[*(pin(f"case:{c}", "execution", c) for c in cases), pin("default", "execution")],
            extra={"switchConfig": {"cases": [{"id": c, "value": c} for c in cases]}},
        )
        self.wire(value, nid, "value")
        return nid

    def subflow(self, node_id: str, label: str, flow_id: str, inputs: Dict[str, Tuple[str, Src]],
                outputs: Sequence[Pin]) -> str:
        nid = self.node(
            node_id, "subflow", label,
            inputs=[EXEC_IN, pin("inherit_context", "boolean"), *(pin(p, t) for p, (t, _) in inputs.items())],
            outputs=[EXEC_OUT, pin("output", "object"), pin("child_output", "object"),
                     *(pin(p, t) for p, t in outputs)],
            pin_defaults={"inherit_context": False},
            extra={"subflowId": flow_id},
        )
        for p, (_, src) in inputs.items():
            self.wire(src, nid, p)
        return nid

    def child_check(self, node_id: str, label: str, subflow_id: str) -> str:
        """After a subflow (NOT inside a loop body): end the run with the child's own sentence when
        the child failed (a child that dies or whose node raised returns ``success: false``)."""
        nid = self.code(
            node_id, label,
            "o = output if isinstance(output, dict) else {}\n"
            "c = child_output if isinstance(child_output, dict) else {}\n"
            "if c.get(\"success\") is False or o.get(\"success\") is False:\n"
            "    err = str(c.get(\"error\") or o.get(\"error\") or \"The step could not finish.\")\n"
            "    for prefix in (\"Runtime error: \", \"Execution error: \"):\n"
            "        if err.startswith(prefix):\n"
            "            err = err[len(prefix):]\n"
            "    raise ValueError(err)\n"
            "return {\"checked\": True}",
            inputs={"output": ("object", (subflow_id, "output")), "child_output": ("object", (subflow_id, "child_output"))},
            outputs=[("checked", "boolean")],
        )
        self.then(subflow_id, nid)
        return nid

    def get_var(self, node_id: str, name: str, default: Any) -> str:
        return self.node(
            node_id, "get_var", f"Get {name}",
            inputs=[pin("name", "string"), pin("default", "any")],
            outputs=[pin("value", "any")],
            pin_defaults={"name": name, "default": default},
        )

    def set_var(self, node_id: str, label: str, name: str, value: Any) -> str:
        return self.node(
            node_id, "set_var", label,
            inputs=[EXEC_IN, pin("name", "string"), pin("value", "any")],
            outputs=[EXEC_OUT, pin("value", "any")],
            pin_defaults={"name": name, "value": value},
        )

    def set_vars(self, node_id: str, label: str, updates: Src) -> str:
        nid = self.node(
            node_id, "set_vars", label,
            inputs=[EXEC_IN, pin("updates", "object")],
            outputs=[EXEC_OUT, pin("updates", "object")],
        )
        self.wire(updates, nid, "updates")
        return nid

    def add_to_list(self, node_id: str, label: str, name: str, item: Src) -> str:
        nid = self.node(
            node_id, "add_to_list", label,
            inputs=[EXEC_IN, pin("name", "string"), pin("item", "any"), pin("items", "array")],
            outputs=[EXEC_OUT, pin("list", "array")],
            pin_defaults={"name": name},
        )
        self.wire(item, nid, "item")
        return nid

    def while_(self, node_id: str, label: str, condition: Src) -> str:
        nid = self.node(
            node_id, "while", label,
            inputs=[EXEC_IN, pin("condition", "boolean")],
            outputs=[pin("loop", "execution"), pin("done", "execution"), pin("index", "number"), pin("item", "any")],
        )
        self.wire(condition, nid, "condition")
        return nid

    def for_each(self, node_id: str, label: str, items: Src) -> str:
        nid = self.node(
            node_id, "loop", label,
            inputs=[EXEC_IN, pin("items", "array")],
            outputs=[pin("loop", "execution"), pin("done", "execution"), pin("item", "any"), pin("index", "number")],
        )
        self.wire(items, nid, "items")
        return nid

    # ------------------------------------------------------------- finish

    def finish(self, *, automation_defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if automation_defaults is not None:
            self.doc["automation_defaults"] = automation_defaults
        self._check_edges()
        layout(self.doc)
        return self.doc

    def _check_edges(self) -> None:
        pins: Dict[str, Tuple[set, set, str]] = {}
        for n in self.doc["nodes"]:
            d = n["data"]
            pins[n["id"]] = ({p["id"] for p in d["inputs"]}, {p["id"] for p in d["outputs"]}, d["nodeType"])
        problems = []
        seen_data_targets: set = set()
        for e in self.doc["edges"]:
            s, t = e["source"], e["target"]
            if s not in pins or t not in pins:
                problems.append(f"edge {e['id']}: unknown node")
                continue
            if e["sourceHandle"] not in pins[s][1] and not (pins[s][2] == "subflow" and e["sourceHandle"] == "child_output"):
                problems.append(f"edge {e['id']}: {s} has no output pin {e['sourceHandle']}")
            if e["targetHandle"] not in pins[t][0]:
                problems.append(f"edge {e['id']}: {t} has no input pin {e['targetHandle']}")
            if e["targetHandle"] != "exec-in":
                key = (t, e["targetHandle"])
                if key in seen_data_targets:
                    problems.append(f"edge {e['id']}: {t}.{e['targetHandle']} has two data wires")
                seen_data_targets.add(key)
        if problems:
            raise ValueError(f"{self.doc['id']}: " + "; ".join(problems))


def _node_height(node: Dict[str, Any]) -> float:
    d = node["data"]
    rows = max(
        len([p for p in d.get("inputs") or [] if p.get("type") != "execution"]),
        len([p for p in d.get("outputs") or [] if p.get("type") != "execution"]),
    )
    return max(NODE_CHROME_H + PIN_ROW_H * rows, MIN_HEIGHT)


def _is_exec_edge(edge: Dict[str, Any], exec_targets: Dict[str, set]) -> bool:
    return edge["targetHandle"] == "exec-in"


def layout(doc: Dict[str, Any]) -> None:
    """Left-to-right columns by exec depth; pure nodes one column left of their first consumer.

    Each column is stacked top-down with the canvas box model, so no two boxes overlap.
    """
    nodes = {n["id"]: n for n in doc["nodes"]}
    exec_children: Dict[str, List[str]] = {}
    data_consumers: Dict[str, List[str]] = {}
    for e in doc["edges"]:
        if e["targetHandle"] == "exec-in":
            exec_children.setdefault(e["source"], []).append(e["target"])
        else:
            data_consumers.setdefault(e["source"], []).append(e["target"])
    depth: Dict[str, int] = {"start": 0} if "start" in nodes else {}
    queue = list(depth)
    while queue:
        cur = queue.pop(0)
        for child in exec_children.get(cur, []):
            if child not in depth:
                depth[child] = depth[cur] + 1
                queue.append(child)
    # Pure nodes: left of their earliest-placed consumer (iterate until stable).
    for _ in range(len(nodes)):
        changed = False
        for nid in nodes:
            if nid in depth:
                continue
            placed = [depth[c] for c in data_consumers.get(nid, []) if c in depth]
            if placed:
                depth[nid] = max(min(placed) - 1, 0)
                changed = True
        if not changed:
            break
    for nid in nodes:
        depth.setdefault(nid, 0)
    columns: Dict[int, List[str]] = {}
    for nid in [n["id"] for n in doc["nodes"]]:
        columns.setdefault(depth[nid], []).append(nid)
    for col, ids in columns.items():
        y = 0.0
        for nid in ids:
            nodes[nid]["position"] = {"x": col * (NODE_WIDTH + COLUMN_GAP_X), "y": y}
            y += _node_height(nodes[nid]) + NODE_GAP_Y
