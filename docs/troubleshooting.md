# Troubleshooting

Each entry starts from what you see (a message, an exit code, a status), then gives the cause,
how to confirm it, and the fix. Setup steps are in [getting-started.md](getting-started.md), the
HTTP and CLI reference in [api.md](api.md), and short conceptual answers in [faq.md](faq.md).

The command line prints results as JSON on standard output and problems as one sentence on
standard error. Exit codes: `0` done, `2` bad input (the sentence says what to change), `3` a
part of AbstractNote that is not installed or not available in this version. `abstractnote
import` exits `1` when some records could not be imported (they are listed under `errors`).

## Notebook and embeddings

### This notebook is pinned to the embedding model

```text
This notebook is pinned to the embedding model 'abstractnote-hashing-v1-256' but the embedder given is 'text-embedding-qwen3-embedding-0.6b'; rebuild with reembed to change models.
```

- **Cause**: the notebook was created with one embedding model and the command gave another.
  `abstractnote-hashing-v1-256` is the built-in embedder used when the first command had no
  `--embed-base-url` / `--embed-model`.
- **Check**: `abstractnote status --notebook DIR` shows `embedding_pin.model_id`.
- **Fix**: give the pinned model's flags again, or move the notebook to the new model with
  `abstractnote rebuild --reembed --notebook DIR --embed-base-url URL --embed-model MODEL`.
  `status` then shows the new pin.

### This notebook's vectors were made with the embedding model ...

```text
This notebook's vectors were made with the embedding model 'text-embedding-qwen3-embedding-0.6b'; open it with that embedder ...
```

- **Cause**: the notebook is pinned to an embedding server and the command gave no
  `--embed-base-url` / `--embed-model`. The notebook then opens read-only: `status` (which shows
  `read_only` with this sentence), `entity`, `day`, `changes` and `search` work, and `search`
  matches words only and says so in its `warnings`. Commands that write or need new vectors
  (capture, processing, user actions, imports, rebuild) refuse with this sentence before writing
  anything.
- **Fix**: add the two flags with the pinned model to the command.
- **Imports**: `abstractnote import` takes the same two flags.

### Give both --embed-base-url and --embed-model

One of the two flags is missing. They always go together.

### Say which notebook with --notebook DIR

Every command except `bundle` needs `--notebook DIR`. A gateway keeps a user's notebook in
`<data dir>/users/<tenant>/<runtime>/abstractnote` (or `<data dir>/abstractnote` without user
accounts).

## Processing and models

### Give --provider and --model for the model that processes or answers

`process --local` and `search --effort standard|deep` run a model. Name it, for example
`--provider mlx --model mlx-community/Qwen3.6-35B-A3B-4bit`.

### MLX dependencies not installed

```text
MLX dependencies not installed. Install with: pip install "abstractcore[apple]"
```

- **Cause**: `--provider mlx` needs the MLX engine, which is not part of the base AbstractCore
  install.
- **Fix**: `pip install "abstractcore[apple]"` in the same environment as `abstractnote` (on
  Apple silicon). On Linux with an NVIDIA or AMD GPU, use `abstractcore[gpu]`; anywhere else, use
  a provider that serves models over HTTP, such as `lmstudio` or `ollama`.

### Processing through a gateway is not available here

`abstractnote process` without `--local` (exit 3). Add `--local` with `--provider` and `--model`
to process on this machine, or use **Process now** in the web app's Settings to start the
gateway's processor.

### Gateway client mode is not available in this version

The command was given `--gateway`. Run it on the machine that holds the notebook with
`--notebook DIR`, or use the HTTP API ([api.md](api.md)).

### Creating the processor needs a gateway

`abstractnote processor ensure` (exit 3). Open the web app, which creates the processor when it
is missing, or call `POST /api/gateway/note/processor/ensure` with your account token.

### Notes stay queued

- **Check**: `abstractnote status` (or `GET /api/gateway/note/queue`) shows `queued`,
  `processing` and `failed`, and `automations.processor_id`.
- **Cause and fix**: with no `processor_id`, the processor automation does not exist yet: open
  the web app or call `processor/ensure` as above. With a processor, notes are processed up to 20 every two minutes,
  one at a time, highest priority first; imports wait behind live notes by design. Headless, run
  `process --local` again until the queue is empty.

### A note failed to process

