# Release preparation for 0.1.0

## Idea
Release order: (1) abstractmemory with `close_assertion` (branch `close-assertion`, worktree untracked/wt/abstractmemory) and raise abstractnote's floor; (2) abstractgateway with the AbstractNote seam (branch `abstractnote-seam`) and name that version in README, getting-started, pyproject `gateway` extra and `abstractnote.gateway`'s import check; (3) abstractnote 0.1.0 on PyPI and `@abstractframework/note` on npm (first publish needs the owner's OTP); (4) the root installer's app list (`AF_NPM_APPS`, `NPM_RELEASE_VERSIONS`) and the framework pins; (5) the watch app through TestFlight once the operator rules on credential storage (0017). Every publication needs the operator's explicit go.

## Status
Proposed; blocked on operator decisions.

## Metadata
- Created: 2026-10-19
- Status: proposed
