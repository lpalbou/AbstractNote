# Getting started

This guide takes you from an install to a working notebook in two steps: first a
**headless notebook** on your machine (no gateway, and no model until you want one), then the
**gateway path** with the web app and the Apple Watch app. For what AbstractNote is, see the
[README](../README.md); for how the parts fit, see [architecture.md](architecture.md). If a step
fails, [troubleshooting.md](troubleshooting.md) lists the messages you may see and their fixes.

## 1. Install

You need Python 3.10 or later. In a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install "abstractnote[gateway]"
.venv/bin/abstractnote --help
```

The `[gateway]` extra adds AbstractGateway 0.17.0 or later (the AbstractNote integration has been in the
gateway since 0.15.11); leave it out (`pip install abstractnote`) if you only want a headless
notebook. AbstractMemory 0.4.0 or later, AbstractRuntime 0.15.0 or later and AbstractCore come with the package.

The examples below call `abstractnote` directly; activate the environment first
(`. .venv/bin/activate`) or prefix the commands with `.venv/bin/`.

### Install from source

To work on AbstractNote itself, clone the framework packages next to this repository (the
AbstractFramework `scripts/clone.sh` clones them side by side) and install them editable; see
[CONTRIBUTING.md](../CONTRIBUTING.md#development-environment). The scratch gateway in section 3
(`scripts/hermetic_gateway.sh`) and the web app's `npm run dev` need this checkout.

## 2. A headless notebook in five minutes

A notebook is a directory. Every command takes `--notebook DIR` and creates the directory the
first time. Output is JSON on standard output, so you can pipe it to `jq` or read it from a
script.

### Choose the embedding model first

A notebook is **pinned to the embedding model that creates it**: the first command that opens
the notebook (a capture included) records the model, and every later command must use the same
one. You have two choices:

- **No flags**: the built-in hashing embedder. It is offline and deterministic, and it matches
  exact and near-exact wording well, but it is not a semantic model.
- **An OpenAI-compatible embedding server**, for example a local LM Studio:
  `--embed-base-url http://127.0.0.1:1234/v1 --embed-model text-embedding-qwen3-embedding-0.6b`.
  Give the same two flags on every command for that notebook.

You can change models later with `abstractnote rebuild --reembed` plus the new flags; it replays
the whole notebook into fresh stores with the new vectors.

### Capture, then look

```sh
abstractnote capture "Met Dana about the LUT budget; she wants the Pareto by Friday." \
    --notebook ~/notes --at 2026-10-19T09:12:00Z --key first-note
abstractnote capture "The plan for the review" --file plan.md --notebook ~/notes
abstractnote search "LUT budget" --notebook ~/notes
abstractnote changes --notebook ~/notes
abstractnote day 2026-10-19 --notebook ~/notes
abstractnote status --notebook ~/notes
```

- `--key` is an idempotency key: sending the same key again answers `"status": "duplicate"` and
  saves nothing new.
- `--file` attaches a file (repeatable). Text, Markdown and PDF attachments are read and
  indexed; the bytes are stored once per distinct content.
- `search` at the default **quick** effort uses full-text plus vector search and no model.
- `status` shows the counts, the queue and the embedding pin.

A note captured from the command line is stored and queued at once. Its understanding (summary,
topics, entities, claims, tasks) comes from processing, below.

### Process the queue with a model

Processing runs the `note-process` flow for each queued note, with the same flows the gateway
runs. A small fast model is recommended. On Apple silicon, install the MLX extra once and use a
mixture-of-experts model such as Qwen3.6 35B-A3B (4-bit):

```sh
pip install "abstractcore[apple]"
abstractnote process --local --notebook ~/notes \
    --provider mlx --model mlx-community/Qwen3.6-35B-A3B-4bit
```

`--max-items` (default 20) and `--budget-s` (default 90) bound one pass; run it again until
`status` shows an empty queue. Any provider that AbstractCore supports works with
`--provider` and `--model` (for example `lmstudio` or `ollama` with a model they serve).

### Ask a question

```sh
abstractnote search "What did Dana ask for, and did it change?" --effort standard \
    --notebook ~/notes --provider mlx --model mlx-community/Qwen3.6-35B-A3B-4bit
```

`standard` expands the quick hits through the graph and writes one answer with `[nt_...]`
citations; `deep` plans several search rounds and stores a dossier as a note. See
[remembering.md](remembering.md) for the design of the effort levels.

### Import what you already have

Imports write notes at bulk priority with their original dates, so live notes are always
processed first. Count first with `--dry-run`, then import:

