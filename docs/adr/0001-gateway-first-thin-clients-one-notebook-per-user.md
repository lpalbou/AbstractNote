# ADR 0001: Gateway-first thin clients, one notebook per user plane

**Status**: Accepted  
**Date**: 2026-10-19

## Context
AbstractNote needs web, watch, phone, CLI and agent clients, several users per gateway, and durable background processing. The earlier SmartNote prototype ran as a tray app with its own runtime wiring. AbstractGateway already authenticates callers and routes each to its own runtime and data plane.

## Decision
Every client is a thin client of AbstractGateway. AbstractNote runs no server of its own. All AbstractNote state for a user lives under that user's plane at `<plane>/abstractnote/`. Clients never send user, tenant, runtime or workspace identifiers. Knowledge-changing actions go through flows started with the runs API; interactive reads go through a read API that resolves the caller's plane with the gateway's own service lookup.

## Consequences
A gateway is required; isolation and auth are inherited rather than reimplemented; the gateway integration is a small explicit change (tool binding, router include, app catalog row) tracked as backlog 0007.
