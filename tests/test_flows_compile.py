"""The generated AbstractNote flows compile, pass AbstractFlow's audit, and keep their contracts."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from abstractnote.flows import build
from abstractnote.flows.bundle import pack
from abstractnote.flows.contract import load_flows

REPO = Path(__file__).resolve().parents[1]
ABSTRACTFLOW = REPO.parent / "abstractflow"
CATALOG = ABSTRACTFLOW / "docs" / "workflow-node-catalog.md"
AUDIT = ABSTRACTFLOW / "scripts" / "audit_flow_graph.py"


@pytest.fixture(scope="module")
def flows():
    return load_flows()


def test_committed_json_matches_the_generator():
    assert build.stale() == [], "run: python -m abstractnote.flows.build"


@pytest.mark.parametrize("root", build.ENTRYPOINTS)
def test_every_entrypoint_compiles_with_its_subflows(flows, root):
    from abstractruntime.visualflow_compiler.compiler import compile_visualflow_tree

    compiled = compile_visualflow_tree(root_id=root, flows_by_id=flows)
    assert compiled


def _catalog_types() -> set[str]:
    if not CATALOG.is_file():
        pytest.skip(f"AbstractFlow node catalog not found at {CATALOG} (sibling checkout needed)")
    text = CATALOG.read_text(encoding="utf-8")
    visible = text.split("## Hidden Or Deprecated Templates")[0]
    return set(re.findall(r"- Node type: `([a-z_0-9]+)`", visible))


def test_only_visible_catalog_node_types(flows):
    allowed = _catalog_types()
    assert "tool_calls" in allowed and "llm_call" in allowed
    used = {n["data"]["nodeType"] for f in flows.values() for n in f["nodes"]}
    assert used - allowed == set()


def test_audit_flow_graph_policy_strict():
    if not AUDIT.is_file():
        pytest.skip(f"audit_flow_graph.py not found at {AUDIT} (sibling checkout needed)")
    paths = [str(build.JSON_DIR / f"{fid}.json") for fid in build.ALL_FLOWS]
    proc = subprocess.run([sys.executable, str(AUDIT), "--policy-strict", *paths], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _edges_into(flow, node_id):
    return {e["targetHandle"]: (e["source"], e["sourceHandle"]) for e in flow["edges"] if e["target"] == node_id}


def test_every_model_node_has_a_schema_and_start_provider_model(flows):
    count = 0
    for f in flows.values():
        for n in f["nodes"]:
            if n["data"]["nodeType"] != "llm_call":
                continue
            count += 1
            schema = (n["data"].get("pinDefaults") or {}).get("resp_schema")
            assert isinstance(schema, dict) and schema.get("type") == "object", (f["id"], n["id"])
            wires = _edges_into(f, n["id"])
            assert wires.get("provider") == ("start", "provider"), (f["id"], n["id"])
            assert wires.get("model") == ("start", "model"), (f["id"], n["id"])
    assert count >= 8


def test_every_flow_exposes_provider_and_model_start_pins(flows):
    for f in flows.values():
        start = next(n for n in f["nodes"] if n["id"] == "start")
        pins = {p["id"] for p in start["data"]["outputs"]}
        assert {"provider", "model"} <= pins, f["id"]


def test_every_flow_end_declares_a_response_pin(flows):
    # output.response exists only when on_flow_end declares a `response` input (review 01 b).
    for f in flows.values():
        ends = [n for n in f["nodes"] if n["data"]["nodeType"] == "on_flow_end"]
        assert ends, f["id"]
        for n in ends:
            assert "response" in {p["id"] for p in n["data"]["inputs"]}, (f["id"], n["id"])


def test_capture_answers_early_and_has_a_session_pin(flows):
    f = flows["note-capture"]
    assert any(n["data"]["nodeType"] == "answer_user" for n in f["nodes"])
    start = next(n for n in f["nodes"] if n["id"] == "start")
    assert {"session_id", "idempotency_key", "context", "process_inline"} <= {p["id"] for p in start["data"]["outputs"]}


def test_recall_accepts_prompt_as_question_alias(flows):
    f = flows["note-recall"]
    start = next(n for n in f["nodes"] if n["id"] == "start")
    assert {"question", "prompt", "effort"} <= {p["id"] for p in start["data"]["outputs"]}
    assert "abstractcode.agent.v1" in f["interfaces"]


def test_interfaces_declared():
    flows = load_flows()
    for fid, ifaces in build.INTERFACES.items():
        assert flows[fid]["interfaces"] == ifaces


def test_automation_defaults_are_valid_for_the_gateway():
    pytest.importorskip("abstractgateway", reason="abstractgateway not installed in this environment")
    from abstractgateway.automation_defaults import validate_flow_automation_defaults

    flows = load_flows()
    drain = validate_flow_automation_defaults(flows["note-drain"]["automation_defaults"])
    assert drain["trigger"]["config"] == {"kind": "every", "every": "2m"}
    assert drain["input_data"]["max_items"] == 20 and drain["input_data"]["budget_s"] == 90
    assert set(drain["input_data"]["_runtime"]["allowed_tools"]) == set(build.NOTE_TOOLS)
    reflect = validate_flow_automation_defaults(flows["note-reflect"]["automation_defaults"])
    assert reflect["trigger"]["config"]["every"] == "7d"


def test_bundle_packs_and_loads(tmp_path):
    from abstractruntime.workflow_bundle import open_workflow_bundle

    path = pack(tmp_path, check_fresh=True)
    assert path.name == f"abstractnote@{build._version()}.flow"
    bundle = open_workflow_bundle(path)
    assert sorted(ep.flow_id for ep in bundle.manifest.entrypoints) == sorted(build.ENTRYPOINTS)
    assert set(bundle.manifest.flows) >= set(build.ALL_FLOWS)
    assert set(bundle.manifest.metadata["automation_defaults"]) == {"note-drain", "note-reflect"}


def test_schema_disagreement_is_refused(monkeypatch):
    import types

    fake = types.ModuleType("abstractnote.process.schemas")
    bad = json.loads(json.dumps(build.FALLBACK_SCHEMAS))
    bad["QuickFiling"]["properties"]["kind"]["enum"] = ["idea"]
    fake.SCHEMAS = bad
    monkeypatch.setitem(sys.modules, "abstractnote.process.schemas", fake)
    monkeypatch.setattr("importlib.util.find_spec", lambda name, *a, **k: object())
    with pytest.raises(RuntimeError, match="QuickFiling differs"):
        build.load_schemas()
