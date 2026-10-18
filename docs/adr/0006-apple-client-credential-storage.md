# ADR 0006: Apple client credential storage is file-sealed in the app container

**Status**: Proposed (operator ruling pending)  
**Date**: 2026-10-19

## Context
The framework rule is no OS keychain for secrets. The AbstractAssistant watch stores the credential in UserDefaults and the iPhone in the Keychain.

## Decision (proposed)
AbstractNote Apple clients store the gateway address and credential in one file in the app container with Data Protection `completeUntilFirstUserAuthentication`, encoded as JSON, deleted on sign-out. No iCloud backup of that file.

## Consequences
Consistent with the framework's file-sealed secrets rule; the operator may prefer UserDefaults parity with the assistant watch; backlog 0017 tracks the ruling.