The queue retries a failed note after one minute and then after ten minutes; the third failure
marks it `failed` with the error. "Processing stopped before it finished." means a run ended
while it held the note (the lease expired). Fix the cause the error names (most often the model
or provider), then press **Retry** in the web app's queue panel or call
`POST /api/gateway/note/queue/{note_id}/retry`.

### the generated flows are stale

```text
the generated flows are stale (note-process); run python -m abstractnote.flows.build
```

`abstractnote bundle` found that the flow JSON in the package no longer matches the generator
(you changed a prompt, a schema or the generator). Run `python -m abstractnote.flows.build` and
commit the regenerated `src/abstractnote/flows/json/*.json`; see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Gateway

### Every /api/gateway/note call answers 404, or the bundle is unknown

- **Cause**: the gateway does not have AbstractNote. Its log says
  `AbstractNote is not installed (...): the note integration is off`.
- **Fix**: install `abstractnote` and the AbstractGateway checkout with the integration in the
  gateway's own environment, then restart the gateway
  ([getting-started.md](getting-started.md#3-the-gateway-path)). The log then says
  `AbstractNote is installed: note tools, /api/gateway/note and its workflows are enabled`.

### Your notebook is not available: ...

Every note tool answers this sentence when the gateway could not bind the tools to your
notebook; the rest of the gateway keeps working. The reason follows the colon. The most common
one is an embedding pin: the notebook was first used with a different embedding route than the
one the gateway has now. Rebuild it with the gateway's model (`abstractnote rebuild --reembed`
on the notebook directory, gateway stopped), or restore the earlier embedding route.

### A capture waits for approval

The watch app says "This note waits for your OK in AbstractNote on another device."; a headless
run raises "run ... waits for approval of [...]; note tools must be auto-approved".

- **Cause**: the note tools are not in the gateway's auto-approve set, so the runtime parks the
  run on a tool-approval wait. The integration adds them; a gateway without it, or a custom
  approval policy that replaced the set, does not.
- **Fix**: run the gateway with the AbstractNote integration and its default approval policy.
  Note tools only read and write the caller's own notebook.

### 409 automation_busy, "Already running."

```json
{"detail": {"reason_code": "automation_busy", "message": "An occurrence is already running or waiting to run."}}
```

**Process now** was pressed while a processing pass is running or queued. Nothing is wrong; the
running pass continues and the next one follows on schedule.

### 413, "Attachment too large"

```text
Attachment too large (26214401 bytes > 26214400 bytes)
```

The file is over the gateway's attachment limit (25 MB per file by default). The web app refuses
it before uploading: "This file is larger than 25 MB; add it from the computer that has it with:
abstractnote import files <folder>." Import it on that machine, or raise the gateway's limit.

### Local file paths are not accepted here

```text
Local file paths are not accepted here; upload the file as an attachment instead.
```

A `note-capture` run was given `{"path": ...}` in `context.attachments`. Inside a gateway the
note tools only read files you uploaded: call `POST /api/gateway/attachments/upload` and pass
`{"$artifact": "<id>"}`. Headless, paths are accepted only inside a folder you allow
(`abstractnote.tools.bind(..., allow_paths_under=DIR)`); outside it the answer is "That file is
outside the folder this notebook may read from."

### Run errors start with "Runtime error: "

A flow that refuses its input still completes; its output is `{"success": false, "error":
"Runtime error: <sentence>"}` with no `response`. The sentence after the prefix is the cause
(for example the path refusal above). Clients strip the prefix before showing it.

### 401 Unauthorized, "You were signed out. Please sign in again."

The token or session is no longer valid (revoked, expired, or for another gateway). Sign in
again with a `user` account token or an emailed code. Admin tokens are not meant for clients.

### 404 "That note is not in your notebook."

The id belongs to another account's notebook or does not exist. Each account sees only its own
notebook, by design.

### The web app says "Your notebook cannot show this yet."

The gateway answered 501: the installed `abstractnote` lacks a read the web app asks for.
Install the same version of `abstractnote` on the gateway as the web app expects, then restart
the gateway.

## Attachments

### Kept the file, but its text could not be read

Capture results carry warnings such as "Kept photo.jpg; no image model is configured to
describe it.", "Kept the recording memo.m4a with its details; no speech model is configured to
transcribe it." or "Kept report.pdf; its text could not be read here (PDF reading is not
installed on this machine)." The file is always stored and linked to the note; only its text is
missing. Image descriptions and transcripts need the extractors described in
[faq.md](faq.md#does-abstractnote-describe-images-and-transcribe-recordings).
