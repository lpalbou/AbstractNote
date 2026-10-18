# Flows bundle and headless runner

## Summary
Generate the six VisualFlow JSON flows (`note-capture`, `note-process`, `note-drain`, `note-recall`, `note-mine`, `note-reflect`) with `abstractnote.flows.build`, pack the `abstractnote` bundle, and provide `abstractnote.flows.runner` to run them headlessly (local Runtime, real or scripted model, tools bound to a notebook directory).

## Why
The flows are the visible cognitive process and the only execution path for capture and processing; the headless runner is the dev loop, the test harness and the bulk processor.

## Scope
- In: catalog-only node types; `provider`/`model` start pins; `resp_schema` on every model node; `answer_user` early answer plus `on_flow_end` response; `interfaces` declared; `compile_check` in tests; `abstractnote bundle`; drive subflow waits in the runner.
- Out: custom node types (impossible), editor changes.

## Dependencies
0002, 0003, 0004 for the tools; AbstractRuntime visualflow compiler (>=0.10.2).

## Expected outcomes
- `compile_visualflow_tree` compiles all flows; the bundle loads in a gateway; the flows open in the Flow editor via Import.
- Headless: capturing then draining one note with a scripted model yields a revision; with a real local model yields plausible entities.

## Current code reality
Example flows to model on: `ltm-ai-kg-ingest-turn`, `ltm-ai-kg-extract-triples`, `ltm-ai-chat`, `event-inbox-react-agent` (abstractflow/examples/flows). Headless recipe: abstractruntime tests/test_kg_learn_and_recall_contract.py.

## Validation
`pytest tests/test_flows_compile.py tests/test_flows_headless.py`; `abstractflow/scripts/audit_flow_graph.py --policy` passes on every generated flow.

## Metadata
- Created: 2026-10-19
- Status: planned
- Area: flows
