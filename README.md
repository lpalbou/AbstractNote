# AbstractNote

One endpoint for everything you want to remember. Send a sentence, a document, a photo, a
recording, a log. AbstractNote files it with its time, place and provenance, links it to what
you already know, and grows a **temporal semantic knowledge graph** of your ideas, projects,
people and decisions. You search it by effort (quick, standard, deep) and revisit it through
time: what you knew, when you learned it, and how your view changed.

AbstractNote is part of the AbstractFramework. It runs on **AbstractGateway** (sign-in, one
runtime and one notebook per user, app hosting), **AbstractRuntime** and **AbstractFlow** (the
capture and understanding workflows), **AbstractMemory** (temporal facts) and **AbstractCore**
(models, structured output, embeddings).

Status: version 0.1.1, alpha. The Python package is `abstractnote` on PyPI and the web app is
`@abstractframework/note` on npm (see [Install](#install)).

## What you get

- **Capture anywhere**: the web app, the Apple Watch app, the `abstractnote` command line, and
  agents. Attachments are de-duplicated by content; a retried send never saves a note twice.
- **Automatic filing**: a small fast model gives each note a title, a kind, topics, an urgency
  and its language, next to the time and place it was made.
- **Self-organising knowledge**: a model resolves people, projects and ideas against candidates
  that exact lookups, full-text and vectors produce. Every change is a revision you can replay,
  and every claim keeps the note it came from.
- **Remember by cue and effort**: quick (no model, under a second), standard (one synthesis with
  citations), deep (a background dossier). Then-versus-now for any person, project, idea or link.
- **Reflection**: open loops, contradictions, things that went quiet and connections you may have
  missed, surfaced as insights.
- **Imports**: Claude Code sessions, an agora hub database, Markdown trees and plain files, with
  their original dates and provenance.

## Install

AbstractNote needs Python 3.10 or later. Install it with the gateway integration:

```sh
pip install "abstractnote[gateway]"
abstractnote --help
```

This brings AbstractMemory 0.3.1 or later and AbstractGateway 0.15.11 or later, the first gateway
release that carries the AbstractNote integration (`abstractgateway.abstractnote_seam`): with
`abstractnote` installed in the gateway's environment, every user gets the note tools, the note
read API under `/api/gateway/note` and the note workflows. `pip install abstractnote` alone is
enough for a headless notebook on one machine. To run models locally on Apple silicon, add
`pip install "abstractcore[apple]"` (MLX).

The web app is the npm package `@abstractframework/note`, which the gateway's app catalog installs
and serves as the Note app. To work on AbstractNote itself, install it from a source checkout; see
[CONTRIBUTING.md](CONTRIBUTING.md) and [docs/getting-started.md](docs/getting-started.md#install-from-source).

## Quick start

A headless notebook on this machine, no gateway and no model:

```sh
abstractnote capture "Met Dana about the LUT budget; she wants the Pareto by Friday." --notebook ~/notes
abstractnote search "LUT budget" --notebook ~/notes
abstractnote status --notebook ~/notes
```

Let a model understand the queued notes (entities, claims, tasks), then ask a question:

```sh
abstractnote process --local --notebook ~/notes --provider mlx --model mlx-community/Qwen3.6-35B-A3B-4bit
abstractnote search "What does Dana want?" --effort standard --notebook ~/notes \
    --provider mlx --model mlx-community/Qwen3.6-35B-A3B-4bit
```

A notebook is pinned to the embedding model that created it. Choose it on the first command
(`--embed-base-url` and `--embed-model`, for example a local LM Studio embedding model) and give
the same flags every time, or start with the built-in offline embedder and change later with
`abstractnote rebuild --reembed`. See [docs/getting-started.md](docs/getting-started.md).

Through a gateway, clients start the `note-capture` and `note-recall` flows with the gateway's
runs API, and `POST /api/gateway/note/processor/ensure` creates the per-user processor that
understands notes in the background. The getting-started guide walks through a private scratch
gateway, the web app and the watch app.

## Clients

| Client | Where | What it does |
|---|---|---|
| Web app | [`web/`](web/) (`@abstractframework/note`) | Capture (a chat with attachments), Remember (since you last looked, cue cards, ask by effort, then and now), Explore (entities and links through time), Processing (what the notebook is doing with your notes, live: the queue and its pace, the notes being understood, failures, and the scheduled consolidation), and a Settings panel |
| Apple Watch app | [`watch/apple/`](watch/apple/README.md) | Note, Ask, Today, Settings; watch-only, watchOS 26 or later |
| Command line | `abstractnote` | Headless capture, search, processing, imports, rebuild, JSON output for scripts and agents |

An iPhone app is planned ([`phone/iphone/`](phone/iphone/README.md)). Agents use the same note
tools and flows, with the effort level as a parameter.

## Documentation

- [docs/README.md](docs/README.md): the documentation index
- [Getting started](docs/getting-started.md): a headless notebook in five minutes, then the gateway path
- [Architecture](docs/architecture.md): components, data plane, request lifecycles
- [API](docs/api.md): runs API usage, note read API, note tools, Python API, CLI
- [Data model](docs/data-model.md), [Cognition](docs/cognition.md), [Remembering](docs/remembering.md),
  [Clients](docs/clients.md), [Importers](docs/importers.md)
- [FAQ](docs/faq.md) and [Troubleshooting](docs/troubleshooting.md)
- [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), [Changelog](CHANGELOG.md),
  [Acknowledgements](ACKNOWLEDGEMENTS.md)

## License

MIT. See [LICENSE](LICENSE).