```sh
abstractnote import markdown ./docs --notebook ~/notes --dry-run
abstractnote import markdown ./docs --notebook ~/notes
abstractnote import files ~/Pictures/whiteboards --notebook ~/notes
```

`claude-sessions` and `agora` work the same way; see [importers.md](importers.md) and the CLI
reference in [api.md](api.md#cli).

## 3. The gateway path

Inside AbstractGateway every user gets their own notebook, the flows run as durable runs, a
processor automation drains the queue every two minutes, and the web and watch apps connect to
it. The integration is on as soon as `abstractnote` is installed in the gateway's own Python
environment: install `"abstractnote[gateway]"` there (section 1) and restart the gateway.

The gateway must carry the AbstractNote seam, its `abstractgateway.abstractnote_seam` module, which
AbstractGateway has from 0.15.11. Importing `abstractnote.gateway` into an older gateway fails at
once with one sentence ("This AbstractGateway does not carry the AbstractNote seam …"), never
half-working.

To check a running gateway, ask for the note status as a signed-in account: `200` means the note
API is mounted, `404` means this gateway has no AbstractNote integration (without a token every
`/api/gateway` path answers `401`, so that tells you nothing).

```sh
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18851/api/gateway/note/status \
     -H "Authorization: Bearer <alice's token>"   # 200
```

### Try it on a private scratch gateway

`scripts/hermetic_gateway.sh` starts a gateway with AbstractNote on `127.0.0.1:18851`, in a
scratch data directory, with a clean environment and a scripted stand-in model (no weights, fixed
answers), and creates two user accounts. It prints each account's token as a parameter you can
paste:

```sh
VENV=$PWD/.venv ROOT=$PWD/untracked/hermetic scripts/hermetic_gateway.sh start
# gateway: http://127.0.0.1:18851 ...
# alice:  --token <alice's token>
# bob:    --token <bob's token>
```

Create the processor and reflect automations for an account (the web app does this for you when
you open it and they are missing):

```sh
curl -s -X POST http://127.0.0.1:18851/api/gateway/note/processor/ensure \
     -H "Authorization: Bearer <alice's token>"
# {"processor_id": "...", "reflect_id": "...", "created": ["note-drain", "note-reflect"]}
```

Send a note through the runs API and read the answer:

```sh
curl -s -X POST http://127.0.0.1:18851/api/gateway/runs/start \
     -H "Authorization: Bearer <alice's token>" -H 'Content-Type: application/json' \
     -d '{"bundle_id": "abstractnote", "flow_id": "note-capture", "session_id": "demo",
          "input_data": {"text": "Met Dana about the LUT budget.", "session_id": "demo",
                         "idempotency_key": "demo-1", "source": {"client": "cli"}}}'
curl -s http://127.0.0.1:18851/api/gateway/runs/<run_id> -H "Authorization: Bearer <alice's token>"
# "output": {"response": "Saved as ...", "note_id": "nt_...", "status": "queued", ...}
```

Stop it with `VENV=$PWD/.venv ROOT=$PWD/untracked/hermetic scripts/hermetic_gateway.sh stop`.
The scratch directory stays until you delete it. Ports must be in the 18800-18899 range
(`PORT` and `STUB_PORT` choose others).

### The web app

The web app needs Node.js 18 or later. A gateway installs and serves it from its app catalog as
the Note app (the npm package `@abstractframework/note`). From a source checkout, against a
gateway:

```sh
cd web
npm install
npm run dev -- --gateway-url http://127.0.0.1:18851
```

Open `http://127.0.0.1:3006/` and sign in with an account token. To look around without a
gateway, `npm run dev:mock` serves the app with a built-in sample notebook. `npm run build`
then `npm start -- --gateway-url <url>` runs the production server on `127.0.0.1:3006`, the way
the gateway serves it at `/apps/note/`.

### The Apple Watch app

The watch app needs Xcode 27.1 with the watchOS 26.5 simulator runtime and XcodeGen.

```sh
cd watch/apple
scripts/build.sh
```

This generates the project, runs the Swift package tests, builds for the simulator and takes a
screenshot of every screen with the built-in sample notebook. Signing in to your own gateway and
installing on a paired watch are described in [watch/apple/BUILD.md](../watch/apple/BUILD.md).

### Accounts

Use a `user` account per person or device, never an admin token. Each account sees only its own
notebook; [clients.md](clients.md) explains sign-in and isolation.

## Next steps

- [api.md](api.md): every HTTP route, the note tools, the Python API and the CLI.
- [cognition.md](cognition.md): what the flows do, step by step.
- [faq.md](faq.md): limits and common questions.
