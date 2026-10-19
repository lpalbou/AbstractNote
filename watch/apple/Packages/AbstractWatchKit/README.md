# AbstractWatchKit

The parts of an AbstractFramework Apple Watch app that do not depend on the app. AbstractNote's
watch app uses it today; it is the **candidate for a shared package in AbstractUIC
(`abstractuic/swift/AbstractWatchKit`)**, to be consumed by both the AbstractNote and the
AbstractAssistant watch apps (abstractnote backlog 0016). Until it moves, this folder is a
documented snapshot of the AbstractAssistant watch code it came from (`SNAPSHOT.json`: source
files, their hashes, what changed).

## Products

| Product | Platforms | What |
|---|---|---|
| `GatewayCore` | watchOS, iOS, macOS (tested on macOS) | `GatewayTransport` (HTTP to one gateway: reads retried after 1 s and 3 s, writes sent once, a 401 locks out until the next sign-in; attachment upload; speech to text through `POST /runs/{owner}/audio/transcribe`), `GatewaySignIn` and `GatewayCredential` (token, or a session from an emailed code), `SavedSignIn` and `CredentialFile` (one sealed file: Data Protection until first unlock, owner-only, excluded from backup, deleted on sign-out; never UserDefaults or the keychain), `Problem` (one specific sentence per failure and its action), the run and error models. |
| `AbstractWatchKit` | watchOS | `SignInView` (user or email → token or emailed code), `ServerView` (editable server address: https by default, http on the local network), `Listener` (tap to talk: 16 kHz mono WAV, speech vs room noise, 3 s of silence ends it, 60 s at most), `MicButton`, `ListeningView` (waveform, elapsed time, Stop, Cancel), `ProblemView` (the sentence plus Sign in or Try again), `RuntimeKeeper`, and the theme (`Theme`, `FitScroll`, `Card`, `FilledButtonStyle`, `LoadingText`, `ErrorLine`, `SectionTitle`, `When`). |

## Files

```
Package.swift
SNAPSHOT.json
Sources/GatewayCore/Auth.swift            sign-in calls and the credential (from the assistant, unchanged)
Sources/GatewayCore/Transport.swift       request path, upload, transcribe, GatewayError
Sources/GatewayCore/Models.swift          JSONValue, Principal, RunSummary, error body, ISO8601
Sources/GatewayCore/CredentialFile.swift  SavedSignIn and the sealed sign-in file
Sources/GatewayCore/Problem.swift         failures as specific sentences
Sources/AbstractWatchKit/SignInView.swift sign-in screen, InputRow, SignInDefaults
Sources/AbstractWatchKit/ServerView.swift server address screen
Sources/AbstractWatchKit/Listener.swift   microphone (tap to talk)
Sources/AbstractWatchKit/Voice.swift      MicButton, ListeningView, ProblemView
Sources/AbstractWatchKit/RuntimeKeeper.swift keeps the app running while it listens or waits
Sources/AbstractWatchKit/Theme.swift      palette, layout and text components
Tests/GatewayCoreTests/                   sign-in, sealed file, transport, problem sentences
```

## Use

An app adds the package and builds on the products: AbstractNote's `Packages/AbstractGatewayClient`
puts its note calls on `GatewayTransport`, and its app target shows `SignInView`, `ServerView`,
`MicButton`/`ListeningView` and `ProblemView` with its own name and mark. Test with
`swift test` (GatewayCore); the watch UI builds with the app (`scripts/build.sh`).

Moving it to AbstractUIC changes no API: the app's `project.yml` points at the new path (or a
tagged release) instead of `Packages/AbstractWatchKit`.
