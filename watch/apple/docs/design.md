# Design

What the watch app looks like, what it says, what it sends, and what it keeps. The contract with
the gateway is [docs/clients.md](../../../docs/clients.md) and [docs/api.md](../../../docs/api.md).

## Copy rules

- Plain verbs: Send, Ask, Dig deeper, Got it, Close, Add a note, Try again, Done, Sign out.
- One sentence per error, said by `AppModel.say`: "No connection.", "Can't reach your notebook.",
  "Please sign in again.", "Already done on another device.", "This is too long to send from the
  watch.", "Your note could not be saved. Try again."
- No system vocabulary on screen: no run, ledger, gateway, workflow, record, flow or ids. The
  server is called "Server" in Settings and the account "Account".
- No state shown by colour alone: every status has an icon or a word (a check for a saved note, a
  clock for a late answer, an exclamation mark for an error, "Due …", "No longer true").
- Switches are labelled by their feature and show their state (**Place** on or off, with a line
  saying what it does now).
- VoiceOver: every control has a label, and a hint when the outcome is not obvious; rows read as
  one element.

## Layout rules

- Actions at the bottom (`FitScroll`): content from the top, the buttons pinned to the bottom when
  everything fits, after the content when it scrolls.
- A spinner while something is on its way; a read keeps its spinner through the client's retries
  and says it failed only after every try (abstractframework ADR-0039), then offers Try again.
- Nothing consequential without a tap: acknowledging an insight, closing a loop and starting a deep
  search each need their own button; opening an item never changes it.
- Four vertical pages (Note, Ask, Today, Settings) in that order: writing a note is the first thing
  the app is for.

## What the watch sends

| When | Request |
|---|---|
| Send | `POST /api/gateway/runs/start` `{bundle_id: "abstractnote", flow_id: "note-capture", session_id, input_data: {text, occurred_at, location?, source: {client: "watch"}, idempotency_key, process_inline: false, session_id, context?: {about}}}` (`input_data.session_id` repeats the request's: the gateway does not hand it to the flow), then `GET /runs/{id}` every second until `output.response` (60 s at most; later than that the screen says the answer is late) |
| Try again after a failed send | the same body with the same `idempotency_key` (made when Send was tapped), or, when the run had started, the same run read again |
| Ask | `runs/start` with `note-recall` `{question, effort: "standard", session_id}`, then `output.response` and `output.evidence` (90 s at most) |
| Dig deeper | `runs/start` with `note-recall` `{question, effort: "deep"}`; not followed: the answer arrives as an insight in Today |
| Today | `GET /note/changes?since=<cursor>`, `/note/insights?acked=false`, `/note/tasks?status=open`, `/note/cues`, `/note/overview` (its `resurfacing` is "On this day"), read together; the cursor in the answer is kept for the next visit |
| Got it / Close | `POST /note/insights/{id}/ack` / `POST /note/tasks/{id}/close`; a 409 means it was already done elsewhere and counts as done |
| Add a note about this | a capture whose `input_data.context.about` is `{kind: insight|task|cue, id, title}` |
| A note's page | `GET /note/notes/{id}`; for an insight, its `insight.evidence_note_ids` give a **Why** link |
| Why (on a cue whose `subject` is set, or an insight) | `GET /note/notes/{id}` for each of the `evidence_note_ids` (at most 8, read together) |
| Sign out of an emailed-code session | `POST /session/logout` |

A run that fails, or a flow that refuses its input (it completes with `output.success: false`
and `output.error`), shows the flow's own sentence without the runtime's "Runtime error: " prefix;
with no sentence, the screen's own one. The watch never starts the processor ("Process now"); a
client that does sends `POST /automations/{id}/commands {"command_id": "<uuid>", "type":
"automation.run_now"}` and reads a 409 `automation_busy` as "already running" (docs/api.md).

Decoding is strict on the fields the watch uses (a missing one says "This answer could not be
read. Update the app.") and ignores every key it does not use, so the gateway can add fields
freely. The processor's throughput (`GET /note/queue` `processor`) is not shown on the watch.

The watch never sends a user, tenant or runtime id: the gateway picks the notebook from the
credential. Reads are retried after 1 s and 3 s; writes are sent once. A 401 stops every request
and returns to the sign-in screen.

## What the watch keeps

One JSON file, `Application Support/AbstractNote/signin.json` in the app's container
(`CredentialFile`, ADR 0006): the server address, the account, the credential (a token, or an
emailed-code session id and its CSRF token), the watch's session id (`note-watch-<uuid>`) and
Today's cursor. It is written atomically with Data Protection
`completeUntilFirstUserAuthentication` (readable after the first unlock since the watch started,
so a note sent with the wrist down still finds it), owner-only (0600), excluded from backup, and
deleted on Sign out. The credential never goes to UserDefaults or the keychain. UserDefaults holds
only the last server address and account typed at sign-in (the next sign-in's defaults) and the
Place switch. The operator's ruling on this storage is pending (backlog 0017); the file is the
only place to change.

## Voice

The microphone button beside the note's field records (tap to talk, `Listener` in AbstractWatchKit:
16 kHz mono WAV, speech told from the room's noise, 3 s of silence or Stop ends it, 60 s at most;
a waveform and the elapsed time show meanwhile). The WAV is uploaded
(`POST /api/gateway/attachments/upload`, the watch's session `note-watch-<uuid>`) and written down
by the server (`POST /api/gateway/runs/{owner}/audio/transcribe {audio_artifact: {"$artifact": id}}`,
the owner being the upload's `run_id`). The words fill the field, editable. **Send** sends the words;
**Send as recording** sends them with the upload's `attachment` reference, unchanged, in
`input_data.context.attachments`, so the original audio stays with the note. Dictation and scribble
on the field itself still work. A server without speech to text answers the transcription with a
server error (recorded: a 500 carrying an install recipe); the watch says one sentence instead, "This
server can't transcribe voice yet. Type or send the recording instead.", keeps the recording, and
**Send as recording** stays available (with no words typed the note is named "Voice note (0:12)").

## Errors

Every failure is one specific sentence with one action (`Problem` in AbstractWatchKit, shown by
`ProblemView`): a 401 says "Sign in to your notebook." with **Sign in** (the sign-in screen, the
server kept); a 404 is checked against `GET /note/status`, whose own 404 means "AbstractNote isn't
set up on this server yet."; no answer says "Can't reach <server>." with **Try again**; anything
else shows the server's own sentence (without "Runtime error: "). Today, Note, Ask, the note and
Why pages follow it.

## Settings and sign-in

With no saved sign-in the app opens on the sign-in screen (user or email → token or emailed code,
AbstractWatchKit's `SignInView`). Settings: **Server** opens the server screen (https unless
`http://` is typed, which the watch allows on the local network only; a different server signs out
and opens its sign-in), **Account** opens the sign-in to change account, **Sign out** at the bottom
deletes the sealed file and keeps the server. A sign-in made by launch arguments reads its account
from `GET /api/gateway/me` and keeps it.

## The Swift packages

`Packages/AbstractWatchKit` holds what any AbstractFramework watch app needs (its README; candidate
for `abstractuic/swift/AbstractWatchKit`, backlog 0016; `SNAPSHOT.json` lists the AbstractAssistant
files it came from). `Packages/AbstractGatewayClient` holds AbstractNote's calls, models and the
sample notebook (`MockGatewayClient`) on top of its `GatewayCore`.
