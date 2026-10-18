# Clients

AbstractNote ships a web app and an Apple Watch app, and reserves the structure for an iPhone
app. All clients are thin: the gateway owns authentication, execution and state. They share one
contract: sign in to a gateway, start the `note-capture` and `note-recall` flows with the runs
API, read the note API for views, and never send user, tenant or runtime identifiers.

## Sign-in and isolation

- A client signs in with a gateway account token (`Authorization: Bearer`) verified against
  `GET /api/gateway/me`, or with an emailed one-time code (`POST /session/recovery/request`, then
  `/redeem`) that yields a session id and CSRF token sent as headers on later requests.
- Sign-out clears the stored credential locally and, for sessions, calls
  `POST /session/logout`. A `401` on any call returns the client to sign-in.
- The gateway routes every request to the caller's own data plane, so one gateway serves many
  users and each sees only their own notebook. Use a dedicated `user`-role account per person or
  device; never an admin token.
- Credential storage on Apple clients: a file in the app container protected with Data
  Protection (`completeUntilFirstUserAuthentication`), not the OS keychain, following the
  framework's file-sealed secrets rule. The gateway address is stored next to it.

## Web app (`web/`)

- npm package `@abstractframework/note`, built with Vite, React and TypeScript, using
  `@abstractframework/ui-kit`, `@abstractframework/panel-chat` and `@abstractframework/app-server`
  from the npm registry with semver ranges.
- Served by the gateway at `/apps/note/` through the app proxy. The app's Node server answers the
  mount header, relays `/api/*` to the gateway with the session cookie, binds `127.0.0.1`, and
  uses relative URLs and the shared shell, as the app-server contract requires.
- Three views: **Capture**, **Graph**, **Remember** (see [remembering.md](remembering.md)), plus a
  small **Settings** drawer (filing model, answering model, inline processing, reflect schedule,
  location permission, queue status with retry, sign-out). Capture asks once for location
  permission and attaches it to notes when granted.
- Theming uses ui-kit tokens only; tested on default-dark, observer-night and solarized-light.
- Registered in the gateway app catalog as `note` (port 3006, cookie prefix `abstractnote`,
  page title `AbstractNote`) and in the framework installer's app list.

Development: `npm install && npm run dev` serves the app against a gateway given by
`--gateway-url`; `npm run build` produces `dist/`; `node bin/cli.js --port 3006 --gateway-url
http://127.0.0.1:8080` runs the production server the way the gateway launches it.

## Apple Watch app (`watch/apple/`)

- XcodeGen project `project.yml`: target `AbstractNoteWatch` (watchOS 26, bundle id
  `ai.abstractframework.note.watch`), an empty iOS container `AbstractNote`
  (`ai.abstractframework.note`) for App Store packaging, a UI test target, Swift 6 with strict
  concurrency. `WKWatchOnly` is set; the app runs independently of a phone.
- Swift package `Packages/AbstractGatewayClient`: the gateway client (credential, sign-in,
  requests with read retries, run start and polling, attachment upload, note API reads) and a
  mock client for previews and screenshots. It starts as a documented snapshot of the
  AbstractAssistant watch client (`SNAPSHOT.json` records the source and hashes) and adds the
  note endpoints; the two clients are candidates for a shared package once their APIs settle.
- Screens:
  - **Note**: a text field (watchOS dictation and scribble come free with it) and a Send button at
    the bottom; after sending, the filing answer ("Saved as idea 'LUT budget'. Related: Dana,
    LUT.") with a Done button. Location is attached when the user allowed it. Each send carries a
    fresh idempotency key so a radio retry never duplicates a note.
  - **Ask**: dictate a question, see the standard-effort answer with the evidence titles; "Dig
    deeper" starts a deep recall and the result shows in Today when ready.
  - **Today**: unacknowledged insights, open loops, on-this-day notes; tap to acknowledge or to
    add a note about it.
  - **Settings**: gateway address, account, sign-out, about.
- Build and test: `scripts/build.sh` (XcodeGen, `swift test`, simulator build, screenshots of
  every screen with the mock client), `scripts/deploy-watch.sh` (signed build and install on a
  paired watch through `devicectl`, gateway and token passed as launch arguments), and a GitHub
  workflow that builds on push.
- End-user copy rules: plain verbs, one-sentence errors, actions at the bottom, no system
  vocabulary, VoiceOver labels on every control, no state shown by colour alone, nothing
  consequential without a tap.

## iPhone app (`phone/iphone/`)

Planned. The folder holds the plan: the same package layout as the watch with a richer Capture
(camera, files, voice memos), the full Graph and Remember views, and share-sheet capture from
other apps. The bundle id reserved is `ai.abstractframework.note.iphone`, following the
AbstractAssistant precedent of watch-only plus separate iPhone app; if the watch app should
later become a companion of the iPhone app, the bundle parentage changes before the first
public release of either.

## CLI and agents

The `abstractnote` CLI is a client when given `--gateway URL --token T`, and a headless tool when
given `--notebook DIR`. Agents and entities running inside the same gateway call the note tools
directly; agents elsewhere use the runs API like any client.
