# Shared Swift watch package (AbstractWatchKit)

## Idea
AbstractAssistant watch, AbstractAssistant iPhone and AbstractNote watch each carried a copy of the
gateway client and of the watch building blocks (sign-in, microphone, theme). Move the shared part
to one package in AbstractUIC, `abstractuic/swift/AbstractWatchKit`, consumed by both watch apps
(and later the iPhone apps for `GatewayCore`).

## Proposal (2026-10-19, operator request)
The package exists already as a local package in this repository:
`watch/apple/Packages/AbstractWatchKit` (README.md there), with two products:

- `GatewayCore` (Foundation only; watchOS, iOS, macOS; tested on macOS): the transport with read
  retries, writes once and the 401 lockout; attachment upload; speech to text; sign-in by token or
  emailed code; the sealed sign-in file (ADR 0006); specific failure sentences (`Problem`); the run
  and error models.
- `AbstractWatchKit` (watchOS): sign-in screen, server screen, tap-to-talk `Listener`, mic button and
  recording view, problem view, `RuntimeKeeper`, theme and layout components.

AbstractNote's note client (`watch/apple/Packages/AbstractGatewayClient`) sits on top of it and keeps
only the note calls, models and the sample notebook.

Steps when scheduled:
1. Copy `watch/apple/Packages/AbstractWatchKit` to `abstractuic/swift/AbstractWatchKit` (with its
   tests and SNAPSHOT.json), tag it, point AbstractNote's `project.yml` at it.
2. Port the AbstractAssistant watch: replace its `Auth.swift`, the request path of
   `GatewayClient.swift`, `Theme.swift`, `RuntimeKeeper.swift`, `Settings/SignInView.swift` and the
   recording part of `Shared/Listener.swift` (its orb keeps its own spectrum analysis on top) with the
   package; adopt `CredentialFile` (pending the credential ruling, backlog 0017) and `Problem`.
3. The iPhone apps adopt `GatewayCore` only.

## File list (local package, 2026-10-19)
- Package.swift, README.md, SNAPSHOT.json
- Sources/GatewayCore/Auth.swift, Transport.swift, Models.swift, CredentialFile.swift, Problem.swift
- Sources/AbstractWatchKit/SignInView.swift, ServerView.swift, Listener.swift, Voice.swift, RuntimeKeeper.swift, Theme.swift
- Tests/GatewayCoreTests/SignInTests.swift, CredentialFileTests.swift, TransportTests.swift

## Status
Proposed; not an implementation commitment for the AbstractUIC move. The local package is built and
tested with the AbstractNote watch app. Promote to planned with a code-reality section when scheduled.

## Metadata
- Created: 2026-10-19
- Updated: 2026-10-19 (local package built; proposal and file list)
- Status: proposed
