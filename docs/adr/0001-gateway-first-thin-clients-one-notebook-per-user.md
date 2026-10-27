# ADR 0001: Gateway-first thin clients, one notebook per user plane

**Status**: Accepted. Amended 2026-10-27: one graph per plane (AbstractMemory ADR 0003); see "Amendment" below.  
**Date**: 2026-10-19

## Context
AbstractNote needs web, watch, phone, CLI and agent clients, several users per gateway, and durable background processing. The earlier SmartNote prototype ran as a tray app with its own runtime wiring. AbstractGateway already authenticates callers and routes each to its own runtime and data plane.

## Decision
Every client is a thin client of AbstractGateway. AbstractNote runs no server of its own. All AbstractNote state for a user lives under that user's plane at `<plane>/abstractnote/`. Clients never send user, tenant, runtime or workspace identifiers. Knowledge-changing actions go through flows started with the runs API; interactive reads go through a read API that resolves the caller's plane with the gateway's own service lookup.

## Consequences
A gateway is required; isolation and auth are inherited rather than reimplemented; the gateway integration is a small explicit change (tool binding, router include, app catalog row) tracked as backlog 0007.

## Amendment (2026-10-27): one graph per plane

AbstractMemory ADR 0003 (`abstractmemory/docs/adr/0003-one-graph-ledger-provenance-and-revisions.md`, decisions
1-3 and 13) makes the plane one *mind*: one AbstractMemory home (`<plane>/abstractmemory/`) that holds what the
plane's applications learn, reached by hosts through AbstractRuntime's memory facade and by applications through
the gateway's `/api/gateway/memory/*` routes. The thin-client rule above is unchanged and extends to knowledge:
the notebook's graph converges into the plane's mind (backlog 0033 phase 2), and `<plane>/abstractnote/` keeps
the notes, their attachments and the app's own state. What the notebook learns belongs to its owner (AbstractMemory
ADR 0003 decision 2, amended 2026-10-27, operator ruling): inside the plane's mind it is partitioned by
`owner_id`, readable by the owner's other applications and by nobody else by default; the plane is not a sharing
scope, and every read surface (runtime facade, gateway routes, memory tools) filters by owner. Every write from a note
names its ledger sources (the capture and processing steps, the note body as an artifact of the capture run;
backlog 0037).
