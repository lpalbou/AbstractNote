# ADR 0002: Flows carry the cognition, tools carry the bookkeeping

**Status**: Accepted  
**Date**: 2026-10-19

## Context
The operator wants the way notes are absorbed, queued and understood to be modelled as AbstractFlow workflows so it is visible and editable, while deterministic work (hashing, storage, candidate lookup, graph writes) needs real code with access to the user's notebook. AbstractRuntime allows only catalog node types; the gateway binds tools per plane by closure (the `read_skill` precedent).

## Decision
Judgement steps (quick filing, extraction, linking, synthesis, mining, reflection) are `llm_call` nodes with structured output in the `abstractnote` bundle. Deterministic steps are plane-bound Python tools (`note_*`) called from `tool_calls` nodes and auto-approved. No AbstractNote logic hides inside a single opaque tool: a flow reader can follow the cognitive process node by node.

## Consequences
Flows are importable in the editor; the same flows run headlessly; tool contracts (api.md) are a stable seam between the core library, the flows, the gateway and the clients.
