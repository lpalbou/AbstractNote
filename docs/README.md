# AbstractNote documentation

Start with the [README](../README.md) for what AbstractNote is, then
[getting-started.md](getting-started.md) to run it. [architecture.md](architecture.md) explains how
the parts fit; the topic pages go deeper into one area each and link back to it.

## Core pages

| Page | What it is for |
|---|---|
| [getting-started.md](getting-started.md) | Install, a headless notebook in five minutes, then the gateway path with the web and watch apps. |
| [architecture.md](architecture.md) | Principles, components, the data plane, request lifecycles, dependencies and boundaries. Start here to understand the system. |
| [api.md](api.md) | The gateway runs API as AbstractNote uses it, the note read API, the note tools, the Python API and the CLI. |
| [faq.md](faq.md) | Short answers to common questions, and the limits of this version. |
| [troubleshooting.md](troubleshooting.md) | Messages and symptoms, their causes, and the fixes. |

## Topic pages

| Page | What it is for |
|---|---|
| [data-model.md](data-model.md) | Notes, attachments, entities, claims, revisions, time semantics, de-duplication and the AbstractMemory projection. |
| [cognition.md](cognition.md) | The flows (capture, process, drain, recall, mine, reflect), the queue, the schemas and prompts, headless execution. |
| [remembering.md](remembering.md) | How recall is designed around cues and effort; the Capture, Remember, Explore and Processing views; agents as users. |
| [clients.md](clients.md) | The web app, the Apple Watch app, the iPhone plan, sign-in and isolation. |
| [importers.md](importers.md) | Turning Claude Code sessions, an agora hub database, Markdown trees and files into notes. |

## Decisions and planning

| Page | What it is for |
|---|---|
| [adr/README.md](adr/README.md) | Architecture decision records: the rules that outlive a single change. |
| [backlog/overview.md](backlog/overview.md) | Work items, priorities and history. |

## Project policies

[CONTRIBUTING.md](../CONTRIBUTING.md), [SECURITY.md](../SECURITY.md),
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md), [CHANGELOG.md](../CHANGELOG.md),
[ACKNOWLEDGEMENTS.md](../ACKNOWLEDGEMENTS.md), [LICENSE](../LICENSE). Maintainers:
[release.md](release.md) (rehearsing and publishing a release).

The app folders have their own guides: [watch/apple/README.md](../watch/apple/README.md),
[watch/apple/BUILD.md](../watch/apple/BUILD.md) and [phone/iphone/README.md](../phone/iphone/README.md).
