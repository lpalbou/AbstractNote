# Building AbstractNote for Apple Watch

A watch-only watchOS 26+ SwiftUI app and two local Swift packages: `AbstractWatchKit` (the shared
watch parts: gateway core, sign-in, server screen, microphone, theme; see its README) and
`AbstractGatewayClient` (AbstractNote's calls on top of it). It runs in the simulator with a built-in sample notebook (`--mock`), or live
against a gateway in the simulator or on a paired watch.

## Requirements

- Xcode 27.1 with the watchOS 26.5 simulator runtime, and XcodeGen (2.45 tested).
- Simulator builds and the package tests need no signing. A build for a physical watch signs with
  the developer team in `project.yml` (`DEVELOPMENT_TEAM`).

## Build and screenshot in the simulator

```sh
cd watch/apple
scripts/build.sh               # generate, test the package, build, install, screenshot every screen
scripts/build.sh --no-screens  # generate, test and build only
```

`scripts/build.sh` runs, in order:

```sh
xcodegen generate --spec project.yml
(cd Packages/AbstractWatchKit && swift test)
(cd Packages/AbstractGatewayClient && swift test)
xcodebuild -project AbstractNoteWatch.xcodeproj -scheme AbstractNoteWatch \
  -destination 'platform=watchOS Simulator,id=<Apple Watch Series 11 (46mm)>' \
  -configuration Debug -derivedDataPath build/DerivedData build CODE_SIGNING_ALLOWED=NO
xcrun simctl install <simulator> build/DerivedData/Build/Products/Debug-watchsimulator/AbstractNoteWatch.app
xcrun simctl launch <simulator> ai.abstractframework.note.watch --mock --screen <name>   # exit code checked
xcrun simctl io <simulator> screenshot ../../untracked/screens/watch/<name>.png
```

Environment: `SIM_ID` (a simulator id; default the first "Apple Watch Series 11 (46mm)", a booted
one first), `DERIVED_DATA`, `SPM_SCRATCH` (the `swift test` build folder), `SCREENS_DIR`, and
`KEEP_APP=1` to leave the app installed. The simulator may be shared with other work: the script
waits while another `xcodebuild` runs, never shuts the simulator down or erases it, and uninstalls
only its own app at the end. Screenshots go outside git (`untracked/screens/watch/`).

The Xcode project is generated from `project.yml` and is not committed.

## Tests

- **AbstractWatchKit tests**: `swift test --package-path Packages/AbstractWatchKit`: sign-in, the
  sealed sign-in file (Data Protection option, owner-only, excluded from backup, gone on sign-out,
  never in UserDefaults), the upload and transcription requests (recorded upload answer), the
  failure sentences.
- **Note client tests**: `swift test --package-path Packages/AbstractGatewayClient`. The live client
  is driven against fixtures in `Tests/AbstractGatewayClientTests/Fixtures` (status codes
  included: 401 locks the client out, 409 is "already done elsewhere", 413 is "too long"); the
  capture body (idempotency key reused on retry, session id, `process_inline: false`, no
  identity fields); the sealed sign-in file (Data Protection option, owner-only, excluded from
  backup, gone on sign-out, never in UserDefaults); sign-in; read retries.
- **Recorded fixtures (review 01 check S5)**: `Fixtures/recorded/` holds answers recorded from the
  hermetic gateway (status codes included: 401, 404, 409, 413), replayed through the live client
  (`RecordedGatewayTests`): request shape, capture and recall outputs, a refused capture, Today's
  reads, the refusals. When the repository's recordings folder (`../../untracked/fixtures/gateway/`,
  or `$ABSTRACTNOTE_GATEWAY_FIXTURES`) exists, the bundled copies must match it (drift fails) and
  every recording must be readable or on the explicit not-called-by-the-watch list; without it
  (CI) that one test is reported as skipped. The hand-written fixtures at the top level cover only
  what is not recorded yet.
- **UI tests**: the `AbstractNoteWatchUITests` scheme taps through the sample notebook:

```sh
xcodebuild test -project AbstractNoteWatch.xcodeproj -scheme AbstractNoteWatchUITests \
  -destination 'platform=watchOS Simulator,id=<simulator>' -derivedDataPath build/DerivedData CODE_SIGNING_ALLOWED=NO
```

## Launch arguments

| Argument | Effect |
|---|---|
| `--mock` | the sample notebook instead of a gateway (screenshots, UI tests); never asks for Location |
| `--screen <name>` | with `--mock`, opens a scene: `signin` `note` `note-listening` `note-recording` `note-sending` `note-saved` `note-error` `ask` `ask-thinking` `ask-answer` `ask-deeper` `today` `today-quiet` `today-signin` `today-notsetup` `today-offline` `insight` `loop` `cue` `cue-why` `note-detail` `settings` `server` `account` `about` |
| `--gateway <url> --token <token> [--user <id>]` | signs in with that gateway and token and keeps it in the sealed sign-in file, so one launch provisions a simulator or a watch; the account is then read from `GET /api/gateway/me` and shown in Settings |
| `--forget-gateway` | deletes the sign-in file (the sign-in screen shows) |

Debug builds also take `--test-identify <user or email>` and `--test-secret <token or code>` to sign
in without typing. Release builds ignore them.

## On a paired watch

Pair the watch first ([docs/pairing.md](docs/pairing.md)), then:

```sh
scripts/deploy-watch.sh --gateway https://gateway.example.org --token <token>  # signed build, install, launch signed in
scripts/deploy-watch.sh --no-build --screen settings                           # relaunch
scripts/deploy-watch.sh --no-build --forget                                     # remove the sign-in on the watch
```

The device id comes from `WATCH_DEVICE` or `../../untracked/watch-device-id.txt`
(`xcrun devicectl list devices`). Give the watch its own `user` account; never an admin token.

## Continuous integration

`ci/watch.yml` is the GitHub workflow (generate, package tests, simulator build, unsigned Release
device build, the watch-only check). GitHub reads workflows only from the repository root, so it
goes live once copied to `.github/workflows/watch.yml`. There is no release job yet.

## App Store packaging

A watch-only app reaches the App Store inside an empty iOS container: the `AbstractNote` target
(`application.watchapp2-container`, bundle `ai.abstractframework.note`) embeds
`AbstractNoteWatch` (`ai.abstractframework.note.watch`, `SKIP_INSTALL: YES`). Archive the
container scheme; device builds keep using the watch scheme. Keep signing and release notes
outside the repository.
