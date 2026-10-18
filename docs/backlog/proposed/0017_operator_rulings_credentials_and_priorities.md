# Operator rulings: Apple credential storage and priority sources

## Idea
Two decisions need the operator: (1) file-sealed credential storage in the app container on Apple clients (proposed default) versus the assistant precedents (UserDefaults on watch, Keychain on iPhone); (2) whether clients may set `priority` explicitly or only the filing model decides. Until ruled, the design uses file-sealed storage and accepts an explicit priority.

## Status
Proposed; not an implementation commitment. Promote to planned with a code-reality section when scheduled.

## Metadata
- Created: 2026-10-19
- Status: proposed
