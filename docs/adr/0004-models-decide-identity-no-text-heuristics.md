# ADR 0004: Models decide identity and change; no text heuristics

**Status**: Accepted  
**Date**: 2026-10-19

## Context
Entity and relationship de-duplication is the hardest part of the system. The operator trusts small fast models (for example Qwen3.6 35B-A3B) over regular expressions or NLP heuristics, and the framework rule forbids adding text heuristics without explicit consent.

## Decision
Deterministic code produces only exact-hash matches and candidate lists (case-folded alias match, full-text, vector neighbours). A model decides, per mention, reuse, create or merge, and per claim, restates, refines, supersedes or contradicts, from a structured schema, and must say `unsure` rather than guess. Thresholds (near-duplicate cosine, candidate counts) are explicit constants documented in data-model.md, never intent classifiers.

Entity kinds are `person, org, project, concept, place, event, artifact, software, thing`: a deliberate extension of AbstractFramework ADR 0025's set (which has `doc` and `claim`, no `project`, `place`, `software`); ids keep ADR 0025's `ex:<kind>-<slug>` shape. Linking schemas refer to candidates by index and use enumerations, never free-form ids, so a small model cannot invent an identifier; out-of-packet references are rejected deterministically (set membership), not interpreted.

## Consequences
Quality depends on prompt and schema design, which are versioned files; consolidation is eventual (a later reflect pass can merge what an earlier pass left as `closeMatch`); every decision carries a reason stored in the revision.
