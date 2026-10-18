"""The bound note tools: AbstractCore @tool definitions, the runtime's executor, specs, and S1."""

from __future__ import annotations

import pytest

from abstractnote.tools import TOOL_NAMES, bind, bound_notebook, tool_specs


def test_bind_returns_every_tool_decorated_and_opens_lazily(tmp_path):
    root = tmp_path / "nb"
    tools = bind(root)
    assert list(tools) == TOOL_NAMES and len(TOOL_NAMES) == 18
    assert not root.exists()  # nothing opened yet
    for name, fn in tools.items():
        td = fn._tool_definition
        assert td.name == name and td.description and td.function is fn
    params = tools["note_capture"]._tool_definition.parameters
    assert "default" not in params.get("text", {"default": ""}) or params["text"]["default"] == ""
    assert params["attachments"]["type"] == "array" and params["priority"]["type"] == "integer"
    assert "default" not in tools["note_file"]._tool_definition.parameters["note_id"]  # required
    out = tools["note_capture"](text="hello from a tool", source={"client": "agent", "author": "tester"})
    assert out["status"] == "new" and root.is_dir()
    assert bound_notebook(tools).note(out["note_id"])["source"]["author"] == "tester"


def test_the_runtime_executor_runs_bound_tools(tmp_path):
    from abstractruntime.integrations.abstractcore.tool_executor import MappingToolExecutor

    tools = bind(tmp_path / "nb", account="user:9")
    ex = MappingToolExecutor.from_tools(list(tools.values()))
    res = ex.execute(tool_calls=[
        {"name": "note_capture", "arguments": {"text": "Dana wants the Pareto", "idempotency_key": "k1",
                                               "priority": None, "attachments": [], "about": {}}, "call_id": "c1"},
        {"name": "note_queue_stats", "arguments": {}, "call_id": "c2"},
        {"name": "note_search", "arguments": {"query": "Pareto", "since": "", "entities": []}, "call_id": "c3"},
        {"name": "note_capture", "arguments": {"text": "x", "attachments": [{"path": "/etc/hosts"}]}, "call_id": "c4"},
    ])
    by_id = {r["call_id"]: r for r in res["results"]}
    assert by_id["c1"]["success"] and by_id["c1"]["output"]["status"] == "new"
    assert by_id["c2"]["output"]["queued"] == 1
    assert by_id["c3"]["output"]["notes"][0]["note_id"] == by_id["c1"]["output"]["note_id"]
    assert by_id["c4"]["success"] is False and "not accepted" in str(by_id["c4"].get("error") or by_id["c4"])
    nb = bound_notebook(tools)
    assert nb.note(by_id["c1"]["output"]["note_id"])["source"]["account"] == "user:9"


def test_tool_specs_are_llm_facing_definitions():
    specs = tool_specs()
    assert [s["name"] for s in specs] == TOOL_NAMES
    cap = specs[0]
    assert cap["parameters"]["text"]["type"] == "string" and "when_to_use" in cap
    assert "note_id" in specs[1]["required_args"]


def test_flows_wire_only_parameters_the_tools_declare(tmp_path):
    """S1 with the flows lane: every tool call argument exists in the ToolDefinition; required ones are wired."""
    contract = pytest.importorskip("abstractnote.flows.contract", reason="the flows lane provides the checker")
    tools = bind(tmp_path / "nb")
    params = contract.tool_parameters(tools)
    assert contract.check_tool_wiring(contract.load_flows(), params) == []
    broken = {k: dict(v) for k, v in params.items()}
    broken["note_file"].pop("one_line")
    assert contract.check_tool_wiring(contract.load_flows(), broken)  # red when a parameter disappears
