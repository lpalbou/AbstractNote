# AbstractNote for iPhone (planned)

Not built yet. This folder holds the plan; the watch app in [../../watch/apple](../../watch/apple)
is the working reference.

## Plan

- **Layout**: the same as the watch: an XcodeGen `project.yml`, an app target
  `AbstractNotePhone` (iOS 26, bundle id `ai.abstractframework.note.iphone`), a UI test target,
  Swift 6 with strict concurrency, and `Packages/AbstractGatewayClient` as a documented snapshot of
  the watch package (`SNAPSHOT.json` with source hashes, as the AbstractAssistant iPhone's
  `WATCH_SNAPSHOT.json`), plus `scripts/build.sh` with screenshots of every screen from the sample
  notebook.
- **Screens**: a richer Capture (text, dictation, camera, files and voice memos through
  `POST /api/gateway/attachments/upload`, 25 MB per file, then `note-capture` with
  `context.attachments`), the full Graph and Remember views of the web app, Today as on the watch,
  and a share-sheet extension that captures from other apps.
- **Sign-in and storage**: as the watch: token or emailed code, the sealed sign-in file in the
  app container (ADR 0006), never the keychain, sign-out deletes it and ends the session.
- **Bundle parentage (decided, docs/clients.md)**: the watch app stays watch-only
  (`ai.abstractframework.note.watch` inside its own empty iOS container `ai.abstractframework.note`)
  and the iPhone app is a separate app, following the AbstractAssistant precedent. If the watch app
  should later become the iPhone app's companion (watch app embedded in the iPhone app, bundle id
  `ai.abstractframework.note.iphone.watchkitapp`, `WKCompanionAppBundleIdentifier`), the bundle
  ids change before the first public release of either app; after it, the change would orphan
  installed copies.
- **Shared client**: once the watch and iPhone calls settle, the two package snapshots become one
  shared Swift package for every AbstractFramework Apple client.
