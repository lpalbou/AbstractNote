"""S1 (review 01): every tool call in the generated flows matches the tool's own definition."""

from __future__ import annotations

import copy

import pytest

from abstractnote.flows.build import NOTE_TOOLS
from abstractnote.flows.contract import check_tool_wiring, load_flows, tool_call_sites, tool_parameters


def _params_from_sites(flows):
    """Parameters that exactly match the flows (every wired argument required)."""
    seen, everywhere = {}, {}
    for flow in flows.values():
        for site in tool_call_sites(flow):
            name = site["tools"][0]
            seen.setdefault(name, set()).update(site["arguments"])
            everywhere[name] = everywhere.get(name, set(site["wired"])) & set(site["wired"])
    return {
        name: {a: ({"type": "string"} if a in everywhere[name] else {"type": "string", "default": None}) for a in args}
        for name, args in seen.items()
    }


def test_every_tool_call_is_fed_by_an_arguments_node():
    flows = load_flows()
    sites = [s for f in flows.values() for s in tool_call_sites(f)]
    assert len(sites) == 45  # 21 + note-consolidate (23, backlog 0032) + the drain's themes check (ADR 0008 §5)
    for s in sites:
        assert s["arguments"] is not None and len(s["tools"]) == 1, s
        assert s["tools"][0] in NOTE_TOOLS


def test_checker_goes_red_when_a_required_wire_is_removed():
    flows = load_flows()
    params = _params_from_sites(flows)
    assert check_tool_wiring(flows, params) == []
    broken = copy.deepcopy(flows)
    cap = broken["note-capture"]
    cap["edges"] = [e for e in cap["edges"] if not (e["target"] == "capture_args" and e["targetHandle"] == "text")]
    problems = check_tool_wiring(broken, params)
    assert problems == ["note-capture.capture: required parameter note_capture.text is not wired"]


def test_checker_goes_red_on_an_argument_the_tool_does_not_declare():
    flows = load_flows()
    params = _params_from_sites(flows)
    del params["note_file"]["one_line"]
    assert check_tool_wiring(flows, params) == ["note-capture.file: note_file has no parameter 'one_line'"]


def test_flows_match_the_core_lane_tool_definitions(tmp_path):
    try:
        from abstractnote.tools import bind
    except ImportError as e:
        pytest.skip(f"abstractnote.tools is not importable yet ({e}); S1 runs once the core lane lands")
    tools = bind(tmp_path / "notebook")
    params = tool_parameters(tools)
    missing = sorted(set(NOTE_TOOLS) - set(params))
    assert missing == [], f"tools the flows call but the core lane does not bind: {missing}"
    assert check_tool_wiring(load_flows(), params) == []
